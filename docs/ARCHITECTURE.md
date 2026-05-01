# Architecture

The substrate has four orthogonal layers. Each can be modified, replaced, or extended
independently of the others. This document explains the contract between them.

```
┌─────────────────────────────────────────────────────────────┐
│  Corpus     SQLite cards table (id, host, category, score, ..) │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Layout     scripts/10_layout_ring.py                          │
│  pipeline   wedge_fill polar layout → layout_*.json            │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  API        api/main.py                                        │
│             /api/game/region, /api/game/layout, /api/stats     │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Render     WORLD_JS (3D walk), GAME_JS (2D + 8 paradigms),   │
│  paradigms  FOURD_JS (sphere), RENDERED_JS (compact subset)    │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Filter     visibleCards = cards.filter(passesFilters)         │
│  pipeline   Set A (display) + Set B (6-dim chips)              │
└─────────────────────────────────────────────────────────────┘
```

## The JSON contract

The single inflection point in the architecture is the **layout JSON file**
(`data/layout_*.json`). Anything upstream (DB, classifier, layout pipeline) must
produce a JSON of this shape; anything downstream (API, renderer, filter pipeline)
consumes only this shape:

```json
{
  "mode": "categories",                         // identifies the layout
  "extent": 60000,                              // outer radius for camera bounds
  "center": [0, 0],
  "cards": [
    {
      "id": 12345,
      "host": "example.com",
      "score": 4.2,
      "era": "midweb",
      "language": "en",
      "category": "tech",
      "site_type": "hobbyist",
      "x": 1234.5,
      "y": -678.9,
      "wc": 3500
    },
    ...
  ],
  "labels": [
    {"text": "tech", "x": 0, "y": -60240, "kind": "category", "color": "#7aa9d8"},
    ...
  ],
  "rings": [...],                                // optional decorative rings
  "trails": [...],                               // optional radial roads
  "category_colors": {"tech": "#7aa9d8", ...},
  "era_band_order": ["old_web", "midweb", ...],
  "n_score_quartiles": 4
}
```

The `cards` array is the substrate's working set. Every render paradigm (mandala,
heatmap, treemap, sankey, sunburst, histograms, bubble, stream, sphere) consumes
this array. Adding a new paradigm requires only writing a function that takes
`cards` and produces visible output — no upstream changes, no schema migrations.

## The four layers, in detail

### 1. Layout pipeline (`scripts/10_layout_ring.py`)

The core algorithm is **wedge-fill polar layout**:

1. Read all cards from the DB.
2. Group by leaf cell `(category, sub_sector, era_band, score_quartile)`.
3. For each cell, compute its angular range and radial range.
4. Place cards within the cell using `wedge_fill_positions` — concentric rings
   spaced at walkable distances, with angular alleys every N buildings and a wider
   radial road every M rings.
5. Defensive tail loop extends inward when a cell over-packs.

Key parameters (`_LAYOUT_OPTS` dict, configurable per corpus):

- `r_outer_buffer` — multiplier in per-category outer radius computation. Lower =
  tighter packing. /world uses 5.0; /wiki uses 3.0.
- `r_outer_min` — floor for sparse-category sectors. /world uses 5500; /wiki uses 3500.
- `where_extra` — SQL fragment appended to selection queries. Used for
  `--rendered-only` mode (only cards with thumb_path).

### 2. API (`api/main.py`)

FastAPI server. Routes:

- `/api/game/layout?mode=X&limit=N` — full layout JSON for mode X. Cached at the
  API process via mtime watch.
- `/api/game/region?mode=X&cx=&cy=&r=&limit=` — proximity query. Returns cards
  within radius r of (cx, cy). Used by 3D world's chunk loader.
- `/api/card/{id}` / `/api/card/{id}/thumb` — per-card metadata + WebP thumbnail.
- `/api/stats` — corpus-wide counts (total, useful, with_thumb, placed-per-mode).
- `/api/category-edges` — pre-computed inter-category linkage matrix (chord diagram).

The API is thin — most "endpoints" are just JSON streams from disk-cached files.

### 3. Render paradigms

**WORLD_JS** (3D walkable): Three.js scene with InstancedMesh of buildings on a
ground plane. Proximity-loaded chunks (only ~2500 cards near the player at any time).
Lazy WebP textures on the nearest 10 cards. Procedural facade fallback.

**GAME_JS** (2D mandala + 8 graph paradigms): canvas 2D with LOD-based renderer
(dot mode → simple rects → full buildings as zoom increases). The eight paradigms
are switchable via the Display panel: mandala, heatmap, treemap, sankey, sunburst,
histograms, bubble, stream, chord.

**FOURD_JS** (sphere): Three.js InstancedMesh of small spheres on a 2-sphere globe.
OrbitControls for spin and zoom. Source dropdown switches corpora live.

**RENDERED_JS** (compact subset): WORLD_JS string-substituted to read from
`layout_rendered.json` (only cards with WebP thumbnails). Tighter pinwheel.

### 4. Filter pipeline

Six-dimensional AND-stack of multi-select chip groups in GAME_JS:

- Category, Language, Era, Site-type, Score quartile, TLD.

Plus continuous score threshold (slider) and Set A display tweaks (sort, color-by,
tooltip detail, graph-type).

The filter projection is recomputed only on filter change (not per frame). All
paradigms consume the post-filter `visibleCards` projection. The filter pipeline is
*paradigm-invariant* — the same filter state means the same thing across mandala,
heatmap, treemap, etc.

## Why this factoring matters

The four layers are independent enough that:

- **Adding a new corpus** requires a new classifier + a new layout adapter (~200-300
  LOC each). Nothing else changes. The reference Wikipedia adapter is in
  `scripts/example_wiki/`; it took ~12 minutes of compute and ~12 minutes of human
  work to integrate.
- **Adding a new paradigm** requires a new canvas/three.js render function (~150-300
  LOC) + adding it to the dropdown. The filter pipeline applies for free.
- **Adding a new filter dimension** requires ~30 LOC: extend the chip-group config,
  extend `passesFilters`. All paradigms re-render through the new filter immediately.
- **Adding a new spatial encoding** (e.g., a hyperbolic-space projection) requires a
  new `layout_*.json` producer. Renderer can be reused or replaced as needed.

This is the architectural payoff. The substrate cost is paid once; corpora,
paradigms, filters, and dimensions accrete on top at near-constant marginal cost.
See [`PHILOSOPHY.md`](PHILOSOPHY.md) for the substantive argument that this factoring
is what makes "make any corpus walkable" tractable in hours rather than months.
