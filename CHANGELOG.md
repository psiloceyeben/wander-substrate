# Changelog

## v0.1.0 — Initial public release

The first release of the Wander substrate. Research-quality, single-server tested.
Reference deployment at wanderaround.io running two demonstration corpora:
the long-tail web (~4M classified websites) and the entirety of English Wikipedia
(~6.84M classified articles).

### What's in the box

**Substrate (corpus-agnostic):**
- `scripts/10_layout_ring.py` — wedge-fill polar layout algorithm with variable
  per-category arms, 4-level hierarchical placement (category → site_type →
  era → score quartile), defensive tail handling for over-packed cells, real
  overlap audit.
- `scripts/13_thumb_render.py` — Playwright background renderer producing
  320×480 WebP screenshots; ~87% success rate on clean URLs, claim-batch SQL
  with proper failed-sentinel handling.
- `scripts/14_wkhtml_render.py` — wkhtmltoimage parallel renderer for
  static / low-JS sites; ~94% success rate.
- `scripts/16_compute_category_edges.py` — daily precompute of inter-category
  link matrix for the chord-diagram paradigm. SQL join over 9.5M+ edges.
- `src/wander/` — shared rendering helpers (facade.py, rendering.py).
- `api/main.py` — FastAPI server with all routes and embedded JS strings:
  - `WORLD_JS` — 3D walkable scene (Three.js, InstancedMesh, proximity chunk
    loading, lazy WebP textures, gold-marker rendering, info modal).
  - `GAME_JS` — 2D mandala with eight visual paradigms (mandala, heatmap,
    treemap, sankey, sunburst, histograms, bubble, stream) and chord-when-precomputed.
    Six-dimensional Set B filter pipeline. Set A display controls. Drag-pan,
    pinch-zoom, continuous bird's-eye-to-street zoom range. Curved arc labels.
  - `FOURD_JS` — Three.js sphere projection with OrbitControls, raycast modal,
    source-dropdown corpus switching, per-source cache.
  - `RENDERED_JS` — WORLD_JS string-substituted for the compact
    "real-screenshots-only" subset.

**Reference example (Wikipedia):**
- `scripts/example_wiki/03_classify.py` — 12-bucket encyclopedic classifier
  via keyword matching against Wikipedia category strings.
- `scripts/example_wiki/04_layout_wiki.py` — wedge-fill layout adapter,
  importing the substrate's helpers via `importlib`.
- `scripts/example_wiki/05_wiki_thumb_render.py` — Playwright renderer for
  en.wikipedia.org/wiki/<title> URLs with PIL decompression-bomb safeguard
  disabled (Wikipedia article pages are very tall).

**Documentation:**
- `README.md` — what it is, quickstart.
- `docs/ARCHITECTURE.md` — four-layer factoring, JSON contract.
- `docs/INTEGRATION.md` — three-step recipe to add a new corpus.
- `docs/SCHEMA.md` — `cards` table contract.
- `docs/PHILOSOPHY.md` — why "place" rather than "visualization"; the
  dimensional argument.
- `CONTRIBUTING.md` — priorities and patterns.

**Vendored:**
- `static/three.module.js` — Three.js r0.165.0
- `static/OrbitControls.js` — patched to import three from `/static/three.module.js`
- `static/PointerLockControls.js` — for /world's first-person mode

**Systemd:**
- `systemd/wander-api.service` — uvicorn unit
- `systemd/wander-category-edges.service` + `.timer` — daily chord precompute
- `systemd/wander-wiki-thumbs.service` — continuous wiki article rendering

### Known limitations

- Single-server tested; production hardening (TLS, rate-limiting, multi-tenant)
  out of scope.
- Layout computation is O(n) but has constant factors that get expensive past
  ~1M cards.
- The `WORLD_JS` central pillar and pylons reference the reference-deployment
  operator's other sites; deployers should customize before hosting.
- Chord diagram requires pre-computed `category_edges.json`; the daily timer
  is provided but populates only after first run.

### Acknowledgments

Built end-to-end in roughly 36 hours of focused work, by one person collaborating
with one LLM, on commodity hardware. The architectural conjecture that this is
*not unusual but in fact what should be possible given correct factoring* is
itself the substantive claim. Future contributors will dramatically reduce the
substrate work for further corpora; the bottleneck has moved from engineering
to cartography.

The cosmography papers from the build phase live in the project's session
memory; they elaborate on what this substrate is for and why "walkable place"
is the right framing.
