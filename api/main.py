"""Wander Around API — placeholder.

Binds to 127.0.0.1:8050 only. Public access flows through the nginx server block
at /etc/nginx/sites-enabled/wander, which terminates TLS, adds security headers,
and reverse-proxies to this process. Never bind to 0.0.0.0.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from wander import db as wander_db
from wander.facade import building_svg, rich_site_facade
from wander.graph import infer_era

app = FastAPI(
    title="Wander Around",
    version="0.0.1",
    docs_url=None,           # disable interactive docs in production
    redoc_url=None,
    openapi_url=None,
)

# /static/ — self-hosted Three.js + controls. The /world and /4d page modules
# import these via absolute /static/ URLs (CSP `script-src 'self'` blocks the
# bare-specifier importmap path that would otherwise need 'unsafe-inline').
# StaticFiles handles path-traversal safely; do not roll a custom file route.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


PLACEHOLDER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <title>Wander Around</title>
  <style>
    :root { color-scheme: dark; }
    html, body { margin: 0; padding: 0; height: 100%; background: #0c0c0e; color: #ece8df; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    body { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 2rem; text-align: center; }
    h1 { font-size: clamp(2rem, 6vw, 3.5rem); letter-spacing: -0.02em; margin: 0 0 0.5rem; font-weight: 500; }
    .tag { font-size: 0.85rem; opacity: 0.55; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 2rem; }
    .blurb { max-width: 36rem; line-height: 1.55; opacity: 0.75; font-size: 1.05rem; }
    .pulse { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #9aa19a; margin-right: 0.5rem; animation: pulse 2s ease-in-out infinite; vertical-align: middle; }
    @keyframes pulse { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
  </style>
</head>
<body>
  <div class="tag"><span class="pulse"></span>under construction</div>
  <h1>Wander Around</h1>
  <p class="blurb">A scrollable feed and explorable game-world over the long-tail web — the dormant 99% that search engines buried under SEO sludge.</p>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    # The feed is the front door now; the placeholder lives at /under-construction.
    return HTMLResponse(content=FEED_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/under-construction", response_class=HTMLResponse)
async def under_construction():
    return HTMLResponse(content=PLACEHOLDER_HTML, headers={
        "Cache-Control": "public, max-age=300",
    })


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "service": "wander-api", "version": "0.0.1"})


# ── Day 7-8 demo: procedural building facades + link graph ─────────────
# DATA_DIR can be overridden via env var; default follows FHS conventions
# with /opt/wander as the install root and /opt/wander/data as the data dir.
import os as _os_data
DATA_DIR = Path(_os_data.environ.get("WANDER_DATA_DIR", "/opt/wander/data"))

DEMO_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wander Around — internal demo</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; padding: 1.5rem; background: #0c0c0e; color: #ece8df;
           font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    h1, h2 { font-weight: 500; letter-spacing: -0.01em; margin: 0 0 0.5rem; }
    h2 { font-size: 1rem; opacity: 0.7; margin-top: 2rem; }
    .panel { background: #14141a; border: 1px solid #2a2a32; border-radius: 6px;
             padding: 1rem; margin-bottom: 1.5rem; }
    .panel img { display: block; max-width: 100%; height: auto; }
    .meta { font-size: 0.8rem; opacity: 0.5; margin-top: 0.5rem; }
  </style>
</head>
<body>
  <h1>Wander Around — internal demo</h1>
  <p class="meta">Procedural facades from rendered cards (Day 7-8). Top row = high-score
  hand-made cluster, bottom row = low-score SEO cluster.</p>

  <div class="panel">
    <h2>Procedural buildings (40 = top 20 + bottom 20 by composite score)</h2>
    <img src="/demo/facades.svg" alt="building facades">
  </div>

  <div class="panel">
    <h2>Link graph (sparse — needs render scale &gt;10k for edge density)</h2>
    <img src="/demo/graph.svg" alt="link graph">
  </div>
</body>
</html>
"""


@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    return HTMLResponse(content=DEMO_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/demo/facades.svg")
async def demo_facades():
    p = DATA_DIR / "facade_demo.svg"
    if not p.exists():
        raise HTTPException(404, "facades not generated")
    return Response(content=p.read_bytes(), media_type="image/svg+xml")


@app.get("/demo/graph.svg")
async def demo_graph():
    p = DATA_DIR / "graph.svg"
    if not p.exists():
        raise HTTPException(404, "graph not generated")
    return Response(content=p.read_bytes(), media_type="image/svg+xml")


# ── Day 9-10 feed API ─────────────────────────────────────────────────
DB_PATH = DATA_DIR / "wander.db"
FEED_MAX_LIMIT = 50


def _row_to_card_dict(row, *, include_features: bool = False) -> dict:
    out = {
        "id": row["id"],
        "url": row["url"],
        "host": row["host"],
        "title": row["title"] or "",
        "snippet": row["snippet"] or "",
        "era": row["era"],
        "score": round(float(row["composite_score"]), 2),
        "thumb_url": f"/api/card/{row['id']}/thumb",
        "facade_url": f"/api/card/{row['id']}/facade.svg",
    }
    if include_features and "word_count" in row.keys():
        out["features"] = {
            "word_count": row["word_count"],
        }
    return out


@app.get("/api/stats")
async def api_stats():
    if not DB_PATH.exists():
        return JSONResponse({"db": "not initialized"})
    with wander_db.connect(DB_PATH) as conn:
        base = wander_db.stats(conn)
        # Thumb progress — useful sites + how many have real WebP thumbs + recent rate
        try:
            useful = conn.execute("SELECT COUNT(*) FROM cards WHERE is_useful_content=1").fetchone()[0]
            thumbed = conn.execute("SELECT COUNT(*) FROM cards WHERE thumb_path IS NOT NULL AND thumb_path != ''").fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE rendered_at > strftime('%s', 'now') - 300 "
                "AND thumb_path IS NOT NULL AND thumb_path != ''"
            ).fetchone()[0]
            base["thumb_progress"] = {
                "useful_total": useful,
                "with_thumb": thumbed,
                "pct_with_thumb": round(100 * thumbed / max(1, useful), 2),
                "rendered_last_5min": recent,
                "rate_per_min": round(recent / 5, 1),
            }
        except Exception as e:
            base["thumb_progress_error"] = str(e)
        # Per-mode placed counts. Only reports layouts whose JSON has already
        # been parsed (cached in memory). Cold layouts aren't reported here —
        # parsing 30MB synchronously would exceed nginx's 30s timeout. Cache
        # warms organically as users hit /world, /game, /rendered, /wiki, /4d.
        try:
            placed = {}
            for mode in _LAYOUT_PATHS:
                cached = _layout_cache.get(mode)
                if cached:
                    placed[mode] = len(cached[1])
            base["placed"] = placed
        except Exception as e:
            base["placed_error"] = str(e)
        return JSONResponse(base)


@app.get("/api/feed/next")
async def api_feed_next(
    seen: Optional[str] = Query(None, max_length=8000),
    limit: int = Query(20, ge=1, le=FEED_MAX_LIMIT),
    min_score: float = Query(-3.0),
):
    """Return the next batch of cards for the swipe stack.

    `seen` is a comma-separated list of IDs the client has already received
    (so the server can exclude them and not repeat). Capped server-side to
    800 entries. Empty/omitted on first call.

    `next_cursor` is preserved for backward compatibility but is now always
    null — pagination is driven by `seen` instead.
    """
    seen_ids: list[int] = []
    if seen:
        for tok in seen.split(",")[:800]:
            tok = tok.strip()
            if tok.isdigit():
                seen_ids.append(int(tok))
    with wander_db.connect(DB_PATH) as conn:
        rows = wander_db.feed_next(
            conn, limit=limit, min_score=min_score, exclude_ids=seen_ids
        )
    cards = [_row_to_card_dict(r) for r in rows]
    return JSONResponse({"cards": cards, "next_cursor": None, "limit": limit})


@app.get("/api/card/{card_id}")
async def api_card(card_id: int):
    with wander_db.connect(DB_PATH) as conn:
        row = wander_db.get_card(conn, card_id)
    if not row:
        raise HTTPException(404, "card not found")
    return JSONResponse(_row_to_card_dict(row, include_features=True))


def _fallback_facade_svg(row) -> bytes:
    """Return procedural facade SVG bytes for a card — RICH version used as
    lazy-thumb fallback when no real WebP thumb has been rendered yet. Encodes
    host name as masthead, era-typed typography, and category-suggested layout."""
    host = ""
    url = row["url"] or ""
    if "://" in url:
        host = url.split("/")[2] if len(url.split("/")) > 2 else ""
    cat = (row["category"] if "category" in row.keys() else None) or "misc"
    svg = rich_site_facade(
        host=host,
        category=cat,
        era=row["era"] or "midweb",
        score=float(row["composite_score"] or 0),
        title=row["title"] or "",
    )
    return svg.encode("utf-8") if isinstance(svg, str) else svg


# Wiki article thumbnails — separate route + DB from /api/card/{id}/thumb so the
# two corpora's autoincrement IDs don't collide. Thumb pipeline is wander-wiki-thumbs
# (scripts/wiki/05_wiki_thumb_render.py) which writes to articles.thumb_path.
WIKI_DB_PATH = DATA_DIR / "wiki" / "wiki.db"
_wiki_thumbed_ids_cache = {"ids": set(), "updated": 0.0}


def _refresh_wiki_thumbed_ids() -> None:
    import time as _t
    if _t.time() - _wiki_thumbed_ids_cache["updated"] < _THUMBED_TTL:
        return
    if not WIKI_DB_PATH.exists():
        return
    try:
        import sqlite3 as _sq
        conn = _sq.connect(WIKI_DB_PATH)
        ids = {row[0] for row in conn.execute(
            "SELECT id FROM articles WHERE thumb_path IS NOT NULL AND thumb_path != ''"
        )}
        conn.close()
        _wiki_thumbed_ids_cache["ids"] = ids
        _wiki_thumbed_ids_cache["updated"] = _t.time()
    except Exception:
        pass


@app.get("/api/wiki/article/{article_id}/thumb")
async def api_wiki_thumb(article_id: int):
    if not WIKI_DB_PATH.exists():
        raise HTTPException(404, "wiki db not found")
    import sqlite3 as _sq
    conn = _sq.connect(WIKI_DB_PATH)
    conn.row_factory = _sq.Row
    try:
        row = conn.execute(
            "SELECT id, title, thumb_path, COALESCE(thumb_priority, 0) AS thumb_priority FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "article not found")
        thumb_path = row["thumb_path"]
        if thumb_path:
            p = Path(thumb_path)
            try:
                p.resolve().relative_to(DATA_DIR.resolve())
            except ValueError:
                raise HTTPException(403, "thumb path outside data dir")
            if p.exists():
                return Response(
                    content=p.read_bytes(), media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        # Bump priority so the renderer picks this article up next.
        try:
            conn.execute(
                "UPDATE articles SET thumb_priority = COALESCE(thumb_priority, 0) + 1 WHERE id = ?",
                (article_id,),
            )
            conn.commit()
        except Exception:
            pass
        # Procedural fallback for wiki: a tiny SVG with the article title.
        title = row["title"] or "(untitled)"
        title_safe = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 480" width="320" height="480">'
            f'<rect width="320" height="480" fill="#1a1410"/>'
            f'<rect x="20" y="20" width="280" height="40" fill="#3a2c1c"/>'
            f'<text x="30" y="46" fill="#e8d8b8" font-family="serif" font-size="18" font-weight="700">Wikipedia</text>'
            f'<text x="160" y="240" text-anchor="middle" fill="#cfd8c8" font-family="serif" font-size="20" font-weight="500">{title_safe[:40]}</text>'
            f'<text x="160" y="270" text-anchor="middle" fill="#9a9a8a" font-family="serif" font-size="11">render queued</text>'
            f'</svg>'
        ).encode()
        return Response(
            content=svg, media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=30"},
        )
    finally:
        conn.close()


@app.get("/api/card/{card_id}/thumb")
async def api_card_thumb(card_id: int):
    with wander_db.connect(DB_PATH) as conn:
        row = wander_db.get_card(conn, card_id)
        if not row:
            raise HTTPException(404, "card not found")
        thumb_path = row["thumb_path"]
        # Fast path: real thumb file exists → serve it as WebP
        if thumb_path:
            p = Path(thumb_path)
            try:
                p.resolve().relative_to(DATA_DIR.resolve())
            except ValueError:
                raise HTTPException(403, "thumb path outside data dir")
            if p.exists():
                return Response(content=p.read_bytes(), media_type="image/webp",
                                headers={"Cache-Control": "public, max-age=86400"})
        # Card has no real thumb yet — bump its priority so the background renderer picks it
        # up next, then return the procedural facade SVG so the user gets something visual now.
        # Priority is a counter on the cards table; higher = render sooner.
        try:
            conn.execute(
                "UPDATE cards SET thumb_priority = COALESCE(thumb_priority, 0) + 1 WHERE id = ?",
                (card_id,),
            )
        except Exception:
            pass  # column may not exist yet on first run; renderer migration adds it
    return Response(
        content=_fallback_facade_svg(row),
        media_type="image/svg+xml",
        # Short cache so the browser re-checks soon after a real thumb gets rendered
        headers={"Cache-Control": "public, max-age=30"},
    )


FEED_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
  <meta name="theme-color" content="#0c0c0e">
  <title>Wander Around</title>
  <style>
    :root { color-scheme: dark; --fg:#ece8df; --bg:#0c0c0e; --panel:#14141a; --line:#2a2a32; --dim:#9a9a8a; }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg);
                 font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow: hidden;
                 overscroll-behavior: none; touch-action: none; }
    .stack { position: fixed; inset: 0; }
    .card { position: absolute; inset: 0; will-change: transform;
            display: flex; flex-direction: column; background: #050507; }
    .card .thumb { flex: 1 1 auto; min-height: 0; background-size: cover; background-position: center top;
                   background-color: #14141a; background-repeat: no-repeat; }
    .card .gradient { position: absolute; left: 0; right: 0; bottom: 0; height: 60%;
                      background: linear-gradient(to top, rgba(12,12,14,0.97) 0%, rgba(12,12,14,0.85) 35%, transparent 100%);
                      pointer-events: none; }
    .card .info { position: absolute; left: 0; right: 0; bottom: 0; padding: 1.2rem 1.2rem max(1.2rem, env(safe-area-inset-bottom));
                  display: flex; flex-direction: column; gap: 0.7rem; }
    .card .title { font-size: 1.35rem; font-weight: 500; line-height: 1.25; letter-spacing: -0.01em;
                   text-shadow: 0 1px 2px rgba(0,0,0,0.6); max-height: 5.5em; overflow: hidden;
                   text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; }
    .card .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
    .pill { font-size: 0.7rem; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--dim); }
    .pill.host { background: rgba(20,20,26,0.8); color: var(--fg); }
    .pill.score { color: #aef0a8; border-color: #2c4030; background: rgba(38,48,42,0.6); }
    .pill.score.neg { color: #f0a8a8; border-color: #402424; background: rgba(48,34,34,0.6); }
    .actions { display: flex; gap: 0.6rem; margin-top: 0.4rem; }
    .btn { flex: 1 1 0; padding: 0.85rem 0.7rem; border-radius: 8px; border: 1px solid var(--line);
           background: rgba(20,20,26,0.7); color: var(--fg); font-family: inherit; font-size: 0.9rem;
           text-align: center; text-decoration: none; cursor: pointer; }
    .btn.primary { background: rgba(40,52,46,0.8); border-color: #2c4030; color: #cff0c8; }
    .btn:active { transform: scale(0.96); }
    .btn .ico { display: block; font-size: 1rem; opacity: 0.9; margin-bottom: 2px; }
    .btn .lbl { font-size: 0.7rem; opacity: 0.85; letter-spacing: 0.04em; }
    .topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 10; padding: 0.7rem 1rem;
              display: flex; justify-content: space-between; align-items: center;
              background: linear-gradient(to bottom, rgba(12,12,14,0.85), transparent);
              pointer-events: none; }
    .topbar .brand { font-size: 0.85rem; font-weight: 500; letter-spacing: 0.02em; opacity: 0.85; }
    .topbar .stats { font-size: 0.7rem; opacity: 0.55; }
    .hint { position: fixed; left: 0; right: 0; bottom: 50%; text-align: center; pointer-events: none;
            font-size: 0.85rem; color: var(--dim); opacity: 0; transition: opacity 0.4s; }
    .hint.show { opacity: 0.7; }
    .empty { position: fixed; inset: 0; display: flex; flex-direction: column; align-items: center;
             justify-content: center; gap: 1rem; text-align: center; padding: 2rem; }
    .empty p { opacity: 0.5; max-width: 28rem; line-height: 1.5; }
    .saved-badge { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%) scale(0.8);
                   background: rgba(40,52,46,0.95); color: #cff0c8; padding: 1rem 2rem; border-radius: 999px;
                   border: 1px solid #2c4030; pointer-events: none; opacity: 0; transition: all 0.3s; z-index: 20; }
    .saved-badge.show { opacity: 1; transform: translate(-50%, -50%) scale(1); }
    .saved-badge.dismiss { background: rgba(52,40,40,0.95); color: #f0c8c8; border-color: #402424; }
    /* Sidebar — desktop only; mobile keeps the full-width swipe stack */
    .sidebar { position: fixed; top: 0; bottom: 0; left: 0; width: 300px; background: #0a0a0c;
               border-right: 1px solid var(--line); overflow-y: auto; overflow-x: hidden;
               padding: 3rem 0 1rem; z-index: 5; display: none;
               scrollbar-width: thin; scrollbar-color: #2a2a32 #0a0a0c; }
    .sidebar::-webkit-scrollbar { width: 8px; }
    .sidebar::-webkit-scrollbar-track { background: #0a0a0c; }
    .sidebar::-webkit-scrollbar-thumb { background: #2a2a32; border-radius: 4px; }
    .sidebar-header { font-size: 0.7rem; opacity: 0.5; padding: 0 1rem 0.5rem; letter-spacing: 0.06em;
                      text-transform: uppercase; }
    .sidebar-item { display: flex; gap: 0.6rem; padding: 0.55rem 0.85rem; cursor: pointer;
                    border-bottom: 1px solid rgba(42,42,50,0.4); align-items: center; transition: background 0.12s; }
    .sidebar-item:hover { background: rgba(20,20,26,0.7); }
    .sidebar-item.active { background: rgba(40,52,46,0.45); border-left: 2px solid #aef0a8; padding-left: calc(0.85rem - 2px); }
    .sidebar-item .thumbmini { width: 40px; height: 40px; flex-shrink: 0; border-radius: 4px;
                               background-size: cover; background-position: center top; background-color: #14141a; }
    .sidebar-item .smeta { flex: 1 1 0; min-width: 0; }
    .sidebar-item .sttl { font-size: 0.78rem; line-height: 1.25; max-height: 2.5em; overflow: hidden;
                          text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .sidebar-item .shst { font-size: 0.65rem; opacity: 0.5; margin-top: 2px; white-space: nowrap;
                          overflow: hidden; text-overflow: ellipsis; }
    .sidebar-item .sscr { font-size: 0.62rem; padding: 1px 5px; border-radius: 999px; margin-left: 4px;
                          color: #aef0a8; border: 1px solid #2c4030; }
    .sidebar-item .sscr.neg { color: #f0a8a8; border-color: #402424; }
    .sidebar-end { padding: 1rem; text-align: center; font-size: 0.7rem; opacity: 0.4; }
    @media (min-width: 800px) {
      .sidebar { display: block; }
      .stack, .topbar, .hint { left: 300px; }
      .topbar .brand { padding-left: 0.4rem; }
    }
  </style>
</head>
<body>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">browse · click to jump</div>
    <div id="sidebar-items"></div>
    <div class="sidebar-end" id="sidebar-end">scroll for more</div>
  </aside>
  <div class="topbar">
    <span class="brand">wander around</span>
    <span class="stats" id="stats"></span>
  </div>
  <div class="stack" id="stack"></div>
  <div class="hint" id="hint">scroll · swipe · or click sidebar</div>
  <div class="saved-badge" id="badge"></div>
  <script src="/feed/main.js"></script>
</body>
</html>
"""


FEED_JS = """
// Wander Around — vertical-swipe feed. Vanilla JS, no framework.
const stackEl = document.getElementById('stack');
const statsEl = document.getElementById('stats');
const hintEl = document.getElementById('hint');
const badgeEl = document.getElementById('badge');
const sidebarItemsEl = document.getElementById('sidebar-items');
const sidebarEndEl = document.getElementById('sidebar-end');

const SWIPE_THRESHOLD = 80;
const PREFETCH_AHEAD = 8;
const WHEEL_THRESHOLD = 25;
const WHEEL_LOCK_MS = 380;

const state = {
  cards: [],          // queue of card objects
  pos: 0,             // index of currently-visible card
  fetching: false,
  exhausted: false,
  domNodes: new Map(), // index -> dom element
  saved: new Set(JSON.parse(localStorage.getItem('wander.saved') || '[]')),
  // Per-session sets only — never persisted. Keeps the feed varied across
  // reloads (server samples randomly from top-1500 by score).
  seenIds: new Set(),
  dismissed: new Set(),
};
// One-time migration: clear historic dismissed list (was accumulating across
// reloads via mouse-wheel and eventually emptied the feed for users).
try { localStorage.removeItem('wander.dismissed'); } catch (e) {}

function escapeHtml(s) {
  return (s || '').replace(/[&<>\"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":"&#39;" })[c]);
}

function persistSet(name, set) {
  localStorage.setItem('wander.' + name, JSON.stringify([...set]));
}

function showBadge(text, kind) {
  badgeEl.textContent = text;
  badgeEl.classList.toggle('dismiss', kind === 'dismiss');
  badgeEl.classList.add('show');
  setTimeout(() => badgeEl.classList.remove('show'), 700);
}

function buildCardEl(c) {
  const el = document.createElement('div');
  el.className = 'card';
  const scoreClass = c.score < 0 ? 'score neg' : 'score';
  const isSaved = state.saved.has(c.id);
  el.innerHTML = `
    <div class="thumb" style="background-image:url('${c.thumb_url}')"></div>
    <div class="gradient"></div>
    <div class="info">
      <div class="title">${escapeHtml(c.title) || '(untitled)'}</div>
      <div class="row">
        <span class="pill host">${escapeHtml(c.host)}</span>
        <span class="pill ${scoreClass}">${c.score >= 0 ? '+' : ''}${c.score}</span>
        <span class="pill">${escapeHtml(c.era || '')}</span>
      </div>
      <div class="actions">
        <a class="btn primary" href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer">
          <span class="ico">↗</span><span class="lbl">open site</span>
        </a>
        <button class="btn save" data-id="${c.id}">
          <span class="ico">${isSaved ? '✓' : '☆'}</span><span class="lbl">${isSaved ? 'saved' : 'save'}</span>
        </button>
        <button class="btn next" data-id="${c.id}">
          <span class="ico">↑</span><span class="lbl">next</span>
        </button>
      </div>
    </div>
  `;

  el.querySelector('.save').addEventListener('click', (e) => {
    e.stopPropagation();
    const id = c.id;
    if (state.saved.has(id)) { state.saved.delete(id); showBadge('removed'); }
    else { state.saved.add(id); showBadge('saved'); }
    persistSet('saved', state.saved);
    // Re-render this card to update label.
    const newEl = buildCardEl(c);
    newEl.style.transform = el.style.transform;
    el.replaceWith(newEl);
    state.domNodes.set(state.cards.indexOf(c), newEl);
    bindSwipe(newEl, state.cards.indexOf(c));
  });

  el.querySelector('.next').addEventListener('click', (e) => {
    e.stopPropagation();
    advance(1);
  });

  return el;
}

function position(idx) {
  // Returns transform string for a card at index idx given current pos.
  const offset = idx - state.pos;
  if (offset === 0) return 'translate3d(0, 0, 0)';
  if (offset > 0) return `translate3d(0, 100%, 0)`;
  return `translate3d(0, -100%, 0)`;
}

function ensureMounted(idx) {
  if (idx < 0 || idx >= state.cards.length) return null;
  if (state.domNodes.has(idx)) return state.domNodes.get(idx);
  const el = buildCardEl(state.cards[idx]);
  el.style.transform = position(idx);
  el.style.zIndex = String(state.cards.length - idx);
  stackEl.appendChild(el);
  state.domNodes.set(idx, el);
  bindSwipe(el, idx);
  return el;
}

function unmount(idx) {
  const el = state.domNodes.get(idx);
  if (el) { el.remove(); state.domNodes.delete(idx); }
}

function refreshMounted() {
  // Mount current ± 1, unmount others.
  const keep = new Set([state.pos - 1, state.pos, state.pos + 1]);
  for (const idx of Array.from(state.domNodes.keys())) {
    if (!keep.has(idx)) unmount(idx);
  }
  for (const idx of keep) ensureMounted(idx);
  // Reposition.
  for (const [idx, el] of state.domNodes.entries()) {
    el.style.transition = 'transform 0.32s cubic-bezier(0.2, 0.8, 0.2, 1)';
    el.style.transform = position(idx);
  }
}

function advance(delta) {
  const newPos = state.pos + delta;
  if (newPos < 0) return;
  if (newPos >= state.cards.length) {
    if (!state.exhausted) loadMore();
    if (newPos >= state.cards.length) return;
  }
  // Mark dismissed if we swiped past without save (in-session only — not persisted).
  if (delta > 0 && !state.saved.has(state.cards[state.pos].id)) {
    state.dismissed.add(state.cards[state.pos].id);
  }
  state.pos = newPos;
  refreshMounted();
  syncSidebar();
  if (state.cards.length - state.pos <= PREFETCH_AHEAD) loadMore();
}

function jumpTo(idx) {
  if (idx < 0 || idx >= state.cards.length) return;
  state.pos = idx;
  refreshMounted();
  syncSidebar();
  if (state.cards.length - state.pos <= PREFETCH_AHEAD) loadMore();
}

function buildSidebarItem(c, idx) {
  const scoreClass = c.score < 0 ? 'sscr neg' : 'sscr';
  const el = document.createElement('div');
  el.className = 'sidebar-item' + (idx === state.pos ? ' active' : '');
  el.dataset.idx = String(idx);
  el.innerHTML = `
    <div class="thumbmini" style="background-image:url('${c.thumb_url}')"></div>
    <div class="smeta">
      <div class="sttl">${escapeHtml(c.title) || '(untitled)'}</div>
      <div class="shst">${escapeHtml(c.host)} <span class="${scoreClass}">${c.score >= 0 ? '+' : ''}${c.score}</span></div>
    </div>
  `;
  el.addEventListener('click', () => jumpTo(parseInt(el.dataset.idx, 10)));
  return el;
}

let lastSidebarLen = 0;
function appendSidebarItems() {
  if (!sidebarItemsEl) return;
  for (let i = lastSidebarLen; i < state.cards.length; i++) {
    sidebarItemsEl.appendChild(buildSidebarItem(state.cards[i], i));
  }
  lastSidebarLen = state.cards.length;
  if (sidebarEndEl) sidebarEndEl.textContent = state.exhausted ? `${state.cards.length} sites · end of feed` : `${state.cards.length} loaded · scroll for more`;
}

function syncSidebar() {
  if (!sidebarItemsEl) return;
  const items = sidebarItemsEl.querySelectorAll('.sidebar-item');
  items.forEach((el, idx) => el.classList.toggle('active', idx === state.pos));
  // Auto-scroll active into view inside the sidebar
  const active = items[state.pos];
  if (active) {
    const sb = active.parentElement.parentElement;  // sidebar
    const r = active.getBoundingClientRect();
    const sbR = sb.getBoundingClientRect();
    if (r.top < sbR.top + 40 || r.bottom > sbR.bottom - 40) {
      active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}

let drag = null;
function bindSwipe(el, idx) {
  el.addEventListener('touchstart', (e) => onStart(e, idx), { passive: true });
  el.addEventListener('touchmove', (e) => onMove(e, idx), { passive: false });
  el.addEventListener('touchend', (e) => onEnd(e, idx));
  el.addEventListener('mousedown', (e) => onStart(e, idx));
}

function onStart(e, idx) {
  if (idx !== state.pos) return;
  const t = e.touches ? e.touches[0] : e;
  drag = { idx, startY: t.clientY, dy: 0, t0: Date.now() };
  const el = state.domNodes.get(idx);
  if (el) el.style.transition = 'none';
  if (!e.touches) {
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }
}

function onMove(e, idx) {
  if (!drag || idx !== drag.idx) return;
  const t = e.touches ? e.touches[0] : e;
  drag.dy = t.clientY - drag.startY;
  const el = state.domNodes.get(drag.idx);
  if (el) el.style.transform = `translate3d(0, ${drag.dy}px, 0)`;
  const next = state.domNodes.get(drag.idx + 1);
  if (next) next.style.transform = `translate3d(0, calc(100% + ${drag.dy}px), 0)`;
  if (e.touches) e.preventDefault();
}

function onMouseMove(e) { onMove(e, drag ? drag.idx : -1); }
function onMouseUp(e) {
  document.removeEventListener('mousemove', onMouseMove);
  document.removeEventListener('mouseup', onMouseUp);
  onEnd(e, drag ? drag.idx : -1);
}

function onEnd(e, idx) {
  if (!drag || idx !== drag.idx) return;
  const dy = drag.dy;
  const elapsed = Date.now() - drag.t0;
  const fast = Math.abs(dy) > 30 && elapsed < 250;
  const commit = Math.abs(dy) > SWIPE_THRESHOLD || fast;
  drag = null;
  if (commit) {
    advance(dy < 0 ? 1 : -1);
  } else {
    refreshMounted();
  }
}

async function loadMore() {
  if (state.fetching || state.exhausted) return;
  state.fetching = true;
  const startCount = state.cards.length;
  let attempts = 0;
  try {
    // Server samples 50 cards randomly from the top-1500-by-score pool,
    // excluding any IDs we've already seen. Loop in case all 50 were just
    // dismissed in this session.
    while (
      state.cards.length === startCount &&
      attempts < 5 &&
      !state.exhausted
    ) {
      // Send up to 800 most recent seen IDs so server can dedupe.
      const seenList = [...state.seenIds].slice(-800);
      const seenParam = seenList.length > 0
        ? `&seen=${seenList.join(',')}`
        : '';
      const url = `/api/feed/next?limit=50${seenParam}`;
      const r = await fetch(url);
      const data = await r.json();
      if (!data.cards || data.cards.length === 0) {
        state.exhausted = true;
        break;
      }
      for (const c of data.cards) {
        if (state.seenIds.has(c.id)) continue;
        state.seenIds.add(c.id);
        if (!state.dismissed.has(c.id)) state.cards.push(c);
      }
      attempts++;
    }
    if (state.cards.length === 1 && startCount === 0) refreshMounted();
    appendSidebarItems();
  } catch (e) {
    // Retry-on-next-swipe is acceptable for v0.
  } finally {
    state.fetching = false;
  }
}

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const s = await r.json();
    statsEl.textContent = `${s.useful_cards} live · ${state.saved.size} saved`;
  } catch (e) {}
}

(async function init() {
  hintEl.classList.add('show');
  setTimeout(() => hintEl.classList.remove('show'), 3500);
  await loadMore();
  refreshMounted();
  loadStats();
  setInterval(loadStats, 30000);
})();

// Keyboard for desktop testing.
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown' || e.key === 'j') advance(1);
  if (e.key === 'ArrowUp' || e.key === 'k') advance(-1);
});

// Mouse wheel / trackpad scroll → advance one card per gesture (debounced).
let wheelLock = false;
let wheelAccum = 0;
window.addEventListener('wheel', (e) => {
  // Don't intercept scrolls inside the sidebar — let it scroll its own list.
  if (e.target && e.target.closest && e.target.closest('.sidebar')) return;
  if (wheelLock) { e.preventDefault(); return; }
  wheelAccum += e.deltaY;
  if (Math.abs(wheelAccum) < WHEEL_THRESHOLD) return;
  e.preventDefault();
  wheelLock = true;
  if (wheelAccum > 0) advance(1); else advance(-1);
  wheelAccum = 0;
  setTimeout(() => { wheelLock = false; }, WHEEL_LOCK_MS);
}, { passive: false });
"""


@app.get("/feed/main.js")
async def feed_js():
    return Response(content=FEED_JS, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/feed", response_class=HTMLResponse)
async def feed_page():
    return HTMLResponse(content=FEED_PAGE, headers={"Cache-Control": "no-store"})


# ── Day 21 v0.1 game: city overview (canvas, no Phaser yet) ─────────
GAME_ERA_BAND = {"old_web": 0, "midweb": 1, "template": 2, "seo_hardened": 3, "modern_spa": 4}


def _layout_for_game(card_id: int, era: str, score: float) -> tuple[float, float]:
    """Deterministic per-card layout: score on X, era band on Y, small jitter for spread."""
    import hashlib
    h = hashlib.sha256(f"wander-game:{card_id}".encode()).digest()
    jx = ((h[0] / 255.0) - 0.5) * 60.0  # ±30 px
    jy = ((h[1] / 255.0) - 0.5) * 160.0  # ±80 px
    band = GAME_ERA_BAND.get(era or "midweb", 1)
    x = score * 90.0 + jx
    y = band * 220.0 + jy
    return x, y


@app.get("/api/game/cards")
async def api_game_cards(limit: int = Query(2000, ge=1, le=10000)):
    """Legacy era-band layout. Kept for backwards-compatibility with old clients."""
    with wander_db.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, host, title, era, composite_score
            FROM cards
            WHERE is_useful_content = 1
            ORDER BY composite_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        era = r["era"] or "midweb"
        score = float(r["composite_score"])
        x, y = _layout_for_game(r["id"], era, score)
        out.append({
            "id": r["id"],
            "host": r["host"] or "",
            "title": (r["title"] or "")[:80],
            "era": era,
            "score": round(score, 2),
            "x": round(x, 1),
            "y": round(y, 1),
        })
    return JSONResponse({"cards": out, "count": len(out)})


# ── Day 22 ring layouts (categories ring + language×site-type ring) ──
# Layout JSONs are produced by scripts/10_layout_ring.py and refreshed
# whenever classification or render counts change. The endpoint just streams
# the cached file; if it doesn't exist it falls back to the legacy layout.

import json as _json

_LAYOUT_PATHS = {
    "categories":      DATA_DIR / "layout_categories.json",
    "types":           DATA_DIR / "layout_types.json",
    "rendered":        DATA_DIR / "layout_rendered.json",
    "categories_flat": DATA_DIR / "layout_categories_flat.json",
    "wiki":            DATA_DIR / "wiki" / "layout_wiki.json",
}


# Pre-computed 12×12 inter-category linkage matrix. The actual SQL join over
# 9.5M edges × 4M cards takes ~1-2min — way past nginx's 30s upstream timeout.
# Compute is moved to scripts/16_compute_category_edges.py (offline / cron),
# which writes data/category_edges.json. The endpoint just streams the file.
@app.get("/api/category-edges")
async def api_category_edges():
    p = DATA_DIR / "category_edges.json"
    if not p.exists():
        # Return empty obj so /game's chord render shows the "computing…" state
        # cleanly instead of erroring. Run script to populate.
        return JSONResponse({}, status_code=503, headers={"Retry-After": "60"})
    return JSONResponse(_json.loads(p.read_text()), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/game/layout")
async def api_game_layout(mode: str = Query("categories"), limit: int = Query(60000, ge=1, le=500000)):
    """Returns precomputed ring layout: cards + labels + ring decorations.

    `mode=categories` → 12 topic categories arranged around a central ring.
    `mode=types`      → 8 language sectors × 6 site-type concentric rings.
    """
    if mode not in _LAYOUT_PATHS:
        raise HTTPException(400, f"unknown mode: {mode}")
    path = _LAYOUT_PATHS[mode]
    if not path.exists():
        raise HTTPException(503, f"layout not generated yet — run scripts/10_layout_ring.py")
    try:
        data = _json.loads(path.read_text())
    except Exception as e:
        raise HTTPException(500, f"layout corrupt: {type(e).__name__}")
    # Cap card count so the canvas doesn't choke on overly-large layouts
    if len(data.get("cards", [])) > limit:
        data = {**data, "cards": data["cards"][:limit]}
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=120"})


GAME_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
  <meta name="theme-color" content="#0c0c0e">
  <title>Wander Around — city</title>
  <style>
    :root { color-scheme: dark; --fg:#ece8df; --bg:#0c0c0e; --panel:#14141a; --line:#2a2a32; --dim:#9a9a8a; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg);
                 font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow: hidden;
                 overscroll-behavior: none; touch-action: none; }
    canvas#world { position: fixed; inset: 0; cursor: grab; }
    canvas#world.grabbing { cursor: grabbing; }
    .topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 5; padding: 0.7rem 1rem;
              display: flex; justify-content: space-between; align-items: baseline; gap: 1rem;
              background: linear-gradient(to bottom, rgba(12,12,14,0.85), transparent);
              pointer-events: none; }
    .brand { font-size: 0.85rem; font-weight: 500; opacity: 0.85; }
    .stats { font-size: 0.7rem; opacity: 0.55; }
    .legend { position: fixed; left: 1rem; bottom: 1rem; z-index: 5; background: rgba(12,12,14,0.85);
              padding: 0.6rem 0.8rem; border: 1px solid var(--line); border-radius: 6px;
              font-size: 0.7rem; line-height: 1.5; max-width: 12rem; }
    .legend .row { display: flex; align-items: center; gap: 0.4rem; }
    .legend .sw { width: 10px; height: 10px; border-radius: 2px; }
    .toggles { position: fixed; right: 1rem; bottom: 1rem; z-index: 5; display: flex; gap: 0.4rem; flex-wrap: wrap; justify-content: flex-end; }
    .toggles a, .toggles button { background: rgba(12,12,14,0.85); border: 1px solid var(--line); border-radius: 6px;
                 padding: 0.5rem 0.8rem; color: var(--fg); text-decoration: none; font-size: 0.75rem;
                 font-family: inherit; cursor: pointer; }
    .toggles button.mode-btn { background: rgba(40,52,46,0.85); border-color: #2c4030; color: #cff0c8; }
    .toggles button.mode-btn:hover { background: rgba(48,62,54,0.95); }
    .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 10;
             display: none; align-items: center; justify-content: center; padding: 1rem; }
    .modal.show { display: flex; }
    .modal .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
                    max-width: 560px; width: 100%; max-height: 85vh; overflow-y: auto;
                    display: flex; flex-direction: column; gap: 0.85rem; padding: 1.2rem; }
    .modal .row { display: flex; gap: 1rem; align-items: flex-start; }
    .modal .facade { width: 96px; height: 144px; flex-shrink: 0; background: #0a0a0c; border-radius: 4px; }
    .modal .info { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: 0.4rem; }
    .modal .title { font-size: 1.1rem; font-weight: 500; line-height: 1.3; }
    .modal .host { font-size: 0.8rem; color: var(--dim); word-break: break-all; }
    .modal .pills { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.4rem; }
    .pill { font-size: 0.7rem; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--dim); }
    .pill.score { color: #aef0a8; border-color: #2c4030; background: rgba(38,48,42,0.6); }
    .pill.score.neg { color: #f0a8a8; border-color: #402424; background: rgba(48,34,34,0.6); }
    .modal .thumb { width: 100%; aspect-ratio: 9/16; background-size: cover; background-position: top;
                    background-color: #0a0a0c; border-radius: 6px; }
    .modal .actions { display: flex; gap: 0.6rem; }
    .btn { flex: 1 1 0; padding: 0.75rem; border-radius: 8px; border: 1px solid var(--line);
           background: rgba(20,20,26,0.7); color: var(--fg); text-align: center; text-decoration: none;
           font-family: inherit; font-size: 0.85rem; cursor: pointer; }
    .btn.primary { background: rgba(40,52,46,0.8); border-color: #2c4030; color: #cff0c8; }
    .hover-info { position: fixed; pointer-events: none; background: rgba(12,12,14,0.95);
                  border: 1px solid var(--line); border-radius: 4px; padding: 0.4rem 0.6rem;
                  font-size: 0.75rem; max-width: 18rem; z-index: 6; display: none; }
    .hover-info.show { display: block; }
    .enter-prompt { position: fixed; left: 50%; top: 60%; transform: translate(-50%, 0);
                    z-index: 4; padding: 0.6rem 1rem; background: rgba(40,52,46,0.95); color: #cff0c8;
                    border: 1px solid #2c4030; border-radius: 6px; font-size: 0.8rem;
                    pointer-events: none; opacity: 0; transition: opacity 0.18s; max-width: 16rem;
                    text-align: center; }
    .enter-prompt.show { opacity: 1; }
    .mobile-pad { position: fixed; bottom: 5rem; right: 1rem; z-index: 7;
                  display: grid; grid-template-columns: 40px 40px 40px; grid-template-rows: 40px 40px 40px;
                  gap: 4px; opacity: 0.85; }
    @media (hover: hover) { .mobile-pad { display: none; } }
    .mobile-pad button { background: rgba(20,20,26,0.85); color: var(--fg);
                         border: 1px solid var(--line); border-radius: 6px; font-family: inherit;
                         font-size: 1rem; user-select: none; -webkit-user-select: none; touch-action: none; }
    .mobile-pad button.up    { grid-column: 2; grid-row: 1; }
    .mobile-pad button.left  { grid-column: 1; grid-row: 2; }
    .mobile-pad button.right { grid-column: 3; grid-row: 2; }
    .mobile-pad button.down  { grid-column: 2; grid-row: 3; }
    .mobile-pad button.enter { grid-column: 2; grid-row: 2; background: rgba(40,52,46,0.85);
                               color: #cff0c8; border-color: #2c4030; font-size: 0.65rem; }
    /* Set A (display) and Set B (filters) panels — slide in from the sides. */
    .ctl-panel { position: fixed; top: 3rem; bottom: 6rem; width: 17rem;
                 background: rgba(12,12,14,0.92); border: 1px solid var(--line);
                 border-radius: 8px; padding: 0.8rem; overflow-y: auto;
                 z-index: 6; font-size: 0.75rem; line-height: 1.5;
                 transform: translateX(-150%); transition: transform 0.18s ease-out;
                 backdrop-filter: blur(4px); }
    .ctl-panel.right { left: auto; right: 1rem; transform: translateX(150%); }
    .ctl-panel.left  { left: 1rem; }
    .ctl-panel.show { transform: translateX(0); }
    .ctl-panel h3 { font-size: 0.78rem; font-weight: 500; margin: 0 0 0.7rem;
                    color: var(--fg); opacity: 0.95; letter-spacing: 0.05em;
                    text-transform: uppercase; }
    .ctl-panel .group { margin-bottom: 0.9rem; padding-bottom: 0.7rem;
                        border-bottom: 1px solid var(--line); }
    .ctl-panel .group:last-child { border-bottom: none; margin-bottom: 0; }
    .ctl-panel .group-label { display: flex; justify-content: space-between;
                              align-items: center; margin-bottom: 0.4rem;
                              opacity: 0.8; font-size: 0.7rem; }
    .ctl-panel .group-label .clear { cursor: pointer; opacity: 0.4;
                                     font-size: 0.65rem; }
    .ctl-panel .group-label .clear:hover { opacity: 0.9; }
    .ctl-panel .chip-row { display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .ctl-panel .chip { padding: 0.18rem 0.5rem; border-radius: 999px;
                       border: 1px solid var(--line); cursor: pointer;
                       font-size: 0.68rem; user-select: none;
                       background: rgba(20,20,26,0.5); color: var(--dim); }
    .ctl-panel .chip:hover { border-color: #6a7480; }
    .ctl-panel .chip.on { background: rgba(40,52,46,0.85); border-color: #2c4030;
                          color: #cff0c8; }
    .ctl-panel .chip[data-color] { border-left-width: 3px; padding-left: 0.4rem; }
    .ctl-panel .slider { width: 100%; }
    .ctl-panel .slider-label { display: flex; justify-content: space-between;
                               opacity: 0.8; margin-bottom: 0.2rem; }
    .ctl-panel .toggle { display: flex; align-items: center; gap: 0.5rem;
                         padding: 0.3rem 0; cursor: pointer; user-select: none; }
    .ctl-panel .toggle .switch { width: 24px; height: 14px; border-radius: 7px;
                                 background: rgba(60,60,70,0.6); position: relative;
                                 transition: background 0.15s; }
    .ctl-panel .toggle .switch::after { content: ''; position: absolute; top: 2px; left: 2px;
                                        width: 10px; height: 10px; border-radius: 50%;
                                        background: var(--fg); transition: transform 0.15s; }
    .ctl-panel .toggle.on .switch { background: rgba(80,160,90,0.7); }
    .ctl-panel .toggle.on .switch::after { transform: translateX(10px); }
    .ctl-panel select { width: 100%; padding: 0.3rem; background: rgba(20,20,26,0.7);
                        color: var(--fg); border: 1px solid var(--line);
                        border-radius: 4px; font-family: inherit; font-size: 0.7rem; }
  </style>
</head>
<body>
  <canvas id="world"></canvas>
  <div class="topbar">
    <span class="brand">wander around — city</span>
    <span class="stats" id="stats">loading…</span>
  </div>
  <div class="legend" id="legend">
    <div style="opacity:0.7;margin-bottom:0.3rem;" id="legend-title">Topic categories</div>
    <div id="legend-rows"></div>
    <div style="opacity:0.6;margin-top:0.5rem;font-size:0.7rem;">WASD / arrows to walk · E to enter</div>
  </div>
  <div class="enter-prompt" id="enter-prompt"></div>
  <div class="toggles">
    <button id="info-toggle" class="mode-btn" title="What is this? (?)">info ?</button>
    <button id="filter-toggle" class="mode-btn" title="Open data filters (/)">filters /</button>
    <button id="display-toggle" class="mode-btn" title="Open display options (\)">display \</button>
    <button id="mode-toggle" class="mode-btn">switch to languages × types</button>
    <button id="render-toggle">3D buildings</button>
    <button id="fit-btn">fit (F)</button>
    <a href="/feed">feed</a>
    <a href="/world">3D world</a>
  </div>
  <!-- Set B filters (left side, hotkey /) — multi-select chip groups stack with AND -->
  <div class="ctl-panel left" id="panel-b" aria-hidden="true">
    <h3>Filters</h3>
    <div id="panel-b-body"></div>
  </div>
  <!-- Set A display (right side, hotkey \) — sliders, toggles, sort/color/tooltip selects -->
  <div class="ctl-panel right" id="panel-a" aria-hidden="true">
    <h3>Display</h3>
    <div id="panel-a-body"></div>
  </div>
  <div class="hover-info" id="hover"></div>
  <div class="modal" id="modal"><div class="panel" id="modal-panel"></div></div>
  <div class="mobile-pad" id="mpad" aria-hidden="true">
    <button class="up" data-key="w">↑</button>
    <button class="left" data-key="a">←</button>
    <button class="enter" data-key="e">enter</button>
    <button class="right" data-key="d">→</button>
    <button class="down" data-key="s">↓</button>
  </div>
  <script src="/game/main.js"></script>
</body>
</html>
"""


GAME_JS = r"""
// Wander Around — character walk + ring city. Canvas + rAF loop, no engine.
const canvas = document.getElementById('world');
const ctx = canvas.getContext('2d', { alpha: false });
const stats = document.getElementById('stats');
const hover = document.getElementById('hover');
const modal = document.getElementById('modal');
const modalPanel = document.getElementById('modal-panel');
const enterPrompt = document.getElementById('enter-prompt');
const mpad = document.getElementById('mpad');
const modeToggleBtn = document.getElementById('mode-toggle');
const legendTitleEl = document.getElementById('legend-title');
const legendRowsEl = document.getElementById('legend-rows');

const ERA_COLOR = {
  old_web:      '#d8a050',
  midweb:       '#7a8a80',
  template:     '#a8a298',
  seo_hardened: '#5a6c80',
  modern_spa:   '#9aa9bc',
};
const ERA_ROOF = {
  old_web:      '#a05030',
  midweb:       '#465a52',
  template:     '#7a7470',
  seo_hardened: '#3a4a5a',
  modern_spa:   '#0c1014',
};

const cam = { x: 0, y: 0, z: 0.7 };  // smooth-follows player; lower z = wider view
const player = { x: 0, y: 0, vx: 0, vy: 0, facing: 0 };
const PLAYER_MAX_V = 180;       // world units / sec
const PLAYER_ACCEL = 700;
const PLAYER_FRICTION = 6.5;
const ENTER_RADIUS = 22;        // world units
const cards = [];
let labels = [];                // ring labels {text, x, y, color, kind?}
let rings = [];                 // ring decorations {radius, color, label}
let trails = [];                // glowing radial main roads {points, color, sector}
let categoryColors = {};        // color per category for category mode
let siteTypeColors = {};        // color per site_type for types mode
let currentMode = 'categories'; // 'categories' | 'types'
let tileMode = true;            // true = flat tile per card (cheap, top-down map), false = 3D-look walls+roof
let layoutExtent = 60000;       // outer label radius — covers personal/community arms at MAX 60K

// Spatial grid index — built on layout load, used to cull non-visible cards each frame
const GRID_RES = 48;            // 48×48 cells covering [-extent..extent] each axis
let grid = null;                // Map from "i,j" → array of card refs
let gridCellSize = 1;
let topByScore = [];            // pre-sorted top-N by score, used at low zoom for LOD
let nearestBuilding = null;     // updated each frame
let bounds = { minX: -1600, maxX: 1600, minY: -1600, maxY: 1600 };
let dpr = Math.min(window.devicePixelRatio || 1, 2);
let lastTs = 0;
const keys = {};                // currently-held keys

function resize() {
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  canvas.style.width = window.innerWidth + 'px';
  canvas.style.height = window.innerHeight + 'px';
}

function worldToScreen(wx, wy) {
  return [
    (wx - cam.x) * cam.z * dpr + canvas.width / 2,
    (wy - cam.y) * cam.z * dpr + canvas.height / 2,
  ];
}

function screenToWorld(sx, sy) {
  return [
    (sx * dpr - canvas.width / 2) / cam.z / dpr + cam.x,
    (sy * dpr - canvas.height / 2) / cam.z / dpr + cam.y,
  ];
}

function buildingHeight(c) {
  // Map score [-15..+5] to height [10..40].
  const norm = Math.max(0, Math.min(1, (c.score + 15) / 20));
  return 10 + norm * 30;
}

function findNearestBuilding() {
  // Grid-accelerated: only check cells near the player
  let best = null;
  let bestD2 = Infinity;
  let candidates;
  if (grid) {
    const i = Math.max(0, Math.min(GRID_RES - 1, Math.floor((player.x + layoutExtent) / gridCellSize)));
    const j = Math.max(0, Math.min(GRID_RES - 1, Math.floor((player.y + layoutExtent) / gridCellSize)));
    candidates = [];
    for (let di = -1; di <= 1; di++) {
      for (let dj = -1; dj <= 1; dj++) {
        const cell = grid.get((i+di) + ',' + (j+dj));
        if (cell) candidates.push(...cell);
      }
    }
    if (candidates.length === 0) candidates = cards;  // fallback if player is outside grid
  } else {
    candidates = cards;
  }
  for (const c of candidates) {
    const dx = c.x - player.x;
    const dy = c.y - player.y;
    const d2 = dx * dx + dy * dy;
    if (d2 < bestD2) { bestD2 = d2; best = c; }
  }
  return { card: best, dist: Math.sqrt(bestD2) };
}

function drawPlayer() {
  const [sx, sy] = worldToScreen(player.x, player.y);
  const s = cam.z * dpr;
  // Shadow
  ctx.fillStyle = 'rgba(0,0,0,0.45)';
  ctx.beginPath();
  ctx.ellipse(sx, sy + 2 * s, 4 * s, 1.5 * s, 0, 0, Math.PI * 2);
  ctx.fill();
  // Body
  ctx.fillStyle = '#cf9050';
  ctx.fillRect(sx - 2.5 * s, sy - 6 * s, 5 * s, 6 * s);
  // Head
  ctx.fillStyle = '#f0d8b8';
  ctx.beginPath();
  ctx.arc(sx, sy - 8 * s, 2.4 * s, 0, Math.PI * 2);
  ctx.fill();
  // Tiny indicator of facing direction (eye dot)
  ctx.fillStyle = '#1a1410';
  const ex = sx + (player.facing > 0 ? 0.8 : (player.facing < 0 ? -0.8 : 0)) * s;
  ctx.beginPath();
  ctx.arc(ex, sy - 8.2 * s, 0.5 * s, 0, Math.PI * 2);
  ctx.fill();
}

function colorForCard(c) {
  // If user has overridden via Set A color-by, honor that first.
  // (filterState may not exist yet during very early render; guard for that.)
  if (typeof filterState !== 'undefined' && filterState.colorBy && filterState.colorBy !== 'category') {
    if (filterState.colorBy === 'era')       return ERA_COLOR[c.era] || ERA_COLOR.midweb;
    if (filterState.colorBy === 'site_type') return siteTypeColors[c.site_type] || ERA_COLOR.midweb;
    if (filterState.colorBy === 'language')  return categoryColors[c.category] || ERA_COLOR.midweb; // fallback (no lang palette)
  }
  if (currentMode === 'categories') {
    return categoryColors[c.category] || ERA_COLOR[c.era] || ERA_COLOR.midweb;
  }
  // types mode: color by site_type
  return siteTypeColors[c.site_type] || ERA_COLOR[c.era] || ERA_COLOR.midweb;
}

function roofForCard(c) {
  // Darken the wall color by roughly 35% for a roof shade
  const wall = colorForCard(c);
  const m = wall.match(/^#([0-9a-f]{6})$/i);
  if (!m) return ERA_ROOF[c.era] || ERA_ROOF.midweb;
  const v = parseInt(m[1], 16);
  const r = Math.max(0, ((v >> 16) & 0xff) - 60);
  const g = Math.max(0, ((v >> 8)  & 0xff) - 60);
  const b = Math.max(0, ( v        & 0xff) - 60);
  return `rgb(${r},${g},${b})`;
}

// Precomputed RGB lookup for dot mode (avoids hex parsing per-pixel-per-frame)
const CATEGORY_RGB = {
  tech:      [0x7a, 0xa9, 0xd8],
  art:       [0xe0, 0xa0, 0x60],
  music:     [0xc8, 0x70, 0xc0],
  food:      [0xe0, 0x70, 0x50],
  gaming:    [0x90, 0x80, 0xd0],
  science:   [0x60, 0xc0, 0xa0],
  education: [0xa0, 0xc0, 0x68],
  news:      [0xd8, 0xa8, 0xa8],
  sports:    [0xe0, 0xc0, 0x60],
  commerce:  [0x9a, 0xa9, 0xbc],
  community: [0x80, 0xc8, 0xd0],
  personal:  [0xd8, 0xa0, 0x50],
  misc:      [0x70, 0x70, 0x80],
};
const SITE_TYPE_RGB = {
  hobbyist:    [0xd8, 0xa0, 0x50],
  blog:        [0xa8, 0xa2, 0x98],
  business:    [0x9a, 0xa9, 0xbc],
  educational: [0xa0, 0xc0, 0x68],
  government:  [0x5a, 0x6c, 0x80],
  unknown:     [0x40, 0x44, 0x48],
};

function rgbForCard(c) {
  if (currentMode === 'categories') {
    return CATEGORY_RGB[c.category] || CATEGORY_RGB.misc;
  }
  return SITE_TYPE_RGB[c.site_type] || SITE_TYPE_RGB.unknown;
}

// Fast-path renderer for dot mode — uses ImageData direct pixel writes for
// 250K+ tiny dots smoothly (orders of magnitude faster than 250K fillRect calls).
function drawDotsImageData() {
  const w = canvas.width, h = canvas.height;
  const imgData = ctx.createImageData(w, h);
  const data = imgData.data;

  // Background — fill all pixels with bg color (faster than calling clearRect first)
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 10; data[i+1] = 12; data[i+2] = 16; data[i+3] = 255;
  }

  const cx = w * 0.5;
  const cy = h * 0.5;
  const zd = cam.z * dpr;
  for (let k = 0; k < cards.length; k++) {
    const c = cards[k];
    const sx = ((c.x - cam.x) * zd + cx) | 0;
    const sy = ((c.y - cam.y) * zd + cy) | 0;
    if (sx < 0 || sx >= w || sy < 0 || sy >= h) continue;
    const rgb = rgbForCard(c);
    // Write a 2×2 block so dots are visible on hi-DPI; fall back to 1×1 at edges
    let idx = (sy * w + sx) * 4;
    data[idx]   = rgb[0]; data[idx+1] = rgb[1]; data[idx+2] = rgb[2]; data[idx+3] = 255;
    if (sx + 1 < w) {
      idx += 4;
      data[idx]   = rgb[0]; data[idx+1] = rgb[1]; data[idx+2] = rgb[2]; data[idx+3] = 255;
    }
    if (sy + 1 < h) {
      idx = ((sy + 1) * w + sx) * 4;
      data[idx]   = rgb[0]; data[idx+1] = rgb[1]; data[idx+2] = rgb[2]; data[idx+3] = 255;
      if (sx + 1 < w) {
        idx += 4;
        data[idx]   = rgb[0]; data[idx+1] = rgb[1]; data[idx+2] = rgb[2]; data[idx+3] = 255;
      }
    }
  }
  ctx.putImageData(imgData, 0, 0);
}

// Re-draw rings + trails + sector spokes + origin marker AFTER putImageData has
// wiped the canvas. Used in dot mode to layer decorations on top of dots.
function redrawDecorations(ox, oy, zd) {
  for (const ring of rings) {
    const r = ring.radius * zd;
    ctx.strokeStyle = ring.color || 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(ox, oy, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  if (trails && trails.length) {
    for (const trail of trails) {
      const pts = trail.points || [];
      if (pts.length < 2) continue;
      ctx.strokeStyle = (trail.color || '#cfd8c8') + '33';
      ctx.lineWidth = Math.max(2, 6 * zd);
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(ox + pts[0][0] * zd, oy + pts[0][1] * zd);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(ox + pts[i][0] * zd, oy + pts[i][1] * zd);
      ctx.stroke();
      ctx.strokeStyle = (trail.color || '#cfd8c8') + 'aa';
      ctx.lineWidth = Math.max(1, 1.5 * zd);
      ctx.beginPath();
      ctx.moveTo(ox + pts[0][0] * zd, oy + pts[0][1] * zd);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(ox + pts[i][0] * zd, oy + pts[i][1] * zd);
      ctx.stroke();
    }
  }
  ctx.fillStyle = 'rgba(255,255,255,0.10)';
  ctx.beginPath();
  ctx.arc(ox, oy, 3, 0, Math.PI * 2);
  ctx.fill();
}

// Detail radius around the player (in grid cells). At cell-size ~70 world units,
// 4 cells radius covers ~280 world units around the player — about a "neighborhood"
// of buildings rendered at full detail. Beyond this, cards remain dots only.
const DETAIL_RADIUS_CELLS = 4;

// ── C5: Heatmap (12 categories × 5 eras matrix; cell color = log(count)) ──
// Ignores spatial layout — pure aggregation of `visibleCards`. Honors all
// filterState filters via the shared visibleCards projection.
function drawHeatmap() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const cats = Object.keys(categoryColors).length ? Object.keys(categoryColors)
    : ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
  const eras = ERA_LIST;
  const counts = {};
  let maxCount = 1;
  for (const c of cats) {
    counts[c] = {};
    for (const e of eras) counts[c][e] = 0;
  }
  const src = visibleCards.length ? visibleCards : cards;
  for (const card of src) {
    const c = card.category, e = card.era;
    if (!counts[c]) continue;
    if (!counts[c][e]) counts[c][e] = 0;
    counts[c][e]++;
    if (counts[c][e] > maxCount) maxCount = counts[c][e];
  }
  // Layout: leave margins for axis labels.
  const margin = { left: 110 * dpr, top: 50 * dpr, right: 30 * dpr, bottom: 90 * dpr };
  const W = canvas.width - margin.left - margin.right;
  const H = canvas.height - margin.top - margin.bottom;
  const cellW = W / eras.length;
  const cellH = H / cats.length;
  const labelFont = `500 ${12 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  // Draw cells
  ctx.font = labelFont;
  ctx.textBaseline = 'middle';
  const logMax = Math.log(maxCount + 1);
  for (let ci = 0; ci < cats.length; ci++) {
    for (let ei = 0; ei < eras.length; ei++) {
      const cnt = counts[cats[ci]][eras[ei]] || 0;
      const t = Math.log(cnt + 1) / logMax;
      const x = margin.left + ei * cellW;
      const y = margin.top + ci * cellH;
      // Background tinted by category color, intensity = log scale of count
      const baseColor = categoryColors[cats[ci]] || '#7aa9d8';
      ctx.fillStyle = baseColor + Math.floor(0x12 + t * 0xed).toString(16).padStart(2, '0');
      ctx.fillRect(x, y, cellW - 2 * dpr, cellH - 2 * dpr);
      // Count text — only if cell big enough and count > 0
      if (cnt > 0 && cellW > 30 * dpr) {
        ctx.fillStyle = t > 0.6 ? '#0c0c0e' : '#ece8df';
        ctx.textAlign = 'center';
        ctx.fillText(String(cnt), x + cellW / 2, y + cellH / 2);
      }
    }
  }
  // Row labels (categories) on the left
  ctx.textAlign = 'right';
  for (let ci = 0; ci < cats.length; ci++) {
    ctx.fillStyle = categoryColors[cats[ci]] || '#cfd8c8';
    ctx.fillText(cats[ci], margin.left - 8 * dpr, margin.top + ci * cellH + cellH / 2);
  }
  // Column labels (eras) on the bottom
  ctx.textAlign = 'center';
  ctx.fillStyle = '#cfd8c8';
  for (let ei = 0; ei < eras.length; ei++) {
    ctx.save();
    const x = margin.left + ei * cellW + cellW / 2;
    const y = margin.top + H + 14 * dpr;
    ctx.translate(x, y);
    ctx.rotate(-Math.PI / 6);
    ctx.fillText(eras[ei], 0, 0);
    ctx.restore();
  }
  // Title
  ctx.textAlign = 'center';
  ctx.font = `500 ${15 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.fillStyle = '#ece8df';
  ctx.fillText('cards by category × era · ' + (visibleCards.length || cards.length).toLocaleString() + ' cards · log scale',
               canvas.width / 2, 24 * dpr);
}

// ── C3: Treemap (squarified — cat → site_type → era nested rects) ──
// Three-level nesting; area proportional to count. Ignores spatial layout.
function drawTreemap() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const src = visibleCards.length ? visibleCards : cards;
  // Aggregate
  const tree = {};
  for (const c of src) {
    if (!tree[c.category]) tree[c.category] = { total: 0, children: {} };
    tree[c.category].total++;
    if (!tree[c.category].children[c.site_type]) {
      tree[c.category].children[c.site_type] = { total: 0, children: {} };
    }
    tree[c.category].children[c.site_type].total++;
    const eras = tree[c.category].children[c.site_type].children;
    if (!eras[c.era]) eras[c.era] = 0;
    eras[c.era]++;
  }
  // Squarified treemap — recursive split of rectangle into rows aligned with the
  // shorter side, each row containing items with similar aspect ratios. This is
  // the standard squarify algorithm (Bruls et al. 2000) — short, no library.
  function layoutNode(items, x, y, w, h) {
    // items: [{key, total, ...}]
    items = items.slice().sort((a, b) => b.total - a.total);
    const total = items.reduce((s, i) => s + i.total, 0);
    if (total === 0 || items.length === 0) return [];
    const result = [];
    let area = w * h;
    let scale = area / total;
    let remaining = items;
    let curX = x, curY = y, curW = w, curH = h;
    while (remaining.length) {
      // Pick stripe along shorter side
      const horizontal = curW > curH;
      const stripeLen = horizontal ? curH : curW;
      let row = [];
      let rowSum = 0;
      let bestRatio = Infinity;
      // Greedy — add items until aspect ratio worsens
      let i = 0;
      while (i < remaining.length) {
        const cand = remaining.slice(0, i + 1);
        const candSum = cand.reduce((s, it) => s + it.total, 0);
        const stripeBreadth = (candSum * scale) / stripeLen;
        // Worst aspect among current row
        let worst = 0;
        for (const it of cand) {
          const itLen = (it.total * scale) / stripeBreadth;
          worst = Math.max(worst, Math.max(itLen / stripeBreadth, stripeBreadth / itLen));
        }
        if (worst > bestRatio && row.length > 0) break;
        row = cand;
        rowSum = candSum;
        bestRatio = worst;
        i++;
      }
      // Place row
      const stripeBreadth = (rowSum * scale) / stripeLen;
      let pos = 0;
      for (const it of row) {
        const itLen = (it.total * scale) / stripeBreadth;
        const rx = horizontal ? curX : curX + pos;
        const ry = horizontal ? curY + pos : curY;
        const rw = horizontal ? stripeBreadth : itLen;
        const rh = horizontal ? itLen : stripeBreadth;
        result.push({ ...it, x: rx, y: ry, w: rw, h: rh });
        pos += itLen;
      }
      // Shrink containing rect
      if (horizontal) { curX += stripeBreadth; curW -= stripeBreadth; }
      else            { curY += stripeBreadth; curH -= stripeBreadth; }
      remaining = remaining.slice(row.length);
    }
    return result;
  }
  // Top level: categories
  const margin = 24 * dpr;
  const items = Object.entries(tree).map(([k, v]) => ({ key: k, total: v.total, children: v.children }));
  const placed = layoutNode(items, margin, margin + 20 * dpr, canvas.width - 2 * margin, canvas.height - 2 * margin - 20 * dpr);
  // Draw each category rect, then nest site_types inside, then era splits inside that
  for (const cat of placed) {
    const baseColor = categoryColors[cat.key] || '#7aa9d8';
    ctx.fillStyle = baseColor + '60';
    ctx.fillRect(cat.x, cat.y, cat.w, cat.h);
    ctx.strokeStyle = '#0c0c0e';
    ctx.lineWidth = 1.5 * dpr;
    ctx.strokeRect(cat.x, cat.y, cat.w, cat.h);
    // Nest site_types
    if (cat.w > 50 * dpr && cat.h > 40 * dpr) {
      const siteItems = Object.entries(cat.children).map(([k, v]) => ({ key: k, total: v.total, children: v.children }));
      const sites = layoutNode(siteItems, cat.x + 2, cat.y + 14 * dpr, cat.w - 4, cat.h - 16 * dpr);
      for (const site of sites) {
        ctx.fillStyle = baseColor + 'a0';
        ctx.fillRect(site.x, site.y, site.w, site.h);
        ctx.strokeStyle = baseColor;
        ctx.lineWidth = 0.8 * dpr;
        ctx.strokeRect(site.x, site.y, site.w, site.h);
        if (site.w > 40 * dpr && site.h > 16 * dpr) {
          ctx.fillStyle = '#0c0c0e';
          ctx.font = `500 ${10 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'top';
          ctx.fillText(site.key + ' ' + site.total, site.x + 3 * dpr, site.y + 2 * dpr);
        }
      }
      // Cat label
      ctx.fillStyle = '#0c0c0e';
      ctx.font = `600 ${12 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(cat.key + ' · ' + cat.total, cat.x + 4 * dpr, cat.y + 2 * dpr);
    }
  }
  // Title
  ctx.textAlign = 'center';
  ctx.font = `500 ${15 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.fillStyle = '#ece8df';
  ctx.textBaseline = 'middle';
  ctx.fillText('treemap · ' + (visibleCards.length || cards.length).toLocaleString() + ' cards · area = count',
               canvas.width / 2, 14 * dpr);
}

// ── C6: Sankey (era → category flow) ──
// Two columns: left = 5 eras as stacked bands; right = 12 categories as stacked
// bands. Ribbons connect each (era, cat) cell, width = count. Pure canvas, no
// library — beziers approximate the curve.
function drawSankey() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const cats = Object.keys(categoryColors).length ? Object.keys(categoryColors)
    : ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
  const eras = ERA_LIST;
  const flow = {}; // era → cat → count
  for (const e of eras) flow[e] = {};
  const src = visibleCards.length ? visibleCards : cards;
  for (const c of src) {
    if (!flow[c.era]) flow[c.era] = {};
    flow[c.era][c.category] = (flow[c.era][c.category] || 0) + 1;
  }
  const total = src.length;
  if (!total) {
    ctx.fillStyle = '#6a7480'; ctx.textAlign = 'center'; ctx.font = `${14*dpr}px monospace`;
    ctx.fillText('no cards in current filter', canvas.width / 2, canvas.height / 2);
    return;
  }
  // Layout: bars at left x=margin, right x=canvas-margin. Bar height proportional
  // to total count of that node.
  const margin = { left: 110 * dpr, right: 130 * dpr, top: 50 * dpr, bottom: 30 * dpr };
  const H = canvas.height - margin.top - margin.bottom;
  const barW = 14 * dpr;
  // Era totals (left side)
  const eraTotals = {};
  for (const e of eras) eraTotals[e] = Object.values(flow[e] || {}).reduce((a, b) => a + b, 0);
  const eraGap = 6 * dpr;
  const eraSumH = H - (eras.length - 1) * eraGap;
  // y position helpers
  const eraY = {};
  let cy = margin.top;
  for (const e of eras) {
    eraY[e] = { y: cy, h: (eraTotals[e] / total) * eraSumH };
    cy += eraY[e].h + eraGap;
  }
  // Category totals (right)
  const catTotals = {};
  for (const c of cats) catTotals[c] = 0;
  for (const e of eras) for (const c of cats) catTotals[c] += (flow[e][c] || 0);
  const catGap = 4 * dpr;
  const catSumH = H - (cats.length - 1) * catGap;
  const catY = {};
  cy = margin.top;
  for (const c of cats) {
    catY[c] = { y: cy, h: (catTotals[c] / total) * catSumH };
    cy += catY[c].h + catGap;
  }
  // Draw ribbons: era→cat. For each pair, sub-bar height = (flow[e][c]/eraTotal) * eraBar.h
  // Track running offset within each bar so multiple ribbons stack nicely.
  const eraOff = {}; for (const e of eras) eraOff[e] = 0;
  const catOff = {}; for (const c of cats) catOff[c] = 0;
  // Order ribbons by left bar position then right bar position to minimize crossing
  const xL = margin.left + barW;
  const xR = canvas.width - margin.right - barW;
  for (const e of eras) {
    for (const c of cats) {
      const cnt = flow[e][c] || 0;
      if (!cnt) continue;
      const lh = (cnt / total) * eraSumH;
      const rh = (cnt / total) * catSumH;
      const ly = eraY[e].y + eraOff[e];
      const ry = catY[c].y + catOff[c];
      eraOff[e] += lh;
      catOff[c] += rh;
      // Ribbon: top edge from (xL, ly) to (xR, ry), bottom edge from (xL, ly+lh) to (xR, ry+rh).
      // Use cubic beziers for the curve.
      const cp1x = xL + (xR - xL) * 0.5;
      const baseColor = categoryColors[c] || '#7aa9d8';
      ctx.fillStyle = baseColor + '50';
      ctx.beginPath();
      ctx.moveTo(xL, ly);
      ctx.bezierCurveTo(cp1x, ly, cp1x, ry, xR, ry);
      ctx.lineTo(xR, ry + rh);
      ctx.bezierCurveTo(cp1x, ry + rh, cp1x, ly + lh, xL, ly + lh);
      ctx.closePath();
      ctx.fill();
    }
  }
  // Era bars (left)
  ctx.font = `500 ${11 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textBaseline = 'middle';
  for (const e of eras) {
    const color = ERA_COLORS[e] || '#cfd8c8';
    ctx.fillStyle = color;
    ctx.fillRect(margin.left, eraY[e].y, barW, eraY[e].h);
    ctx.textAlign = 'right';
    ctx.fillText(e, margin.left - 6 * dpr, eraY[e].y + eraY[e].h / 2);
  }
  // Cat bars (right)
  for (const c of cats) {
    const color = categoryColors[c] || '#7aa9d8';
    ctx.fillStyle = color;
    ctx.fillRect(canvas.width - margin.right - barW, catY[c].y, barW, catY[c].h);
    ctx.textAlign = 'left';
    ctx.fillText(c, canvas.width - margin.right - barW + barW + 6 * dpr,
                 catY[c].y + catY[c].h / 2);
  }
  // Title
  ctx.font = `500 ${15 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.fillStyle = '#ece8df'; ctx.textAlign = 'center';
  ctx.fillText('sankey · era (left) → category (right) · ' + total.toLocaleString() + ' cards',
               canvas.width / 2, 24 * dpr);
}

// ── C9: Histogram array (12 panels — score distribution per category) ──
// Small multiples — 3 rows × 4 cols. Each panel: bar histogram of score for one
// category, x-axis is score bucket, y-axis is count (log-scaled).
function drawHistograms() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const cats = Object.keys(categoryColors).length ? Object.keys(categoryColors)
    : ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
  const src = visibleCards.length ? visibleCards : cards;
  // Bucket [-5..5] into 20 bins of width 0.5. Cards with score outside clipped to ends.
  const N_BINS = 20;
  const SCORE_LO = -5, SCORE_HI = 5;
  const buckets = {};
  for (const c of cats) buckets[c] = new Array(N_BINS).fill(0);
  for (const card of src) {
    if (!buckets[card.category]) continue;
    const t = (Math.max(SCORE_LO, Math.min(SCORE_HI, card.score)) - SCORE_LO) / (SCORE_HI - SCORE_LO);
    const bin = Math.min(N_BINS - 1, Math.floor(t * N_BINS));
    buckets[card.category][bin]++;
  }
  const COLS = 4, ROWS = 3;
  const margin = { top: 50 * dpr, side: 20 * dpr, bottom: 20 * dpr };
  const PW = (canvas.width - 2 * margin.side) / COLS;
  const PH = (canvas.height - margin.top - margin.bottom) / ROWS;
  ctx.font = `500 ${11 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  for (let i = 0; i < cats.length; i++) {
    const cat = cats[i];
    const r = Math.floor(i / COLS);
    const c = i % COLS;
    const px = margin.side + c * PW;
    const py = margin.top + r * PH;
    const inset = 8 * dpr;
    const bx = px + inset, by = py + inset + 14 * dpr;
    const bw = PW - 2 * inset, bh = PH - 2 * inset - 14 * dpr;
    // Frame
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = 1;
    ctx.strokeRect(px + inset / 2, py + inset / 2, PW - inset, PH - inset);
    // Bars (log scale)
    const max = Math.max(...buckets[cat], 1);
    const logMax = Math.log(max + 1);
    const binW = bw / N_BINS;
    for (let bi = 0; bi < N_BINS; bi++) {
      const cnt = buckets[cat][bi];
      const t = Math.log(cnt + 1) / logMax;
      const h = t * bh;
      ctx.fillStyle = (categoryColors[cat] || '#7aa9d8') + 'cc';
      ctx.fillRect(bx + bi * binW, by + bh - h, binW - 1, h);
    }
    // Title
    ctx.fillStyle = categoryColors[cat] || '#cfd8c8';
    ctx.textAlign = 'left';
    ctx.fillText(cat, px + inset, py + inset + 11 * dpr);
    ctx.textAlign = 'right';
    ctx.fillStyle = '#6a7480';
    ctx.fillText(buckets[cat].reduce((a, b) => a + b, 0).toLocaleString(),
                 px + PW - inset, py + inset + 11 * dpr);
  }
  // Title
  ctx.font = `500 ${15 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.fillStyle = '#ece8df'; ctx.textAlign = 'center';
  ctx.fillText('histograms · score distribution per category · log-scaled · ' +
               src.length.toLocaleString() + ' cards',
               canvas.width / 2, 24 * dpr);
}

// ── C4: Sunburst (radial treemap — cat → site_type → era) ──
// Three concentric rings from center. Inner = categories, outer = subtypes,
// outermost = eras. Angular extent = count.
function drawSunburst() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const cats = Object.keys(categoryColors).length ? Object.keys(categoryColors)
    : ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
  const src = visibleCards.length ? visibleCards : cards;
  // Aggregate
  const tree = {};
  for (const c of src) {
    if (!tree[c.category]) tree[c.category] = { total: 0, sub: {} };
    tree[c.category].total++;
    if (!tree[c.category].sub[c.site_type]) tree[c.category].sub[c.site_type] = { total: 0, era: {} };
    tree[c.category].sub[c.site_type].total++;
    const era = tree[c.category].sub[c.site_type].era;
    era[c.era] = (era[c.era] || 0) + 1;
  }
  const total = src.length;
  if (!total) return;
  const cx = canvas.width / 2;
  const cy = canvas.height / 2 + 10 * dpr;
  const maxR = Math.min(canvas.width, canvas.height) * 0.45;
  const r1 = maxR * 0.30; // inner: empty / center
  const r2 = maxR * 0.55; // category ring
  const r3 = maxR * 0.78; // site_type ring
  const r4 = maxR * 1.00; // era ring
  // Walk categories in CATEGORIES order so colors match the legend.
  let angle = -Math.PI / 2;
  for (const cat of cats) {
    const node = tree[cat];
    if (!node || !node.total) continue;
    const arc = (node.total / total) * Math.PI * 2;
    const baseColor = categoryColors[cat] || '#7aa9d8';
    // Cat segment
    ctx.fillStyle = baseColor + 'c0';
    ctx.beginPath();
    ctx.moveTo(cx + r1 * Math.cos(angle), cy + r1 * Math.sin(angle));
    ctx.arc(cx, cy, r2, angle, angle + arc);
    ctx.arc(cx, cy, r1, angle + arc, angle, true);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = '#0c0c0e'; ctx.lineWidth = 1.5; ctx.stroke();
    // Cat label (radial)
    if (arc > 0.12) {
      const midAng = angle + arc / 2;
      ctx.save();
      ctx.translate(cx + (r1 + r2) / 2 * Math.cos(midAng), cy + (r1 + r2) / 2 * Math.sin(midAng));
      const rotate = midAng + (Math.sin(midAng) > 0 ? Math.PI / 2 : -Math.PI / 2);
      ctx.rotate(rotate - (Math.sin(midAng) > 0 ? Math.PI : 0));
      ctx.fillStyle = '#0c0c0e';
      ctx.font = `600 ${11 * dpr}px ui-monospace, monospace`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(cat, 0, 0);
      ctx.restore();
    }
    // Site_type ring
    let subAng = angle;
    for (const [st, sub] of Object.entries(node.sub)) {
      const subArc = (sub.total / node.total) * arc;
      ctx.fillStyle = baseColor + '80';
      ctx.beginPath();
      ctx.moveTo(cx + r2 * Math.cos(subAng), cy + r2 * Math.sin(subAng));
      ctx.arc(cx, cy, r3, subAng, subAng + subArc);
      ctx.arc(cx, cy, r2, subAng + subArc, subAng, true);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = '#0c0c0e'; ctx.lineWidth = 0.8; ctx.stroke();
      // Era ring
      let eraAng = subAng;
      for (const [era, cnt] of Object.entries(sub.era)) {
        const eraArc = (cnt / sub.total) * subArc;
        ctx.fillStyle = (ERA_COLORS[era] || '#cfd8c8') + 'a0';
        ctx.beginPath();
        ctx.moveTo(cx + r3 * Math.cos(eraAng), cy + r3 * Math.sin(eraAng));
        ctx.arc(cx, cy, r4, eraAng, eraAng + eraArc);
        ctx.arc(cx, cy, r3, eraAng + eraArc, eraAng, true);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = '#0c0c0e'; ctx.lineWidth = 0.4; ctx.stroke();
        eraAng += eraArc;
      }
      subAng += subArc;
    }
    angle += arc;
  }
  // Title
  ctx.font = `500 ${15 * dpr}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.fillStyle = '#ece8df'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('sunburst · cat (inner) → site_type → era (outer) · ' +
               total.toLocaleString() + ' cards',
               canvas.width / 2, 24 * dpr);
}

// ── C7: Chord diagram (inter-category linkage from link_edges) ──
// Lazy-fetches /api/category-edges (cached server-side for 1h). Renders 12 cat
// arcs around the perimeter, ribbons inside connecting each pair sized by count.
let _chordData = null;
let _chordFetching = false;
function drawChord() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!_chordData && !_chordFetching) {
    _chordFetching = true;
    fetch('/api/category-edges').then(r => r.json()).then(d => { _chordData = d; _chordFetching = false; })
      .catch(e => { console.error('chord fetch failed', e); _chordFetching = false; });
  }
  if (!_chordData) {
    ctx.fillStyle = '#6a7480'; ctx.textAlign = 'center'; ctx.font = `${14*dpr}px monospace`;
    ctx.fillText('computing inter-category links (heavy join — first request takes ~30s)…',
                 canvas.width / 2, canvas.height / 2);
    return;
  }
  const cats = Object.keys(categoryColors).length ? Object.keys(categoryColors)
    : ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
  // Per-category total outflow (excluding self-loops for cleaner chord).
  const outflow = {};
  for (const c of cats) {
    outflow[c] = 0;
    if (_chordData[c]) for (const dst in _chordData[c]) {
      if (dst !== c) outflow[c] += _chordData[c][dst];
    }
  }
  const total = Object.values(outflow).reduce((a, b) => a + b, 0);
  if (!total) {
    ctx.fillStyle = '#6a7480'; ctx.textAlign = 'center'; ctx.font = `${14*dpr}px monospace`;
    ctx.fillText('no inter-category links yet', canvas.width / 2, canvas.height / 2);
    return;
  }
  const cx = canvas.width / 2;
  const cy = canvas.height / 2 + 10 * dpr;
  const R = Math.min(canvas.width, canvas.height) * 0.42;
  const ringW = 14 * dpr;
  const gapAngle = 0.02;
  const totalGapAngle = gapAngle * cats.length;
  const usableAngle = Math.PI * 2 - totalGapAngle;
  // Compute per-cat arc range
  const catRange = {}; let cum = -Math.PI / 2;
  for (const c of cats) {
    const arc = (outflow[c] / total) * usableAngle;
    catRange[c] = { a0: cum, a1: cum + arc };
    cum += arc + gapAngle;
  }
  // Draw perimeter arcs
  for (const c of cats) {
    if (!outflow[c]) continue;
    ctx.beginPath();
    ctx.arc(cx, cy, R, catRange[c].a0, catRange[c].a1);
    ctx.arc(cx, cy, R - ringW, catRange[c].a1, catRange[c].a0, true);
    ctx.closePath();
    ctx.fillStyle = categoryColors[c] || '#7aa9d8';
    ctx.fill();
    // Label outside the arc
    const mid = (catRange[c].a0 + catRange[c].a1) / 2;
    const lx = cx + (R + 14 * dpr) * Math.cos(mid);
    const ly = cy + (R + 14 * dpr) * Math.sin(mid);
    ctx.fillStyle = categoryColors[c] || '#cfd8c8';
    ctx.font = `500 ${11 * dpr}px ui-monospace, monospace`;
    ctx.textAlign = Math.cos(mid) > 0 ? 'left' : 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(c, lx, ly);
  }
  // Ribbons: within each cat, allocate sub-arc per dst-cat; bezier to dst's mirrored slot.
  for (const src of cats) {
    if (!_chordData[src]) continue;
    let off = 0;
    const srcSpan = catRange[src].a1 - catRange[src].a0;
    for (const dst of cats) {
      if (dst === src) continue;
      const cnt = _chordData[src][dst] || 0;
      if (!cnt) continue;
      const subSrc = (cnt / outflow[src]) * srcSpan;
      // Compute the dst sub-range. We need to know how much of dst's arc is from src.
      // Since dst's arc is by total outflow (which excludes its own incoming), we
      // approximate by stacking incoming proportionally.
      // Simpler: ribbon starts at src's sub-arc, ends at midpoint of dst arc minus a small offset.
      const a0 = catRange[src].a0 + off;
      const a1 = a0 + subSrc;
      off += subSrc;
      const dstMid = (catRange[dst].a0 + catRange[dst].a1) / 2;
      const dst0 = dstMid - subSrc / 2;
      const dst1 = dstMid + subSrc / 2;
      const innerR = R - ringW;
      const p0 = [cx + innerR * Math.cos(a0), cy + innerR * Math.sin(a0)];
      const p1 = [cx + innerR * Math.cos(a1), cy + innerR * Math.sin(a1)];
      const q0 = [cx + innerR * Math.cos(dst0), cy + innerR * Math.sin(dst0)];
      const q1 = [cx + innerR * Math.cos(dst1), cy + innerR * Math.sin(dst1)];
      ctx.fillStyle = (categoryColors[src] || '#7aa9d8') + '30';
      ctx.beginPath();
      ctx.moveTo(p0[0], p0[1]);
      ctx.quadraticCurveTo(cx, cy, q1[0], q1[1]);
      ctx.arc(cx, cy, innerR, dst1, dst0, true);
      ctx.quadraticCurveTo(cx, cy, p0[0], p0[1]);
      ctx.arc(cx, cy, innerR, a0, a1);
      ctx.closePath();
      ctx.fill();
    }
  }
  // Title
  ctx.font = `500 ${15 * dpr}px ui-monospace, monospace`;
  ctx.fillStyle = '#ece8df'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('chord · inter-category links · ' + total.toLocaleString() + ' edges',
               canvas.width / 2, 24 * dpr);
}

// ── C8: Bubble chart (x=score, y=log(word_count), color=category) ──
// Each card is a small dot. Sample down at high counts to keep render snappy.
function drawBubble() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const src = visibleCards.length ? visibleCards : cards;
  if (!src.length) return;
  const margin = { left: 60 * dpr, right: 30 * dpr, top: 50 * dpr, bottom: 50 * dpr };
  const W = canvas.width - margin.left - margin.right;
  const H = canvas.height - margin.top - margin.bottom;
  // Axes
  ctx.strokeStyle = 'rgba(255,255,255,0.15)'; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(margin.left, margin.top); ctx.lineTo(margin.left, margin.top + H);
  ctx.moveTo(margin.left, margin.top + H); ctx.lineTo(margin.left + W, margin.top + H);
  ctx.stroke();
  // Map ranges
  const SCORE_LO = -5, SCORE_HI = 10;
  const WC_LO_LOG = Math.log(1), WC_HI_LOG = Math.log(50000);
  // Sample for perf: render up to 30K dots, rest stochastically
  const cap = 30000;
  const step = Math.max(1, Math.floor(src.length / cap));
  for (let i = 0; i < src.length; i += step) {
    const c = src[i];
    const wc = c.wc || 0;
    const sx = margin.left + ((c.score - SCORE_LO) / (SCORE_HI - SCORE_LO)) * W;
    const sy = margin.top + H - ((Math.log(wc + 1) - WC_LO_LOG) / (WC_HI_LOG - WC_LO_LOG)) * H;
    if (sx < margin.left - 4 || sx > margin.left + W + 4) continue;
    if (sy < margin.top - 4 || sy > margin.top + H + 4) continue;
    ctx.fillStyle = (categoryColors[c.category] || '#7aa9d8') + '70';
    ctx.fillRect(sx - 1, sy - 1, 2 * dpr, 2 * dpr);
  }
  // Axis labels
  ctx.fillStyle = '#cfd8c8'; ctx.font = `${11 * dpr}px monospace`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  for (let s = SCORE_LO; s <= SCORE_HI; s += 2.5) {
    const sx = margin.left + ((s - SCORE_LO) / (SCORE_HI - SCORE_LO)) * W;
    ctx.fillText(s.toFixed(1), sx, margin.top + H + 4 * dpr);
  }
  ctx.fillText('score →', margin.left + W / 2, margin.top + H + 24 * dpr);
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (const wc of [1, 100, 1000, 10000]) {
    const sy = margin.top + H - ((Math.log(wc + 1) - WC_LO_LOG) / (WC_HI_LOG - WC_LO_LOG)) * H;
    ctx.fillText(wc.toLocaleString(), margin.left - 6 * dpr, sy);
  }
  ctx.save();
  ctx.translate(20 * dpr, margin.top + H / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = 'center'; ctx.fillText('word_count (log) ↑', 0, 0);
  ctx.restore();
  // Title
  ctx.font = `500 ${15 * dpr}px monospace`;
  ctx.fillStyle = '#ece8df'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  const sampleStr = step > 1 ? ' (1/' + step + ' sampled)' : '';
  ctx.fillText('bubble · score × word_count · ' + src.length.toLocaleString() + ' cards' + sampleStr,
               canvas.width / 2, 24 * dpr);
}

// ── C10: Stream graph (corpus over id range, stacked by category) ──
// id is autoincrement so it correlates with discovery order. Bucket id range
// into N buckets, count per category per bucket, render as stacked filled
// areas. No true timestamp needed.
function drawStream() {
  ctx.fillStyle = '#0c0c0e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const src = visibleCards.length ? visibleCards : cards;
  if (!src.length) return;
  let idMin = Infinity, idMax = -Infinity;
  for (const c of src) {
    if (c.id < idMin) idMin = c.id;
    if (c.id > idMax) idMax = c.id;
  }
  const N_BUCKETS = 80;
  const cats = Object.keys(categoryColors).length ? Object.keys(categoryColors)
    : ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
  const buckets = []; for (let i = 0; i < N_BUCKETS; i++) {
    buckets.push(Object.fromEntries(cats.map(c => [c, 0])));
  }
  const span = (idMax - idMin) || 1;
  for (const c of src) {
    const bi = Math.min(N_BUCKETS - 1, Math.floor(((c.id - idMin) / span) * N_BUCKETS));
    if (buckets[bi][c.category] != null) buckets[bi][c.category]++;
  }
  const margin = { left: 30 * dpr, right: 30 * dpr, top: 50 * dpr, bottom: 60 * dpr };
  const W = canvas.width - margin.left - margin.right;
  const H = canvas.height - margin.top - margin.bottom;
  // Per-bucket totals → max for scaling
  const totals = buckets.map(b => Object.values(b).reduce((a, b) => a + b, 0));
  const maxTotal = Math.max(...totals, 1);
  const stepX = W / (N_BUCKETS - 1);
  // Stack from bottom up. For each cat layer, draw a polygon along x with
  // y = stacked offset.
  let prevTop = totals.map(t => H);  // baseline at bottom
  for (const cat of cats) {
    const top = [];
    for (let bi = 0; bi < N_BUCKETS; bi++) {
      const cnt = buckets[bi][cat] || 0;
      const h = (cnt / maxTotal) * H;
      top.push(prevTop[bi] - h);
    }
    // Fill polygon: (x, top[i]) ... (x, prevTop[i] reversed)
    ctx.fillStyle = (categoryColors[cat] || '#7aa9d8') + 'd0';
    ctx.beginPath();
    ctx.moveTo(margin.left, margin.top + top[0]);
    for (let bi = 0; bi < N_BUCKETS; bi++) {
      ctx.lineTo(margin.left + bi * stepX, margin.top + top[bi]);
    }
    for (let bi = N_BUCKETS - 1; bi >= 0; bi--) {
      ctx.lineTo(margin.left + bi * stepX, margin.top + prevTop[bi]);
    }
    ctx.closePath();
    ctx.fill();
    prevTop = top;
  }
  // Axis
  ctx.fillStyle = '#cfd8c8'; ctx.font = `${10 * dpr}px monospace`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  ctx.fillText('id ' + idMin.toLocaleString(),     margin.left + 2 * dpr, margin.top + H + 6 * dpr);
  ctx.fillText('id ' + idMax.toLocaleString(),     margin.left + W - 2 * dpr, margin.top + H + 6 * dpr);
  ctx.fillText('discovery order →',                margin.left + W / 2,       margin.top + H + 26 * dpr);
  // Title
  ctx.font = `500 ${15 * dpr}px monospace`;
  ctx.fillStyle = '#ece8df'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('stream · corpus over discovery order · stacked by category · ' +
               src.length.toLocaleString() + ' cards',
               canvas.width / 2, 24 * dpr);
}

function drawScene() {
  // Set C — alternative graph types. Each one fully replaces the canvas;
  // mandala (default) falls through to the existing pipeline below.
  if (typeof filterState !== 'undefined') {
    if (filterState.graphType === 'heatmap')   { drawHeatmap();    return; }
    if (filterState.graphType === 'treemap')   { drawTreemap();    return; }
    if (filterState.graphType === 'sankey')    { drawSankey();     return; }
    if (filterState.graphType === 'histograms'){ drawHistograms(); return; }
    if (filterState.graphType === 'sunburst')  { drawSunburst();   return; }
    if (filterState.graphType === 'chord')     { drawChord();      return; }
    if (filterState.graphType === 'bubble')    { drawBubble();     return; }
    if (filterState.graphType === 'stream')    { drawStream();     return; }
  }

  const [ox, oy] = worldToScreen(0, 0);
  const zd = cam.z * dpr;

  // ── Layer 1: ALWAYS draw the dot layer (every card, ImageData fast path) ──
  // The dot layer is the persistent "map" — the shape of the entire corpus
  // is always visible, regardless of zoom or proximity.
  drawDotsImageData();

  // ── Layer 2: redraw ring/trail/spoke decorations on top of dots ──
  redrawDecorations(ox, oy, zd);
  // Faint sector spokes — only at close zoom
  if (cam.z > 0.6) {
    const sectorCount = currentMode === 'categories' ? 12 : 8;
    const sectorArc = (Math.PI * 2) / sectorCount;
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < sectorCount; i++) {
      const a = i * sectorArc - Math.PI / 2 + sectorArc / 2;
      const x1 = ox + Math.cos(a) * 200 * zd;
      const y1 = oy + Math.sin(a) * 200 * zd;
      const x2 = ox + Math.cos(a) * layoutExtent * zd;
      const y2 = oy + Math.sin(a) * layoutExtent * zd;
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    }
    ctx.stroke();
  }

  // LOD: render-set chosen by zoom
  // - dot mode (z < 0.40): all visibleCards as 1-2px dots
  // - simple mode (0.40..0.55): top-8K visible by score, simple rects
  // - full mode (>= 0.55): grid-culled cells, inline passesFilters() check
  let renderSet;
  if (cam.z < 0.40 || !grid) {
    renderSet = visibleCards.length ? visibleCards : cards;
  } else if (!tileMode && cam.z < 0.55) {
    const src = topByScoreVisible.length ? topByScoreVisible : topByScore;
    renderSet = src.slice(0, 8000);
  } else {
    // Viewport-culled cells from spatial index. Inline filter check skips cards
    // that don't pass current filterState — cheap because cell iteration is
    // already bounded to viewport (typically a few thousand cards).
    const padW = 100 / zd;
    const wx0 = (-canvas.width / 2 / zd) + cam.x - padW;
    const wx1 = ( canvas.width / 2 / zd) + cam.x + padW;
    const wy0 = (-canvas.height / 2 / zd) + cam.y - padW;
    const wy1 = ( canvas.height / 2 / zd) + cam.y + padW;
    const i0 = Math.max(0, Math.floor((wx0 + layoutExtent) / gridCellSize));
    const i1 = Math.min(GRID_RES - 1, Math.floor((wx1 + layoutExtent) / gridCellSize));
    const j0 = Math.max(0, Math.floor((wy0 + layoutExtent) / gridCellSize));
    const j1 = Math.min(GRID_RES - 1, Math.floor((wy1 + layoutExtent) / gridCellSize));
    renderSet = [];
    const cap = tileMode ? 250000 : (cam.z < 1.0 ? 12000 : 60000);
    for (let i = i0; i <= i1 && renderSet.length < cap; i++) {
      for (let j = j0; j <= j1 && renderSet.length < cap; j++) {
        const cell = grid.get(i + ',' + j);
        if (cell) {
          for (const c of cell) {
            if (!passesFilters(c)) continue;
            renderSet.push(c);
            if (renderSet.length >= cap) break;
          }
        }
      }
    }
  }

  // Two render modes (toggled by `tileMode`):
  //   tile (default):    flat fillRect per card, color-coded, gaps between for "city plan" feel
  //   3D-look:           wall + roof + outline, taller score buildings (legacy)
  const baseW = 6 * zd;
  const minDrawWidth = 0.4;
  const fastDraw = cam.z < 0.80;

  if (cam.z < 0.40) {
    // Dot mode — Layer 1 + 2 already drew dots and decorations. Just labels + player.
    drawLabels(ox, oy, zd);
    drawPlayer();
    return;
  }

  if (tileMode) {
    // Flat tile per card — single fillRect, slightly smaller than baseW so gaps
    // form visible "streets" between tiles. Score → small saturation boost.
    const gapFactor = 0.72;  // 28% gap between adjacent tiles for breathing room
    for (let k = 0; k < renderSet.length; k++) {
      const c = renderSet[k];
      const [sx, sy] = worldToScreen(c.x, c.y);
      if (sx < -10 || sx > canvas.width + 10 || sy < -10 || sy > canvas.height + 10) continue;
      const ts = Math.max(1.2, baseW * gapFactor);
      const tx = sx - ts / 2, ty = sy - ts / 2;
      ctx.fillStyle = colorForCard(c);
      ctx.fillRect(tx, ty, ts, ts);
      if (c.score > 4 && cam.z > 0.8) {
        ctx.strokeStyle = 'rgba(154,240,144,0.7)';
        ctx.lineWidth = 0.5;
        ctx.strokeRect(tx - 0.5, ty - 0.5, ts + 1, ts + 1);
      }
      if (nearestBuilding && nearestBuilding.card === c) {
        ctx.strokeStyle = '#ffeec0';
        ctx.lineWidth = 1.4;
        ctx.strokeRect(tx - 1, ty - 1, ts + 2, ts + 2);
      }
    }
    drawLabels(ox, oy, zd);
    drawPlayer();
    return;
  }

  // 3D-look mode — wall + roof + outline
  for (const c of renderSet) {
    const [sx, sy] = worldToScreen(c.x, c.y);
    if (sx < -50 || sx > canvas.width + 50 || sy < -100 || sy > canvas.height + 50) continue;
    if (baseW < minDrawWidth) continue;
    const h = buildingHeight(c) * zd;
    const w = baseW;
    const wallX = sx - w / 2;
    const wallY = sy - h;
    ctx.fillStyle = colorForCard(c);
    ctx.fillRect(wallX, wallY, w, h);
    if (!fastDraw && h > 14) {
      ctx.fillStyle = roofForCard(c);
      ctx.beginPath();
      ctx.moveTo(wallX, wallY);
      ctx.lineTo(wallX + w, wallY);
      ctx.lineTo(wallX + w - 1, wallY - 3 * zd);
      ctx.lineTo(wallX + 1, wallY - 3 * zd);
      ctx.closePath();
      ctx.fill();
    }
    if (!fastDraw && c.score > 2 && cam.z > 0.8) {
      ctx.strokeStyle = 'rgba(154,240,144,0.7)';
      ctx.lineWidth = 0.5;
      ctx.strokeRect(wallX - 0.5, wallY - 0.5, w + 1, h + 1);
    }
    if (nearestBuilding && nearestBuilding.card === c) {
      ctx.strokeStyle = '#ffeec0';
      ctx.lineWidth = 1.4;
      ctx.strokeRect(wallX - 1, wallY - 1, w + 2, h + 2);
    }
  }

  // Labels last — drawn over buildings, scaled with zoom but clamped legibility
  drawLabels(ox, oy, zd);
  drawPlayer();
}

// Draw text along an arc — characters rotated tangent to the radius. Bottom-half
// labels (sin(angle) > 0 in canvas space, where +y is down) flip so they read
// upright. Uses dark stroke + light fill for legibility without a pill background
// (pill on a curve looks weird and we'd need a banana shape).
function drawCurvedLabel(text, cxScr, cyScr, radiusScr, centerAngleRad, fontPx, color) {
  ctx.font = `500 ${fontPx}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const textWidth = ctx.measureText(text).width;
  // If radius is too small (text would wrap > 60% of circle), bail to flat draw.
  const maxArc = Math.PI * 0.6;
  const totalArc = textWidth / Math.max(40, radiusScr);
  const flipped = Math.sin(centerAngleRad) > 0;  // bottom half → flip so it reads upright
  if (totalArc > maxArc || radiusScr < 60) {
    // Fall back to flat: place at the angle, rotated tangent (single rotation).
    const x = cxScr + radiusScr * Math.cos(centerAngleRad);
    const y = cyScr + radiusScr * Math.sin(centerAngleRad);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(centerAngleRad + Math.PI / 2 + (flipped ? Math.PI : 0));
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(12,12,14,0.85)';
    ctx.strokeText(text, 0, 0);
    ctx.fillStyle = color;
    ctx.fillText(text, 0, 0);
    ctx.restore();
    return;
  }
  // Per-character placement along the arc. Character i sits at the angle that
  // corresponds to its cumulative width at the label's radius. Direction reverses
  // on the bottom half so text doesn't read backward.
  let cum = 0;
  for (const ch of text) {
    const charW = ctx.measureText(ch).width;
    const charCenterFromStart = cum + charW / 2;
    const offsetFromCenter = charCenterFromStart - textWidth / 2;
    const dir = flipped ? -1 : 1;
    const angle = centerAngleRad + (offsetFromCenter / radiusScr) * dir;
    const x = cxScr + radiusScr * Math.cos(angle);
    const y = cyScr + radiusScr * Math.sin(angle);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle + Math.PI / 2 + (flipped ? Math.PI : 0));
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(12,12,14,0.85)';
    ctx.strokeText(ch, 0, 0);
    ctx.fillStyle = color;
    ctx.fillText(ch, 0, 0);
    ctx.restore();
    cum += charW;
  }
}

function drawLabels(ox, oy, zd) {
  const baseFont = currentMode === 'categories' ? 18 : 16;
  // Label font sizes follow zoom but stay readable
  const fontPx = Math.max(11, Math.min(28, baseFont * Math.sqrt(cam.z)));
  ctx.font = `500 ${fontPx}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const lab of labels) {
    // Show district + site_type labels at default fit-view zoom (was gated at
    // 0.65 / 0.5 — too high; user couldn't see sub-sector names at all on the
    // flat-categories layout where fit-view zoom ≈ 0.33).
    if (lab.kind === 'district' && cam.z < 0.15) continue;
    if (lab.kind === 'site_type' && cam.z < 0.10) continue;
    const sx = ox + lab.x * zd;
    const sy = oy + lab.y * zd;
    if (sx < -200 || sx > canvas.width + 200 || sy < -100 || sy > canvas.height + 100) continue;

    // Curved labels for category / district / language — text follows the arc.
    // site_type labels stay flat (they're axis labels, not sector labels).
    if (lab.kind === 'category' || lab.kind === 'district' || lab.kind === 'language') {
      const radiusWorld = Math.hypot(lab.x, lab.y);
      const angleRad = Math.atan2(lab.y, lab.x);
      const radiusScr = radiusWorld * zd;
      const cxScr = ox;
      const cyScr = oy;
      drawCurvedLabel(lab.text, cxScr, cyScr, radiusScr, angleRad, fontPx, lab.color || '#ece8df');
      continue;
    }

    // Flat pill for site_type and any other label kind.
    const padX = 7, padY = 4;
    const m = ctx.measureText(lab.text);
    const w = m.width + padX * 2;
    const h = fontPx + padY * 2;
    ctx.fillStyle = 'rgba(12,12,14,0.78)';
    ctx.strokeStyle = (lab.color || '#cfd8c8') + 'cc';
    ctx.lineWidth = 1.2;
    const rx = sx - w / 2, ry = sy - h / 2;
    const radius = 6;
    ctx.beginPath();
    ctx.moveTo(rx + radius, ry);
    ctx.lineTo(rx + w - radius, ry);
    ctx.quadraticCurveTo(rx + w, ry, rx + w, ry + radius);
    ctx.lineTo(rx + w, ry + h - radius);
    ctx.quadraticCurveTo(rx + w, ry + h, rx + w - radius, ry + h);
    ctx.lineTo(rx + radius, ry + h);
    ctx.quadraticCurveTo(rx, ry + h, rx, ry + h - radius);
    ctx.lineTo(rx, ry + radius);
    ctx.quadraticCurveTo(rx, ry, rx + radius, ry);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = lab.color || '#ece8df';
    ctx.fillText(lab.text, sx, sy);
  }
}

function pickAt(sx, sy) {
  // Grid-accelerated pick: check only cards in the grid cell under the cursor + 1-cell padding
  const baseW = 6 * cam.z * dpr;
  const px = sx * dpr;
  const py = sy * dpr;
  const [wx, wy] = screenToWorld(sx, sy);
  let candidates;
  if (grid) {
    const i0 = Math.max(0, Math.floor((wx + layoutExtent) / gridCellSize) - 1);
    const i1 = Math.min(GRID_RES - 1, i0 + 2);
    const j0 = Math.max(0, Math.floor((wy + layoutExtent) / gridCellSize) - 1);
    const j1 = Math.min(GRID_RES - 1, j0 + 2);
    candidates = [];
    for (let i = i0; i <= i1; i++) {
      for (let j = j0; j <= j1; j++) {
        const cell = grid.get(i + ',' + j);
        if (cell) candidates.push(...cell);
      }
    }
  } else {
    candidates = cards;
  }
  let best = null;
  let bestDist2 = Infinity;
  for (const c of candidates) {
    const [bsx, bsy] = worldToScreen(c.x, c.y);
    const h = buildingHeight(c) * cam.z * dpr;
    if (Math.abs(px - bsx) > baseW * 1.2 || py < bsy - h - 4 || py > bsy + 4) continue;
    const dx = px - bsx;
    const dy = py - (bsy - h / 2);
    const d2 = dx * dx + dy * dy;
    if (d2 < bestDist2) { bestDist2 = d2; best = c; }
  }
  return best;
}

// Tooltip cache for hover-fetched titles (lazy-loaded so the layout payload stays small)
const titleCache = new Map();
let pendingTitleFetch = null;

function renderHover(c, ex, ey, title) {
  hover.style.left = (ex + 12) + 'px';
  hover.style.top = (ey + 12) + 'px';
  // Tooltip detail per Set A — minimal = host only; standard (default) = host
  // + title + score/era/category; full = adds site_type, language, TLD.
  const detail = (typeof filterState !== 'undefined' && filterState.tooltipDetail) || 'standard';
  let html = `<div style="font-weight:500">${escapeHtml(c.host)}</div>`;
  if (detail !== 'minimal') {
    if (title) html += `<div style="opacity:0.7">${escapeHtml(title).slice(0, 60)}</div>`;
    html += `<div style="opacity:0.5;margin-top:2px">${c.score >= 0 ? '+' : ''}${c.score} · ${c.era} · ${c.category}</div>`;
  }
  if (detail === 'full') {
    html += `<div style="opacity:0.5;margin-top:2px">${c.site_type} · ${c.language} · ${tldOf(c.host)}</div>`;
  }
  hover.innerHTML = html;
  hover.classList.add('show');
}

canvas.addEventListener('mousemove', (e) => {
  const c = pickAt(e.clientX, e.clientY);
  if (c) {
    canvas.style.cursor = 'pointer';
    const cached = titleCache.get(c.id);
    renderHover(c, e.clientX, e.clientY, cached);
    // Lazy-fetch title in the background — debounced via cancellation
    if (!cached) {
      const myFetch = c.id;
      pendingTitleFetch = myFetch;
      fetch(`/api/card/${c.id}`).then(r => r.json()).then(full => {
        if (full && full.title) titleCache.set(c.id, full.title);
        // Only update DOM if user is still hovering this same card
        if (pendingTitleFetch === myFetch) renderHover(c, e.clientX, e.clientY, full.title);
      }).catch(() => {});
    }
  } else {
    hover.classList.remove('show');
    canvas.style.cursor = 'default';
  }
});

// Click vs drag: if mouse moved < 6px between down and up, treat as click; else
// it was a pan and we suppress the click (avoid accidental "open card").
let panState = null;
canvas.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;  // left mouse only — right reserved for future
  panState = {
    sx: e.clientX, sy: e.clientY,
    cx0: cam.x, cy0: cam.y,
    moved: false,
  };
  canvas.classList.add('grabbing');
});
window.addEventListener('mousemove', (e) => {
  if (!panState) return;
  const dx = e.clientX - panState.sx;
  const dy = e.clientY - panState.sy;
  if (!panState.moved && Math.hypot(dx, dy) < 6) return;
  panState.moved = true;
  // World-space delta = screen-space delta divided by zoom × dpr, negated
  // (drag right → camera moves left → content slides right with the cursor).
  const dpr = window.devicePixelRatio || 1;
  cam.x = panState.cx0 - dx / (cam.z * dpr) * dpr;
  cam.y = panState.cy0 - dy / (cam.z * dpr) * dpr;
});
window.addEventListener('mouseup', () => {
  canvas.classList.remove('grabbing');
});
canvas.addEventListener('click', (e) => {
  // Suppress the click if the user was panning. mousedown sets panState,
  // mouseup leaves it set, so the next click checks moved-flag and clears.
  const wasPan = panState && panState.moved;
  panState = null;
  if (wasPan) return;
  const c = pickAt(e.clientX, e.clientY);
  if (c) openCard(c);
});

// Wheel zoom (anchored to cursor). Range covers bird's-eye (0.005, full 60K
// categories layout fits screen) up to street/character level (20× = each world
// unit becomes 20 screen pixels → a 12-unit building reads ~240px wide).
canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor = e.deltaY > 0 ? 0.9 : 1.1;
  const [wx, wy] = screenToWorld(e.clientX, e.clientY);
  cam.z = Math.max(0.005, Math.min(20.0, cam.z * factor));
  const [wx2, wy2] = screenToWorld(e.clientX, e.clientY);
  cam.x += wx - wx2;
  cam.y += wy - wy2;
}, { passive: false });

// Fit-all-to-view helper (centered on origin, adjusts zoom so layout fits in viewport).
// Lower clamp dropped from 0.1 → 0.005 so the categories layout's 60K extent
// can actually fit the screen — at zoom 0.1 only ~1/6 of the world was visible.
function fitToView() {
  const minDim = Math.min(window.innerWidth, window.innerHeight);
  const target = (minDim / (layoutExtent * 2.2)) || 0.4;
  cam.z = Math.max(0.005, Math.min(1.5, target));
  cam.x = 0; cam.y = 0;
}

// Touch — pinch zoom + tap to enter (no swipe-pan, character moves via mobile-pad)
let pinch = null;
canvas.addEventListener('touchstart', (e) => {
  if (e.touches.length === 2) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    pinch = { dist0: Math.hypot(dx, dy), z0: cam.z };
  } else if (e.touches.length === 1) {
    pinch = { tap: { x: e.touches[0].clientX, y: e.touches[0].clientY, t0: Date.now() } };
  }
}, { passive: true });

canvas.addEventListener('touchmove', (e) => {
  if (pinch && pinch.dist0 && e.touches.length === 2) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    cam.z = Math.max(0.005, Math.min(20.0, pinch.z0 * (Math.hypot(dx, dy) / pinch.dist0)));
    e.preventDefault();
  } else if (pinch && pinch.tap && e.touches.length === 1) {
    const ddx = e.touches[0].clientX - pinch.tap.x;
    const ddy = e.touches[0].clientY - pinch.tap.y;
    if (Math.hypot(ddx, ddy) > 12) pinch.tap = null;  // dragging — not a tap
  }
}, { passive: false });

canvas.addEventListener('touchend', (e) => {
  if (pinch && pinch.tap && e.changedTouches.length) {
    const t = e.changedTouches[0];
    if (Date.now() - pinch.tap.t0 < 600) {
      const c = pickAt(t.clientX, t.clientY);
      if (c) openCard(c);
    }
  }
  pinch = null;
});

// Keyboard — character movement + E to enter nearest building
const KEY_MAP = {
  'KeyW': 'up', 'ArrowUp': 'up',
  'KeyS': 'down', 'ArrowDown': 'down',
  'KeyA': 'left', 'ArrowLeft': 'left',
  'KeyD': 'right', 'ArrowRight': 'right',
  'KeyE': 'enter', 'Enter': 'enter', 'Space': 'enter',
  'KeyF': 'fit',  // F = fit-all view
};
window.addEventListener('keydown', (e) => {
  const action = KEY_MAP[e.code];
  if (!action) return;
  e.preventDefault();
  if (action === 'enter') {
    if (nearestBuilding && nearestBuilding.dist <= ENTER_RADIUS && nearestBuilding.card) {
      openCard(nearestBuilding.card);
    }
  } else if (action === 'fit') {
    fitToView();
  } else {
    keys[action] = true;
  }
});
window.addEventListener('keyup', (e) => {
  const action = KEY_MAP[e.code];
  if (action && action !== 'enter') keys[action] = false;
});

// Mobile pad — press to set the same key state
function bindPadButton(btn) {
  const action = btn.dataset.key === 'w' ? 'up' :
                 btn.dataset.key === 's' ? 'down' :
                 btn.dataset.key === 'a' ? 'left' :
                 btn.dataset.key === 'd' ? 'right' : 'enter';
  const press = (e) => {
    e.preventDefault();
    if (action === 'enter') {
      if (nearestBuilding && nearestBuilding.dist <= ENTER_RADIUS && nearestBuilding.card) {
        openCard(nearestBuilding.card);
      }
    } else { keys[action] = true; }
  };
  const release = () => { if (action !== 'enter') keys[action] = false; };
  btn.addEventListener('pointerdown', press);
  btn.addEventListener('pointerup', release);
  btn.addEventListener('pointerleave', release);
  btn.addEventListener('pointercancel', release);
}
mpad.querySelectorAll('button').forEach(bindPadButton);

modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('show'); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.classList.remove('show'); });

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":"&#39;" })[c]);
}

async function openCard(c) {
  const scoreClass = c.score < 0 ? 'score neg' : 'score';
  // Title lazy-loaded; use cached if available, placeholder otherwise
  const titleNow = titleCache.get(c.id) || '(loading…)';
  modalPanel.innerHTML = `
    <div class="row">
      <img class="facade" src="/api/card/${c.id}/facade.svg" alt="">
      <div class="info">
        <div class="title" id="modal-title">${escapeHtml(titleNow)}</div>
        <div class="host">${escapeHtml(c.host)}</div>
        <div class="pills">
          <span class="pill ${scoreClass}">${c.score >= 0 ? '+' : ''}${c.score}</span>
          <span class="pill">${escapeHtml(c.era)}</span>
        </div>
      </div>
    </div>
    <div class="thumb" style="background-image:url('/api/card/${c.id}/thumb')"></div>
    <div class="actions">
      <a class="btn primary" href="" target="_blank" rel="noopener noreferrer" id="modal-open">open site ↗</a>
      <button class="btn" id="modal-close">close</button>
    </div>
  `;
  // Fetch the full card for title + URL (title not in layout payload anymore).
  try {
    const r = await fetch(`/api/card/${c.id}`);
    const full = await r.json();
    document.getElementById('modal-open').href = full.url;
    if (full.title) {
      titleCache.set(c.id, full.title);
      const titleEl = document.getElementById('modal-title');
      if (titleEl) titleEl.textContent = full.title;
    }
    // If the destination is plain HTTP, add a visible "http" warning pill
    // so users know before they click into a non-TLS site.
    if (full.url && full.url.startsWith('http://')) {
      const pills = modalPanel.querySelector('.pills');
      if (pills) {
        const warn = document.createElement('span');
        warn.className = 'pill';
        warn.style.cssText = 'color:#f0c8a8;border-color:#604030;background:rgba(60,40,30,0.6)';
        warn.textContent = 'http (not secure)';
        pills.appendChild(warn);
      }
    }
  } catch (e) {}
  document.getElementById('modal-close').addEventListener('click', () => modal.classList.remove('show'));
  modal.classList.add('show');
  // Poll for the real thumb: if the first request returned the SVG fallback,
  // the background renderer will be churning to render it now (we bumped its priority).
  // Refresh the .thumb background after a few seconds to swap in the real WebP.
  const thumbDiv = modalPanel.querySelector('.thumb');
  if (thumbDiv) {
    let attempts = 0;
    const tick = () => {
      attempts++;
      if (!modal.classList.contains('show') || attempts > 6) return;
      const cacheBust = Date.now();
      thumbDiv.style.backgroundImage = `url('/api/card/${c.id}/thumb?_=${cacheBust}')`;
      setTimeout(tick, 4000);
    };
    setTimeout(tick, 4000);
  }
}

function step(dt) {
  // Input → target velocity
  let tx = 0, ty = 0;
  if (keys.up)    ty -= 1;
  if (keys.down)  ty += 1;
  if (keys.left)  tx -= 1;
  if (keys.right) tx += 1;
  if (tx || ty) {
    const len = Math.hypot(tx, ty);
    tx = (tx / len) * PLAYER_MAX_V;
    ty = (ty / len) * PLAYER_MAX_V;
    player.facing = tx > 0 ? 1 : (tx < 0 ? -1 : player.facing);
    player.vx += (tx - player.vx) * Math.min(1, PLAYER_ACCEL * dt / PLAYER_MAX_V);
    player.vy += (ty - player.vy) * Math.min(1, PLAYER_ACCEL * dt / PLAYER_MAX_V);
  } else {
    const decay = Math.exp(-PLAYER_FRICTION * dt);
    player.vx *= decay;
    player.vy *= decay;
    if (Math.abs(player.vx) < 0.5) player.vx = 0;
    if (Math.abs(player.vy) < 0.5) player.vy = 0;
  }
  player.x += player.vx * dt;
  player.y += player.vy * dt;

  // Camera follow (lerp)
  cam.x += (player.x - cam.x) * Math.min(1, dt * 6);
  cam.y += (player.y - cam.y) * Math.min(1, dt * 6);

  // Proximity check
  const near = findNearestBuilding();
  nearestBuilding = near.dist <= ENTER_RADIUS * 1.6 ? near : null;

  if (nearestBuilding && nearestBuilding.dist <= ENTER_RADIUS) {
    enterPrompt.innerHTML = `<b>${escapeHtml(nearestBuilding.card.host)}</b><br>` +
      `<span style="opacity:.85">${escapeHtml(nearestBuilding.card.title || '(untitled)').slice(0, 70)}</span><br>` +
      `<span style="opacity:.6;font-size:.7rem">press <b>E</b> to enter</span>`;
    enterPrompt.classList.add('show');
  } else {
    enterPrompt.classList.remove('show');
  }
}

function loop(ts) {
  if (!lastTs) lastTs = ts;
  const dt = Math.min(0.05, (ts - lastTs) / 1000);
  lastTs = ts;
  step(dt);
  drawScene();
  requestAnimationFrame(loop);
}

function rebuildLegend(mode) {
  legendTitleEl.textContent = mode === 'categories' ? 'Topic categories' : 'Site types · 8 lang sectors';
  const entries = mode === 'categories'
    ? Object.entries(categoryColors)
    : Object.entries(siteTypeColors);
  legendRowsEl.innerHTML = entries.map(([k, v]) =>
    `<div class="row"><span class="sw" style="background:${v}"></span> ${k}</div>`
  ).join('');
  modeToggleBtn.textContent = mode === 'categories'
    ? 'switch to languages × types'
    : 'switch to topic categories';
}

function buildSpatialIndex() {
  // 48×48 grid covering [-extent..extent] each axis. Each cell holds card refs.
  const span = layoutExtent * 2;
  gridCellSize = span / GRID_RES;
  grid = new Map();
  for (let k = 0; k < cards.length; k++) {
    const c = cards[k];
    const i = Math.max(0, Math.min(GRID_RES - 1, Math.floor((c.x + layoutExtent) / gridCellSize)));
    const j = Math.max(0, Math.min(GRID_RES - 1, Math.floor((c.y + layoutExtent) / gridCellSize)));
    const key = i + ',' + j;
    let cell = grid.get(key);
    if (!cell) { cell = []; grid.set(key, cell); }
    cell.push(c);
  }
  // Pre-sort top-by-score for LOD when zoomed-out (dot mode renders up to 30K)
  topByScore = cards.slice().sort((a, b) => b.score - a.score).slice(0, 30000);
}

// Async-build the spatial index in chunks so the UI thread doesn't freeze
// when parsing 100K+ cards. Returns a promise that resolves when grid is ready.
function buildSpatialIndexAsync() {
  return new Promise((resolve) => {
    const span = layoutExtent * 2;
    gridCellSize = span / GRID_RES;
    grid = new Map();
    const CHUNK = 5000;
    let k = 0;
    function step() {
      const end = Math.min(k + CHUNK, cards.length);
      for (; k < end; k++) {
        const c = cards[k];
        const i = Math.max(0, Math.min(GRID_RES - 1, Math.floor((c.x + layoutExtent) / gridCellSize)));
        const j = Math.max(0, Math.min(GRID_RES - 1, Math.floor((c.y + layoutExtent) / gridCellSize)));
        const key = i + ',' + j;
        let cell = grid.get(key);
        if (!cell) { cell = []; grid.set(key, cell); }
        cell.push(c);
      }
      stats.textContent = `building index ${Math.floor(k * 100 / cards.length)}%…`;
      if (k < cards.length) {
        setTimeout(step, 0);
      } else {
        // Sort top-by-score on a separate tick so UI stays responsive
        setTimeout(() => {
          topByScore = cards.slice().sort((a, b) => b.score - a.score).slice(0, 30000);
          resolve();
        }, 0);
      }
    }
    step();
  });
}

async function loadLayout(mode) {
  stats.textContent = 'loading layout…';
  cards.length = 0;
  labels = [];
  rings = [];
  trails = [];
  try {
    stats.textContent = 'fetching layout…';
    // /game's "categories" mode reads the flat-rings layout (12 cat sectors x 6
    // site_type rings, fixed 1500-radius bounds) instead of /world's variable-
    // arm pinwheel. Same mode name in the UI/legend/colors — only the data file
    // differs. This is what makes /game's category view look evenly distributed.
    const apiMode = mode === 'categories' ? 'categories_flat' : mode;
    // Limit bumped 80K → 250K. The layouts cap at 200K cards and tile mode
    // renders that fast (single fillRect each); 3D-look mode auto-degrades via
    // the LOD selector for slower hardware. Layout JSON is ~30MB — slow first
    // load but cached for 2 minutes server-side.
    const r = await fetch(`/api/game/layout?mode=${apiMode}&limit=250000`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    stats.textContent = 'parsing buildings…';
    const data = await r.json();
    // Loop-push avoids spread call-stack-overflow at 100K+ items in some browsers
    const incoming = data.cards || [];
    for (let i = 0; i < incoming.length; i++) cards.push(incoming[i]);
    labels = data.labels || [];
    rings = data.rings || [];
    trails = data.trails || [];
    layoutExtent = data.extent || 1600;

    // Build the spatial grid asynchronously so the UI doesn't freeze
    await buildSpatialIndexAsync();
    if (mode === 'categories') {
      categoryColors = data.category_colors || {};
    } else {
      siteTypeColors = data.site_type_colors || {};
    }
    currentMode = mode;
    // Build spatial grid index for fast viewport culling
    buildSpatialIndex();
    // Spawn near the central plaza
    player.x = 0; player.y = 0; player.vx = 0; player.vy = 0;
    bounds = { minX: -layoutExtent, maxX: layoutExtent, minY: -layoutExtent, maxY: layoutExtent };
    fitToView();  // auto-fit camera to whole map on load
    updateStatsLine(mode);  // "200K loaded · 4M screened · 200K visible · categories"
    rebuildLegend(mode);
    // Set A/B panels need per-card quartile and top TLDs — compute now that
    // cards are loaded. Then refresh chip rows so TLD list reflects this layout
    // and run the filter pipeline for the first time (visibleCards = cards).
    recomputeDerivedFields();
    rebuildPanels();
    applyFilters();
  } catch (e) {
    stats.textContent = 'failed: ' + (e && e.message ? e.message.slice(0, 80) : 'unknown error');
    console.error('layout load failed', e);
  }
}

modeToggleBtn.addEventListener('click', () => {
  const next = currentMode === 'categories' ? 'types' : 'categories';
  loadLayout(next);
});

document.getElementById('fit-btn').addEventListener('click', fitToView);

// ── Stats line: "200K loaded · 4M screened · 200K visible · categories" ──
// Updates on layout load and on every filter change. Mirrors /world's bar but
// adds a 'visible' count post-filter so user sees how many cards survived.
let _totalScreened = null, _totalPlacedByMode = null;
fetch('/api/stats').then(r => r.json()).then(s => {
  if (s.useful_cards) _totalScreened = s.useful_cards;
  if (s.placed) _totalPlacedByMode = s.placed;
  if (cards.length) updateStatsLine(currentMode);
}).catch(() => {});

function fmtNum(n) {
  return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n);
}

function updateStatsLine(mode) {
  const loaded = cards.length;
  const visible = visibleCards.length || loaded;
  const screenedStr = _totalScreened ? ` · ${fmtNum(_totalScreened)} screened` : '';
  const placedStr = (() => {
    if (!_totalPlacedByMode) return '';
    // /game's "categories" mode actually fetches categories_flat from API.
    const apiMode = mode === 'categories' ? 'categories_flat' : mode;
    const p = _totalPlacedByMode[apiMode];
    return p ? ` · ${fmtNum(p)} placed` : '';
  })();
  const visStr = visible !== loaded ? ` · ${fmtNum(visible)} visible` : '';
  stats.textContent = `${fmtNum(loaded)} loaded${placedStr}${screenedStr}${visStr} · ${mode}`;
}

// ── Set A (display) + Set B (filters) panels ────────────────────────
// State drives the filter pipeline. Multi-select sets default to 'all' (empty
// set means "no filter on this dim"); when chips are selected, only matching
// cards pass. Stack with AND across all dimensions.
const filterState = {
  // Set B — data slicers
  cats: new Set(),         // selected category names; empty = all
  langs: new Set(),
  eras: new Set(),
  siteTypes: new Set(),
  quartiles: new Set(),    // q0/q1/q2/q3 (per-card quartile, computed on layout load)
  tlds: new Set(),
  // Set A — display tweaks
  scoreMin: -10,           // score threshold (cards below are hidden)
  sortMode: 'score_desc',  // score_desc / score_asc / random / alpha
  colorBy: 'category',     // category / era / site_type / language
  tooltipDetail: 'standard', // minimal / standard / full
  // Set C — render paradigm. 'mandala' = current pinwheel/flat sectors.
  // 'heatmap' = 2D grid (cat × era). 'treemap' = nested rects.
  graphType: 'mandala',
};

const ERA_LIST = ['old_web','midweb','template','seo_hardened','modern_spa'];
const ERA_COLORS = {
  old_web: '#d8a050', midweb: '#a8a298', template: '#9aa9bc',
  seo_hardened: '#5a6c80', modern_spa: '#7aa9d8',
};
const SITE_TYPE_LIST = ['hobbyist','blog','business','educational','government','unknown'];
const SITE_TYPE_COLORS = {
  hobbyist: '#d8a050', blog: '#a8a298', educational: '#a0c068',
  business: '#9aa9bc', government: '#5a6c80', unknown: '#404448',
};
const LANG_LIST = ['en','ja','zh','es','de','fr','ru','pt','und'];

let perCardQuartile = new Map();   // id → 0..3 score quartile (from current layout)
let topTlds = [];                  // top-20 host suffixes from current layout

function tldOf(host) {
  if (!host) return '';
  const m = host.toLowerCase().match(/([^.]+\.[a-z]{2,})$/);
  return m ? m[1] : host;
}

function recomputeDerivedFields() {
  // Per-card score quartile across the WHOLE layout (not per-cat — that's the
  // pinwheel layout's own thing). Uses 25/50/75 percentiles of the card list.
  perCardQuartile.clear();
  if (!cards.length) return;
  const scores = cards.map(c => c.score).sort((a, b) => a - b);
  const q1 = scores[Math.floor(scores.length * 0.25)];
  const q2 = scores[Math.floor(scores.length * 0.50)];
  const q3 = scores[Math.floor(scores.length * 0.75)];
  for (const c of cards) {
    const q = c.score < q1 ? 0 : c.score < q2 ? 1 : c.score < q3 ? 2 : 3;
    perCardQuartile.set(c.id, q);
  }
  // Top-20 TLDs by host count.
  const counts = new Map();
  for (const c of cards) {
    const t = tldOf(c.host);
    if (!t) continue;
    counts.set(t, (counts.get(t) || 0) + 1);
  }
  topTlds = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20).map(e => e[0]);
}

function passesFilters(c) {
  if (c.score < filterState.scoreMin) return false;
  if (filterState.cats.size && !filterState.cats.has(c.category)) return false;
  if (filterState.langs.size && !filterState.langs.has(c.language)) return false;
  if (filterState.eras.size && !filterState.eras.has(c.era)) return false;
  if (filterState.siteTypes.size && !filterState.siteTypes.has(c.site_type)) return false;
  if (filterState.quartiles.size) {
    const q = perCardQuartile.get(c.id);
    if (!filterState.quartiles.has('q' + q)) return false;
  }
  if (filterState.tlds.size) {
    if (!filterState.tlds.has(tldOf(c.host))) return false;
  }
  return true;
}

function colorOfCard(c) {
  if (filterState.colorBy === 'era')       return ERA_COLORS[c.era] || '#cfd8c8';
  if (filterState.colorBy === 'site_type') return SITE_TYPE_COLORS[c.site_type] || '#404448';
  if (filterState.colorBy === 'language')  return '#7aa9d8'; // single accent for now
  return categoryColors[c.category] || '#7aa9d8';
}

function rebuildPanels() {
  // ── Set B (filters) ────────────────────────────────────────────────
  const bBody = document.getElementById('panel-b-body');
  bBody.innerHTML = '';
  const cats = Object.keys(categoryColors);
  const groupsB = [
    { label: 'Category', state: filterState.cats, items: cats, colorMap: categoryColors },
    { label: 'Language', state: filterState.langs, items: LANG_LIST },
    { label: 'Era', state: filterState.eras, items: ERA_LIST, colorMap: ERA_COLORS },
    { label: 'Site type', state: filterState.siteTypes, items: SITE_TYPE_LIST, colorMap: SITE_TYPE_COLORS },
    { label: 'Score quartile', state: filterState.quartiles, items: ['q0','q1','q2','q3'] },
    { label: 'TLD', state: filterState.tlds, items: topTlds.length ? topTlds : ['(layout loading…)'] },
  ];
  for (const g of groupsB) {
    const div = document.createElement('div');
    div.className = 'group';
    div.innerHTML = `
      <div class="group-label">
        <span>${g.label}</span>
        <span class="clear" data-clear="${g.label}">clear</span>
      </div>
      <div class="chip-row"></div>
    `;
    const row = div.querySelector('.chip-row');
    for (const it of g.items) {
      const chip = document.createElement('span');
      chip.className = 'chip' + (g.state.has(it) ? ' on' : '');
      chip.textContent = it;
      if (g.colorMap && g.colorMap[it]) {
        chip.dataset.color = g.colorMap[it];
        chip.style.borderLeftColor = g.colorMap[it];
      }
      chip.addEventListener('click', () => {
        if (g.state.has(it)) g.state.delete(it); else g.state.add(it);
        rebuildPanels();
        applyFilters();
      });
      row.appendChild(chip);
    }
    div.querySelector('.clear').addEventListener('click', () => { g.state.clear(); rebuildPanels(); applyFilters(); });
    bBody.appendChild(div);
  }

  // ── Set A (display) ────────────────────────────────────────────────
  const aBody = document.getElementById('panel-a-body');
  aBody.innerHTML = `
    <div class="group">
      <div class="group-label"><span>Graph type</span></div>
      <select id="graph-type-select">
        <option value="mandala"${filterState.graphType==='mandala'?' selected':''}>mandala (pinwheel)</option>
        <option value="heatmap"${filterState.graphType==='heatmap'?' selected':''}>heatmap (cat × era)</option>
        <option value="treemap"${filterState.graphType==='treemap'?' selected':''}>treemap (cat → site → era)</option>
        <option value="sankey"${filterState.graphType==='sankey'?' selected':''}>sankey (era → cat)</option>
        <option value="histograms"${filterState.graphType==='histograms'?' selected':''}>histograms (score per cat)</option>
        <option value="sunburst"${filterState.graphType==='sunburst'?' selected':''}>sunburst (radial nested)</option>
        <option value="bubble"${filterState.graphType==='bubble'?' selected':''}>bubble (score × word_count)</option>
        <option value="stream"${filterState.graphType==='stream'?' selected':''}>stream (corpus over id)</option>
        <option value="chord"${filterState.graphType==='chord'?' selected':''}>chord (inter-category links · daily refresh)</option>
      </select>
    </div>
    <div class="group">
      <div class="slider-label"><span>Score ≥</span><span id="score-min-val">${filterState.scoreMin.toFixed(1)}</span></div>
      <input class="slider" type="range" id="score-min-slider" min="-10" max="10" step="0.5" value="${filterState.scoreMin}">
    </div>
    <div class="group">
      <div class="group-label"><span>Sort within cell</span></div>
      <select id="sort-mode-select">
        <option value="score_desc"${filterState.sortMode==='score_desc'?' selected':''}>score (high → low)</option>
        <option value="score_asc"${filterState.sortMode==='score_asc'?' selected':''}>score (low → high)</option>
        <option value="random"${filterState.sortMode==='random'?' selected':''}>random</option>
        <option value="alpha"${filterState.sortMode==='alpha'?' selected':''}>host A → Z</option>
      </select>
    </div>
    <div class="group">
      <div class="group-label"><span>Color by</span></div>
      <select id="color-by-select">
        <option value="category"${filterState.colorBy==='category'?' selected':''}>category</option>
        <option value="era"${filterState.colorBy==='era'?' selected':''}>era</option>
        <option value="site_type"${filterState.colorBy==='site_type'?' selected':''}>site type</option>
        <option value="language"${filterState.colorBy==='language'?' selected':''}>language</option>
      </select>
    </div>
    <div class="group">
      <div class="group-label"><span>Tooltip detail</span></div>
      <select id="tooltip-detail-select">
        <option value="minimal"${filterState.tooltipDetail==='minimal'?' selected':''}>minimal (host)</option>
        <option value="standard"${filterState.tooltipDetail==='standard'?' selected':''}>standard</option>
        <option value="full"${filterState.tooltipDetail==='full'?' selected':''}>full (all features)</option>
      </select>
    </div>
  `;
  aBody.querySelector('#graph-type-select').addEventListener('change', (e) => {
    filterState.graphType = e.target.value;
    // /game render loop runs every frame, so no explicit rebuild trigger needed.
  });
  aBody.querySelector('#score-min-slider').addEventListener('input', (e) => {
    filterState.scoreMin = parseFloat(e.target.value);
    document.getElementById('score-min-val').textContent = filterState.scoreMin.toFixed(1);
    applyFilters();
  });
  aBody.querySelector('#sort-mode-select').addEventListener('change', (e) => {
    filterState.sortMode = e.target.value; applyFilters();
  });
  aBody.querySelector('#color-by-select').addEventListener('change', (e) => {
    filterState.colorBy = e.target.value;
  });
  aBody.querySelector('#tooltip-detail-select').addEventListener('change', (e) => {
    filterState.tooltipDetail = e.target.value;
  });
}

// `visibleCards` is the post-filter projection of `cards`. Recomputed only when
// filter state changes, so per-frame rendering doesn't pay the filter cost. For
// grid-based rendering at high zoom, an inline passesFilters() check is added
// to the cell-iteration loop (small viewport → cheap).
let visibleCards = [];
let topByScoreVisible = [];

function applyFilters() {
  const sortFns = {
    score_desc: (a, b) => b.score - a.score,
    score_asc:  (a, b) => a.score - b.score,
    random:     () => Math.random() - 0.5,
    alpha:      (a, b) => (a.host || '').localeCompare(b.host || ''),
  };
  visibleCards = cards.filter(passesFilters);
  visibleCards.sort(sortFns[filterState.sortMode] || sortFns.score_desc);
  // topByScoreVisible always sorts by score-DESC regardless of user's sort
  // choice — used by the LOD path to pick "most important to show" at low zoom.
  topByScoreVisible = visibleCards.slice().sort((a, b) => b.score - a.score).slice(0, 30000);
  if (typeof updateStatsLine === 'function') updateStatsLine(currentMode);
}

document.getElementById('filter-toggle').addEventListener('click', () => {
  document.getElementById('panel-b').classList.toggle('show');
});
document.getElementById('display-toggle').addEventListener('click', () => {
  document.getElementById('panel-a').classList.toggle('show');
});
document.getElementById('info-toggle').addEventListener('click', showInfoModal);
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === '/') {
    e.preventDefault();
    document.getElementById('panel-b').classList.toggle('show');
  } else if (e.key === '\\') {
    e.preventDefault();
    document.getElementById('panel-a').classList.toggle('show');
  } else if (e.key === '?') {
    e.preventDefault();
    showInfoModal();
  }
});

// ── Info modal ──
// Reuses the existing #modal element. Explains what the page is, the controls,
// and the data behind it. Press ? or click the info button.
function showInfoModal() {
  const panel = document.getElementById('modal-panel');
  panel.innerHTML = `
    <div style="font-size: 1.05rem; font-weight: 500; margin-bottom: 0.4rem;">Wander Around — 2D map</div>
    <div style="opacity: 0.7; line-height: 1.6;">
      <p>A flat top-down view of <b>~4 million screened websites</b> from Common Crawl. ~200K of them are "placed" into the visible pinwheel below — every other site in the corpus is filterable, just not spatially-laid-out.</p>
      <p style="margin-top: 0.6rem"><b>Each building = one website.</b> Color = its category by default; size and inner-ring placement encode score (higher = nearer the outer rim of its sector). Hover for host + title. Click to open the full card with link.</p>

      <div style="margin-top: 0.8rem; font-weight: 500; opacity: 0.9;">Controls</div>
      <ul style="margin: 0.3rem 0 0 1rem; padding: 0;">
        <li><b>Drag</b> the canvas to pan. <b>Scroll</b> to zoom (anchored to cursor).</li>
        <li><b>Pinch</b> to zoom on touch.</li>
        <li><b>F</b> — fit the whole map to your screen.</li>
        <li><b>WASD / arrows</b> — walk a character around. <b>E</b> to enter the nearest building.</li>
      </ul>

      <div style="margin-top: 0.8rem; font-weight: 500; opacity: 0.9;">Filters & display</div>
      <ul style="margin: 0.3rem 0 0 1rem; padding: 0;">
        <li><b>/</b> — open <b>Filters</b> (Set B): category, language, era, site type, score quartile, TLD. Pick chips to slice the corpus; filters AND-stack across all dimensions.</li>
        <li><b>\\</b> — open <b>Display</b> (Set A): graph type (mandala / heatmap / treemap), score threshold slider, sort, color-by, tooltip detail.</li>
        <li>Stats bar shows <i>loaded · placed · screened · visible</i> live as you filter.</li>
      </ul>

      <div style="margin-top: 0.8rem; font-weight: 500; opacity: 0.9;">Modes</div>
      <ul style="margin: 0.3rem 0 0 1rem; padding: 0;">
        <li><b>Topic categories</b> (default) — 12 sectors by topic, 6 site-type rings inside each. Walking from the center outward = moving from hobbyist → blog → business sites.</li>
        <li><b>Languages × types</b> (toggle bottom-left) — same map, but sectored by language with concentric rings of site_type.</li>
      </ul>

      <div style="margin-top: 0.8rem; font-weight: 500; opacity: 0.9;">What this is for</div>
      <p style="margin-top: 0.3rem">A research/exploration tool over the long-tail web — the dormant 99% search engines buried under SEO sludge. The 3D walkable version is at <a href="/world" style="color: #cff0c8;">/world</a>; only the cards with real screenshots get a tighter compact rendering at <a href="/rendered" style="color: #cff0c8;">/rendered</a>.</p>
    </div>
    <div class="actions" style="margin-top: 0.8rem;">
      <button class="btn primary" id="info-close">close</button>
    </div>
  `;
  document.getElementById('modal').classList.add('show');
  document.getElementById('info-close').addEventListener('click', () => {
    document.getElementById('modal').classList.remove('show');
  });
}

const renderToggleBtn = document.getElementById('render-toggle');
renderToggleBtn.addEventListener('click', () => {
  tileMode = !tileMode;
  renderToggleBtn.textContent = tileMode ? '3D buildings' : 'flat tiles';
});

async function init() {
  resize();
  window.addEventListener('resize', resize);
  await loadLayout('categories');
  requestAnimationFrame(loop);
}

init();
"""


@app.get("/game", response_class=HTMLResponse)
async def game_page():
    return HTMLResponse(content=GAME_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/game/main.js")
async def game_js():
    return Response(content=GAME_JS, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})


# In-memory cache of layout cards keyed by (mode), refreshed when the file's mtime changes.
# Used by the proximity-loading /api/game/region endpoint to avoid re-parsing the
# 30-50MB layout JSON on every chunk request.
_layout_cache: dict[str, tuple[float, list]] = {}


def _get_layout_cards(mode: str) -> list:
    path = _LAYOUT_PATHS.get(mode)
    if not path or not path.exists():
        return []
    mtime = path.stat().st_mtime
    cached = _layout_cache.get(mode)
    if cached and cached[0] == mtime:
        return cached[1]
    cards = _json.loads(path.read_text()).get("cards", [])
    _layout_cache[mode] = (mtime, cards)
    return cards


# In-memory cache of card IDs with real (non-fallback) WebP thumbs.
# Refreshed every 30s so newly-rendered thumbs become visible promptly.
_thumbed_ids_cache = {"ids": set(), "updated": 0.0}
_THUMBED_TTL = 30.0

def _refresh_thumbed_ids() -> None:
    import time as _t
    if _t.time() - _thumbed_ids_cache["updated"] < _THUMBED_TTL:
        return
    try:
        with wander_db.connect(DB_PATH) as conn:
            ids = {row[0] for row in conn.execute(
                "SELECT id FROM cards WHERE thumb_path IS NOT NULL AND thumb_path != ''"
            )}
        _thumbed_ids_cache["ids"] = ids
        _thumbed_ids_cache["updated"] = _t.time()
    except Exception:
        pass  # if DB temporarily unavailable, keep prior cache


@app.get("/api/game/region")
async def api_game_region(
    cx: float = Query(0.0),
    cy: float = Query(0.0),
    r: float = Query(400.0, ge=1.0, le=4000.0),
    mode: str = Query("categories"),
    limit: int = Query(8000, ge=1, le=20000),
):
    """Return cards within radius r of (cx, cy) — used by /world for proximity-based
    chunk loading (only buildings near the player are sent to the client)."""
    cards = _get_layout_cards(mode)
    if not cards:
        raise HTTPException(503, "layout not generated yet")
    r2 = r * r
    # Refresh the appropriate has-thumb cache based on which corpus we're serving.
    # /wiki uses wiki article IDs from articles table; everything else uses cards.id.
    if mode == "wiki":
        _refresh_wiki_thumbed_ids()
        thumbed = _wiki_thumbed_ids_cache["ids"]
    else:
        _refresh_thumbed_ids()
        thumbed = _thumbed_ids_cache["ids"]
    out = []
    for c in cards:
        dx = c.get("x", 0) - cx
        dy = c.get("y", 0) - cy
        if dx * dx + dy * dy <= r2:
            cc = dict(c)
            cc["has_thumb"] = c["id"] in thumbed
            out.append(cc)
            if len(out) >= limit:
                break
    return JSONResponse(
        {"cx": cx, "cy": cy, "r": r, "count": len(out), "cards": out},
        headers={"Cache-Control": "public, max-age=30"},
    )


@app.get("/world", response_class=HTMLResponse)
async def world_page():
    return HTMLResponse(content=WORLD_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/world/main.js")
async def world_js():
    return Response(content=WORLD_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/card/{card_id}/facade.svg")
async def api_card_facade(card_id: int):
    """Procedural facade — rendered on demand from features in the DB."""
    with wander_db.connect(DB_PATH) as conn:
        row = wander_db.get_card(conn, card_id)
    if not row:
        raise HTTPException(404, "card not found")
    feats = {
        "title": row["title"],
        "word_count": row["word_count"],
        "n_external_scripts": row["n_external_scripts"],
        "h1_count": row["h1_count"],
        "image_count": row["image_count"],
        "has_old_html_tags": bool(row["has_old_html_tags"]),
        "has_email_contact": bool(row["has_email_contact"]),
        "has_template_cms": bool(row["has_template_cms"]),
        "has_amp": bool(row["has_amp"]),
    }
    svg = building_svg(
        features=feats,
        score=float(row["composite_score"]),
        era=row["era"],
        url_seed=row["url"],
    )
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


WORLD_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
  <meta name="theme-color" content="#0c0c0e">
  <title>Wander Around — 3D world</title>
  <style>
    :root { color-scheme: dark; --fg:#ece8df; --bg:#0c0c0e; --line:#2a2a32; --dim:#9a9a8a; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg);
                 font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow: hidden; }
    #world { position: fixed; inset: 0; }
    .topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 5; padding: 0.7rem 1rem;
              display: flex; justify-content: space-between; align-items: baseline; gap: 1rem;
              background: linear-gradient(to bottom, rgba(12,12,14,0.85), transparent);
              pointer-events: none; }
    .brand { font-size: 0.85rem; font-weight: 500; opacity: 0.85; }
    .stats { font-size: 0.7rem; opacity: 0.55; }
    .crosshair { position: fixed; left: 50%; top: 50%; width: 8px; height: 8px;
                 transform: translate(-50%, -50%); pointer-events: none; z-index: 4;
                 border: 1px solid rgba(255,255,255,0.5); border-radius: 50%; mix-blend-mode: difference; }
    .hint { position: fixed; left: 50%; top: 60%; transform: translateX(-50%); z-index: 5;
            background: rgba(12,12,14,0.92); border: 1px solid var(--line); border-radius: 8px;
            padding: 1rem 1.4rem; font-size: 0.85rem; max-width: 28rem; text-align: center;
            display: none; line-height: 1.5; }
    .hint.show { display: block; }
    .hint b { color: #cff0c8; }
    .coords { position: fixed; top: 2.5rem; left: 1rem; z-index: 5; padding: 0.4rem 0.7rem;
              background: rgba(12,12,14,0.78); border: 1px solid var(--line); border-radius: 6px;
              font-size: 0.75rem; color: var(--fg); pointer-events: none;
              max-width: 22rem; line-height: 1.4; }
    .coords .row { display: flex; gap: 0.5rem; }
    .coords .row b { color: #cff0c8; font-weight: 500; }
    .building-label { position: fixed; bottom: 4.5rem; left: 50%; transform: translateX(-50%); z-index: 5;
                      padding: 0.5rem 0.9rem; background: rgba(12,12,14,0.92); border: 1px solid var(--line);
                      border-radius: 8px; font-size: 0.85rem; color: var(--fg); pointer-events: none;
                      max-width: 32rem; text-align: center; display: none; }
    .building-label.show { display: block; }
    .building-label b { color: #cff0c8; }
    .building-label .meta { font-size: 0.72rem; opacity: 0.65; margin-top: 0.2rem; }
    #minimap { position: fixed; top: 3.5rem; right: 1rem; z-index: 5; width: 200px; height: 200px;
               background: rgba(12,12,14,0.85); border: 1px solid var(--line); border-radius: 6px;
               cursor: pointer; }
    #expand-map-btn { position: fixed; top: calc(3.5rem + 200px + 0.4rem); right: 1rem; z-index: 5;
                      width: 200px; padding: 0.45rem 0.6rem; background: rgba(12,12,14,0.85);
                      border: 1px solid var(--line); border-radius: 6px; color: var(--fg);
                      font-family: inherit; font-size: 0.72rem; cursor: pointer; }
    #expand-map-btn:hover { background: rgba(20,20,28,0.95); }
    .fullmap { position: fixed; inset: 0; z-index: 20; background: rgba(8,8,12,0.96);
               display: none; align-items: center; justify-content: center; }
    .fullmap.show { display: flex; }
    .fullmap canvas { background: #0c0c10; border: 1px solid var(--line); border-radius: 4px; cursor: crosshair; }
    .fullmap .close { position: absolute; top: 1rem; right: 1rem; padding: 0.5rem 0.9rem;
                      background: rgba(20,20,28,0.9); border: 1px solid var(--line); border-radius: 6px;
                      color: var(--fg); font-family: inherit; font-size: 0.85rem; cursor: pointer; }
    .legend { position: fixed; left: 1rem; bottom: 1rem; z-index: 5; background: rgba(12,12,14,0.85);
              padding: 0.6rem 0.8rem; border: 1px solid var(--line); border-radius: 6px;
              font-size: 0.7rem; line-height: 1.55; max-width: 13rem; }
    .legend .row { display: flex; align-items: center; gap: 0.4rem; }
    .legend .sw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; border: 1px solid rgba(0,0,0,0.35); }
    .legend .head { opacity: 0.7; margin-bottom: 0.3rem; }
    .speed-ctrl { position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%); z-index: 5;
                  background: rgba(12,12,14,0.85); border: 1px solid var(--line); border-radius: 6px;
                  padding: 0.45rem 0.85rem; font-size: 0.7rem; display: flex; gap: 0.6rem; align-items: center; }
    .speed-ctrl input[type=range] { width: 130px; accent-color: #cff0c8; }
    .speed-ctrl b { color: #cff0c8; min-width: 2.5rem; text-align: right; }
    .toggles { position: fixed; right: 1rem; bottom: 1rem; z-index: 5; display: flex; gap: 0.4rem; flex-wrap: wrap; justify-content: flex-end; }
    .toggles a, .toggles button { background: rgba(12,12,14,0.85); border: 1px solid var(--line); border-radius: 6px;
                 padding: 0.5rem 0.8rem; color: var(--fg); text-decoration: none; font-size: 0.75rem;
                 font-family: inherit; cursor: pointer; }
    .landmark-prompt { position: fixed; left: 50%; top: 60%; transform: translate(-50%, 0);
                       z-index: 4; padding: 0.6rem 1rem; background: rgba(40,52,46,0.95); color: #cff0c8;
                       border: 1px solid #2c4030; border-radius: 6px; font-size: 0.85rem;
                       pointer-events: none; opacity: 0; transition: opacity 0.18s;
                       text-align: center; max-width: 22rem; }
    .landmark-prompt.show { opacity: 1; }
    .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 10;
             display: none; align-items: center; justify-content: center; padding: 1rem; }
    .modal.show { display: flex; }
    .modal .panel { background: #14141a; border: 1px solid var(--line); border-radius: 10px;
                    max-width: 640px; width: 100%; max-height: 80vh; overflow-y: auto;
                    display: flex; flex-direction: column; gap: 0.85rem; padding: 1.4rem; line-height: 1.55; }
    .modal .panel p { margin: 0.4rem 0; }
    .modal .row { display: flex; gap: 1rem; align-items: flex-start; }
    .modal .facade { width: 96px; height: 144px; flex-shrink: 0; background: #0a0a0c; border-radius: 4px; }
    .modal .info { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: 0.4rem; }
    .modal .title { font-size: 1.1rem; font-weight: 500; line-height: 1.3; }
    .modal .host { font-size: 0.8rem; color: var(--dim); word-break: break-all; }
    .modal .pills { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.4rem; }
    .pill { font-size: 0.7rem; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--dim); }
    .pill.score { color: #aef0a8; border-color: #2c4030; background: rgba(38,48,42,0.6); }
    .pill.score.neg { color: #f0a8a8; border-color: #402424; background: rgba(48,34,34,0.6); }
    .modal .thumb { width: 100%; aspect-ratio: 9/16; background-size: cover; background-position: top;
                    background-color: #0a0a0c; border-radius: 6px; }
    .modal .actions { display: flex; gap: 0.6rem; }
    .btn { flex: 1 1 0; padding: 0.75rem; border-radius: 8px; border: 1px solid var(--line);
           background: rgba(20,20,26,0.7); color: var(--fg); text-align: center; text-decoration: none;
           font-family: inherit; font-size: 0.85rem; cursor: pointer; }
    .btn.primary { background: rgba(40,52,46,0.8); border-color: #2c4030; color: #cff0c8; }
  </style>
</head>
<body>
  <canvas id="world"></canvas>
  <div class="crosshair"></div>
  <div class="topbar">
    <span class="brand">wander around — 3D</span>
    <span class="stats" id="stats">loading…</span>
  </div>
  <div class="coords" id="coords"></div>
  <div class="building-label" id="building-label"></div>
  <div class="landmark-prompt" id="landmark-prompt"></div>
  <canvas id="minimap" width="200" height="200"></canvas>
  <button id="expand-map-btn">expand map ↗</button>
  <div class="fullmap" id="fullmap">
    <canvas id="fullmap-canvas" width="900" height="900"></canvas>
    <button class="close" id="fullmap-close">close ✕</button>
  </div>
  <div class="legend" id="legend">
    <div class="head">Topic categories</div>
    <div id="legend-rows"></div>
  </div>
  <div class="speed-ctrl">
    walk speed: <input type="range" id="speed-slider" min="20" max="200" value="100" step="5">
    <b id="speed-val">100</b>
  </div>
  <div class="hint" id="hint">
    Click to enter the world.<br>
    <span style="opacity:0.7;font-size:0.75rem">
    <b>WASD</b> walk · <b>mouse</b> look · <b>shift</b> sprint · <b>space</b> jump · <b>double-tap space</b> fly · <b>E</b> enter · <b>ESC</b> release
    </span>
  </div>
  <div class="toggles">
    <a href="/rendered" id="rendered-link" title="Show only buildings with real screenshots — compact pinwheel">rendered</a>
    <a href="/wiki" title="Walk a parallel mandala built from ~6.8M Wikipedia articles">wiki</a>
    <a href="/4d" title="Same corpus as a 3D globe (sphere projection)">4D sphere</a>
    <a href="/game">2D map</a>
    <a href="/feed">feed</a>
  </div>
  <div class="modal" id="modal"><div class="panel" id="modal-panel"></div></div>
  <script type="module" src="/world/main.js"></script>
</body>
</html>
"""


WORLD_JS = r"""
// Self-hosted Three.js + PointerLockControls (avoids CDN load + CSP-blocked inline importmap)
import * as THREE from '/static/three.module.js';
import { PointerLockControls } from '/static/PointerLockControls.js';

const stats = document.getElementById('stats');
const hint = document.getElementById('hint');
const modal = document.getElementById('modal');
const modalPanel = document.getElementById('modal-panel');

// ── Scene ────────────────────────────────────────────────────────────
const SKY_COLOR = 0xa8d2f0;      // soft cartoon sky blue
const HORIZON_COLOR = 0xfce8c8;  // warm cream at horizon (fog tint)
const GROUND_COLOR = 0xd6c8a8;   // warm tan "city plan" ground
const PLAZA_COLOR = 0xfff2d8;    // creamy plaza paving
const SIDEWALK_COLOR = 0xece4cc; // pale stone

const scene = new THREE.Scene();
scene.background = new THREE.Color(SKY_COLOR);
scene.fog = new THREE.Fog(HORIZON_COLOR, 120, 700);

const camera = new THREE.PerspectiveCamera(72, window.innerWidth / window.innerHeight, 0.5, 4000);
camera.position.set(0, 4, 0);

const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('world'), antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Lighting — cartoon flat: bright ambient, soft warm key, no harsh shadows
scene.add(new THREE.HemisphereLight(SKY_COLOR, GROUND_COLOR, 0.85));
const sun = new THREE.DirectionalLight(0xfff5e0, 0.6);
sun.position.set(400, 500, 300);
scene.add(sun);

// Ground plane — warm tan
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(8000, 8000),
  new THREE.MeshStandardMaterial({ color: GROUND_COLOR, roughness: 1.0, metalness: 0.0 })
);
ground.rotation.x = -Math.PI / 2;
ground.position.y = 0;
scene.add(ground);

// Faint city grid on the ground (every 30 units) — reads as streets
const grid = new THREE.GridHelper(2000, 66, 0xb8a888, 0xc8b898);
grid.position.y = 0.02;
grid.material.opacity = 0.35;
grid.material.transparent = true;
scene.add(grid);

// Central plaza — R_CENTER_PLAZA(1500) * world_scale(0.8) = 1200
const plaza = new THREE.Mesh(
  new THREE.RingGeometry(0, 1200, 64),
  new THREE.MeshStandardMaterial({ color: PLAZA_COLOR, roughness: 0.95, metalness: 0.0, side: THREE.DoubleSide })
);
plaza.rotation.x = -Math.PI / 2;
plaza.position.y = 0.05;
scene.add(plaza);

// ── Spawn-area orientation: central pillar + 12 directional pylons ──
const pillarMat = new THREE.MeshStandardMaterial({ color: 0x4a6a90, roughness: 0.8 });
const pillar = new THREE.Mesh(new THREE.CylinderGeometry(9, 13, 100, 16), pillarMat);
pillar.position.set(0, 50, 0);
scene.add(pillar);
const pillarTop = new THREE.Mesh(new THREE.ConeGeometry(15, 22, 16), new THREE.MeshStandardMaterial({ color: 0xe06850, roughness: 0.7 }));
pillarTop.position.set(0, 111, 0);
scene.add(pillarTop);

// 12 directional pylons at radius 140 (inside expanded plaza inner clearance)
const CATEGORY_COLOR = {
  tech: 0x7aa9d8, art: 0xe0a060, music: 0xc870c0, food: 0xe07050,
  gaming: 0x9080d0, science: 0x60c0a0, education: 0xa0c068, news: 0xd8a8a8,
  sports: 0xe0c060, commerce: 0x9aa9bc, community: 0x80c8d0, personal: 0xd8a050,
  misc: 0x707080,
};
const PYLON_CATEGORIES = ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
const pylonGeom = new THREE.BoxGeometry(2.5, 8, 2.5);
const pylonGeomBig = new THREE.BoxGeometry(5, 22, 5);

// Per-pylon info — opens when player presses E near each pylon. Edit freely.
const PYLON_INFO = {
  tech:      { title: 'Studio Prometheus7',     body: 'Spore Animation Studio — frame-by-frame generative animation engine. Renders narrated visuals from text prompts in real time.', url: 'https://studio.prometheus7.com' },
  art:       { title: 'Paintings auction',       body: 'Live oil paintings auction site. Real paintings, bid from anywhere.',  url: 'https://paintings.prometheus7.com' },
  music:     { title: 'Spore animation landing', body: 'Public landing page for Spore Animation Studio with demo reel.',       url: 'https://sporeanimation.prometheus7.com' },
  food:      { title: 'Coming soon',             body: 'Reserved slot — link to be assigned.',                                 url: null },
  gaming:    { title: 'Wander Around game',      body: 'You are here. The walkable map of the forgotten web you are exploring right now.', url: null },
  science:   { title: 'Prometheus7 Institute',   body: 'The institute behind everything in this world. Solo research lab building substrate-level systems for ensouled software.', url: 'https://prometheus7.com' },
  education: { title: 'Prometheus7 Institute',   body: 'Educational research from the Institute on substrate-level architectures.', url: 'https://prometheus7.com' },
  news:      { title: 'The Daily Spore',          body: 'AI-curated daily newsroom for the Spore ecosystem.',                  url: null },
  sports:    { title: 'Coming soon',             body: 'Reserved slot — link to be assigned.',                                 url: null },
  commerce:  { title: '$HW — Hermes Webkit token',  body: '$HW is the token of Hermes Webkit, the first product of the Prometheus7 Institute. Hermes Webkit is a vessel architecture for AI-inhabited websites: 12 vessel types tuned for human domains (communication, knowledge, creation, fabrication, sustenance, coordination, health, governance, home, security, experience, transformation). You describe a site in plain English, the routing tree generates HTML at build time (not visit time), and you serve a static page that holds a real identity. The Ensouled marketplace runs on $HW — a Solana / Pump.fun integration where AI-inhabited sites trade as agents. The full $HW spec lives at prometheus7.com under the $HW Token tab.', url: 'https://prometheus7.com' },
  community: { title: 'Ensouled Agents',          body: 'Multi-agent systems with persistent memory. The community-of-agents project — agents that remember, persist, and act on behalf of their humans.', url: 'https://ensouledagents.com' },
  personal:  { title: 'About psiloceyeben',       body: 'Solo builder behind Prometheus7. Goes by psiloceyeben online. Builds substrate-level systems, generative animation engines, and walkable cartographies of the forgotten web.', url: null },
};
// Landmarks: central pillar (about Wander Around) + 12 pylons (Ben's other sites).
// Edit titles/bodies/urls below to update what each pillar links to.
const LANDMARKS = [
  { name: 'central', x: 0, z: 0, radius: 22,
    title: 'Wander Around — the anti-SEO visualization engine',
    body: `<p><b>What you are standing in:</b> a walkable map of ~200,000 hand-made websites the modern search-optimized web has buried. Every building around you is a real site — its position, height, and color encode <i>what</i> it is and <i>how hand-made</i> it scores.</p>
<p><b>The thesis:</b> the virtual realm is a dimension. We just never had a projection of it. SEO turned the web into a flat ranked list optimized for clickthrough — but the web is actually a vast city of hand-built homesites, weird hobbyist pages, irregular blogs, raw HTML. Most of it is invisible to the algorithms that decide what you see. This is a different projection: a cartographic instrument that lets you walk through what the algorithms threw away.</p>
<p><b>How buildings get placed:</b> each site is scored by an anti-SEO + handmade signal (irregular HTML, no tracking pixels, hand-coded layouts, low template-detection score). High score → outer ring of its sector, more prominent. Each of the 12 pie slices around you is a topic category (tech, art, science, personal, etc.) — slice <i>length</i> is proportional to how much of the web is in that category. Most of the web turns out to be personal + community sites. Sports sites are rare.</p>
<p><b>The data:</b> ~200K cards now from Common Crawl. Wikipedia parsing in progress (~4M articles). Plan extends to ~1M corpus this quarter, then scales to full-web tile rendering at ~200M sites.</p>
<p><b>Where this is going (Phase 7):</b> the residential layer. Sites become houses, URLs become addresses, residents own their improvements. The web becomes <i>inhabitable</i> again — a place you can homestead instead of just publish to. Funded via a Henry-George style commons: the unimproved location-value of every plot is taxed back into shared infrastructure (the cartographer); improvements remain owned by their resident. ~$5-25/year subscription model.</p>
<p><b>Built by:</b> psiloceyeben (Prometheus7 Institute). Solo. The 12 pylons around this pillar link to the rest of the ecosystem — walk over and press E.</p>`,
    url: null },
];

// Make a billboard text label as a THREE.Sprite (canvas-textured).
function makeLabelSprite(text, color = '#ece8df') {
  const c = document.createElement('canvas');
  c.width = 384; c.height = 80;
  const ctx = c.getContext('2d');
  ctx.fillStyle = 'rgba(20,20,28,0.92)';
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  ctx.strokeRect(2, 2, c.width - 4, c.height - 4);
  ctx.fillStyle = color;
  ctx.font = 'bold 36px ui-monospace, Menlo, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, c.width / 2, c.height / 2);
  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(24, 5, 1);
  return sprite;
}

// Make a billboard PREVIEW sprite — bigger, with title + body excerpt + URL.
// Used on pillar/pylons so each landmark broadcasts its content from a distance.
function _wrapText(ctx, text, maxWidth) {
  const words = text.split(' ');
  const lines = [];
  let line = '';
  for (const word of words) {
    const test = line + (line ? ' ' : '') + word;
    if (ctx.measureText(test).width > maxWidth && line) {
      lines.push(line);
      line = word;
    } else {
      line = test;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function makePreviewSprite(title, body, url, hexColor) {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 640;
  const ctx = c.getContext('2d');
  // Outer color frame from category color
  ctx.fillStyle = '#' + hexColor.toString(16).padStart(6, '0');
  ctx.fillRect(0, 0, c.width, c.height);
  // Inner dark panel
  ctx.fillStyle = 'rgba(18,18,24,0.92)';
  ctx.fillRect(14, 14, c.width - 28, c.height - 28);
  // Border
  ctx.strokeStyle = '#ece8df';
  ctx.lineWidth = 3;
  ctx.strokeRect(16, 16, c.width - 32, c.height - 32);
  // Title
  ctx.fillStyle = '#ece8df';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.font = 'bold 36px ui-monospace, Menlo, monospace';
  const titleLines = _wrapText(ctx, title, c.width - 80);
  let y = 50;
  for (const ln of titleLines) {
    ctx.fillText(ln, c.width / 2, y);
    y += 44;
  }
  y += 20;
  // Body excerpt
  ctx.font = '22px ui-monospace, Menlo, monospace';
  ctx.fillStyle = '#cfc8b8';
  const bodyText = body.replace(/<[^>]+>/g, ' ').slice(0, 280);
  const bodyLines = _wrapText(ctx, bodyText, c.width - 80);
  for (const ln of bodyLines.slice(0, 9)) {
    ctx.fillText(ln, c.width / 2, y);
    y += 30;
  }
  // URL or "press E"
  ctx.textBaseline = 'bottom';
  if (url) {
    ctx.font = 'bold 22px ui-monospace, Menlo, monospace';
    ctx.fillStyle = '#cff0c8';
    ctx.fillText(url.replace(/^https?:\/\//, ''), c.width / 2, c.height - 50);
  }
  ctx.font = '20px ui-monospace, Menlo, monospace';
  ctx.fillStyle = '#9a9a8a';
  ctx.fillText('press E to read', c.width / 2, c.height - 25);

  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(28, 35, 1);
  return sprite;
}

// Central pillar's label + giant preview sprite
{
  const lbl = makeLabelSprite('Wander Around', '#ffeec0');
  lbl.position.set(0, 100, 0);
  lbl.scale.set(60, 12, 1);
  scene.add(lbl);
  const preview = makePreviewSprite(
    'Wander Around',
    'Walkable map of the forgotten web. 200K+ hand-made sites. Each pillar around you links to a related project. Walk over and press E to read about it.',
    null,
    0xffeec0,
  );
  preview.position.set(0, 60, 0);
  preview.scale.set(40, 50, 1);
  scene.add(preview);
}
PYLON_CATEGORIES.forEach((cat, i) => {
  const angle = (i / 12) * Math.PI * 2 - Math.PI / 2;
  const x = Math.cos(angle) * 140;
  const z = Math.sin(angle) * 140;
  const pyl = new THREE.Mesh(pylonGeomBig, new THREE.MeshStandardMaterial({ color: CATEGORY_COLOR[cat], roughness: 0.85 }));
  pyl.position.set(x, 11, z);
  scene.add(pyl);
  const cap = new THREE.Mesh(new THREE.ConeGeometry(4, 7, 4), new THREE.MeshStandardMaterial({ color: CATEGORY_COLOR[cat], roughness: 0.7 }));
  cap.position.set(x, 25.5, z);
  cap.rotation.y = Math.PI / 4;
  scene.add(cap);
  LANDMARKS.push({ name: cat, x, z, radius: 18, ...PYLON_INFO[cat] });
  // Label sprite (small) above each pylon
  const lbl = makeLabelSprite(cat, '#' + CATEGORY_COLOR[cat].toString(16).padStart(6, '0'));
  lbl.position.set(x, 28, z);
  scene.add(lbl);
  // Larger preview sprite ABOVE the label — readable from across the plaza
  const info = PYLON_INFO[cat];
  const preview = makePreviewSprite(info.title, info.body, info.url, CATEGORY_COLOR[cat]);
  preview.position.set(x, 50, z);
  scene.add(preview);
});

// Pylon info — placeholder mappings to Ben's sites. Edit freely.
// (Defined ABOVE this point as `const PYLON_INFO`.)

// ── Fetch screened + placed counts for the stats bar ─────────────
// _totalScreened = total cards in the DB (~4M, growing as wander-warc runs).
// _totalPlaced   = how many of those landed in this view's pinwheel layout
//                  (~200K for /world's 12-cat pinwheel, ~13K for /rendered's compact mirror).
fetch('/api/stats').then(r => r.json()).then(s => {
  if (s.useful_cards) window._totalScreened = s.useful_cards;
  if (s.placed && s.placed.categories) window._totalPlaced = s.placed.categories;
}).catch(() => {});

// ── Expand-map button + fullscreen 2D dot view ──────────────────────
{
  const btn = document.getElementById('expand-map-btn');
  const fullmap = document.getElementById('fullmap');
  const closeBtn = document.getElementById('fullmap-close');
  const fcanvas = document.getElementById('fullmap-canvas');
  const fctx = fcanvas.getContext('2d');
  let allCards = null;
  const FULLMAP_CAT_RGB = {
    tech:[122,169,216], art:[224,160,96], music:[200,112,192], food:[224,112,80],
    gaming:[144,128,208], science:[96,192,160], education:[160,192,104], news:[216,168,168],
    sports:[224,192,96], commerce:[154,169,188], community:[128,200,208], personal:[216,160,80],
    misc:[112,112,128],
  };
  function drawFullmap() {
    if (!allCards) return;
    const W = fcanvas.width, H = fcanvas.height;
    const img = fctx.createImageData(W, H);
    const data = img.data;
    // Background
    for (let i = 0; i < data.length; i += 4) {
      data[i] = 8; data[i+1] = 9; data[i+2] = 14; data[i+3] = 255;
    }
    // Auto-fit extent from card bounds
    let maxR = 1000;
    for (const c of allCards) {
      const m = Math.max(Math.abs(c.x), Math.abs(c.y));
      if (m > maxR) maxR = m;
    }
    const cx = W / 2, cy = H / 2;
    const scale = Math.min(W, H) / (maxR * 2.1);
    for (const c of allCards) {
      const px = (c.x * scale + cx) | 0;
      const py = (c.y * scale + cy) | 0;
      if (px < 0 || px >= W || py < 0 || py >= H) continue;
      const rgb = FULLMAP_CAT_RGB[c.category] || [120,120,130];
      const idx = (py * W + px) * 4;
      data[idx] = rgb[0]; data[idx+1] = rgb[1]; data[idx+2] = rgb[2]; data[idx+3] = 255;
    }
    fctx.putImageData(img, 0, 0);
    // Player position overlay
    const lx = camera.position.x / 0.8;
    const ly = camera.position.z / 0.8;
    const ppx = lx * scale + cx;
    const ppy = ly * scale + cy;
    fctx.fillStyle = '#ffeec0';
    fctx.beginPath(); fctx.arc(ppx, ppy, 6, 0, Math.PI * 2); fctx.fill();
    fctx.strokeStyle = '#1a1410'; fctx.lineWidth = 2; fctx.stroke();
    // Annotation
    fctx.fillStyle = '#ece8df';
    fctx.font = '13px ui-monospace, Menlo, monospace';
    fctx.fillText(`${allCards.length.toLocaleString()} buildings · click anywhere to fly there`, 10, H - 14);
  }
  btn.addEventListener('click', async () => {
    fullmap.classList.add('show');
    if (!allCards) {
      btn.textContent = 'loading map…';
      try {
        const r = await fetch('/api/game/layout?mode=categories&limit=250000');
        const d = await r.json();
        allCards = d.cards || [];
      } catch (e) { console.error('map fetch failed', e); }
      btn.textContent = 'expand map ↗';
    }
    drawFullmap();
  });
  closeBtn.addEventListener('click', () => fullmap.classList.remove('show'));
  fcanvas.addEventListener('click', (e) => {
    if (!allCards) return;
    const rect = fcanvas.getBoundingClientRect();
    const cx = fcanvas.width / 2, cy = fcanvas.height / 2;
    let maxR = 1000;
    for (const c of allCards) {
      const m = Math.max(Math.abs(c.x), Math.abs(c.y));
      if (m > maxR) maxR = m;
    }
    const scale = Math.min(fcanvas.width, fcanvas.height) / (maxR * 2.1);
    const mx = (e.clientX - rect.left) * (fcanvas.width / rect.width);
    const my = (e.clientY - rect.top) * (fcanvas.height / rect.height);
    const lx = (mx - cx) / scale;
    const ly = (my - cy) / scale;
    camera.position.x = lx * 0.8;
    camera.position.z = ly * 0.8;
    player.velocity.set(0, 0, 0);
    fullmap.classList.remove('show');
    setTimeout(maintainChunks, 50);
  });
  // Repaint fullmap every 200ms while open (so player dot tracks)
  setInterval(() => { if (fullmap.classList.contains('show')) drawFullmap(); }, 200);
}

// ── Bottom-left legend: 12 category swatches ───────────────────────
{
  const rows = document.getElementById('legend-rows');
  rows.innerHTML = PYLON_CATEGORIES.map(cat =>
    `<div class="row"><span class="sw" style="background:#${CATEGORY_COLOR[cat].toString(16).padStart(6,'0')}"></span> ${cat}</div>`
  ).join('');
}

// (Speed slider wired below, after `player` const is declared.)

// ── Landmark interaction: nearest pillar/pylon, E to open modal ─────
const landmarkPromptEl = document.getElementById('landmark-prompt');
let nearestLandmark = null;
function updateNearestLandmark() {
  const px = camera.position.x, pz = camera.position.z;
  let best = null, bestD = Infinity;
  for (const lm of LANDMARKS) {
    const dx = lm.x - px, dz = lm.z - pz;
    const d = Math.sqrt(dx*dx + dz*dz);
    if (d < lm.radius && d < bestD) { best = lm; bestD = d; }
  }
  nearestLandmark = best;
  if (best) {
    landmarkPromptEl.innerHTML = `<b>${best.title}</b><br><span style="opacity:0.75;font-size:0.72rem">press <b>E</b> to read</span>`;
    landmarkPromptEl.classList.add('show');
  } else {
    landmarkPromptEl.classList.remove('show');
  }
}

function openLandmark(lm) {
  controls.unlock();
  // lm.body may contain HTML (e.g., the central pillar's multi-paragraph body).
  // We trust it because all landmark content is authored in this file, not user-supplied.
  modalPanel.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:0.6rem;">
      <div class="title" style="font-size:1.25rem;font-weight:500">${escapeHtml(lm.title)}</div>
      <div style="color:var(--fg);opacity:0.92;font-size:0.85rem">${lm.body}</div>
    </div>
    <div class="actions">
      ${lm.url ? `<a class="btn primary" href="${escapeHtml(lm.url)}" target="_blank" rel="noopener noreferrer">visit ${escapeHtml(lm.url.replace(/^https?:\/\//, ''))} ↗</a>` : ''}
      <button class="btn" id="modal-close">close</button>
    </div>
  `;
  document.getElementById('modal-close').addEventListener('click', () => modal.classList.remove('show'));
  modal.classList.add('show');
}

// ── Controls (first-person walk + jump) ──────────────────────────────
const controls = new PointerLockControls(camera, document.body);
hint.classList.add('show');
document.body.addEventListener('click', (e) => {
  if (e.target.closest('.toggles, .modal, .speed-ctrl, .legend')) return;
  if (!controls.isLocked) controls.lock();
});
controls.addEventListener('lock', () => hint.classList.remove('show'));
controls.addEventListener('unlock', () => hint.classList.add('show'));

const keys = {};
let lastSpaceTime = 0;
window.addEventListener('keydown', (e) => { keys[e.code] = true;
  // E priority: landmark > building (so pressing E near a pylon opens its info, not a building)
  if (e.code === 'KeyE') {
    if (nearestLandmark) openLandmark(nearestLandmark);
    else if (nearestCard) openCard(nearestCard);
  }
  if (e.code === 'Space' && !e.repeat) {
    const now = performance.now();
    if (now - lastSpaceTime < 300) {
      player.flying = !player.flying;
      player.velocity.y = 0;
    }
    lastSpaceTime = now;
  }
});
window.addEventListener('keyup',   (e) => { keys[e.code] = false; });

const player = {
  velocity: new THREE.Vector3(0, 0, 0),
  height: 4,
  speed: 100,
  sprintMult: 3.5,
  flySpeed: 200,
  gravity: 50,
  jumpV: 14,
  onGround: true,
  flying: false,
};

// Speed slider — wires here (after `player` is initialized) to avoid TDZ.
{
  const slider = document.getElementById('speed-slider');
  const val = document.getElementById('speed-val');
  slider.value = player.speed;
  val.textContent = player.speed;
  slider.addEventListener('input', () => {
    const v = parseInt(slider.value, 10);
    player.speed = v;
    player.flySpeed = v * 2;  // fly stays 2x walk speed
    val.textContent = v;
  });
  slider.addEventListener('mousedown', (e) => e.stopPropagation());
  slider.addEventListener('click', (e) => e.stopPropagation());
}
const tmpV = new THREE.Vector3();
const tmpForward = new THREE.Vector3();
const tmpRight = new THREE.Vector3();

// ── Buildings (InstancedMesh for 100K+ instances at 60fps) ───────────
// CATEGORY_COLOR declared earlier (in the spawn-pylon section).

let cards = [];
let buildingsMesh = null;
let cardByInstance = [];
let nearestCard = null;
const HOVER_DIST = 16; // world units

function buildBuildings() {
  if (buildingsMesh) {
    scene.remove(buildingsMesh);
    buildingsMesh.geometry.dispose();
    buildingsMesh.material.dispose();
  }
  const geom = new THREE.BoxGeometry(1, 1, 1);
  // Move the geometry's pivot to its bottom so y=0 sits on the ground
  geom.translate(0, 0.5, 0);
  const mat = new THREE.MeshStandardMaterial({
    vertexColors: false,
    roughness: 0.85,
    metalness: 0.0,
  });
  const N = cards.length;
  const mesh = new THREE.InstancedMesh(geom, mat, N);
  mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
  mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(N * 3), 3);

  const m = new THREE.Matrix4();
  const color = new THREE.Color();

  for (let i = 0; i < N; i++) {
    const c = cards[i];
    // Position: x,y from layout become x,z in world; y is the building's "ground" position (0)
    const wx = c.x * 0.8;
    const wz = c.y * 0.8;
    const h = Math.max(15, 15 + (c.score + 2) * 2.5);  // floor ~15 (4-story); range 15-45+ for skyline
    const w = 10 + (c.score + 6) * 0.30;              // width: ~8 to ~15 (real-city scale)
    m.makeScale(w, h, w);
    m.setPosition(wx, 0, wz);
    mesh.setMatrixAt(i, m);
    const hex = CATEGORY_COLOR[c.category] ?? CATEGORY_COLOR.misc;
    color.setHex(hex);
    mesh.setColorAt(i, color);
    cardByInstance.push(c);
  }
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  scene.add(mesh);
  buildingsMesh = mesh;
}

// ── Lazy thumb-textured detail meshes for ~8 nearest in-frustum buildings ──
// Each detail building becomes an individual Mesh layered over the InstancedMesh
// with a thumb texture loaded from /api/card/{id}/thumb. EdgesGeometry adds a
// border so the thumb has a visible frame. Disposed when the card leaves the
// (proximity ∩ frustum) set so GPU memory stays bounded.
const DETAIL_RADIUS = 60;
const MAX_DETAIL = 8;
const detailEntries = new Map();  // cardId → { mesh, edges, texture, c }
const _detailFrustum = new THREE.Frustum();
const _detailMatrix = new THREE.Matrix4();
const _detailCard = new THREE.Vector3();
let _lastDetailUpdate = 0;
const _texLoader = new THREE.TextureLoader();

function updateDetailMeshes(now) {
  if (now - _lastDetailUpdate < 500) return;  // throttle to 2 Hz
  _lastDetailUpdate = now;
  if (cards.length === 0) return;

  // Build frustum from current camera projection × inverse world matrix
  _detailMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  _detailFrustum.setFromProjectionMatrix(_detailMatrix);

  const px = camera.position.x, pz = camera.position.z;
  const candidates = [];
  for (let i = 0; i < cards.length; i++) {
    const c = cards[i];
    const wx = c.x * 0.8, wz = c.y * 0.8;
    const dx = wx - px, dz = wz - pz;
    const d2 = dx*dx + dz*dz;
    if (d2 > DETAIL_RADIUS * DETAIL_RADIUS) continue;
    _detailCard.set(wx, 6, wz);
    if (!_detailFrustum.containsPoint(_detailCard)) continue;
    candidates.push({ c, d2, wx, wz });
  }
  candidates.sort((a, b) => a.d2 - b.d2);
  const keep = candidates.slice(0, MAX_DETAIL);
  const keepIds = new Set(keep.map(x => x.c.id));

  // Drop entries no longer in the keep set (free GPU memory)
  for (const [id, e] of detailEntries) {
    if (!keepIds.has(id)) {
      scene.remove(e.mesh);
      scene.remove(e.edges);
      e.mesh.geometry.dispose();
      e.mesh.material.dispose();
      e.edges.geometry.dispose();
      e.edges.material.dispose();
      if (e.texture) e.texture.dispose();
      if (e.marker) {
        scene.remove(e.marker);
        e.marker.geometry.dispose();
        e.marker.material.dispose();
      }
      detailEntries.delete(id);
    }
  }

  // Add detail meshes for new keepers
  for (const { c, wx, wz } of keep) {
    if (detailEntries.has(c.id)) continue;
    const h = Math.max(15, 15 + (c.score + 2) * 2.5);
    const w = (10 + (c.score + 6) * 0.30) * 1.02;  // slightly oversized so it occludes the instance cleanly
    const geom = new THREE.BoxGeometry(w, h, w);
    geom.translate(0, h / 2, 0);
    const mat = new THREE.MeshStandardMaterial({
      color: CATEGORY_COLOR[c.category] ?? CATEGORY_COLOR.misc,
      roughness: 0.85,
      metalness: 0.0,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.position.set(wx, 0, wz);
    scene.add(mesh);
    // Edges around the detail mesh — gives every nearby building a visible border
    const edgeGeom = new THREE.EdgesGeometry(geom);
    const edgeMat = new THREE.LineBasicMaterial({ color: 0x1a1a1f, transparent: true, opacity: 0.9 });
    const edges = new THREE.LineSegments(edgeGeom, edgeMat);
    edges.position.set(wx, 0, wz);
    scene.add(edges);
    const entry = { mesh, edges, texture: null, marker: null, c };
    detailEntries.set(c.id, entry);
    // If this card has a real WebP thumb (server told us via has_thumb), float
    // a small gold marker above the building so player can SEE which buildings
    // are "real" (vs. procedural facade fallback).
    if (c.has_thumb) {
      const markerMat = new THREE.MeshBasicMaterial({ color: 0xffd700 });
      const marker = new THREE.Mesh(new THREE.ConeGeometry(1.0, 2.0, 5), markerMat);
      marker.position.set(wx, h + 4, wz);
      marker.rotation.x = Math.PI;  // point downward
      scene.add(marker);
      entry.marker = marker;
    }
    // Async-load the thumb; swap it in when ready, ignore errors (fallback color stays)
    _texLoader.load(
      `/api/card/${c.id}/thumb`,
      (tex) => {
        if (!detailEntries.has(c.id)) { tex.dispose(); return; }
        tex.colorSpace = THREE.SRGBColorSpace;
        const e = detailEntries.get(c.id);
        e.texture = tex;
        e.mesh.material.map = tex;
        e.mesh.material.color.setHex(0xffffff);  // let texture show through
        e.mesh.material.needsUpdate = true;
      },
      undefined,
      () => { /* ignore — keep colored fallback */ }
    );
  }
}

// ── HUD: building label, coordinate tracker, mini-map ────────────────
const buildingLabelEl = document.getElementById('building-label');
const coordsEl = document.getElementById('coords');
const minimapCanvas = document.getElementById('minimap');
const minimapCtx = minimapCanvas.getContext('2d');

// 12 topic categories at 30° each, starting at top (-90°), going clockwise
const CATEGORY_NAMES = ['tech','art','music','food','gaming','science','education','news','sports','commerce','community','personal'];
const SUB_SECTOR_NAMES = ['hobbyist','blog','business','other'];  // angular sub-sectors within parent
const ERA_BAND_ORDER = ['old_web','midweb','template','seo_hardened','modern_spa'];
const R_INNER_LAYOUT = 200, R_OUTER_LAYOUT = 1400;  // layout-space radii
const ERA_BAND_WIDTH_LAYOUT = (R_OUTER_LAYOUT - R_INNER_LAYOUT) / ERA_BAND_ORDER.length;

function describeLocation(wx, wz) {
  // Convert world-space (player) → layout-space (where the ring math lives)
  const lx = wx / 0.8;
  const ly = wz / 0.8;
  const radius = Math.hypot(lx, ly);
  // angle: 0 = north (top of map). atan2(ly, lx) returns radians from +x axis
  // Our layout puts category[0] (tech) at -π/2 (north), so:
  let angleFromNorth = Math.atan2(lx, -ly);  // 0 at north, increasing clockwise
  if (angleFromNorth < 0) angleFromNorth += Math.PI * 2;
  const sectorArc = (Math.PI * 2) / 12;
  const rawSector = angleFromNorth / sectorArc;
  const categoryIdx = Math.floor(rawSector + 0.5) % 12;
  const withinSector = rawSector - (Math.floor(rawSector + 0.5));  // -0.5..+0.5 of sector
  // Sub-sector (4 angular slices within parent, offsets -0.375 / -0.125 / +0.125 / +0.375)
  let subIdx;
  if (withinSector < -0.25) subIdx = 0;
  else if (withinSector < 0)  subIdx = 1;
  else if (withinSector < 0.25) subIdx = 2;
  else subIdx = 3;
  // Era band (radial)
  let eraIdx = 0;
  if (radius < R_INNER_LAYOUT * 0.9) {
    return { region: 'central plaza', radius, sector: null, sub: null, era: null };
  } else {
    eraIdx = Math.max(0, Math.min(ERA_BAND_ORDER.length - 1,
      Math.floor((radius - R_INNER_LAYOUT) / ERA_BAND_WIDTH_LAYOUT)));
  }
  return {
    region: 'sector',
    radius, sector: CATEGORY_NAMES[categoryIdx],
    sub: SUB_SECTOR_NAMES[subIdx],
    era: ERA_BAND_ORDER[eraIdx],
  };
}

function updateCoordsHUD() {
  const loc = describeLocation(camera.position.x, camera.position.z);
  const flyTag = player.flying ? ` · <span style="color:#cff0c8">flying</span>` : '';
  if (loc.region === 'central plaza') {
    coordsEl.innerHTML = `<div class="row"><b>Central plaza</b> · misc cards · ${loc.radius.toFixed(0)}m from origin${flyTag}</div>`;
  } else {
    coordsEl.innerHTML = `<div class="row"><b>${loc.sector}</b> district · <b>${loc.sub}</b> · <b>${loc.era}</b> era${flyTag}</div>` +
      `<div class="row" style="opacity:0.6;font-size:0.68rem">${loc.radius.toFixed(0)}m from origin · alt ${camera.position.y.toFixed(0)}m · facing ${(THREE.MathUtils.radToDeg(camera.rotation.y) | 0)}°</div>`;
  }
}

function updateBuildingLabel(c) {
  if (!c) {
    buildingLabelEl.classList.remove('show');
    return;
  }
  const score = c.score >= 0 ? '+' + c.score : c.score;
  buildingLabelEl.innerHTML = `<b>${escapeHtml(c.host)}</b><div class="meta">${escapeHtml(c.category)} · ${escapeHtml(c.site_type)} · ${escapeHtml(c.era)} · score ${score}</div>`;
  buildingLabelEl.classList.add('show');
}

// Mini-map: PAN view centered on player. Shows ~2000 layout-unit radius of nearby
// cards. Player stays at canvas center; cards move past as player walks.
let minimapDirty = true;  // kept for parity but we now redraw every frame
const MINIMAP_VIEW_RADIUS = 2000;  // layout units shown around player (pan view)
function renderMinimap() {
  const W = minimapCanvas.width, H = minimapCanvas.height;
  const cx = W / 2, cy = H / 2;
  const scale = Math.min(W, H) / (MINIMAP_VIEW_RADIUS * 2);
  // Player position in layout space
  const px_layout = camera.position.x / 0.8;
  const py_layout = camera.position.z / 0.8;
  // Repaint dot layer every frame — cards relative to player position
  const img = minimapCtx.createImageData(W, H);
  const data = img.data;
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 12; data[i+1] = 14; data[i+2] = 18; data[i+3] = 255;
  }
  for (let k = 0; k < cards.length; k++) {
    const c = cards[k];
    const dx = c.x - px_layout;
    const dy = c.y - py_layout;
    if (Math.abs(dx) > MINIMAP_VIEW_RADIUS || Math.abs(dy) > MINIMAP_VIEW_RADIUS) continue;
    const mx = (dx * scale + cx) | 0;
    const my = (dy * scale + cy) | 0;
    if (mx < 0 || mx >= W || my < 0 || my >= H) continue;
    const rgb = MINIMAP_CATEGORY_RGB[c.category] || [120, 120, 130];
    const idx = (my * W + mx) * 4;
    data[idx]   = rgb[0]; data[idx+1] = rgb[1]; data[idx+2] = rgb[2]; data[idx+3] = 255;
  }
  minimapCtx.putImageData(img, 0, 0);
  // Player at canvas center always (since minimap pans with player)
  minimapCtx.fillStyle = '#ffeec0';
  minimapCtx.beginPath();
  minimapCtx.arc(cx, cy, 4, 0, Math.PI * 2);
  minimapCtx.fill();
  minimapCtx.strokeStyle = '#1a1410';
  minimapCtx.lineWidth = 1.5;
  minimapCtx.stroke();
  // Facing direction indicator
  const ang = -camera.rotation.y - Math.PI / 2;
  minimapCtx.strokeStyle = '#ffeec0';
  minimapCtx.lineWidth = 1.5;
  minimapCtx.beginPath();
  minimapCtx.moveTo(cx, cy);
  minimapCtx.lineTo(cx + Math.cos(ang) * 8, cy + Math.sin(ang) * 8);
  minimapCtx.stroke();
}

const MINIMAP_CATEGORY_RGB = {
  tech:[122,169,216], art:[224,160,96], music:[200,112,192], food:[224,112,80],
  gaming:[144,128,208], science:[96,192,160], education:[160,192,104], news:[216,168,168],
  sports:[224,192,96], commerce:[154,169,188], community:[128,200,208], personal:[216,160,80],
  misc:[112,112,128],
};

// Mini-map click to fast-travel — clicked offset interpreted relative to player
minimapCanvas.addEventListener('click', (e) => {
  const rect = minimapCanvas.getBoundingClientRect();
  const mx = (e.clientX - rect.left) * (minimapCanvas.width / rect.width);
  const my = (e.clientY - rect.top) * (minimapCanvas.height / rect.height);
  const W = minimapCanvas.width, H = minimapCanvas.height;
  const cx = W / 2, cy = H / 2;
  const scale = Math.min(W, H) / (MINIMAP_VIEW_RADIUS * 2);
  // Click offset in layout units, ADDED to current player position
  const px_layout = camera.position.x / 0.8;
  const py_layout = camera.position.z / 0.8;
  const lx = (mx - cx) / scale + px_layout;
  const ly = (my - cy) / scale + py_layout;
  // Layout coords → world coords
  const wx = lx * 0.8, wz = ly * 0.8;
  camera.position.x = wx;
  camera.position.z = wz;
  player.velocity.set(0, 0, 0);
  // Force a chunk refresh near the new position
  setTimeout(maintainChunks, 50);
});

// ── Interaction: pick the nearest building under the crosshair ───────
const raycaster = new THREE.Raycaster();
const screenCenter = new THREE.Vector2(0, 0);
function updateNearest() {
  if (!buildingsMesh) { nearestCard = null; return; }
  raycaster.setFromCamera(screenCenter, camera);
  raycaster.far = 80;
  const hits = raycaster.intersectObject(buildingsMesh, false);
  if (hits.length > 0) {
    const hit = hits[0];
    nearestCard = cardByInstance[hit.instanceId];
  } else {
    // Fallback: nearest building by player-distance (independent of crosshair)
    let best = null, bestD = HOVER_DIST;
    const px = camera.position.x, pz = camera.position.z;
    // Brute-force only checks cards in a coarse radius for performance
    for (let i = 0; i < cards.length; i++) {
      const c = cards[i];
      const dx = c.x * 0.8 - px, dz = c.y * 0.8 - pz;
      const d = Math.sqrt(dx * dx + dz * dz);
      if (d < bestD) { bestD = d; best = c; }
    }
    nearestCard = best;
  }
}

async function openCard(c) {
  if (!c) return;
  controls.unlock();
  const scoreClass = c.score < 0 ? 'score neg' : 'score';
  modalPanel.innerHTML = `
    <div class="row">
      <img class="facade" src="/api/card/${c.id}/facade.svg" alt="">
      <div class="info">
        <div class="title" id="modal-title">(loading…)</div>
        <div class="host">${escapeHtml(c.host)}</div>
        <div class="pills">
          <span class="pill ${scoreClass}">${c.score >= 0 ? '+' : ''}${c.score}</span>
          <span class="pill">${escapeHtml(c.era)}</span>
          <span class="pill">${escapeHtml(c.category)}</span>
        </div>
      </div>
    </div>
    <div class="thumb" style="background-image:url('/api/card/${c.id}/thumb')"></div>
    <div class="actions">
      <a class="btn primary" href="" target="_blank" rel="noopener noreferrer" id="modal-open">open site ↗</a>
      <button class="btn" id="modal-close">close</button>
    </div>
  `;
  try {
    const r = await fetch(`/api/card/${c.id}`);
    const full = await r.json();
    document.getElementById('modal-open').href = full.url;
    if (full.title) document.getElementById('modal-title').textContent = full.title;
    if (full.url && full.url.startsWith('http://')) {
      const pills = modalPanel.querySelector('.pills');
      const warn = document.createElement('span');
      warn.className = 'pill';
      warn.style.cssText = 'color:#f0c8a8;border-color:#604030;background:rgba(60,40,30,0.6)';
      warn.textContent = 'http';
      pills.appendChild(warn);
    }
  } catch (e) {}
  document.getElementById('modal-close').addEventListener('click', () => modal.classList.remove('show'));
  modal.classList.add('show');
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":"&#39;" })[c]);
}

modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('show'); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.classList.remove('show'); });

// ── Frame loop ───────────────────────────────────────────────────────
let lastT = 0;
function frame(now) {
  const dt = Math.min(0.05, (now - lastT) / 1000) || 0;
  lastT = now;

  if (controls.isLocked) {
    // Movement
    const isShift = keys['ShiftLeft'] || keys['ShiftRight'];
    const sprint = (isShift && !player.flying) ? player.sprintMult : 1;
    const moveSpeed = (player.flying ? player.flySpeed : player.speed) * sprint;
    let mx = 0, mz = 0;
    if (keys['KeyW']) mz -= 1;
    if (keys['KeyS']) mz += 1;
    if (keys['KeyA']) mx -= 1;
    if (keys['KeyD']) mx += 1;

    if (mx || mz) {
      camera.getWorldDirection(tmpForward);
      if (!player.flying) tmpForward.y = 0;
      tmpForward.normalize();
      tmpRight.copy(tmpForward).cross(camera.up).normalize();
      tmpV.set(0, 0, 0)
          .addScaledVector(tmpForward, -mz)
          .addScaledVector(tmpRight, mx)
          .normalize().multiplyScalar(moveSpeed * dt);
      camera.position.x += tmpV.x;
      camera.position.y += tmpV.y;
      camera.position.z += tmpV.z;
    }

    if (player.flying) {
      // Vertical: space = up, shift = down. No gravity.
      const vSpeed = player.flySpeed * 0.8;
      if (keys['Space']) camera.position.y += vSpeed * dt;
      if (isShift)       camera.position.y -= vSpeed * dt;
      if (camera.position.y < player.height) camera.position.y = player.height;
      player.velocity.y = 0;
      player.onGround = false;
    } else {
      // Gravity + jump
      player.velocity.y -= player.gravity * dt;
      if (keys['Space'] && player.onGround) {
        player.velocity.y = player.jumpV;
        player.onGround = false;
      }
      camera.position.y += player.velocity.y * dt;
      if (camera.position.y < player.height) {
        camera.position.y = player.height;
        player.velocity.y = 0;
        player.onGround = true;
      }
    }

    updateNearest();
  }

  // HUD updates (cheap, every frame)
  updateNearestLandmark();
  updateBuildingLabel(nearestLandmark ? null : nearestCard);  // hide building label when near a landmark
  updateCoordsHUD();
  renderMinimap();
  updateDetailMeshes(now);  // throttled to 2 Hz internally

  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

// ── Proximity loading — only buildings near the player are loaded ──
// Like a video game: the world is a possibility-space; chunks load on demand
// as the player walks into them, far chunks are unloaded to keep memory bounded.
const CHUNK_SIZE = 300;       // world units per chunk side
const VIEW_RADIUS_CHUNKS = 3; // load this many chunks around player on each axis
const VIEW_RADIUS = CHUNK_SIZE * VIEW_RADIUS_CHUNKS;
const loadedChunks = new Map();  // key "i,j" → Set of card-IDs in that chunk
const cardsById = new Map();     // id → card record (currently in scene)

function chunkKey(cx, cy) { return `${cx},${cy}`; }
function playerChunk() {
  const px = camera.position.x;
  const pz = camera.position.z;
  return [Math.round(px / CHUNK_SIZE), Math.round(pz / CHUNK_SIZE)];
}

let loadInFlight = new Set();
async function ensureChunkLoaded(ci, cj) {
  const key = chunkKey(ci, cj);
  if (loadedChunks.has(key) || loadInFlight.has(key)) return;
  loadInFlight.add(key);
  const cxLayout = ci * CHUNK_SIZE / 0.8;
  const cyLayout = cj * CHUNK_SIZE / 0.8;
  const rLayout = (CHUNK_SIZE / 0.8) * 1.05;
  try {
    const url = `/api/game/region?cx=${cxLayout}&cy=${cyLayout}&r=${rLayout}&limit=2500`;
    const r = await fetch(url);
    if (!r.ok) {
      console.warn(`[chunk ${key}] HTTP ${r.status}`);
      loadedChunks.set(key, new Set());  // mark as loaded-empty so we don't retry
      return;
    }
    const d = await r.json();
    const newCards = (d.cards || []).filter(c => !cardsById.has(c.id));
    const ids = new Set();
    for (const c of newCards) {
      cardsById.set(c.id, c);
      ids.add(c.id);
      cards.push(c);
    }
    loadedChunks.set(key, ids);
    console.log(`[chunk ${key}] +${newCards.length} cards (total ${cards.length})`);
  } catch (e) {
    console.warn(`[chunk ${key}] fetch error`, e);
  } finally {
    loadInFlight.delete(key);
  }
}

function unloadFarChunks(pi, pj) {
  // Drop chunks that are more than VIEW_RADIUS_CHUNKS+2 away from the player
  const drop = [];
  for (const key of loadedChunks.keys()) {
    const [ci, cj] = key.split(',').map(Number);
    if (Math.abs(ci - pi) > VIEW_RADIUS_CHUNKS + 2 || Math.abs(cj - pj) > VIEW_RADIUS_CHUNKS + 2) {
      drop.push(key);
    }
  }
  if (drop.length === 0) return false;
  for (const key of drop) {
    const ids = loadedChunks.get(key);
    if (ids) for (const id of ids) cardsById.delete(id);
    loadedChunks.delete(key);
  }
  cards = Array.from(cardsById.values());
  return true;
}

// Debounced rebuild: any chunk completion triggers a deferred mesh rebuild
// so buildings appear as soon as their chunk data arrives.
let rebuildScheduled = false;
function scheduleRebuild() {
  if (rebuildScheduled) return;
  rebuildScheduled = true;
  setTimeout(() => {
    rebuildScheduled = false;
    cardByInstance = [];
    buildBuildings();
    minimapDirty = true;  // trigger mini-map dot-layer repaint
    // "1,234 loaded · 198K placed · 4.0M screened · 8 chunks"
    const fmt = n => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : n.toString();
    const placedStr = window._totalPlaced ? ` · ${fmt(window._totalPlaced)} placed` : '';
    const screenedStr = window._totalScreened ? ` · ${fmt(window._totalScreened)} screened` : '';
    stats.textContent = `${cards.length.toLocaleString()} loaded${placedStr}${screenedStr} · ${loadedChunks.size} chunks`;
  }, 200);
}

async function loadChunkAndRebuild(ci, cj) {
  await ensureChunkLoaded(ci, cj);
  scheduleRebuild();
}

async function maintainChunks() {
  const [pi, pj] = playerChunk();
  // Spiral outward from player, fire-and-forget each fetch
  for (let r = 0; r <= VIEW_RADIUS_CHUNKS; r++) {
    for (let di = -r; di <= r; di++) {
      for (let dj = -r; dj <= r; dj++) {
        if (Math.max(Math.abs(di), Math.abs(dj)) !== r) continue;
        const key = chunkKey(pi + di, pj + dj);
        if (!loadedChunks.has(key) && !loadInFlight.has(key)) {
          loadChunkAndRebuild(pi + di, pj + dj);
        }
      }
    }
  }
  const dropped = unloadFarChunks(pi, pj);
  if (dropped) scheduleRebuild();
}

async function init() {
  stats.textContent = 'connecting to world…';
  requestAnimationFrame(frame);  // render loop starts immediately — user sees ground+plaza
  console.log('[world] init: loading first chunk');
  // Stage 1: load player's current chunk first for fast first paint
  try {
    await loadChunkAndRebuild(0, 0);
    console.log(`[world] first chunk loaded: ${cards.length} cards`);
  } catch (e) {
    console.error('[world] first chunk failed', e);
    stats.textContent = 'load failed: ' + (e && e.message || 'unknown');
    return;
  }
  // Stage 2: progressively load surrounding rings in background
  for (let r = 1; r <= VIEW_RADIUS_CHUNKS; r++) {
    const ringR = r;
    setTimeout(() => {
      for (let di = -ringR; di <= ringR; di++) {
        for (let dj = -ringR; dj <= ringR; dj++) {
          if (Math.max(Math.abs(di), Math.abs(dj)) !== ringR) continue;
          loadChunkAndRebuild(di, dj);
        }
      }
    }, r * 600);
  }
  // Periodic: load new chunks as player walks, drop far ones
  setInterval(maintainChunks, 1200);
}

init();
"""


# ── /rendered: compact mirror of /world that shows only cards with real WebP thumbs.
# Generated by string-substitution on WORLD_PAGE/WORLD_JS so /world stays untouched.
# Backed by data/layout_rendered.json (output of `scripts/10_layout_ring.py --rendered-only`).
RENDERED_PAGE = (
    WORLD_PAGE
    .replace("/world/main.js", "/rendered/main.js")
    .replace("Wander Around — 3D world", "Wander Around — Rendered")
    # Reciprocal swap link: /world's "rendered" button becomes "full world" on /rendered.
    .replace(
        '<a href="/rendered" id="rendered-link" title="Show only buildings with real screenshots — compact pinwheel">rendered</a>',
        '<a href="/world" id="rendered-link" title="Switch back to the full 200K-card world">full world</a>',
    )
)
RENDERED_JS = (
    WORLD_JS
    .replace("/api/game/region?cx=", "/api/game/region?mode=rendered&cx=")
    .replace("/api/game/layout?mode=categories", "/api/game/layout?mode=rendered")
    # Make the stats bar say "13K placed" instead of "200K placed" on /rendered.
    .replace("s.placed && s.placed.categories", "s.placed && s.placed.rendered")
    .replace("s.placed.categories", "s.placed.rendered")
)


@app.get("/rendered", response_class=HTMLResponse)
async def rendered_page():
    return HTMLResponse(content=RENDERED_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/rendered/main.js")
async def rendered_js():
    return Response(content=RENDERED_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


# ── /wiki: same walkable mandala but populated by ~6.8M Wikipedia articles.
# Same architectural pattern as /rendered — string-substitute the /world page
# and JS so /world is untouched. Layout JSON at data/wiki/layout_wiki.json
# (same shape as layout_categories.json — 12 sectors, 4 sub-sectors, era bands).
WIKI_PAGE = (
    WORLD_PAGE
    .replace("/world/main.js", "/wiki/main.js")
    .replace("Wander Around — 3D world", "Wander Around — Wiki")
    .replace(
        '<a href="/rendered" id="rendered-link" title="Show only buildings with real screenshots — compact pinwheel">rendered</a>',
        '<a href="/world" id="rendered-link" title="Back to the long-tail web world">long-tail web</a>',
    )
)
WIKI_JS = (
    WORLD_JS
    .replace("/api/game/region?cx=", "/api/game/region?mode=wiki&cx=")
    .replace("/api/game/layout?mode=categories", "/api/game/layout?mode=wiki")
    .replace("s.placed && s.placed.categories", "s.placed && s.placed.wiki")
    .replace("s.placed.categories", "s.placed.wiki")
    # Lazy-thumb URL swap — wiki article IDs live in a different DB and namespace
    # so they need their own endpoint. /api/wiki/article/{id}/thumb mirrors
    # /api/card/{id}/thumb but reads from articles table.
    .replace("/api/card/${c.id}/thumb", "/api/wiki/article/${c.id}/thumb")
)


@app.get("/wiki", response_class=HTMLResponse)
async def wiki_page():
    return HTMLResponse(content=WIKI_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/wiki/main.js")
async def wiki_js():
    return Response(content=WIKI_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


# ── /4d: sphere projection of the corpus (closed manifold; no edges). ──────
# Standalone Three.js scene — not derived from WORLD_JS because the
# rendering paradigm is fundamentally different (no chunks, no proximity,
# no buildings, no ground plane). Everything is a single InstancedMesh of
# small sphere primitives mapped onto a globe.
#
# Projection: each card's (layout_x, layout_y) is normalized to (-1, 1) by
# layout extent, then mapped onto a 2-sphere via:
#   r_norm = hypot(x, y) / R_OUTER   // 0 at center, 1 at rim
#   lat   = π/2 - r_norm * π         // pole at center, equator at rim
#   lon   = atan2(y, x)              // angular axis preserved
# Categories at adjacent angular positions in the layout become adjacent
# longitudinal continents on the sphere. Score adds a small radial offset
# (high-score cards float above the sphere surface).
FOURD_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
  <meta name="theme-color" content="#0a0a0e">
  <title>Wander Around — 4D sphere</title>
  <style>
    :root { color-scheme: dark; --fg:#ece8df; --bg:#0a0a0e; --panel:#14141a; --line:#2a2a32; --dim:#9a9a8a; }
    * { box-sizing: border-box; }
    html, body { margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg);
                 font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow:hidden; }
    #scene { position: fixed; inset: 0; }
    .topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 5; padding: 0.7rem 1rem;
              display: flex; justify-content: space-between; align-items: baseline; gap: 1rem;
              background: linear-gradient(to bottom, rgba(10,10,14,0.85), transparent);
              pointer-events: none; }
    .brand { font-size: 0.85rem; font-weight: 500; opacity: 0.85; }
    .stats { font-size: 0.7rem; opacity: 0.55; }
    .toggles { position: fixed; right: 1rem; bottom: 1rem; z-index: 5; display: flex; gap: 0.4rem; flex-wrap: wrap; justify-content: flex-end; }
    .toggles a, .toggles button { background: rgba(12,12,14,0.85); border: 1px solid var(--line); border-radius: 6px;
                 padding: 0.5rem 0.8rem; color: var(--fg); text-decoration: none; font-size: 0.75rem;
                 font-family: inherit; cursor: pointer; }
    .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 10;
             display: none; align-items: center; justify-content: center; padding: 1rem; }
    .modal.show { display: flex; }
    .modal .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
                    max-width: 560px; width: 100%; max-height: 85vh; overflow-y: auto;
                    display: flex; flex-direction: column; gap: 0.85rem; padding: 1.2rem; }
    .modal .title { font-size: 1.1rem; font-weight: 500; line-height: 1.3; }
    .modal .host { font-size: 0.8rem; color: var(--dim); word-break: break-all; }
    .modal .pills { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.4rem; }
    .pill { font-size: 0.7rem; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--dim); }
    .pill.score { color: #aef0a8; border-color: #2c4030; background: rgba(38,48,42,0.6); }
    .modal .actions { display: flex; gap: 0.6rem; }
    .btn { flex: 1 1 0; padding: 0.75rem; border-radius: 8px; border: 1px solid var(--line);
           background: rgba(20,20,26,0.7); color: var(--fg); text-align: center; text-decoration: none;
           font-family: inherit; font-size: 0.85rem; cursor: pointer; }
    .btn.primary { background: rgba(40,52,46,0.8); border-color: #2c4030; color: #cff0c8; }
    .hover-info { position: fixed; pointer-events: none; background: rgba(12,12,14,0.95);
                  border: 1px solid var(--line); border-radius: 4px; padding: 0.4rem 0.6rem;
                  font-size: 0.75rem; max-width: 18rem; z-index: 6; display: none; }
    .hover-info.show { display: block; }
    .source-pick { position: fixed; left: 1rem; bottom: 1rem; z-index: 5;
                   background: rgba(12,12,14,0.85); border: 1px solid var(--line);
                   border-radius: 6px; padding: 0.4rem 0.6rem; font-size: 0.7rem; }
    .source-pick select { background: rgba(20,20,26,0.7); color: var(--fg);
                          border: 1px solid var(--line); border-radius: 4px;
                          font-family: inherit; font-size: 0.7rem; padding: 0.2rem; }
    .legend { position: fixed; left: 1rem; top: 3rem; z-index: 5;
              background: rgba(12,12,14,0.78); border: 1px solid var(--line);
              border-radius: 6px; padding: 0.5rem 0.7rem; font-size: 0.7rem;
              max-width: 12rem; line-height: 1.5; }
    .legend .row { display: flex; align-items: center; gap: 0.4rem; }
    .legend .sw { width: 10px; height: 10px; border-radius: 2px; }
  </style>
</head>
<body>
  <canvas id="scene"></canvas>
  <div class="topbar">
    <span class="brand">wander around — 4D sphere</span>
    <span class="stats" id="stats">loading globe…</span>
  </div>
  <div class="legend" id="legend"><div style="opacity:0.7;margin-bottom:0.3rem;">Continents (categories)</div><div id="legend-rows"></div></div>
  <div class="source-pick">
    source: <select id="source-select">
      <option value="categories">long-tail web (4M sites)</option>
      <option value="wiki">wikipedia (6.8M articles)</option>
    </select>
  </div>
  <div class="toggles">
    <a href="/world">walkable</a>
    <a href="/game">2D map</a>
    <a href="/rendered">rendered</a>
    <a href="/wiki">wiki</a>
    <a href="/feed">feed</a>
  </div>
  <div class="hover-info" id="hover"></div>
  <div class="modal" id="modal"><div class="panel" id="modal-panel"></div></div>
  <!-- No inline diagnostic / importmap — CSP `script-src 'self'` blocks both.
       /4d/main.js uses full /static/ URLs in its import statements; OrbitControls.js
       was patched in-place to also use full /static/ URLs (no bare specifiers). -->
  <script type="module" src="/4d/main.js"></script>
</body>
</html>"""

FOURD_JS = r"""// /4d sphere — single InstancedMesh of N small spheres on a 2-sphere globe.
// Categories become longitudinal continents; score becomes radial offset above
// the sphere surface; OrbitControls let you spin and zoom.
//
// Full /static/ URLs because nginx CSP `script-src 'self'` blocks bare-specifier
// importmaps (which require 'unsafe-inline'). OrbitControls.js was patched in
// place to also import three from the full URL.

import * as THREE from '/static/three.module.js';
import { OrbitControls } from '/static/OrbitControls.js';

// Surface module-init errors to the visible stats bar instead of console-only.
window.addEventListener('error', (e) => {
  const stats = document.getElementById('stats');
  if (stats) stats.textContent = 'ERR: ' + (e.message || 'unknown').slice(0, 80);
});
window.addEventListener('unhandledrejection', (e) => {
  const stats = document.getElementById('stats');
  const msg = (e.reason && e.reason.message) || String(e.reason || 'unknown');
  if (stats) stats.textContent = 'PROMISE ERR: ' + msg.slice(0, 80);
});

document.getElementById('stats').textContent = 'modules loaded — initializing scene…';

const canvas = document.getElementById('scene');
const stats = document.getElementById('stats');
const hover = document.getElementById('hover');
const modal = document.getElementById('modal');
const modalPanel = document.getElementById('modal-panel');
const sourceSelect = document.getElementById('source-select');
const legendRowsEl = document.getElementById('legend-rows');

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x070712);
// Fog was at 0.018 — way too dense; at the camera's 150-unit distance from the
// sphere surface, visibility dropped to ~0.07% which made the globe invisible
// even though every other parameter was correct (coordinates computing, raycasts
// landing, OrbitControls responding). The "inversion" was that the sphere and
// background rendered the same near-black color and the fog erased the rest.
scene.fog = new THREE.FogExp2(0x070712, 0.0025);

const dpr = Math.min(window.devicePixelRatio || 1, 2);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(dpr);
renderer.setSize(window.innerWidth, window.innerHeight, false);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.set(0, 30, 150);
camera.lookAt(0, 0, 0);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.rotateSpeed = 0.6;
controls.zoomSpeed = 0.8;
controls.minDistance = 60;   // can't dive into the sphere
controls.maxDistance = 400;  // hard back-out limit

// ── Globe scaffolding ─────────────────────────────────────────────
// Lit ocean sphere — uses MeshLambertMaterial so the directional light produces
// visible terminator shading. Color is a deep navy with enough contrast against
// the near-black background that the sphere reads as a body even before any
// cards are placed on it.
const GLOBE_R = 60;
const oceanGeom = new THREE.SphereGeometry(GLOBE_R - 2, 64, 48);
const oceanMat = new THREE.MeshLambertMaterial({ color: 0x1a2848 });
scene.add(new THREE.Mesh(oceanGeom, oceanMat));

// Brighter wireframe globe — visible at the sphere surface, encodes the
// great-circle structure so longitude/latitude is readable as you orbit.
const wireGeom = new THREE.SphereGeometry(GLOBE_R - 1.5, 36, 24);
const wireMat = new THREE.MeshBasicMaterial({
  color: 0x4a6080, wireframe: true, transparent: true, opacity: 0.55,
});
scene.add(new THREE.Mesh(wireGeom, wireMat));

// Lighting — strong hemisphere fill + warm directional sun so the globe and the
// cards on it read as a body, not as flat-shaded silhouettes.
scene.add(new THREE.HemisphereLight(0xb8d8ff, 0x1a1828, 1.4));
const dirLight = new THREE.DirectionalLight(0xfff0d8, 0.8);
dirLight.position.set(80, 100, 60);
scene.add(dirLight);
// Ambient backstop — guarantees no face is fully unlit.
scene.add(new THREE.AmbientLight(0x405060, 0.3));

// ── Card sphere instances ─────────────────────────────────────────
const SPHERE_GEOM = new THREE.IcosahedronGeometry(0.45, 0);
let cardMesh = null;       // current InstancedMesh
let cardData = [];         // parallel array of card objects (for raycast → modal)
let categoryColors = {};   // { cat → hex }

const TMP_OBJ = new THREE.Object3D();
const TMP_COLOR = new THREE.Color();

function projectToSphere(card, extent) {
  // Polar projection: r_norm = hypot(x, y) / extent
  // lat = π/2 - r_norm * π   (north pole at center, south pole opposite, equator at rim)
  // Actually nicer: pole at the rim is uglier than equator at the rim, so:
  //   r_norm = hypot(x, y) / extent
  //   theta  = r_norm * π/2 + π/4    // map center→45°, rim→135° latitude
  //   phi    = atan2(y, x)            // longitude preserved
  // This places "central plaza" cards at the visual front pole and "outer rim" cards
  // wrapped around the back — a viewer rotates the globe to see different sectors.
  const r = Math.hypot(card.x, card.y);
  const rn = Math.min(1, r / extent);
  const theta = rn * Math.PI;          // 0 (front pole) to π (back pole)
  const phi = Math.atan2(card.y, card.x);
  // Score → radial offset above the sphere surface (cathedrals)
  const elevation = Math.max(0, (card.score || 0)) * 0.35;
  const R = GLOBE_R + elevation;
  const x = R * Math.sin(theta) * Math.cos(phi);
  const y = R * Math.cos(theta);
  const z = R * Math.sin(theta) * Math.sin(phi);
  return [x, y, z];
}

function buildCardMesh(cards, colorMap, extent) {
  if (cardMesh) {
    scene.remove(cardMesh);
    cardMesh.dispose && cardMesh.dispose();
  }
  const count = cards.length;
  const mat = new THREE.MeshLambertMaterial({ vertexColors: true });
  cardMesh = new THREE.InstancedMesh(SPHERE_GEOM, mat, count);
  cardMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  cardMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
  // Pre-resolve per-category RGB triples once (vs THREE.Color.set on hex string per instance)
  const rgbByCat = {};
  for (const cat in colorMap) {
    const tc = new THREE.Color(colorMap[cat]);
    rgbByCat[cat] = [tc.r, tc.g, tc.b];
  }
  const fallbackRgb = [0.48, 0.66, 0.85];
  // Direct write to the underlying matrix array — bypass Object3D + matrix.compose.
  // Matrix4 layout is column-major: [m11,m21,m31,m41, m12,m22,m32,m42, ...]
  // For a translation+uniform-scale matrix s*I + T:
  //   col0 = (s, 0, 0, 0); col1 = (0, s, 0, 0); col2 = (0, 0, s, 0); col3 = (x, y, z, 1)
  const matArr = cardMesh.instanceMatrix.array;
  const colArr = cardMesh.instanceColor.array;
  for (let i = 0; i < count; i++) {
    const c = cards[i];
    const r = Math.hypot(c.x, c.y);
    const rn = r > extent ? 1 : r / extent;
    const theta = rn * Math.PI;
    const phi = Math.atan2(c.y, c.x);
    const elevation = Math.max(0, c.score || 0) * 0.35;
    const R = 60 /* GLOBE_R */ + elevation;
    const sinT = Math.sin(theta);
    const x = R * sinT * Math.cos(phi);
    const y = R * Math.cos(theta);
    const z = R * sinT * Math.sin(phi);
    const s = 0.7 + Math.max(0, c.score || 0) * 0.18;
    const m = i * 16;
    matArr[m+0]=s; matArr[m+1]=0; matArr[m+2]=0; matArr[m+3]=0;
    matArr[m+4]=0; matArr[m+5]=s; matArr[m+6]=0; matArr[m+7]=0;
    matArr[m+8]=0; matArr[m+9]=0; matArr[m+10]=s; matArr[m+11]=0;
    matArr[m+12]=x; matArr[m+13]=y; matArr[m+14]=z; matArr[m+15]=1;
    const rgb = rgbByCat[c.category] || fallbackRgb;
    const k = i * 3;
    colArr[k]   = rgb[0];
    colArr[k+1] = rgb[1];
    colArr[k+2] = rgb[2];
  }
  cardMesh.instanceMatrix.needsUpdate = true;
  cardMesh.instanceColor.needsUpdate = true;
  scene.add(cardMesh);
}

function rebuildLegend(colorMap) {
  legendRowsEl.innerHTML = Object.entries(colorMap).map(([k, v]) =>
    `<div class="row"><span class="sw" style="background:${v}"></span> ${k}</div>`
  ).join('');
}

// ── Loading ────────────────────────────────────────────────────────
// Per-source cache of fetched layout — switching back to a previously-loaded
// source is instant after the first fetch.
const _sourceCache = {};

// Limit kept smaller than /world's 250K — 80K is plenty of density for a
// sphere (the surface area can't display more meaningfully) and the JSON
// fetch is ~12MB instead of ~30MB → ~3× faster cold load.
const FETCH_LIMIT = 80000;

async function fetchWithProgress(url, onProgress) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const total = parseInt(r.headers.get('Content-Length') || '0', 10);
  if (!total || !r.body || !r.body.getReader) {
    return await r.json();  // fallback: no progress
  }
  const reader = r.body.getReader();
  let received = 0;
  const chunks = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress && onProgress(received, total);
  }
  // Concatenate Uint8Arrays → text → JSON
  const merged = new Uint8Array(received);
  let off = 0;
  for (const c of chunks) { merged.set(c, off); off += c.length; }
  return JSON.parse(new TextDecoder().decode(merged));
}

async function loadSource(mode) {
  // Cached source → instant rebuild
  if (_sourceCache[mode]) {
    const { cards, colors, extent } = _sourceCache[mode];
    cardData = cards;
    categoryColors = colors;
    rebuildLegend(colors);
    stats.textContent = 'rebuilding globe (' + cards.length.toLocaleString() + ' cached)…';
    await new Promise(r => requestAnimationFrame(r));
    buildCardMesh(cards, colors, extent);
    stats.textContent = cards.length.toLocaleString() + ' cards · ' + mode + ' · drag to spin';
    return;
  }
  stats.textContent = 'fetching layout (' + mode + ')… 0%';
  try {
    const data = await fetchWithProgress(
      `/api/game/layout?mode=${mode}&limit=${FETCH_LIMIT}`,
      (got, total) => {
        const mb = (got / 1048576).toFixed(1);
        const tot = (total / 1048576).toFixed(1);
        stats.textContent = `fetching layout (${mode})… ${mb} / ${tot} MB`;
      },
    );
    const cards = data.cards || [];
    cardData = cards;
    categoryColors = data.category_colors || {};
    rebuildLegend(categoryColors);
    const extent = data.extent || 5000;
    _sourceCache[mode] = { cards, colors: categoryColors, extent };
    stats.textContent = 'building globe (' + cards.length.toLocaleString() + ' instances)…';
    await new Promise(r => requestAnimationFrame(r));
    buildCardMesh(cards, categoryColors, extent);
    stats.textContent = cards.length.toLocaleString() + ' cards · ' + mode + ' · drag to spin';
  } catch (e) {
    stats.textContent = 'failed: ' + (e && e.message ? e.message : 'unknown');
    console.error('4d load failed', e);
  }
}

sourceSelect.addEventListener('change', (e) => loadSource(e.target.value));

// ── Raycast picking ────────────────────────────────────────────────
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();

function pickAt(clientX, clientY) {
  if (!cardMesh) return -1;
  const rect = canvas.getBoundingClientRect();
  ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
  ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(ndc, camera);
  const hits = raycaster.intersectObject(cardMesh, false);
  return hits.length ? hits[0].instanceId : -1;
}

canvas.addEventListener('mousemove', (e) => {
  const i = pickAt(e.clientX, e.clientY);
  if (i >= 0) {
    const c = cardData[i];
    hover.style.left = (e.clientX + 12) + 'px';
    hover.style.top = (e.clientY + 12) + 'px';
    hover.innerHTML = `<div style="font-weight:500">${escapeHtml(c.host || '(no title)')}</div>` +
      `<div style="opacity:0.5;margin-top:2px">${(c.score>=0?'+':'') + c.score} · ${c.era} · ${c.category}</div>`;
    hover.classList.add('show');
    canvas.style.cursor = 'pointer';
  } else {
    hover.classList.remove('show');
    canvas.style.cursor = 'grab';
  }
});

canvas.addEventListener('click', (e) => {
  const i = pickAt(e.clientX, e.clientY);
  if (i >= 0) openCard(cardData[i]);
});

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

function openCard(c) {
  const isWiki = sourceSelect.value === 'wiki';
  const externalUrl = isWiki
    ? `https://en.wikipedia.org/wiki/${encodeURIComponent((c.host || '').replace(/ /g, '_'))}`
    : `/api/card/${c.id}`;  // For long-tail web, the API has the URL
  modalPanel.innerHTML = `
    <div class="title">${escapeHtml(c.host || '(untitled)')}</div>
    <div class="host">${escapeHtml(c.category)} · ${escapeHtml(c.era)} · ${escapeHtml(c.language)}</div>
    <div class="pills">
      <span class="pill score">score ${c.score}</span>
      <span class="pill">${escapeHtml(c.site_type)}</span>
      ${c.wc ? `<span class="pill">${c.wc.toLocaleString()} words</span>` : ''}
    </div>
    <div class="actions">
      ${isWiki
        ? `<a class="btn primary" target="_blank" rel="noopener" href="${externalUrl}">open on wikipedia →</a>`
        : `<a class="btn primary" target="_blank" rel="noopener" href="javascript:void(0)" onclick="(async () => { const r = await fetch('/api/card/${c.id}'); const d = await r.json(); if (d && d.url) window.open(d.url, '_blank'); })()">open site →</a>`}
      <button class="btn" onclick="document.getElementById('modal').classList.remove('show')">close</button>
    </div>
  `;
  modal.classList.add('show');
}
modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('show'); });

// ── Resize ─────────────────────────────────────────────────────────
window.addEventListener('resize', () => {
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
});

// ── Render loop ────────────────────────────────────────────────────
function tick() {
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}

loadSource('categories');
tick();
"""


@app.get("/4d", response_class=HTMLResponse)
async def fourd_page():
    return HTMLResponse(content=FOURD_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/4d/main.js")
async def fourd_js():
    return Response(content=FOURD_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


# Self-hosted Three.js (vendored to /opt/wander/lib/) so /4d's importmap doesn't
# depend on a public CDN — eliminates the cold-load tax of fetching 1MB of
# JavaScript across the Atlantic before the page can render.
LIB_DIR = Path(__file__).parent.parent / "lib"


@app.get("/lib/{filename}")
async def lib_file(filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(404, "not found")
    p = LIB_DIR / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"{filename} not found in /lib")
    return Response(
        content=p.read_bytes(),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.middleware("http")
async def strip_server_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["server"] = "wander"
    return response
