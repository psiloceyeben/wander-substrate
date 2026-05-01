"""Classify each Wikipedia article into one of 12 top-level buckets, using
the per-article `categories` column (pipe-separated top-8 wiki categories
already extracted by 02_parse_dump.py).

Output: `category` column added to articles table; populated for every row.

Buckets (chosen for encyclopedic corpus, distinct from Wander Around's web
categories which are tuned for long-tail web — Wikipedia is different):

    science, history, geography, arts, technology, sports,
    biography, religion, society, entertainment, nature, misc

Method: keyword-set scoring against the article's wiki-categories field.
Each match against a keyword in a bucket's set scores +1 for that bucket.
The bucket with the highest score wins; ties broken by bucket order
(earlier in the list wins). Articles with no matches → `misc`.

The keyword sets aren't claiming to be exhaustive Wikipedia ontology — they
encode "what the bucket means to a human cartographer" so the resulting
spatial mandala has interpretable angular sectors.

Run:
    python3 scripts/wiki/03_classify.py
    python3 scripts/wiki/03_classify.py --reset   # wipe existing labels first
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

DB = Path("data/wiki/wiki.db")

# Keyword sets per bucket. Matched as case-insensitive substring against the
# pipe-separated `categories` field. Order matters for tie-breaks (earlier wins).
BUCKETS: list[tuple[str, list[str]]] = [
    ("science", [
        "Physics", "Chemistry", "Biology", "Mathematics", "Astronomy",
        "Geology", "Statistics", "Logic", "Cognitive science", "Neuroscience",
        "Microbiology", "Botany", "Zoology", "Genetics", "Ecology", "Anatomy",
        "Medicine", "Pharmacology", "Mathematic", "Scientist", "Theorem",
        "Equation", "Element", "Particle", "Molecule", "Atom", "Quantum",
        "Relativity", "Algebra", "Calculus", "Geometry",
    ]),
    ("history", [
        "History of", "Historical events", "Wars", "Battles", "Revolutions",
        "Dynasty", "Dynasties", "Empire", "Ancient", "Medieval", "Renaissance",
        "Civilizations", "Roman", "Greek", "Egyptian", "Mesopotamian",
        "Ottoman", "Soviet", "Holocaust", "Crusades", "Treaty", "Treaties",
        "Conflicts", "Imperial", "Colonial", "Cold War", "World War",
        "Century BC", "th century", "Centuries", "Archaeolog",
    ]),
    ("geography", [
        "Cities", "Towns", "Villages", "Countries", "Rivers", "Mountains",
        "Lakes", "Oceans", "Continent", "Provinces", "Districts", "Regions",
        "Islands", "Capitals", "Settlements", "Geography of", "Populated places",
        "Geographical features", "Geographic", "Borders", "Counties",
        "Municipalities", "States of", "Territories",
    ]),
    ("arts", [
        "Art ", "Painters", "Sculpture", "Painting", "Architecture",
        "Photography", "Photographers", "Literature", "Authors", "Novelists",
        "Poets", "Poems", "Plays", "Drama", "Theatre", "Theater",
        "Music", "Composers", "Musicians", "Singers", "Songwriters",
        "Albums", "Songs", "Visual arts", "Performing arts", "Dance",
        "Choreographers", "Designers", "Artists", "Films", "Filmmakers",
        "Directors", "Screenwriters", "Books", "Novels", "Short stories",
        "Operas", "Symphonies", "Sculptors",
    ]),
    ("technology", [
        "Computing", "Computer", "Software", "Hardware", "Engineering",
        "Internet", "Programming language", "Algorithms", "Cryptography",
        "Operating systems", "Databases", "Inventions", "Inventors",
        "Engineers", "Technology", "Mechanical", "Electrical",
        "Electronics", "Telecommunications", "Robotics", "Aerospace",
        "Civil engineering", "Industrial", "Patents", "Tech companies",
        "Spacecraft", "Aircraft", "Vehicles",
    ]),
    ("sports", [
        "Sportspeople", "Athletes", "Footballers", "Football", "Basketball",
        "Soccer", "Tennis", "Olympics", "Olympic", "Athletics", "Boxing",
        "Wrestling", "Baseball", "Cricket", "Rugby", "Hockey",
        "Skiing", "Swimming", "Cycling", "Golf", "Volleyball",
        "Sports clubs", "Sports teams", "Sports", " sport",
        "FIFA", "NBA", "NFL", "Marathon",
    ]),
    ("religion", [
        "Christianity", "Christian", "Catholic", "Protestant", "Orthodox",
        "Islam", "Muslim", "Mosque", "Buddhism", "Buddhist",
        "Judaism", "Jewish", "Hindu", "Hinduism", "Sikh",
        "Bible", "Quran", "Torah", "Theology", "Spirituality",
        "Saints", "Popes", "Bishops", "Religion", "Religious",
        "Churches", "Mosques", "Temples", "Synagogues", "Mythology",
        "Mythological", "Deity", "Deities", "Gods", "Goddesses",
    ]),
    ("society", [
        "Politics", "Politicians", "Government", "Governance", "Law",
        "Lawyers", "Judges", "Economics", "Economists", "Sociology",
        "Sociologists", "Education", "Schools", "Universities", "Colleges",
        "Politicians of", "Political parties", "Elections", "Treaties",
        "Diplomacy", "Diplomats", "Activists", "Activism", "Philosophy",
        "Philosophers", "Ethics", "Public policy", "Civil rights",
        "Human rights", "Feminism", "Communism", "Socialism", "Anarchism",
        "Capitalism", "Liberalism", "Conservatism",
    ]),
    ("entertainment", [
        "Television", "TV series", "TV shows", "Animated", "Cartoons",
        "Anime", "Manga", "Video games", "Computer games", "Game designers",
        "Celebrities", "Actresses", "Actors", "Comedians", "Reality television",
        "Game shows", "Sitcoms", "Movies", "Hollywood",
        "Pornographic", "Talk shows",
    ]),
    ("nature", [
        "Animals", "Mammals", "Birds", "Reptiles", "Fish",
        "Amphibians", "Insects", "Arachnids", "Plants", "Flora",
        "Trees", "Flowers", "Fungi", "Bacteria", "Species",
        "Ecosystems", "Biodiversity", "Wildlife", "Conservation",
        "National parks", "Nature reserves", "Forests", "Deserts",
        "Wetlands", "Climate", "Weather", "Storms",
    ]),
    ("biography", [
        # Processed last because biographical categories are extremely broad
        # ("Living people", "Births in YYYY") and would overpower other buckets.
        "Living people", "births", "deaths", "People from", "20th-century",
        "21st-century", "19th-century", "18th-century", "Members of", "Recipients of",
        "Alumni of", "Fellows of", "by occupation",
    ]),
    ("misc", []),  # fallback
]


def classify(categories: str) -> str:
    """Score each bucket by keyword matches; return best match (or 'misc')."""
    if not categories:
        return "misc"
    cats_lower = categories.lower()
    best_bucket = "misc"
    best_score = 0
    for bucket, keywords in BUCKETS:
        if not keywords:
            continue
        score = sum(1 for kw in keywords if kw.lower() in cats_lower)
        if score > best_score:
            best_score = score
            best_bucket = bucket
    return best_bucket


def ensure_category_column(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    if "category" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN category TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)")
        conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--reset", action="store_true",
                    help="Clear existing category labels first (re-run from scratch)")
    ap.add_argument("--batch", type=int, default=20000)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, isolation_level=None, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_category_column(conn)
    if args.reset:
        conn.execute("UPDATE articles SET category = NULL")
        print("[classify] reset — all labels cleared", flush=True)

    total = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE category IS NULL AND is_disambig = 0"
    ).fetchone()[0]
    print(f"[classify] {total:,} articles to process", flush=True)
    if not total:
        return

    t0 = time.time()
    done = 0
    counts: dict[str, int] = {b: 0 for b, _ in BUCKETS}
    while True:
        rows = conn.execute(
            """
            SELECT id, categories FROM articles
            WHERE category IS NULL AND is_disambig = 0
            LIMIT ?
            """,
            (args.batch,),
        ).fetchall()
        if not rows:
            break
        updates = []
        for rid, cats in rows:
            label = classify(cats or "")
            counts[label] += 1
            updates.append((label, rid))
        conn.executemany("UPDATE articles SET category = ? WHERE id = ?", updates)
        done += len(rows)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        pct = 100 * done / total if total else 0
        print(
            f"[classify] {done:,}/{total:,} ({pct:.1f}%) rate={rate:.0f}/s elapsed={elapsed/60:.1f}m",
            flush=True,
        )

    print("\n[classify] distribution:", flush=True)
    for b, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = 100 * c / done if done else 0
        print(f"  {b:14s} {c:>9,}  ({pct:5.1f}%)", flush=True)
    print(f"\n[classify] total {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
