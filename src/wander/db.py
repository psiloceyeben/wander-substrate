"""SQLite persistence layer.

One file (`data/wander.db`) holds:
  - cards          : every rendered URL with extracted features + score
  - link_edges     : (src_root, dst_root) outbound-link edges
  - render_queue   : shuffled URL pool with render status (for resumable bulk render)

Postgres migration deferred until concurrent-writer load justifies it. SQLite handles
hundreds of thousands of rows + read-heavy serving fine for v0.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "wander.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT NOT NULL UNIQUE,
    host                TEXT NOT NULL,
    registrable_root    TEXT NOT NULL,
    title               TEXT,
    snippet             TEXT,
    era                 TEXT,
    composite_score     REAL NOT NULL DEFAULT 0,
    anti_seo_score      REAL DEFAULT 0,
    hand_made_score     REAL DEFAULT 0,
    word_count          INTEGER DEFAULT 0,
    n_external_scripts  INTEGER DEFAULT 0,
    n_meta_tags         INTEGER DEFAULT 0,
    h1_count            INTEGER DEFAULT 0,
    image_count         INTEGER DEFAULT 0,
    iframe_count        INTEGER DEFAULT 0,
    has_old_html_tags   INTEGER DEFAULT 0,
    has_email_contact   INTEGER DEFAULT 0,
    has_template_cms    INTEGER DEFAULT 0,
    has_amp             INTEGER DEFAULT 0,
    has_analytics       INTEGER DEFAULT 0,
    has_ad_network      INTEGER DEFAULT 0,
    is_useful_content   INTEGER NOT NULL DEFAULT 1,
    invalid_reason      TEXT,
    thumb_path          TEXT,
    final_url           TEXT,
    render_status       INTEGER,
    render_ms           INTEGER,
    rendered_at         INTEGER NOT NULL,
    x                   REAL,
    y                   REAL,
    cluster_id          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cards_score   ON cards(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_cards_useful  ON cards(is_useful_content);
CREATE INDEX IF NOT EXISTS idx_cards_root    ON cards(registrable_root);
CREATE INDEX IF NOT EXISTS idx_cards_era     ON cards(era);
CREATE INDEX IF NOT EXISTS idx_cards_cluster ON cards(cluster_id);

CREATE TABLE IF NOT EXISTS link_edges (
    src_root TEXT NOT NULL,
    dst_root TEXT NOT NULL,
    PRIMARY KEY (src_root, dst_root)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON link_edges(src_root);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON link_edges(dst_root);

CREATE TABLE IF NOT EXISTS render_queue (
    url             TEXT PRIMARY KEY,
    host            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER DEFAULT 0,
    last_error      TEXT,
    last_attempt_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON render_queue(status);
"""


@contextmanager
def connect(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    # Pragmas for write-throughput + read-concurrency.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-32000")  # 32 MB
    conn.execute("PRAGMA busy_timeout=15000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def upsert_card(conn: sqlite3.Connection, *, url: str, host: str, registrable_root: str,
                features: dict, scores: dict, era: str,
                final_url: Optional[str] = None, render_status: Optional[int] = None,
                render_ms: Optional[int] = None, thumb_path: Optional[str] = None,
                rendered_at: int = 0) -> int:
    """Insert or replace a card by URL. Returns row id."""
    cur = conn.execute(
        """
        INSERT INTO cards (url, host, registrable_root, title, snippet, era,
            composite_score, anti_seo_score, hand_made_score,
            word_count, n_external_scripts, n_meta_tags, h1_count, image_count, iframe_count,
            has_old_html_tags, has_email_contact, has_template_cms, has_amp,
            has_analytics, has_ad_network,
            is_useful_content, invalid_reason,
            thumb_path, final_url, render_status, render_ms, rendered_at)
        VALUES (?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title              = excluded.title,
            snippet            = excluded.snippet,
            era                = excluded.era,
            composite_score    = excluded.composite_score,
            anti_seo_score     = excluded.anti_seo_score,
            hand_made_score    = excluded.hand_made_score,
            word_count         = excluded.word_count,
            n_external_scripts = excluded.n_external_scripts,
            n_meta_tags        = excluded.n_meta_tags,
            h1_count           = excluded.h1_count,
            image_count        = excluded.image_count,
            iframe_count       = excluded.iframe_count,
            has_old_html_tags  = excluded.has_old_html_tags,
            has_email_contact  = excluded.has_email_contact,
            has_template_cms   = excluded.has_template_cms,
            has_amp            = excluded.has_amp,
            has_analytics      = excluded.has_analytics,
            has_ad_network     = excluded.has_ad_network,
            is_useful_content  = excluded.is_useful_content,
            invalid_reason     = excluded.invalid_reason,
            thumb_path         = excluded.thumb_path,
            final_url          = excluded.final_url,
            render_status      = excluded.render_status,
            render_ms          = excluded.render_ms,
            rendered_at        = excluded.rendered_at
        """,
        (
            url, host, registrable_root,
            (features.get("title") or "")[:512],
            (features.get("meta_description") or "")[:1024],
            era,
            float(scores.get("composite", 0.0)),
            float(scores.get("anti_seo", 0.0)),
            float(scores.get("hand_made", 0.0)),
            int(features.get("word_count", 0) or 0),
            int(features.get("n_external_scripts", 0) or 0),
            int(features.get("n_meta_tags", 0) or 0),
            int(features.get("h1_count", 0) or 0),
            int(features.get("image_count", 0) or 0),
            int(features.get("iframe_count", 0) or 0),
            int(bool(features.get("has_old_html_tags"))),
            int(bool(features.get("has_email_contact"))),
            int(bool(features.get("has_template_cms"))),
            int(bool(features.get("has_amp"))),
            int(bool(features.get("has_analytics"))),
            int(bool(features.get("has_ad_network"))),
            int(bool(features.get("is_useful_content", True))),
            features.get("invalid_reason") or "",
            thumb_path,
            final_url,
            render_status,
            render_ms,
            int(rendered_at),
        ),
    )
    return cur.lastrowid


def upsert_edges(conn: sqlite3.Connection, src_root: str, dst_roots: list[str]) -> int:
    if not dst_roots:
        return 0
    payload = [(src_root, d) for d in dst_roots if d and d != src_root]
    cur = conn.executemany("INSERT OR IGNORE INTO link_edges (src_root, dst_root) VALUES (?, ?)", payload)
    return cur.rowcount


def feed_next(conn: sqlite3.Connection, *, limit: int = 20, min_score: float = -3.0,
              exclude_ids: Optional[list] = None) -> list[sqlite3.Row]:
    """Return next N cards above min_score, score-weighted random.

    The previous version used `id > cursor` against `ORDER BY score DESC, id ASC`
    — which silently skipped most of the corpus after the first page (cards with
    low ids in score-DESC order are mostly < cursor, so they got filtered out
    forever, and the remaining pool kept resurfacing the same top batch). This
    version takes the top-1500 by score, randomizes within that pool, and
    excludes already-seen ids — which gives variety per session AND across
    reloads, and visits the long tail without repeating.

    `exclude_ids` is a list of card IDs the client has already shown.
    Capped at 800 to stay within SQLite's default placeholder limit (999).
    """
    inner_sql = """
        SELECT id, url, host, registrable_root, title, snippet, era,
               composite_score, thumb_path, final_url, word_count
        FROM cards
        WHERE is_useful_content = 1 AND composite_score >= ?
    """
    params: list = [min_score]
    if exclude_ids:
        excl = list(exclude_ids)[:800]
        if excl:
            placeholders = ",".join("?" * len(excl))
            inner_sql += f" AND id NOT IN ({placeholders})"
            params.extend(excl)
    inner_sql += " ORDER BY composite_score DESC LIMIT 1500"
    sql = f"SELECT * FROM ({inner_sql}) ORDER BY RANDOM() LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def get_card(conn: sqlite3.Connection, card_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


def stats(conn: sqlite3.Connection) -> dict:
    out = {}
    out["total_cards"] = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    out["useful_cards"] = conn.execute("SELECT COUNT(*) FROM cards WHERE is_useful_content = 1").fetchone()[0]
    out["edges"] = conn.execute("SELECT COUNT(*) FROM link_edges").fetchone()[0]
    out["queue_pending"] = conn.execute("SELECT COUNT(*) FROM render_queue WHERE status = 'pending'").fetchone()[0]
    out["queue_done"] = conn.execute("SELECT COUNT(*) FROM render_queue WHERE status = 'rendered'").fetchone()[0]
    out["queue_failed"] = conn.execute("SELECT COUNT(*) FROM render_queue WHERE status = 'failed'").fetchone()[0]
    return out
