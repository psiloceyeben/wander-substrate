"""Day 7-8 link graph + city layout.

Reads cards.jsonl (each card carries `features.outbound_hosts`), filters edges
to only those between known hosts, runs force-directed layout for (x, y) per node,
detects communities for district labels.

Output: graph.json
{
  "nodes": {
    "<registrable_root>": {"x": float, "y": float, "cluster": int,
                           "score": float, "title": str, "url": str}
  },
  "clusters": [{"id": int, "size": int, "centroid": [x, y], "label": str}],
  "edges": [[a, b], ...],
}
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import networkx as nx
import tldextract


def registrable_root(host: str) -> str:
    ext = tldextract.extract(host)
    if not (ext.suffix and ext.domain):
        return ""
    return f"{ext.domain}.{ext.suffix}".lower()


def load_cards(path: Path) -> list[dict]:
    cards = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("scores", {}).get("killed"):
                continue
            if "features" not in rec:
                continue
            cards.append(rec)
    return cards


def load_cards_from_db(conn) -> tuple[list[dict], dict[str, set[str]]]:
    """Load cards from SQLite. Returns (cards_list, edges_by_src_root).

    Each card dict mimics the JSONL shape: {url, scores:{composite}, features:{...}}.
    Edges are returned separately because they're stored in link_edges, not on cards."""
    cards = []
    rows = conn.execute(
        """
        SELECT id, url, host, registrable_root, title, era,
               composite_score, anti_seo_score, hand_made_score,
               word_count, n_external_scripts, h1_count, image_count, iframe_count,
               has_old_html_tags, has_email_contact, has_template_cms, has_amp,
               has_analytics, has_ad_network
        FROM cards
        WHERE is_useful_content = 1
        """
    ).fetchall()

    for r in rows:
        cards.append({
            "url": r["url"],
            "scores": {"composite": float(r["composite_score"])},
            "features": {
                "title": r["title"] or "",
                "word_count": r["word_count"] or 0,
                "n_external_scripts": r["n_external_scripts"] or 0,
                "h1_count": r["h1_count"] or 0,
                "image_count": r["image_count"] or 0,
                "iframe_count": r["iframe_count"] or 0,
                "has_old_html_tags": bool(r["has_old_html_tags"]),
                "has_email_contact": bool(r["has_email_contact"]),
                "has_template_cms": bool(r["has_template_cms"]),
                "has_amp": bool(r["has_amp"]),
                "has_analytics": bool(r["has_analytics"]),
                "has_ad_network": bool(r["has_ad_network"]),
            },
            "_db_id": r["id"],
            "_root": r["registrable_root"],
        })

    edges: dict[str, set[str]] = {}
    for r in conn.execute("SELECT src_root, dst_root FROM link_edges").fetchall():
        edges.setdefault(r["src_root"], set()).add(r["dst_root"])

    # Inject outbound_hosts from edges so build_graph() can use the same path.
    by_root = {c["_root"]: c for c in cards if c.get("_root")}
    for root, dsts in edges.items():
        if root in by_root:
            by_root[root]["features"]["outbound_hosts"] = sorted(dsts)

    return cards, edges


def write_layout_to_db(conn,
                       positions: dict[str, tuple[float, float]],
                       clusters: dict[str, int]) -> int:
    """Persist (x, y, cluster_id) per registrable_root back to the cards table."""
    updates = []
    for root in positions.keys() | clusters.keys():
        x, y = positions.get(root, (None, None))
        cid = clusters.get(root, -1)
        updates.append((x, y, cid, root))
    cur = conn.executemany(
        "UPDATE cards SET x = ?, y = ?, cluster_id = ? WHERE registrable_root = ?",
        updates,
    )
    return cur.rowcount


def build_graph(cards: list[dict]) -> nx.Graph:
    """Build an undirected graph among hosts in our pool. Edge exists if either
    direction of linkage exists. Self-loops dropped."""
    g = nx.Graph()

    from urllib.parse import urlparse
    by_root: dict[str, dict] = {}
    for c in cards:
        host = urlparse(c["url"]).netloc.lower()
        root = registrable_root(host)
        if not root:
            continue
        by_root[root] = c

    pool = set(by_root.keys())
    for root, c in by_root.items():
        g.add_node(
            root,
            url=c["url"],
            title=(c["features"].get("title") or "")[:120],
            score=c["scores"].get("composite", 0.0),
            era=infer_era(c["features"]),
            n_external_scripts=c["features"].get("n_external_scripts", 0),
            word_count=c["features"].get("word_count", 0),
            host=urlparse(c["url"]).netloc.lower(),
        )

    edge_count = 0
    for root, c in by_root.items():
        outbound = c["features"].get("outbound_hosts", []) or []
        for other in outbound:
            other_root = registrable_root(other) if "." in other else other
            if not other_root or other_root == root:
                continue
            if other_root in pool:
                g.add_edge(root, other_root)
                edge_count += 1

    return g


def infer_era(features: dict) -> str:
    """Coarse era classification used by both layout colouring and the facade."""
    if features.get("has_old_html_tags"):
        return "old_web"          # 1995-2005 hand-coded
    if features.get("has_template_cms"):
        return "template"          # WordPress / Squarespace / Wix era
    if features.get("n_external_scripts", 0) > 25:
        return "modern_spa"        # 2018-now React/SPA
    if features.get("has_amp") or features.get("has_schema_org") and features.get("has_opengraph"):
        return "seo_hardened"      # 2014-2020 SEO-optimized
    return "midweb"                # 2005-2014 default


def layout(g: nx.Graph, scale: float = 1000.0, seed: int = 7) -> dict[str, tuple[float, float]]:
    """Force-directed layout. Returns {node: (x, y)} in [-scale, scale]."""
    if g.number_of_nodes() == 0:
        return {}
    # Use largest connected component for stability; isolates get random scatter.
    components = sorted(nx.connected_components(g), key=len, reverse=True)
    main = g.subgraph(components[0]).copy() if components else g
    pos = nx.spring_layout(main, seed=seed, k=1.0 / math.sqrt(max(main.number_of_nodes(), 1)), iterations=80)

    # Scatter isolates around the perimeter.
    import random
    rng = random.Random(seed)
    isolates: list[str] = []
    for comp in components[1:]:
        isolates.extend(comp)

    for n in isolates:
        angle = rng.uniform(0, 2 * math.pi)
        radius = rng.uniform(1.05, 1.6)
        pos[n] = (math.cos(angle) * radius, math.sin(angle) * radius)

    # Normalize/scale.
    return {n: (float(x) * scale, float(y) * scale) for n, (x, y) in pos.items()}


def cluster(g: nx.Graph) -> dict[str, int]:
    """Greedy modularity communities. Returns {node: cluster_id}."""
    if g.number_of_edges() == 0:
        return {n: 0 for n in g.nodes()}
    try:
        communities = nx.community.greedy_modularity_communities(g)
    except Exception:
        communities = [set(g.nodes())]
    out: dict[str, int] = {}
    for cid, comm in enumerate(communities):
        for n in comm:
            out[n] = cid
    # Isolates get a sentinel cluster.
    for n in g.nodes():
        out.setdefault(n, -1)
    return out


def label_clusters(g: nx.Graph, clusters: dict[str, int]) -> dict[int, str]:
    """Label each cluster by the most common era among its members."""
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for node, cid in clusters.items():
        era = g.nodes[node].get("era", "midweb")
        by_cluster[cid].append(era)
    out = {}
    for cid, eras in by_cluster.items():
        if cid == -1:
            out[cid] = "outskirts"
            continue
        # Mode era.
        from collections import Counter
        out[cid] = Counter(eras).most_common(1)[0][0]
    return out


def export_graph(g: nx.Graph,
                 positions: dict[str, tuple[float, float]],
                 clusters: dict[str, int],
                 cluster_labels: dict[int, str]) -> dict:
    nodes = {}
    for n in g.nodes():
        x, y = positions.get(n, (0.0, 0.0))
        nodes[n] = {
            "x": x,
            "y": y,
            "cluster": clusters.get(n, -1),
            "score": g.nodes[n].get("score", 0.0),
            "title": g.nodes[n].get("title", ""),
            "url": g.nodes[n].get("url", ""),
            "era": g.nodes[n].get("era", "midweb"),
            "host": g.nodes[n].get("host", ""),
        }

    cluster_summary = []
    for cid, members in defaultdict_groupby(clusters).items():
        if not members:
            continue
        cx = sum(positions.get(m, (0, 0))[0] for m in members) / len(members)
        cy = sum(positions.get(m, (0, 0))[1] for m in members) / len(members)
        cluster_summary.append({
            "id": cid,
            "size": len(members),
            "centroid": [cx, cy],
            "label": cluster_labels.get(cid, "outskirts"),
        })

    edges = [list(e) for e in g.edges()]
    return {"nodes": nodes, "clusters": cluster_summary, "edges": edges}


def defaultdict_groupby(d: dict[str, int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for k, v in d.items():
        out[v].append(k)
    return out
