"""Background thumb renderer for Wikipedia articles. Mirrors the architecture
of scripts/13_thumb_render.py but reads from wiki.db's articles table and
renders the canonical en.wikipedia.org URL for each article.

Selection priority: thumb_priority DESC (recently-clicked first), then
word_count DESC (biggest articles first → most-likely-to-be-rendered get
real screenshots first).

Output: data/wiki/thumbs/{hash}.webp; articles.thumb_path updated.

Run:
    python3 scripts/wiki/05_wiki_thumb_render.py --parallelism 2 --batch-size 10
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path

# Disable PIL's decompression-bomb safeguard. Wikipedia article pages are tall
# (10K+ words → hundreds of millions of pixels in a full-page screenshot)
# which trips the default 178M-pixel limit. We trust our own renders, and
# make_thumb crops to 1280×320 anyway before saving.
import PIL.Image
PIL.Image.MAX_IMAGE_PIXELS = None

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from wander.rendering import render_batch, save_thumb

WIKI_DB = Path("data/wiki/wiki.db")
WIKI_THUMBS = Path("data/wiki/thumbs")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    if "thumb_path" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN thumb_path TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_thumb_path ON articles(thumb_path)")
    if "thumb_priority" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN thumb_priority INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_thumb_priority ON articles(thumb_priority)")
    conn.commit()


def claim_batch(conn: sqlite3.Connection, batch_size: int) -> list[tuple[int, str]]:
    """Claim N articles without thumbs. Order: thumb_priority DESC (clicked),
    then word_count DESC (big articles first). Excludes empty-string sentinel
    so failed renders don't get re-attempted forever."""
    rows = conn.execute(
        """
        SELECT id, title FROM articles
        WHERE thumb_path IS NULL
          AND is_disambig = 0
          AND word_count > 200
        ORDER BY COALESCE(thumb_priority, 0) DESC, word_count DESC
        LIMIT ?
        """,
        (batch_size,),
    ).fetchall()
    return [(r["id"], r["title"]) for r in rows]


def wiki_url(title: str) -> str:
    # Wikipedia URLs use underscores for spaces; URL-encode the rest.
    safe = urllib.parse.quote(title.replace(" ", "_"), safe=":/_-()&,.")
    return f"https://en.wikipedia.org/wiki/{safe}"


async def run_loop(args) -> None:
    db = Path(args.db)
    thumbs_dir = Path(args.thumbs)
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    rendered_total = 0
    rendered_ok = 0
    t_start = time.time()
    conn = _connect(db)
    ensure_columns(conn)

    while True:
        batch = claim_batch(conn, args.batch_size)
        if not batch:
            print("[wiki-thumbs] no articles to render — sleeping 5min", flush=True)
            await asyncio.sleep(300)
            continue
        urls = [wiki_url(t) for _, t in batch]
        ids_by_url = {wiki_url(t): aid for aid, t in batch}

        try:
            results = await render_batch(urls, parallelism=args.parallelism, timeout_ms=args.timeout_ms)
        except Exception as e:
            print(f"[wiki-thumbs] batch error {type(e).__name__}: {e} — sleeping 30s", flush=True)
            await asyncio.sleep(30)
            continue

        for r in results:
            rendered_total += 1
            aid = ids_by_url.get(r.url)
            if aid is None:
                continue
            if r.ok and r.thumb_bytes:
                try:
                    thumb_path = save_thumb(r.thumb_bytes, r.url, thumbs_dir)
                    conn.execute(
                        "UPDATE articles SET thumb_path = ? WHERE id = ?",
                        (str(thumb_path), aid),
                    )
                    rendered_ok += 1
                except Exception as e:
                    print(f"[wiki-thumbs] save error id={aid}: {e}", flush=True)
            else:
                # Tried-and-failed sentinel — exclude from future claims.
                conn.execute(
                    "UPDATE articles SET thumb_path = '' WHERE id = ? AND thumb_path IS NULL",
                    (aid,),
                )
        elapsed = time.time() - t_start
        rate = rendered_total / elapsed if elapsed else 0
        ok_pct = (rendered_ok / rendered_total * 100) if rendered_total else 0
        print(
            f"[wiki-thumbs] processed={rendered_total} ok={rendered_ok} ({ok_pct:.0f}%) "
            f"rate={rate:.2f}/s elapsed={elapsed/60:.1f}m",
            flush=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(WIKI_DB))
    ap.add_argument("--thumbs", default=str(WIKI_THUMBS))
    # Modest parallelism — Wikipedia has soft rate limits per IP.
    ap.add_argument("--parallelism", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--timeout-ms", type=int, default=15000)
    args = ap.parse_args()
    asyncio.run(run_loop(args))


if __name__ == "__main__":
    main()
