"""Pre-compute the 12×12 inter-category linkage matrix from `link_edges`.

The full join over 9.5M edges × 4M cards × 2 takes ~1-2 minutes — way past
nginx's 30s upstream timeout. Run this offline (cron / refresh_layouts.sh) and
write the result as JSON to disk; the API endpoint reads the file.

Output: data/category_edges.json — shape `{src_cat: {dst_cat: count, ...}, ...}`

Run:
    python3 scripts/16_compute_category_edges.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

DB = Path("data/wander.db")
OUT = Path("data/category_edges.json")


def main() -> None:
    t0 = time.time()
    conn = sqlite3.connect(DB)
    print(f"[edges] running join (heavy — ~1-2 min for 9.5M edges × 4M cards)...", flush=True)
    rows = conn.execute(
        """
        SELECT c1.category, c2.category, COUNT(*) AS n
        FROM link_edges le
        JOIN cards c1 ON le.src_root = c1.registrable_root
        JOIN cards c2 ON le.dst_root = c2.registrable_root
        WHERE c1.is_useful_content = 1 AND c2.is_useful_content = 1
          AND c1.category IS NOT NULL AND c2.category IS NOT NULL
        GROUP BY c1.category, c2.category
        """
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for src, dst, n in rows:
        out.setdefault(src, {})[dst] = n
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    cells = sum(len(v) for v in out.values())
    total = sum(sum(v.values()) for v in out.values())
    print(
        f"[edges] wrote {OUT}: {cells} (cat,cat) cells, {total:,} total edges, {time.time() - t0:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
