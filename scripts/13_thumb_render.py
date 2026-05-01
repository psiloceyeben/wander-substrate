"""Background thumb renderer: pick cards without thumbs, render with Playwright,
save WebP, update DB. Runs continuously as a low-priority service.

Selection priority: highest composite_score first → most-likely-to-be-viewed cards
get real thumbs first; lower-score cards trickle in over time.

Run:
    python3 scripts/13_thumb_render.py --parallelism 2 --batch-size 16

Idempotent: skips cards that already have thumb_path set. Safe to re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wander.rendering import render_batch, save_thumb


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_priority_column(conn):
    """Add thumb_priority column if missing — set when a card gets viewed in /game modal."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()]
    if "thumb_priority" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN thumb_priority INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_thumb_priority ON cards(thumb_priority)")
        conn.commit()


def claim_batch(conn, batch_size: int) -> list[tuple[int, str]]:
    """Claim N cards without thumbs. Order: thumb_priority DESC (recently-viewed cards
    first), then composite_score DESC (high-score next). This means cards a user
    actually opens in the modal jump to the front of the rendering queue.

    Excludes cards with thumb_path = '' (the "tried-and-failed" sentinel). Without this
    filter, dead URLs got re-claimed every batch and choked throughput at ~5% success."""
    rows = conn.execute(
        """
        SELECT id, url FROM cards
        WHERE is_useful_content = 1
          AND thumb_path IS NULL
        ORDER BY COALESCE(thumb_priority, 0) DESC, composite_score DESC
        LIMIT ?
        """,
        (batch_size,),
    ).fetchall()
    return [(r["id"], r["url"]) for r in rows]


async def run_loop(args):
    db = Path(args.db)
    thumbs_dir = Path(args.thumbs)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    rendered_total = 0
    rendered_ok = 0
    t_start = time.time()

    conn = _connect(db)
    ensure_priority_column(conn)

    while True:
        batch = claim_batch(conn, args.batch_size)
        if not batch:
            print("[thumbs] no cards without thumbs — sleeping 5min", flush=True)
            await asyncio.sleep(300)
            continue

        urls = [u for _, u in batch]
        ids_by_url = {u: cid for cid, u in batch}

        try:
            results = await render_batch(urls, parallelism=args.parallelism, timeout_ms=args.timeout_ms)
        except Exception as e:
            print(f"[thumbs] batch error {type(e).__name__}: {e} — sleeping 30s", flush=True)
            await asyncio.sleep(30)
            continue

        for r in results:
            rendered_total += 1
            cid = ids_by_url.get(r.url)
            if cid is None:
                # final URL after redirects might differ — try to find by URL anyway
                continue
            if r.ok and r.thumb_bytes:
                try:
                    thumb_path = save_thumb(r.thumb_bytes, r.url, thumbs_dir)
                    conn.execute(
                        "UPDATE cards SET thumb_path = ? WHERE id = ?",
                        (str(thumb_path), cid),
                    )
                    rendered_ok += 1
                except Exception as e:
                    print(f"[thumbs] save error id={cid}: {e}", flush=True)
            else:
                # mark thumb_path as empty string so we don't re-attempt this URL forever;
                # we use empty-string as a "tried and failed" sentinel
                conn.execute(
                    "UPDATE cards SET thumb_path = '' WHERE id = ? AND thumb_path IS NULL",
                    (cid,),
                )

        elapsed = time.time() - t_start
        rate = rendered_total / elapsed if elapsed else 0
        ok_pct = (rendered_ok / rendered_total * 100) if rendered_total else 0
        print(
            f"[thumbs] processed={rendered_total} ok={rendered_ok} ({ok_pct:.0f}%) "
            f"rate={rate:.2f}/s elapsed={elapsed/60:.1f}m",
            flush=True,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/wander.db")
    ap.add_argument("--thumbs", default="data/thumbs")
    ap.add_argument("--parallelism", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--timeout-ms", type=int, default=15000)
    args = ap.parse_args()
    asyncio.run(run_loop(args))


if __name__ == "__main__":
    main()
