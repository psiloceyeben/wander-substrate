"""Compute the wiki spatial layout — the same wedge-fill mandala that powers
/world and /game, applied to ~6.8M Wikipedia articles instead of crawled
websites.

Reuses the geometric helpers from `scripts/10_layout_ring.py` (variable-arm
pinwheel with per-category outer radii, polar ring fill within wedge cells,
walkable angular alleys and radial avenues). Only differs in:

  - Source DB: data/wiki/wiki.db (table `articles`, not `cards`)
  - Categories: 12 encyclopedic buckets from 03_classify.py
  - Score: log10(word_count + 1) — biggest articles sit at the outer rim of
    their sector (most "important" by length proxy)
  - Era: derived from word_count quintile (proxy for stub→FA quality)
  - Site_type: collapsed since wiki has no equivalent — bucket all articles
    into 4 size classes (stub, short, medium, long) for the sub-sector axis

Output: data/wiki/layout_wiki.json with the same JSON shape as
data/layout_categories.json so /api/game/layout?mode=wiki and the existing
/api/game/region endpoint can serve it without any Python changes.

Run:
    python3 scripts/wiki/04_layout_wiki.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

# ── Load the shared geometry helpers from 10_layout_ring.py ──────────
# That module isn't a regular import target (numeric prefix), so we load via
# importlib. The functions we need: wedge_fill_positions, compute_R_OUTER_from_counts.
_ring_path = Path(__file__).resolve().parent.parent / "10_layout_ring.py"
_spec = importlib.util.spec_from_file_location("layout_ring_module", _ring_path)
_ring = importlib.util.module_from_spec(_spec)
sys.modules["layout_ring_module"] = _ring
_spec.loader.exec_module(_ring)
wedge_fill_positions = _ring.wedge_fill_positions
compute_R_OUTER_from_counts = _ring.compute_R_OUTER_from_counts

# ── Configuration ─────────────────────────────────────────────────────
DB = Path("data/wiki/wiki.db")
OUT = Path("data/wiki/layout_wiki.json")

# 12 wiki buckets — each with a color. Distinct from /world's web cats.
WIKI_CATEGORIES: list[tuple[str, str]] = [
    ("science",       "#60c0a0"),
    ("history",       "#c08850"),
    ("geography",     "#7aa9d8"),
    ("arts",          "#e0a060"),
    ("technology",    "#a8c068"),
    ("sports",        "#e0c060"),
    ("religion",      "#c870c0"),
    ("society",       "#9aa9bc"),
    ("entertainment", "#e07050"),
    ("nature",        "#80c8d0"),
    ("biography",     "#d8a050"),
    ("misc",          "#6a7480"),
]

# Sub-sectors (size class) — wiki articles don't have site_type, but we want
# the same 4-sub-sector visual structure for consistency with /world's mandala.
# Bucket by word_count quartile within each category.
SUB_SECTORS = [
    ("stub",   -0.375),  # < 25th percentile (within cat)
    ("short",  -0.125),  # 25-50
    ("medium",  0.125),  # 50-75
    ("long",    0.375),  # 75-100
]

# Era as proxy for "stub → start → C → B → GA/FA" quality (5 bands), keyed by
# word count: <500=stub, 500-2K=start, 2-5K=C, 5-15K=B, 15K+=A/FA
ERA_BAND_ORDER = ["stub", "start", "c_class", "b_class", "fa_class"]
N_ERA_BANDS = len(ERA_BAND_ORDER)
N_SCORE_QUARTILES = 4


def era_for_wc(wc: int) -> str:
    if wc < 500:    return "stub"
    if wc < 2000:   return "start"
    if wc < 5000:   return "c_class"
    if wc < 15000:  return "b_class"
    return "fa_class"


def sub_for_wc(wc: int, thresholds: tuple[int, int, int]) -> str:
    if wc <= thresholds[0]: return "stub"
    if wc <= thresholds[1]: return "short"
    if wc <= thresholds[2]: return "medium"
    return "long"


# Module-level overrides (matching 10_layout_ring's _LAYOUT_OPTS pattern).
# Wiki is more compact than /world (which uses buffer 5.0) but not too tight —
# at buffer 2.0 the per-cell capacity dropped so far that the defensive tail
# loop spilled cards back into the plaza, making the world look empty from
# spawn. 3.0 / 3500 keeps wedge cells loose enough to not overflow while still
# producing a noticeably-tighter mandala than /world.
_ring._LAYOUT_OPTS["where_extra"] = ""
_ring._LAYOUT_OPTS["r_outer_buffer"] = 3.0
_ring._LAYOUT_OPTS["r_outer_min"] = 3500.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--max-cards", type=int, default=200000,
                    help="cap on articles in layout (memory + JSON size; default 200K like /world)")
    args = ap.parse_args()

    db_path = Path(args.db)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cats = [c[0] for c in WIKI_CATEGORIES]
    cat_color = dict(WIKI_CATEGORIES)
    sector_count = len(cats)
    sector_arc = 2 * math.pi / sector_count

    # Pull top-N articles by word_count (a proxy for "important" / "fully fleshed out")
    print("[wiki-layout] selecting top articles by word_count...", flush=True)
    rows = conn.execute(
        """
        SELECT id, title, language, word_count, category
        FROM articles
        WHERE category IS NOT NULL AND is_disambig = 0 AND word_count > 0
        ORDER BY word_count DESC
        LIMIT ?
        """,
        (args.max_cards,),
    ).fetchall()
    print(f"[wiki-layout] {len(rows):,} articles selected", flush=True)

    # Score: log10(word_count + 1) — keeps the spread reasonable
    # (typical wiki article: 500-50000 words → score 2.7-4.7)

    # Per-category outer radii (variable-arm pinwheel, same as /world)
    from collections import Counter
    sample_counts = Counter(r["category"] for r in rows if r["category"] in cats)
    R_OUTER_CAT = compute_R_OUTER_from_counts(sample_counts, cats)
    print(f"[wiki-layout] per-cat R_OUTER: " + ", ".join(
        f"{c}={R_OUTER_CAT[c]:.0f}" for c in cats), flush=True)

    # Per-category word_count quartile thresholds (for sub-sector assignment)
    cat_wc_thresholds: dict[str, tuple[int, int, int]] = {}
    cat_wcs: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["category"] in cats:
            cat_wcs[r["category"]].append(int(r["word_count"]))
    for cat, wcs in cat_wcs.items():
        wcs.sort()
        if len(wcs) < 4:
            cat_wc_thresholds[cat] = (wcs[0], wcs[0], wcs[0]) if wcs else (0, 0, 0)
        else:
            cat_wc_thresholds[cat] = (
                wcs[int(len(wcs) * 0.25)],
                wcs[int(len(wcs) * 0.50)],
                wcs[int(len(wcs) * 0.75)],
            )

    # Per-cat per-era band widths (proportional to era counts within cat)
    R_INNER = _ring.R_INNER
    cat_era_counts = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cat = r["category"]
        if cat in cats:
            era = era_for_wc(int(r["word_count"]))
            cat_era_counts[cat][era] += 1
    MIN_ERA_WIDTH_FRAC = 0.06
    cat_era_widths: dict[str, dict[str, float]] = {}
    cat_era_inner: dict[str, dict[str, float]] = {}
    for cat in cats:
        available = R_OUTER_CAT[cat] - R_INNER
        total_in_cat = sum(cat_era_counts[cat].get(era, 0) for era in ERA_BAND_ORDER)
        if total_in_cat == 0:
            cat_era_widths[cat] = {era: available / N_ERA_BANDS for era in ERA_BAND_ORDER}
        else:
            min_w = available * MIN_ERA_WIDTH_FRAC
            raw = {era: available * cat_era_counts[cat].get(era, 0) / total_in_cat for era in ERA_BAND_ORDER}
            adjusted = {era: max(min_w, w) for era, w in raw.items()}
            scale = available / sum(adjusted.values())
            cat_era_widths[cat] = {era: w * scale for era, w in adjusted.items()}
        cumulative = R_INNER
        cat_era_inner[cat] = {}
        for era in ERA_BAND_ORDER:
            cat_era_inner[cat][era] = cumulative
            cumulative += cat_era_widths[cat][era]

    # Score quartile per (cat, era) cell — for radial sub-band placement
    cat_era_score_thresholds: dict[tuple[str, str], tuple[float, float, float]] = {}
    cat_era_scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        cat = r["category"]
        wc = int(r["word_count"])
        era = era_for_wc(wc)
        if cat in cats:
            cat_era_scores[(cat, era)].append(math.log10(wc + 1))
    for k, scores_list in cat_era_scores.items():
        scores_list.sort()
        if len(scores_list) < 4:
            cat_era_score_thresholds[k] = (0.0, 0.0, 0.0)
        else:
            cat_era_score_thresholds[k] = (
                scores_list[int(len(scores_list) * 0.25)],
                scores_list[int(len(scores_list) * 0.50)],
                scores_list[int(len(scores_list) * 0.75)],
            )

    def quartile_of(score: float, cat: str, era: str) -> int:
        thr = cat_era_score_thresholds.get((cat, era), (0.0, 0.0, 0.0))
        if score < thr[0]: return 0
        if score < thr[1]: return 1
        if score < thr[2]: return 2
        return 3

    # Group cards by leaf cell (cat, sub_name, era_idx, quartile)
    sub_offsets = {name: off for name, off in SUB_SECTORS}
    groups: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        cat = r["category"]
        if cat not in cats:
            continue
        wc = int(r["word_count"])
        sub_name = sub_for_wc(wc, cat_wc_thresholds.get(cat, (0, 0, 0)))
        era = era_for_wc(wc)
        era_idx = ERA_BAND_ORDER.index(era)
        score = math.log10(wc + 1)
        qi = quartile_of(score, cat, era)
        groups[(cat, sub_name, era_idx, qi)].append(r)
    for k in groups:
        groups[k].sort(key=lambda r: -int(r["word_count"]))  # high-wc first within cell

    # Place cards via wedge_fill_positions (same algorithm as /world)
    sub_arc_size = sector_arc * 0.25  # 7.5° per sub-sector
    cards_out: list[dict] = []
    WEDGE_ANGULAR_PADDING = _ring.WEDGE_ANGULAR_PADDING

    print("[wiki-layout] placing articles via wedge fill...", flush=True)
    for key, group_rows in groups.items():
        cat, sub_name, era_idx, qi = key
        sector_idx = cats.index(cat)
        sector_center = sector_idx * sector_arc - math.pi / 2
        sub_center_offset = sub_offsets[sub_name] * sector_arc
        sub_center_angle = sector_center + sub_center_offset
        padded_arc = sub_arc_size * WEDGE_ANGULAR_PADDING
        cell_jitter_offset = (era_idx * 0.37 + qi * 0.21) * 0.0005
        cell_min_angle = sub_center_angle - padded_arc * 0.5 + cell_jitter_offset
        era_name = ERA_BAND_ORDER[era_idx]
        era_band_width = cat_era_widths[cat][era_name]
        score_subband_width = era_band_width / N_SCORE_QUARTILES
        era_band_inner = cat_era_inner[cat][era_name]
        cell_inner_r = era_band_inner + qi * score_subband_width
        cell_outer_r = cell_inner_r + score_subband_width
        positions = wedge_fill_positions(
            cell_inner_r, cell_outer_r,
            cell_min_angle, padded_arc,
            len(group_rows),
        )
        for (x, y), r in zip(positions, group_rows):
            wc = int(r["word_count"])
            cards_out.append({
                "id": r["id"],
                "host": r["title"][:80],   # title fits the host slot in card schema
                "score": round(math.log10(wc + 1), 2),
                "era": era_for_wc(wc),
                "language": r["language"] or "en",
                "category": cat,
                "site_type": sub_name,    # the sub-sector becomes the site_type field
                "x": round(x, 1),
                "y": round(y, 1),
                "wc": wc,
            })

    # Outer-perimeter category labels
    R_LABEL_PAD = _ring.R_LABEL_PAD
    labels: list[dict] = []
    for i, (cat, color) in enumerate(WIKI_CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        r_label_cat = R_OUTER_CAT[cat] + R_LABEL_PAD
        labels.append({
            "text": cat,
            "x": round(math.cos(sector_center) * r_label_cat, 1),
            "y": round(math.sin(sector_center) * r_label_cat, 1),
            "angle_deg": round(math.degrees(sector_center), 1),
            "color": color,
            "kind": "category",
        })

    # Sub-sector "district" labels at mid-radius
    sub_color = {
        "stub":   "#6a7480",
        "short":  "#9aa9bc",
        "medium": "#a8c068",
        "long":   "#d8a050",
    }
    for i, (cat, _color) in enumerate(WIKI_CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        r_sub_label = (R_INNER + R_OUTER_CAT[cat]) / 2
        for sub_name, sub_off in SUB_SECTORS:
            angle = sector_center + sub_off * sector_arc
            labels.append({
                "text": sub_name,
                "x": round(math.cos(angle) * r_sub_label, 1),
                "y": round(math.sin(angle) * r_sub_label, 1),
                "angle_deg": round(math.degrees(angle), 1),
                "color": sub_color[sub_name],
                "kind": "district",
                "parent": cat,
            })

    # Trails — radial roads, one per category sector
    TRAIL_INNER = _ring.TRAIL_INNER
    trails: list[dict] = []
    for i, (cat, color) in enumerate(WIKI_CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        trail_outer = R_OUTER_CAT[cat] + R_LABEL_PAD
        points = []
        n_segments = 8
        for s in range(n_segments + 1):
            t = s / n_segments
            r_t = TRAIL_INNER + t * (trail_outer - TRAIL_INNER)
            wobble = math.sin(t * math.pi * 2 + i * 0.7) * (sector_arc * 0.04) * (1 - abs(t - 0.5) * 2)
            angle = sector_center + wobble
            points.append([round(math.cos(angle) * r_t, 1), round(math.sin(angle) * r_t, 1)])
        trails.append({"sector": cat, "color": color, "points": points})

    rings = [
        {"radius": _ring.R_CENTER_PLAZA, "color": "rgba(255,255,255,0.05)", "label": "plaza"},
    ]

    max_extent = max(R_OUTER_CAT.values()) + R_LABEL_PAD + 100

    layout = {
        "mode": "wiki",
        "labels": labels,
        "rings": rings,
        "trails": trails,
        "center": [0, 0],
        "extent": max_extent,
        "cards": cards_out,
        "sector_arc_deg": round(math.degrees(sector_arc), 1),
        "category_colors": {k: v for k, v in WIKI_CATEGORIES},
        "era_band_order": ERA_BAND_ORDER,
        "n_score_quartiles": N_SCORE_QUARTILES,
        "r_outer_per_category": {k: round(v, 1) for k, v in R_OUTER_CAT.items()},
    }

    print(f"[wiki-layout] writing {out_path}...", flush=True)
    out_path.write_text(json.dumps(layout, separators=(",", ":")))
    size_kb = out_path.stat().st_size / 1024
    print(f"[done] {out_path}: {len(cards_out):,} articles, {len(labels)} labels, {size_kb:.1f}KB", flush=True)


if __name__ == "__main__":
    main()
