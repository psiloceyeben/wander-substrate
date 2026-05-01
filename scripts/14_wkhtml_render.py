#!/usr/bin/env python3
"""Parallel fast-lane thumb renderer using wkhtmltoimage.

Targets cards with low JS density (likely static handmade pages — most of the
corpus). Runs alongside the Playwright pipeline (scripts/13_thumb_render.py),
which handles JS-heavy sites. wkhtmltoimage is 5-10x faster per render with
no browser overhead — process spawn per render, no Playwright orchestration.

Run:
    python3 scripts/14_wkhtml_render.py --parallelism 4 --batch-size 16
"""
import argparse, asyncio, hashlib, io, os, sqlite3, sys, time
from pathlib import Path
from PIL import Image

THUMB_W, THUMB_H = 540, 960
THUMB_QUALITY = 75


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def claim_batch(conn, batch_size, max_scripts=8):
    """Take cards with low JS density (wkhtml handles these well).

    Excludes thumb_path='' (tried-and-failed sentinel) — without this, dead URLs
    re-enter the queue every batch and starve fresh cards."""
    rows = conn.execute(
        '''
        SELECT id, url FROM cards
        WHERE is_useful_content = 1
          AND thumb_path IS NULL
          AND COALESCE(n_external_scripts, 0) < ?
        ORDER BY COALESCE(thumb_priority, 0) DESC, composite_score DESC
        LIMIT ?
        ''',
        (max_scripts, batch_size),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


async def render_one(url, out_dir, timeout_s=10):
    h = url_hash(url)
    tmp_png = out_dir / f'{h}.tmp.png'
    final_webp = out_dir / f'{h}.webp'
    proc = await asyncio.create_subprocess_exec(
        'wkhtmltoimage',
        '--quiet',
        '--width', str(THUMB_W),
        '--height', str(THUMB_H),
        '--javascript-delay', '300',
        '--format', 'png',
        '--load-error-handling', 'ignore',
        '--load-media-error-handling', 'ignore',
        url, str(tmp_png),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try: proc.kill()
        except Exception: pass
        return None
    if not tmp_png.exists() or tmp_png.stat().st_size < 500:
        return None
    try:
        img = Image.open(tmp_png).convert('RGB')
        img.save(final_webp, format='WebP', quality=THUMB_QUALITY, method=4)
        tmp_png.unlink()
        return final_webp
    except Exception:
        return None


async def worker(name, conn, out_dir, batch_size, timeout_s, sem):
    rendered_total = 0
    rendered_ok = 0
    t_start = time.time()
    while True:
        batch = claim_batch(conn, batch_size)
        if not batch:
            await asyncio.sleep(60)
            continue
        async def process(cid, url):
            async with sem:
                return cid, url, await render_one(url, out_dir, timeout_s)
        results = await asyncio.gather(*[process(cid, url) for cid, url in batch])
        for cid, url, p in results:
            rendered_total += 1
            if p is not None:
                conn.execute('UPDATE cards SET thumb_path = ? WHERE id = ?', (str(p), cid))
                rendered_ok += 1
            else:
                conn.execute("UPDATE cards SET thumb_path = '' WHERE id = ? AND thumb_path IS NULL", (cid,))
        elapsed = time.time() - t_start
        rate = rendered_total / elapsed if elapsed else 0
        ok_pct = (rendered_ok / rendered_total * 100) if rendered_total else 0
        print(f'[wkhtml-{name}] processed={rendered_total} ok={rendered_ok} ({ok_pct:.0f}%) rate={rate:.2f}/s', flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='data/wander.db')
    ap.add_argument('--thumbs', default='data/thumbs')
    ap.add_argument('--parallelism', type=int, default=4)
    ap.add_argument('--batch-size', type=int, default=16)
    ap.add_argument('--timeout-s', type=int, default=10)
    args = ap.parse_args()
    out_dir = Path(args.thumbs); out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db, isolation_level=None, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    sem = asyncio.Semaphore(args.parallelism)
    await worker('w1', conn, out_dir, args.batch_size, args.timeout_s, sem)


if __name__ == '__main__':
    asyncio.run(main())
