# The `cards` Schema Contract

The substrate's renderer consumes a JavaScript array of objects with this shape.
Every layout adapter must produce JSON in which `cards` matches this contract.

## Required fields

| field | type | example | meaning |
|---|---|---|---|
| `id` | INTEGER | `12345` | Stable, unique within the corpus. Used for raycast picking, modal lookup, thumb URL keying. |
| `host` | STRING | `"example.com"` or `"Anarchism"` | Display name. URL host for websites; article title for wiki; product name for marketplaces. Shown in hover label and modal header. |
| `category` | STRING | `"tech"` | One of the angular-sector buckets your classifier produces. Drives sector placement and color. |
| `score` | NUMBER | `4.2` | Quality / importance / size — drives radial position within the sector and building height in 3D. Score is roughly normalized to a `-5..+10` range; outside that range, things may not render legibly. |
| `x` | NUMBER | `1234.5` | Layout x-coordinate, in arbitrary world units. The layout pipeline produces these. |
| `y` | NUMBER | `-678.9` | Layout y-coordinate, same scale. |

## Recommended fields

| field | type | example | meaning |
|---|---|---|---|
| `era` | STRING | `"midweb"` | Concentric-band axis. Discrete bucket (typically 5 values) used as a radial sub-band within each category. For websites: `old_web / midweb / template / seo_hardened / modern_spa`. For wiki: `stub / start / c_class / b_class / fa_class`. For papers: decade buckets. Used by `/game`'s heatmap and stream paradigms. |
| `language` | STRING | `"en"` | Language tag. Used by Set B filter and the `types` layout (8-language sectors). For non-multilingual corpora, set to `"en"` everywhere. |
| `site_type` | STRING | `"hobbyist"` | Sub-sector axis (typically 4-6 values). Within a category, divides the angular slice into smaller districts. For websites: `hobbyist / blog / business / educational / government / unknown`. For wiki: `stub / short / medium / long` by article size. |

## Optional fields

| field | type | example | meaning |
|---|---|---|---|
| `wc` | INTEGER | `3500` | Word count. Drives the bubble chart's y-axis (`y = log(wc)`). For non-textual corpora, omit or substitute another numeric quality (file size, item count, comment count). |
| `thumb_path` | STRING | `"data/thumbs/abc123.webp"` | Set by the thumb pipeline once a screenshot has been rendered. NULL = not yet rendered; empty string = tried-and-failed sentinel. |
| `has_thumb` | BOOLEAN | `true` | Auto-populated by `/api/game/region` from the thumbed-IDs cache; not stored in the layout JSON itself. Drives the gold-marker rendering in /world. |

## Top-level layout JSON

The complete layout JSON consumed by the API:

```json
{
  "mode": "categories",                     // identifier ("categories", "wiki", etc.)
  "extent": 60000,                          // outer radius for camera bounds
  "center": [0, 0],                         // origin, almost always (0, 0)
  "cards": [ ... ],                         // array of card objects (above)
  "labels": [
    {"text": "tech",  "x": 0, "y": -60240, "kind": "category", "color": "#7aa9d8", "angle_deg": -90},
    {"text": "blog",  "x": ..., "y": ..., "kind": "district",  "color": "#a8a298", "parent": "tech"},
    ...
  ],
  "rings": [
    {"radius": 1500, "color": "rgba(255,255,255,0.05)", "label": "plaza"},
    ...
  ],
  "trails": [
    {"sector": "tech", "color": "#7aa9d8", "points": [[x1,y1], [x2,y2], ...]},
    ...
  ],
  "category_colors": {"tech": "#7aa9d8", "art": "#e0a060", ...},
  "era_band_order": ["old_web", "midweb", ...],
  "n_score_quartiles": 4,
  "sector_arc_deg": 30.0
}
```

### `labels`

Array of text labels rendered at fixed positions in world space.

- `kind: "category"` — sector labels at the outer perimeter. One per category.
- `kind: "district"` — sub-sector labels at mid-radius. Hidden by default; appear when zoom > 0.15.
- `kind: "site_type"` — axis labels (e.g., site_type ring names along the +X axis).
- `kind: "language"` — language sector labels in the `types` layout.

`angle_deg` is used by the curved-arc renderer (`drawCurvedLabel` in `GAME_JS`) to
arc text along sector boundaries.

### `rings`

Optional decorative concentric rings (e.g., plaza boundary, era band markers).
Rendered as alpha-blended circles in `/game`.

### `trails`

Optional radial roads — one per sector, from inner radius to outer rim. Used as
visual sector dividers in `/game` and as walkable trails in `/world`.

### `category_colors`

Map of category name → hex color string. Used by all paradigms for consistent
coloring across views. Renderer reads this on layout load.

### `era_band_order`

Ordered list of era values, innermost to outermost. Drives the era-axis in
heatmap, sankey, sunburst paradigms.

## Validation

Before serving, validate your layout JSON:

```bash
python3 -c "
import json
d = json.loads(open('data/your_corpus/layout.json').read())
required_top = {'mode', 'extent', 'cards', 'labels', 'category_colors'}
missing = required_top - set(d.keys())
assert not missing, f'missing top-level keys: {missing}'
required_card = {'id', 'host', 'category', 'score', 'x', 'y'}
for c in d['cards'][:100]:
    missing = required_card - set(c.keys())
    assert not missing, f'card {c.get(\"id\")} missing: {missing}'
print(f'OK: {len(d[\"cards\"])} cards, {len(d[\"labels\"])} labels')
"
```
