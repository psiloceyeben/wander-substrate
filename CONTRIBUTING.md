# Contributing

Welcome. The substrate is at v0.1 — research-quality, single-server tested, with
two demonstration corpora (long-tail web + Wikipedia) running at the reference
deployment. The most useful contributions, in priority order:

## 1. New corpus adapters

Every classified-and-scored public corpus is a candidate for spatialization. To
add one:

1. Read [`docs/INTEGRATION.md`](docs/INTEGRATION.md) and the reference adapter at
   `scripts/example_wiki/`.
2. Create `scripts/your_corpus/` with `classify.py`, `layout_X.py`, optionally
   `thumb_render.py`.
3. Add an entry to `_LAYOUT_PATHS` in `api/main.py`.
4. (Optional) Add a dedicated `/your_corpus` page via the `WIKI_PAGE` substitution
   pattern.
5. Open a PR with: the adapter scripts, sample classifier output (distribution),
   layout JSON validation, and a screenshot of the resulting mandala.

Good corpus candidates:

- **Reference databases**: arXiv, PubMed, OpenAlex, Crossref, SSRN
- **Cultural catalogs**: IMDb, Letterboxd, Discogs, BoardGameGeek, Goodreads,
  Bandcamp, museum collections
- **Marketplaces** (with caution re: TOS): eBay, Etsy, Amazon listings
- **Forums**: Stack Exchange (per-site), Reddit (per-subreddit), Hacker News
- **Code**: GitHub repos (per-language), npm/PyPI/RubyGems
- **Government/civic**: open-data portals, court records, patents, regulatory
  filings
- **Personal**: own email archive, browser history, file system, Anki decks
  (these typically run as private deployments, not public hub additions)

## 2. New visual paradigms

The substrate has eight paradigms baked in (mandala, heatmap, treemap, sankey,
sunburst, histograms, bubble, stream, plus chord behind the daily precompute).
New paradigms are roughly 100-300 LOC each.

To add one:

1. Write a `drawYourParadigm()` function in `GAME_JS` (search for the existing
   `drawHeatmap` / `drawTreemap` etc. for the pattern).
2. Add it to the `drawScene()` dispatch at the top.
3. Add an `<option>` to the graph-type `<select>` in the Set A panel.
4. Open a PR with a screenshot showing the new paradigm rendering both
   demonstration corpora correctly.

Paradigm candidates that aren't yet implemented:

- **Force-directed graph** — using corpus's intrinsic link structure
- **Parallel coordinates** — multi-dim per-card across numeric features
- **Geographic overlay** — for corpora with lat/lon (museums, businesses,
  geo-tagged photos)
- **Polar histogram** (rose chart) — distribution per category around a circle
- **Hierarchical edge bundling** — for inter-category linkage
- **Hyperbolic disk projection** — high-degree nodes at center
- **Time-stratified streamgraph** — corpus growth + decay over time
- **3D bar chart on the sphere** — `/4d` extension

## 3. New filter dimensions

The substrate's six default Set B filter dimensions (category, language, era,
site_type, score_quartile, TLD) are biased toward web corpora. Other corpora
have natural facets that aren't yet exposed.

To add a filter dimension:

1. Add the chip group in `rebuildPanels()` (see existing groups for pattern).
2. Add the field to `filterState` and `passesFilters()` in `GAME_JS`.
3. Ensure the field is populated in your corpus's layout JSON (see
   [`docs/SCHEMA.md`](docs/SCHEMA.md)).
4. ~30 LOC total.

Filter dimensions worth adding:

- **License / copyright** (open / fair-use / public-domain / restricted)
- **Has external link** (boolean — to anything off-site)
- **Estimated read time** (continuous, in minutes)
- **Multimedia density** (images per 1K words)
- **Authorship type** (single author / collaborative / institutional)
- **Last modified era** (decade buckets)

## 4. Self-serve onboarding

The substrate's biggest near-term opportunity is **self-serve corpus
onboarding** — a single declarative config file that generates the classifier
and layout adapter from a corpus's structured metadata, eliminating the manual
adapter-writing step.

Sketch:

```yaml
# corpus.yaml
name: my_corpus
db: data/my_corpus.db
table: items
fields:
  id: row_id
  host: title
  category: { source: tags, top_n: 12 }    # auto-cluster top-N tags into 12 buckets
  score: { source: log10(view_count + 1) }
  era: { source: year, buckets: [1990, 2000, 2010, 2020] }
```

The substrate would generate the classifier and layout adapter from this config,
run them, and serve the result. This would expand the addressable corpora from
~hundreds (manual integrations) to ~thousands (declarative).

This is a substantial contribution; reach out before starting. The schema design
needs care so that we don't lock in early decisions that prevent later
flexibility.

## 5. Performance and infrastructure

- **Layout computation at >1M cards**: the current `wedge_fill_positions` is
  O(n) but has constant factors that get expensive past a million. A Numba/Cython
  port would speed up regen by ~10×.
- **InstancedMesh of >1M points**: works but pushes hardware limits. A
  level-of-detail aggregator that bins distant cards into voxel meshes would let
  the renderer handle 10M+ cards.
- **Multi-tenant deployment patterns**: the current substrate assumes single
  deployment; an organization wanting to host many private corpora would need
  isolation, auth, separate refresh cadences.
- **Streaming corpora**: corpora that produce new items continuously (Twitter
  firehose, IoT) need a different memory pattern — sliding-window materialization
  rather than batch regen.

## Code style

- Python: 4-space indent, type hints where they help, comments for non-obvious
  decisions. No formal style enforcement at v0.1; common-sense readability.
- JavaScript: 2-space indent, vanilla ES modules (no TypeScript at the substrate
  level; deployments can transpile if they want), use the existing canvas/Three.js
  patterns.
- Comments matter more than perfect code. Future contributors (including future
  you) need to know *why* a decision was made, not just *what* the code does.

## Testing

There is no test suite at v0.1. The substrate is integration-tested by running
the reference Wikipedia adapter end-to-end and verifying the resulting mandala
renders. Contributions that add a test suite are welcome but not required.

## License

All contributions are MIT-licensed (see [`LICENSE`](LICENSE)). By submitting a
PR you agree to this license.

## Communication

The reference deployment lives at [wanderaround.io](https://wanderaround.io).
Project discussion happens via GitHub issues for now. If contribution volume
grows, a Discord or matrix channel will follow.
