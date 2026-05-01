# Wander

**A substrate for making large structured corpora walkable as 3D space.**

Point this at a database of websites, articles, products, papers, posts, or any other
addressable items with classification and scoring axes. The substrate produces a
walkable mandala-shaped 3D environment where each item is a building, sectors are
categories, radial position encodes score, and a body can move through the result.

The reference deployment is at **[wanderaround.io](https://wanderaround.io)** (when
the domain is registered) — a hub running this substrate over the long-tail web (~4M
websites) and English Wikipedia (~6.84M articles), with `/world`, `/game`, `/rendered`,
`/wiki`, and `/4d` (sphere projection) surfaces.

This repository is the substrate itself. Use it to walkable-ify any corpus you care
about. Documentation is in [`docs/`](docs/).

---

## What is this, exactly

The substrate has four orthogonal layers:

1. **Layout pipeline** (`scripts/10_layout_ring.py`) — computes 2D positions for every
   item via wedge-fill polar layout. Variable-arm pinwheel: each category is an angular
   sector; each sector is sized by card count; cards are placed via a polar ring fill
   with walkable angular alleys and radial roads.
2. **Renderer** (`api/main.py`'s `WORLD_JS`, `GAME_JS`, `FOURD_JS`) — Three.js for the
   3D world and 4D sphere; canvas 2D for the mandala/heatmap/treemap/sankey/sunburst/
   histogram/bubble/stream paradigms. All paradigms consume the same `cards` array.
3. **Filter pipeline** (`GAME_JS`'s Set B chips + Set A controls) — six-dimensional
   AND-stacked multi-select on category / language / era / site_type / score quartile /
   TLD, plus continuous score threshold and display tweaks.
4. **Thumb pipeline** (`scripts/13_thumb_render.py` + `14_wkhtml_render.py`) —
   background workers render 320×480 WebP screenshots of each item's URL, surfaced as
   building facades around the player.

These four layers are independent. New corpora plug in by writing a classifier and a
layout adapter; new visual paradigms plug in by writing a 100–250 LOC canvas/three.js
function; new filter dimensions plug in by adding ~30 LOC.

---

## Quickstart: walk a corpus you already have

The corpus needs a SQLite database with a `cards`-shaped table:

| column | type | required | meaning |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | yes | stable item id |
| `host` | TEXT | yes | display name (URL host, article title, product name) |
| `category` | TEXT | yes | one of N angular-sector buckets |
| `score` | REAL | yes | quality / importance — drives radial position |
| `era` | TEXT | recommended | concentric-band axis (e.g. age/quality class) |
| `language` | TEXT | recommended | for multi-lingual corpora |
| `site_type` | TEXT | recommended | sub-sector axis |
| `word_count` | INTEGER | optional | drives bubble chart, etc. |
| `thumb_path` | TEXT | optional | populated by thumb pipeline |

See [`docs/SCHEMA.md`](docs/SCHEMA.md) for full contract.

Three steps to walkable:

```bash
# 1. Classify your corpus into ~12 angular buckets (write a script per docs/INTEGRATION.md)
python3 scripts/your_corpus/classify.py

# 2. Run the layout pipeline (mostly corpus-agnostic; tune R_INNER / R_OUTER for your scale)
python3 scripts/10_layout_ring.py --db data/your_corpus.db --out-dir data/

# 3. Boot the API server. Visit /world.
uvicorn api.main:app --host 0.0.0.0 --port 8050
```

That is the whole loop. The reference end-to-end example, ingesting English Wikipedia
in ~12 minutes of compute time, lives in [`scripts/example_wiki/`](scripts/example_wiki/).

---

## What this is not

- **Not a search engine.** No keyword retrieval. The substrate organizes by *spatial*
  proximity (angular = ontology, radial = depth) instead of textual similarity.
- **Not a metaverse.** The space is generated *from* the corpus's intrinsic structure,
  not imposed on top of it as a virtual mall. There is no skeuomorphism.
- **Not a knowledge graph.** Graph relationships exist (in the chord-diagram paradigm)
  but the substrate's primary primitive is *terrain*, not nodes-and-edges.
- **Not a data visualization tool.** The output is *inhabited*, not viewed.
  Visualizations get closed; places get remembered. See [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).

---

## Status

**v0.1.0 — initial public release.**

- Substrate is research-quality, single-server tested. APIs may change pre-1.0.
- Walking is real. Two demonstration corpora (long-tail web, Wikipedia) are running at
  the reference deployment.
- Production hardening (TLS, rate-limiting on hub access, multi-tenant deployment
  patterns) is out of scope for this release.

---

## Documentation

- [**`docs/ARCHITECTURE.md`**](docs/ARCHITECTURE.md) — The four-layer substrate, the
  JSON contract between layout and renderer, why the layers are independent.
- [**`docs/INTEGRATION.md`**](docs/INTEGRATION.md) — Step-by-step recipe to add a
  new corpus, with the reference Wikipedia adapter as worked example.
- [**`docs/SCHEMA.md`**](docs/SCHEMA.md) — The `cards` table contract; what fields
  are required vs optional; how to derive each from your corpus.
- [**`docs/PHILOSOPHY.md`**](docs/PHILOSOPHY.md) — What this substrate *is*, in
  philosophical terms; why "place" rather than "visualization"; the dimensional
  argument; why the speed of the reference build (36 hours from concept to two
  walkable corpora) is the substantive claim.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The most useful contributions, in priority
order:

1. New corpus adapters (`scripts/example_*/`) — every classified-and-scored public
   corpus is a candidate.
2. New visual paradigms (canvas/three.js render functions in `GAME_JS` / `FOURD_JS`)
   — chord, force-directed, parallel-coordinates, geographic-overlay, etc.
3. New filter dimensions (Set B chip groups in `GAME_JS`) — tune to whichever facets
   your corpus exposes.
4. Self-serve onboarding (a single config file that generates classifier+layout from
   declarative corpus metadata).

---

## License

MIT. See [`LICENSE`](LICENSE).
