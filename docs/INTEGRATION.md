# Integrating a New Corpus

This is the practical recipe. The reference example is `scripts/example_wiki/` which
ingests the entire English Wikipedia (~6.84M articles) in roughly 12 minutes of
compute. Read it alongside this guide.

## Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- A SQLite database containing your corpus, with at minimum: a primary key, a
  display name (URL host or article title or product name), and any features you
  want to use as classification/scoring axes.
- (Optional) Playwright + wkhtmltoimage if you want screenshot rendering.

## The three steps

### Step 1 — Write a classifier

The substrate organizes corpora by *angular sector* (category) and *radial position*
(score). You decide the categories and the scoring function.

A classifier is a script that adds a `category` column to your corpus table.

**Reference example**: `scripts/example_wiki/03_classify.py` — assigns each Wikipedia
article to one of 12 encyclopedic buckets (science / history / geography / arts /
technology / sports / biography / religion / society / entertainment / nature / misc)
via keyword matching against the article's pre-extracted Wikipedia categories.

Pattern:

```python
BUCKETS = [
    ("bucket_a", ["keyword1", "keyword2", ...]),
    ("bucket_b", ["keyword3", ...]),
    ...
    ("misc", []),  # fallback
]

def classify(item) -> str:
    text_to_match = item['some_field'].lower()
    best_score = 0
    best_bucket = "misc"
    for bucket, keywords in BUCKETS:
        score = sum(1 for kw in keywords if kw.lower() in text_to_match)
        if score > best_score:
            best_score = score
            best_bucket = bucket
    return best_bucket
```

Then iterate your corpus, applying `classify`, writing the result back to a
`category` column.

**Choosing buckets**: you want *roughly 12* (more than 8 = good angular resolution;
fewer than 16 = each sector is still legible). They should be *roughly balanced*
in size (within 10× of each other; the substrate handles imbalance via per-category
R_OUTER but extreme skew is ugly). They should be *meaningful to your domain* — a
domain expert is a better classifier-author than a software engineer.

The classifier doesn't have to be keyword-based. A trained model, a pre-existing
taxonomy (Wikipedia's category graph, MeSH for medicine, Library of Congress
classification for books), or hand-curated heuristics all work.

### Step 2 — Write a layout adapter

The layout adapter wraps `scripts/10_layout_ring.py`'s helpers, reading from your
corpus's table instead of the default `cards` table.

**Reference example**: `scripts/example_wiki/04_layout_wiki.py` — reads from
`articles` table, scores by `log10(word_count + 1)`, eras derived from word_count
buckets, sub-sectors from per-category word_count quartiles. Imports
`wedge_fill_positions` and `compute_R_OUTER_from_counts` from the parent script via
`importlib`.

Pattern:

```python
import importlib.util
_ring = importlib.util.spec_from_file_location("ring", "scripts/10_layout_ring.py")
# ... load the module ...

# Override layout options for your corpus
_ring._LAYOUT_OPTS["r_outer_buffer"] = 3.0     # tighter packing
_ring._LAYOUT_OPTS["r_outer_min"] = 3500.0     # smaller sparse-cat floor

# Pull rows from your DB
rows = conn.execute("SELECT id, name, category, score_field, ... FROM your_table").fetchall()

# Build the layout JSON in the standard shape
groups = defaultdict(list)
for r in rows:
    cat = r["category"]
    era = derive_era(r)
    sub = derive_subsector(r)
    quartile = derive_quartile(r)
    groups[(cat, sub, era_idx, quartile)].append(r)

# Place each cell's cards via wedge_fill_positions
for key, group_rows in groups.items():
    positions = _ring.wedge_fill_positions(
        cell_inner_r, cell_outer_r,
        cell_min_angle, cell_arc,
        len(group_rows),
    )
    for (x, y), r in zip(positions, group_rows):
        cards_out.append({
            "id": r["id"],
            "host": r["name"],
            "category": cat,
            "score": ...,
            "era": era,
            "language": ...,
            "site_type": sub,
            "x": round(x, 1),
            "y": round(y, 1),
        })

# Write the layout JSON
out_path.write_text(json.dumps({
    "mode": "your_corpus",
    "extent": max_extent,
    "cards": cards_out,
    "labels": [...],
    "category_colors": {...},
    ...
}, separators=(",", ":")))
```

The JSON shape is documented in [`SCHEMA.md`](SCHEMA.md). Stick to the contract;
the renderer assumes it.

### Step 3 — Register and serve

Add your layout to `_LAYOUT_PATHS` in `api/main.py`:

```python
_LAYOUT_PATHS = {
    "categories":      DATA_DIR / "layout_categories.json",
    "wiki":            DATA_DIR / "wiki" / "layout_wiki.json",
    "your_corpus":     DATA_DIR / "your_corpus" / "layout.json",   # ← add this
}
```

Restart the API. Your corpus is now accessible via:

- `http://your-host/api/game/layout?mode=your_corpus` — full layout JSON
- `http://your-host/api/game/region?mode=your_corpus&cx=&cy=&r=` — proximity query
- `http://your-host/game?mode=your_corpus` — 2D mandala (the default `/game` page
  still uses the default layout; for a dedicated `/your_corpus` page, follow the
  `WIKI_PAGE` substitution pattern in `api/main.py`)

For a dedicated walkable page like `/wiki`, copy the `WIKI_PAGE` / `WIKI_JS` block
in `api/main.py` and substitute your corpus's mode name. This is roughly an 8-line
change.

## Optional: thumbnail rendering

If your corpus items are addressable by URL (websites, Wikipedia articles, products,
papers with PDFs), the thumb pipeline auto-renders 320×480 WebP screenshots:

- Add a `thumb_path` column to your table.
- Adapt `scripts/13_thumb_render.py` (Playwright) or `scripts/14_wkhtml_render.py`
  (wkhtmltoimage) — change the SQL query in `claim_batch` to read your table.
- The reference Wikipedia thumb script is `scripts/example_wiki/05_wiki_thumb_render.py`.
- Set up a systemd unit (templates in `systemd/`) so it runs continuously.

## Optional: corpus-specific filters

The default Set B filter dimensions are: category, language, era, site_type,
score_quartile, TLD. If your corpus has a different natural set (e.g. for academic
papers: discipline, year-decade, citation-quartile, journal-vs-conference,
open-access-bool, OA-license), edit `GAME_JS`'s filter chip groups. ~30 LOC per new
dimension.

## Validation

A correctly-integrated corpus passes these smoke tests:

```bash
# Layout is generated
ls -la data/your_corpus/layout.json   # should be ~5-50MB depending on N

# API serves it
curl 'http://localhost:8050/api/game/layout?mode=your_corpus&limit=10' | jq '.cards | length'
# → 10

# Region query works
curl 'http://localhost:8050/api/game/region?mode=your_corpus&cx=0&cy=0&r=2000&limit=5' | jq '.count'
# → ~5 (depending on your layout density at origin)

# Stats reflect placement
curl 'http://localhost:8050/api/stats' | jq '.placed.your_corpus'
# → number of cards in cache (populated after first region query)
```

Open `http://localhost:8050/game?mode=your_corpus` in a browser. You should see a
mandala with your corpus's category colors, sub-sectors, and items. If the items
cluster too tightly near the plaza, increase `r_outer_buffer`. If sectors don't
fill out, decrease it.

## When this isn't enough

Some corpora exceed what the default substrate handles cleanly:

- **>10M items**: subset to the top N by score for the spatial layout; keep the rest
  in the substrate as filterable population (see "modulated" tier in
  [`PHILOSOPHY.md`](PHILOSOPHY.md)).
- **Streaming corpora** (Twitter firehose, IoT sensors): adapt the stream graph
  paradigm with time-windowed materialization.
- **High-dimensional latent corpora** (LLM embeddings): replace the wedge-fill
  layout with a UMAP/t-SNE projection. The renderer accepts any (x, y) → JSON.

These are documented as future work; if you implement one cleanly, contribute it
back via a PR.
