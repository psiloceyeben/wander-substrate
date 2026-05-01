"""Compute two ring layouts for the /game canvas and write JSONs to data/.

Outputs:
    data/layout_categories.json  — 12 topic-category sectors around the center
    data/layout_types.json        — 8 language sectors with 6 site_type concentric rings within each

Each JSON contains:
    {
      "mode": "categories" | "types",
      "labels": [{ "text": str, "x": float, "y": float, "angle_deg": float, "color": str }],
      "rings":  [{ "radius": float, "color": str, "label": str }],
      "center": [0, 0],
      "extent": float,            # rough max-radius for camera bounds
      "cards": [
        { "id": int, "host": str, "title": str, "score": float,
          "era": str, "language": str, "category": str, "site_type": str,
          "x": float, "y": float }
      ]
    }

Idempotent — overwrites JSONs each run.

Run:
    python3 scripts/10_layout_ring.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

# ───────────────────────────────────────────────────────────────────────
# City-block layout — replaces _jitter for in-cell placement.
# Cards within a (cat, sub, era, quartile) leaf cell are sorted by score
# desc and laid into 5×5 building blocks with a 2-cell gap between blocks.

BLOCK_SIDE = 5                   # 5x5 buildings per block (used only for plaza)
BLOCK_CAPACITY = BLOCK_SIDE * BLOCK_SIDE  # 25
SLOT_SIZE_MAX = 24.0             # max slot — sparse cells get spacious streets
SLOT_SIZE_MIN = 16.0             # min slot ≥ max building width (15) → no in-block overlap
BLOCK_GAP_CELLS = 2              # 2-cell street between plaza blocks
BLOCK_PITCH_CELLS = BLOCK_SIDE + BLOCK_GAP_CELLS  # 8 cells block-to-block

# Wedge-block grouping: every N buildings → angular alley; every M rings → radial road
WEDGE_BLOCK_BUILDINGS = 4        # blocks of 4 buildings before an angular street (smaller rows = more alleys)
WEDGE_BLOCK_RINGS = 3            # main radial road every 3 rings (perimeter→spawn avenue)
WEDGE_STREET_GAP = 2.0           # block-to-block 2× slot (32-unit alleys, walkable)
WEDGE_RADIAL_GAP_FACTOR = 1.4    # radial roads 1.4× normal ring spacing (extra-wide avenues)
WEDGE_ANGULAR_PADDING = 0.96     # use 96% of sub-sector arc; tiny 4% gap between slices
RING_SPACING_FACTOR = 2.0        # rings are 2× slot apart radially (32-unit walkable streets between rows)


# Module-level layout options — overridable via CLI for the --rendered-only build.
# Default values preserve original behavior exactly.
_LAYOUT_OPTS = {
    "where_extra": "",            # SQL fragment appended to layout_categories WHERE clauses
    "r_outer_buffer": 5.0,        # multiplier in compute_R_OUTER_from_counts
    "r_outer_min": None,          # if set, overrides R_OUTER_MIN
}


def compute_R_OUTER_from_counts(counts: dict, cats: list) -> dict:
    """Per-category outer radius from per-cat card counts (use the LIMIT-200K
    subset's counts, not full DB). Each sector gets enough radial extent to hold
    its cards at SLOT_SIZE_MIN density. Equal angular width preserved (30° each)."""
    sector_arc = 2 * math.pi / len(cats)
    s = SLOT_SIZE_MIN
    r_outer_min = _LAYOUT_OPTS["r_outer_min"] if _LAYOUT_OPTS["r_outer_min"] is not None else R_OUTER_MIN
    buffer = _LAYOUT_OPTS["r_outer_buffer"]
    out = {}
    for cat in cats:
        n = counts.get(cat, 100)
        needed_sq = R_INNER ** 2 + n * 2 * RING_SPACING_FACTOR * s * s / (sector_arc * WEDGE_ANGULAR_PADDING)
        r_out = math.sqrt(needed_sq) * buffer
        out[cat] = max(r_outer_min, min(R_OUTER_MAX, r_out))
    return out


def adaptive_plaza_slot(total: int, cell_w: float, cell_h: float) -> float:
    """Plaza-only adaptive slot size (axis-aligned 5x5 block grid)."""
    blocks_per_side = max(1, math.ceil(math.sqrt(math.ceil(total / BLOCK_CAPACITY))))
    cap = blocks_per_side * BLOCK_PITCH_CELLS
    fit = min(cell_w, cell_h) / cap
    return max(SLOT_SIZE_MIN, min(SLOT_SIZE_MAX, fit))


def plaza_block_offset(slot_idx: int, total: int, slot_size: float) -> tuple[float, float]:
    """5x5 block grid offset for plaza cells (axis-aligned, no rotation)."""
    blocks_per_side = max(1, math.ceil(math.sqrt(math.ceil(total / BLOCK_CAPACITY))))
    block_idx = slot_idx // BLOCK_CAPACITY
    slot_in_block = slot_idx % BLOCK_CAPACITY
    sx = slot_in_block % BLOCK_SIDE
    sy = slot_in_block // BLOCK_SIDE
    bx = block_idx % blocks_per_side
    by = block_idx // blocks_per_side
    bx_centered = bx - (blocks_per_side - 1) / 2
    by_centered = by - (blocks_per_side - 1) / 2
    sx_centered = sx - (BLOCK_SIDE - 1) / 2
    sy_centered = sy - (BLOCK_SIDE - 1) / 2
    block_pitch = BLOCK_PITCH_CELLS * slot_size
    return (
        bx_centered * block_pitch + sx_centered * slot_size,
        by_centered * block_pitch + sy_centered * slot_size,
    )


def wedge_fill_positions(cell_inner_r: float, cell_outer_r: float,
                         cell_min_angle: float, cell_angular_extent: float,
                         n_cards: int) -> list[tuple[float, float]]:
    """Place n_cards as polar rings with walkable spacing — preserves wedge cone shape.
    Every WEDGE_BLOCK_BUILDINGS along a ring → angular alley.
    Every WEDGE_BLOCK_RINGS rings → wider radial road (perimeter→spawn avenue).
    Sparse cells spread cards across all rings (≥1 per occupied ring) so dense
    sectors never appear to start at an outer band.
    Defensive tail distributes any overflow across new inward rings (no stacking)."""
    cell_radial = cell_outer_r - cell_inner_r
    mean_r = (cell_inner_r + cell_outer_r) / 2
    street_overhead = 1.5  # account for both alleys (angular) and roads (radial)
    cell_area = cell_radial * cell_angular_extent * mean_r
    s = math.sqrt(max(1.0, cell_area / street_overhead) / max(1, n_cards))
    s = max(SLOT_SIZE_MIN, min(SLOT_SIZE_MAX, s))
    alley = s * WEDGE_STREET_GAP

    # Variable ring spacing: every WEDGE_BLOCK_RINGS rings adds a wider radial road
    base_spacing = s * RING_SPACING_FACTOR
    road_extra = base_spacing * (WEDGE_RADIAL_GAP_FACTOR - 1.0)
    n_rings_target = max(1, round(cell_radial / base_spacing))
    n_road_gaps = max(0, (n_rings_target - 1) // WEDGE_BLOCK_RINGS)
    radial_used_for_roads = n_road_gaps * road_extra
    n_rings = max(1, round((cell_radial - radial_used_for_roads) / base_spacing))
    base_ring_spacing = max(s * 1.2, (cell_radial - n_road_gaps * road_extra) / n_rings)
    ring_centers = []
    cursor = cell_outer_r
    for ring_i in range(n_rings):
        cursor -= base_ring_spacing
        ring_centers.append(cursor + base_ring_spacing * 0.5)
        if (ring_i + 1) % WEDGE_BLOCK_RINGS == 0 and ring_i + 1 < n_rings:
            cursor -= road_extra

    # Distribute cards across rings.
    # - Dense (n_cards >= n_rings): proportional to ring circumference (outer rings get more)
    # - Sparse (n_cards < n_rings): spread one card per ring, evenly sampled — so the cell
    #   doesn't appear to "start at an outer band" (cards reach inner edge too)
    # Distribute cards across rings proportional to each ring's circumference.
    # If cell over-packs, fill rings to natural cap; overflow flows to defensive
    # tail loop (extends rings inward — cells smear, but no building stacking).
    ring_caps = [max(1, int(r * cell_angular_extent / s)) for r in ring_centers]
    total_cap = sum(ring_caps)
    cards_per_ring = []
    if n_cards >= total_cap:
        cards_per_ring = list(ring_caps)  # don't compress per ring; overflow → tail
    else:
        # Proportional distribution — each ring gets share ∝ its capacity, capped at cap
        for cap in ring_caps:
            share = max(1, int(n_cards * cap / total_cap))
            cards_per_ring.append(min(share, cap))
        # Adjust to exactly n_cards — add to outer rings, trim from inner if needed
        diff = n_cards - sum(cards_per_ring)
        if diff > 0:
            i = 0  # add to outer rings (more capacity)
            while diff > 0:
                if cards_per_ring[i % n_rings] < ring_caps[i % n_rings]:
                    cards_per_ring[i % n_rings] += 1
                    diff -= 1
                i += 1
                if i > n_rings * 100:  # safety
                    break
        elif diff < 0:
            for _ in range(-diff):
                for j in range(len(cards_per_ring) - 1, -1, -1):
                    if cards_per_ring[j] > 0:
                        cards_per_ring[j] -= 1
                        break

    positions = []
    for ring_i, current_r in enumerate(ring_centers):
        n_buildings = cards_per_ring[ring_i]
        if n_buildings == 0:
            continue
        n_buildings = min(n_buildings, n_cards - len(positions))
        if n_buildings <= 0:
            break
        n_block_groups = max(1, n_buildings // WEDGE_BLOCK_BUILDINGS)
        usable_arc = cell_angular_extent - (n_block_groups - 1) * (alley / current_r)
        usable_arc = max(cell_angular_extent * 0.4, usable_arc)
        ang_step = usable_arc / n_buildings
        for i in range(n_buildings):
            block_idx = i // WEDGE_BLOCK_BUILDINGS
            ang = cell_min_angle + (i + 0.5) * ang_step + block_idx * (alley / current_r)
            positions.append((current_r * math.cos(ang), current_r * math.sin(ang)))

    # Defensive tail: extend rings INWARD past cell_inner_r at proper spacing.
    # Cells smear inward (overlap adjacent inner cells' territory) but each
    # additional ring uses base_ring_spacing → no card stacking. Caller still
    # gets all n_cards placed at distinct positions.
    overflow = n_cards - len(positions)
    ring_offset = 0
    while overflow > 0:
        ring_offset += 1
        r = cell_inner_r - ring_offset * base_ring_spacing
        if r < 200:  # past spawn — drop final remainder rather than land in plaza
            break
        n_per = max(1, int(r * cell_angular_extent / s))
        take = min(n_per, overflow)
        ang_step = cell_angular_extent / n_per
        for i in range(take):
            ang = cell_min_angle + (i + 0.5) * ang_step
            positions.append((r * math.cos(ang), r * math.sin(ang)))
        overflow -= take
    return positions

DATA_DIR = Path("data")

# ───────────────────────────────────────────────────────────────────────
# Config

# Mode A — 12 topic categories around the central ring
CATEGORIES = [
    ("tech",       "#7aa9d8"),
    ("art",        "#e0a060"),
    ("music",      "#c870c0"),
    ("food",       "#e07050"),
    ("gaming",     "#9080d0"),
    ("science",    "#60c0a0"),
    ("education",  "#a0c068"),
    ("news",       "#d8a8a8"),
    ("sports",     "#e0c060"),
    ("commerce",   "#9aa9bc"),
    ("community",  "#80c8d0"),
    ("personal",   "#d8a050"),
]
# "misc" gets a special inner stand-alone position; not on the ring.

# Mode B — 8 language outer sectors × 6 site_type concentric rings
DEFAULT_LANG_BUCKETS = ["en", "ja", "zh", "es", "de", "fr", "ru", "pt"]
SITE_TYPE_RINGS = [
    # innermost → outermost. innermost = most personal/handmade.
    ("hobbyist",     "#d8a050"),
    ("blog",         "#a8a298"),
    ("educational",  "#a0c068"),
    ("business",     "#9aa9bc"),
    ("government",   "#5a6c80"),
    ("unknown",      "#404448"),
]

R_INNER = 1700.0      # first sector band — leaves 200u CLEAR ring between plaza and sectors
R_OUTER_MIN = 5500.0  # min sector outer radius (sparse sectors get presence)
R_OUTER_MAX = 60000.0 # max — dense cats stretch as far as needed; flying covers the distance
R_LABEL_PAD = 240.0   # per-category label sits this far outside that sector's R_OUTER
R_CENTER_PLAZA = 1500 # expanded central plaza (was 1000) — more misc breathing room
R_PLAZA_INNER = 350   # spawn-area clearance (no buildings inside)

# 4-LEVEL HIERARCHY (cat → site_type → era → score quartile):
# Level 1 (angular outer): 12 categories at 30° each
# Level 2 (angular sub-sector): 4 site_types per category at 7.5° each
# Level 3 (radial concentric band): 5 eras per sub-sector
# Level 4 (radial sub-band within era): 4 score quartiles per era band
# Total: 12 × 4 × 5 × 4 = 960 leaf cells

# Level 2 — site_type sub-sectors (angular)
SUB_SECTORS = [
    ("hobbyist",   -0.375),
    ("blog",       -0.125),
    ("business",    0.125),
    ("other",       0.375),
]
SITE_TYPE_TO_SUB = {
    "hobbyist":    "hobbyist",
    "blog":        "blog",
    "business":    "business",
    "educational": "other",
    "government":  "other",
    "unknown":     "other",
}

# Level 3 — era as concentric radial bands (innermost = oldest, outermost = newest)
ERA_BAND_ORDER = ["old_web", "midweb", "template", "seo_hardened", "modern_spa"]
ERA_TO_BAND = {era: i for i, era in enumerate(ERA_BAND_ORDER)}
N_ERA_BANDS = len(ERA_BAND_ORDER)

# Level 4 — score quartile as radial sub-band within era band
N_SCORE_QUARTILES = 4

# Per-category ERA_BAND_WIDTH and SCORE_SUBBAND_WIDTH are computed dynamically
# from R_OUTER_per_category(cat) inside layout_categories() — variable-radial-extent
# sectors mean each category has its own band widths.

# Trails — radial roads, one per category sector (per-cat outer radius set in layout fn)
TRAIL_INNER = 60.0

R_TYPES_INNER = 220
R_TYPES_OUTER = 1500
R_TYPES_LABEL_LANG = 1660
R_TYPES_LABEL_RING = 95   # site-type ring labels along the inside

# ───────────────────────────────────────────────────────────────────────


def _jitter(seed_str: str, n: int = 2) -> list[float]:
    """Deterministic small floats in [-1, 1) seeded by a card id string."""
    h = hashlib.sha256(seed_str.encode()).digest()
    return [((h[i] / 255.0) - 0.5) * 2 for i in range(n)]


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def layout_categories(conn: sqlite3.Connection, max_cards: int) -> dict:
    """Pie of 12 topic categories. Higher score → closer to outer rim of sector."""
    cats = [c[0] for c in CATEGORIES]
    cat_color = dict(CATEGORIES)

    sector_count = len(cats)
    sector_arc = 2 * math.pi / sector_count  # 30° each

    where_extra = _LAYOUT_OPTS["where_extra"]

    # Score normalization — use the 5..95 percentile so outliers don't dominate
    scores = [r[0] for r in conn.execute(
        f"SELECT composite_score FROM cards WHERE is_useful_content=1 AND category IS NOT NULL{where_extra}"
    )]
    if not scores:
        return {"mode": "categories", "cards": [], "labels": [], "rings": [], "center": [0, 0], "extent": R_OUTER_MIN}

    # (R_OUTER_CAT computed below, after rows are fetched, so we use the LIMIT
    # 200K subset's counts — not the full DB.)
    scores.sort()
    s_lo = scores[max(0, int(len(scores) * 0.05))]
    s_hi = scores[min(len(scores) - 1, int(len(scores) * 0.95))]
    s_range = max(0.001, s_hi - s_lo)

    rows = conn.execute(
        f"""
        SELECT id, host, title, era, composite_score, language, category, site_type, site_subtype,
               COALESCE(word_count, 0) AS word_count
        FROM cards
        WHERE is_useful_content = 1 AND category IS NOT NULL{where_extra}
        ORDER BY composite_score DESC
        LIMIT ?
        """,
        (max_cards,),
    ).fetchall()

    sub_offsets = {name: off for name, off in SUB_SECTORS}

    # Per-category R_OUTER computed from the LIMIT-200K row subset
    from collections import Counter as _Counter
    sample_counts = _Counter(r["category"] for r in rows if r["category"] in cats)
    R_OUTER_CAT = compute_R_OUTER_from_counts(sample_counts, cats)
    print(f"[layout] per-category R_OUTER: " + ", ".join(f"{c}={R_OUTER_CAT[c]:.0f}" for c in cats))

    # Per-category quartile thresholds — so each cat's q0..q3 cells are populated
    # from ITS OWN score distribution. Otherwise score-skewed cats (e.g., tech
    # which trends high) have empty inner-quartile cells → "starts at outer band".
    cat_scores = defaultdict(list)
    for r in rows:
        if r["category"] in cats:
            cat_scores[r["category"]].append(float(r["composite_score"]))
    cat_quartile_thresholds = {}
    for cat, sc in cat_scores.items():
        sc.sort()
        if len(sc) < 4:
            cat_quartile_thresholds[cat] = (sc[0] if sc else 0, sc[0] if sc else 0, sc[0] if sc else 0)
        else:
            cat_quartile_thresholds[cat] = (
                sc[int(len(sc) * 0.25)],
                sc[int(len(sc) * 0.50)],
                sc[int(len(sc) * 0.75)],
            )
    def quartile_of_cat(score: float, cat: str) -> int:
        thr = cat_quartile_thresholds.get(cat)
        if thr is None: return 1  # default if unknown cat
        if score < thr[0]: return 0
        if score < thr[1]: return 1
        if score < thr[2]: return 2
        return 3

    # Per-category per-era card counts → allocate radial extent proportionally.
    # Without this, midweb-heavy cats (personal/community) cram all cards into
    # a single equal-width era band → severe in-cell overlap.
    cat_era_counts = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cat = r["category"]
        if cat in cats:
            era = r["era"] or "midweb"
            cat_era_counts[cat][era] += 1
    # Compute per-cat era band widths (radial extent for each era within the cat)
    MIN_ERA_WIDTH_FRAC = 0.06  # each era gets at least 6% of available radial extent
    cat_era_widths = {}
    for cat in cats:
        available = (R_OUTER_CAT[cat] - R_INNER) if cat in (sample_counts) else (R_OUTER_MIN - R_INNER)
        total = sum(cat_era_counts[cat].get(era, 0) for era in ERA_BAND_ORDER)
        if total == 0:
            cat_era_widths[cat] = {era: available / N_ERA_BANDS for era in ERA_BAND_ORDER}
        else:
            min_w = available * MIN_ERA_WIDTH_FRAC
            raw = {era: available * cat_era_counts[cat].get(era, 0) / total for era in ERA_BAND_ORDER}
            adjusted = {era: max(min_w, w) for era, w in raw.items()}
            scale = available / sum(adjusted.values())
            cat_era_widths[cat] = {era: w * scale for era, w in adjusted.items()}
    # Pre-compute per-cat per-era inner radius
    cat_era_inner = {}
    for cat in cats:
        cumulative = R_INNER
        cat_era_inner[cat] = {}
        for era in ERA_BAND_ORDER:
            cat_era_inner[cat][era] = cumulative
            cumulative += cat_era_widths[cat][era]

    # Misc plaza is now subdivided into 4 quadrants by site_type — same axis as
    # the level-2 districts in the outer ring. Gives the central plaza visible
    # structure instead of an undifferentiated cluster.
    PLAZA_QUADRANTS = {
        "hobbyist": (-1, -1),  # NW
        "blog":     ( 1, -1),  # NE
        "business": ( 1,  1),  # SE
        "other":    (-1,  1),  # SW
    }

    # ── City-block placement ──────────────────────────────────────────
    # Group cards by leaf cell, sort by score desc within group, place via
    # block_offset(). Highest-scoring cards land at the top-left of the first
    # block (most prominent slot), giving "downtown" feel to score peaks.
    groups = defaultdict(list)
    for r in rows:
        cat = r["category"]
        if cat == "misc" or cat not in cats:
            sub_name = SITE_TYPE_TO_SUB.get(r["site_type"] or "unknown", "other")
            groups[("plaza", sub_name)].append(r)
        else:
            sub_name = SITE_TYPE_TO_SUB.get(r["site_type"] or "unknown", "other")
            era = r["era"] or "midweb"
            era_idx = ERA_TO_BAND.get(era, 1)
            qi = quartile_of_cat(float(r["composite_score"]), cat)
            groups[("cat", cat, sub_name, era_idx, qi)].append(r)
    for k in groups:
        groups[k].sort(key=lambda r: -float(r["composite_score"]))

    cards_out = []
    sub_arc_size = sector_arc * 0.25  # 7.5° per sub-sector
    for key, group_rows in groups.items():
        total = len(group_rows)
        if key[0] == "plaza":
            # Plaza quadrants are 90° wedges centered around the four diagonals.
            # Use the same polar fill as sectors so the plaza reads as continuous
            # with the surrounding wedges and edges curve along the plaza disc.
            sub_name = key[1]
            qx, qy = PLAZA_QUADRANTS[sub_name]
            quadrant_center_angle = math.atan2(qy, qx)  # diagonal angle
            quadrant_arc = math.pi / 2  # 90° per quadrant
            cell_min_angle = quadrant_center_angle - quadrant_arc * 0.5
            positions = wedge_fill_positions(
                R_PLAZA_INNER, R_CENTER_PLAZA,
                cell_min_angle, quadrant_arc,
                total,
            )
            for (x, y), r in zip(positions, group_rows):
                cards_out.append({
                    "id": r["id"],
                    "host": r["host"] or "",
                    "score": round(float(r["composite_score"]), 2),
                    "era": r["era"] or "midweb",
                    "language": r["language"] or "und",
                    "category": r["category"],
                    "site_type": r["site_type"] or "unknown",
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "wc": int(r["word_count"]) if "word_count" in r.keys() else 0,
                })
        else:
            # Wedge cells: place buildings as polar rings filling the slice's
            # actual wedge shape — outer ring takes highest-score cards, inner
            # rings descend. Buildings curve along the arc, narrowing inward.
            _, cat, sub_name, era_idx, qi = key
            sector_idx = cats.index(cat)
            sector_center = sector_idx * sector_arc - math.pi / 2
            sub_center_offset = sub_offsets[sub_name] * sector_arc
            sub_center_angle = sector_center + sub_center_offset
            padded_arc = sub_arc_size * WEDGE_ANGULAR_PADDING
            # Tiny per-cell angular offset (breaks same-angle alignment between
            # adjacent cells' tail-extensions and inner cell main rings → no stacking)
            cell_jitter_offset = (era_idx * 0.37 + qi * 0.21) * 0.0005
            cell_min_angle = sub_center_angle - padded_arc * 0.5 + cell_jitter_offset
            era_name = ERA_BAND_ORDER[era_idx]
            era_band_width = cat_era_widths[cat][era_name]
            score_subband_width = era_band_width / N_SCORE_QUARTILES
            era_band_inner = cat_era_inner[cat][era_name]
            cell_inner_r = era_band_inner + qi * score_subband_width
            cell_outer_r = cell_inner_r + score_subband_width
            positions = wedge_fill_positions(
                cell_inner_r, cell_outer_r,
                cell_min_angle, padded_arc,
                total,
            )
            for (x, y), r in zip(positions, group_rows):
                cards_out.append({
                    "id": r["id"],
                    "host": r["host"] or "",
                    "score": round(float(r["composite_score"]), 2),
                    "era": r["era"] or "midweb",
                    "language": r["language"] or "und",
                    "category": r["category"],
                    "site_type": r["site_type"] or "unknown",
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "wc": int(r["word_count"]) if "word_count" in r.keys() else 0,
                })

    # Outer-perimeter category labels — sit at this category's own R_OUTER + pad
    labels = []
    for i, (cat, color) in enumerate(CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        r_label_cat = R_OUTER_CAT[cat] + R_LABEL_PAD
        labels.append({
            "text": cat,
            "x": round(math.cos(sector_center) * r_label_cat, 1),
            "y": round(math.sin(sector_center) * r_label_cat, 1),
            "angle_deg": round(math.degrees(sector_center), 1),
            "color": color,
            "kind": "category",
        })

    # Sub-sector district labels at mid-radius — one per sub-sector per category,
    # sitting at the midpoint of THAT category's own radial extent
    sub_color = {
        "hobbyist":   "#d8a050",
        "blog":       "#a8a298",
        "business":   "#9aa9bc",
        "other":      "#6a7480",
    }
    for i, (cat, _color) in enumerate(CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        r_sub_label = (R_INNER + R_OUTER_CAT[cat]) / 2
        for sub_name, sub_off in SUB_SECTORS:
            angle = sector_center + sub_off * sector_arc
            labels.append({
                "text": sub_name,
                "x": round(math.cos(angle) * r_sub_label, 1),
                "y": round(math.sin(angle) * r_sub_label, 1),
                "angle_deg": round(math.degrees(angle), 1),
                "color": sub_color[sub_name],
                "kind": "district",
                "parent": cat,
            })

    rings = [
        {"radius": R_CENTER_PLAZA, "color": "rgba(255,255,255,0.05)", "label": "plaza"},
    ]
    # No global era-band rings — each category now has its own band widths,
    # so a single concentric ring across all sectors is misleading.

    # Trails — 12 main roads, each spans its own sector's R_INNER → R_OUTER_CAT
    trails = []
    for i, (cat, color) in enumerate(CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        trail_outer = R_OUTER_CAT[cat] + R_LABEL_PAD
        points = []
        n_segments = 8
        for s in range(n_segments + 1):
            t = s / n_segments
            r_t = TRAIL_INNER + t * (trail_outer - TRAIL_INNER)
            wobble = math.sin(t * math.pi * 2 + i * 0.7) * (sector_arc * 0.04) * (1 - abs(t - 0.5) * 2)
            angle = sector_center + wobble
            points.append([round(math.cos(angle) * r_t, 1), round(math.sin(angle) * r_t, 1)])
        trails.append({
            "sector": cat,
            "color": color,
            "points": points,
        })

    max_extent = max(R_OUTER_CAT.values()) + R_LABEL_PAD + 100

    return {
        "mode": "categories",
        "labels": labels,
        "rings": rings,
        "trails": trails,
        "center": [0, 0],
        "extent": max_extent,
        "cards": cards_out,
        "sector_arc_deg": round(math.degrees(sector_arc), 1),
        "category_colors": {k: v for k, v in CATEGORIES},
        "era_band_order": ERA_BAND_ORDER,
        "n_score_quartiles": N_SCORE_QUARTILES,
        "r_outer_per_category": {k: round(v, 1) for k, v in R_OUTER_CAT.items()},
    }


def layout_types(conn: sqlite3.Connection, max_cards: int) -> dict:
    """Outer ring split into 8 language sectors. Inside each sector, buildings
    are placed at concentric rings keyed by site_type (inner = hobbyist, outer = government)."""
    # Pick the top 8 languages by count (excluding 'und')
    lang_counts = list(conn.execute(
        "SELECT language, COUNT(*) c FROM cards WHERE is_useful_content=1 AND language IS NOT NULL AND language != 'und' GROUP BY language ORDER BY c DESC LIMIT 8"
    ).fetchall())
    if not lang_counts:
        langs = DEFAULT_LANG_BUCKETS
    else:
        langs = [r["language"] for r in lang_counts]

    sector_count = len(langs)
    sector_arc = 2 * math.pi / sector_count  # 45° each for 8 langs

    type_index = {st: i for i, (st, _) in enumerate(SITE_TYPE_RINGS)}
    type_color = {st: c for st, c in SITE_TYPE_RINGS}

    rows = conn.execute(
        """
        SELECT id, host, title, era, composite_score, language, category, site_type, site_subtype,
               COALESCE(word_count, 0) AS word_count
        FROM cards
        WHERE is_useful_content = 1 AND language IS NOT NULL AND site_type IS NOT NULL
        ORDER BY composite_score DESC
        LIMIT ?
        """,
        (max_cards,),
    ).fetchall()

    type_step = (R_TYPES_OUTER - R_TYPES_INNER) / max(1, len(SITE_TYPE_RINGS))

    cards_out = []
    for r in rows:
        lang = r["language"]
        st = r["site_type"]
        if lang not in langs or st not in type_index:
            # Park in the central plaza
            jx, jy = _jitter(f"misc-types:{r['id']}")
            x = jx * R_CENTER_PLAZA * 0.7
            y = jy * R_CENTER_PLAZA * 0.7
        else:
            sector_idx = langs.index(lang)
            sector_center = sector_idx * sector_arc - math.pi / 2
            ring_idx = type_index[st]
            base_radius = R_TYPES_INNER + ring_idx * type_step
            jx, jy = _jitter(f"types:{r['id']}:{lang}:{st}")
            angle = sector_center + jx * (sector_arc * 0.42)
            # Era as small radial offset within the type ring band
            era_idx = ERA_TO_BAND.get(r["era"] or "midweb", 1)
            era_off = (era_idx - 2) * 22.0  # -44 to +44 across the 5 eras
            radius = base_radius + (type_step * 0.32) * jy + era_off
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius

        cards_out.append({
            "id": r["id"],
            "host": r["host"] or "",
            "score": round(float(r["composite_score"]), 2),
            "era": r["era"] or "midweb",
            "language": lang,
            "category": r["category"] or "misc",
            "site_type": st,
            "x": round(x, 0),
            "y": round(y, 0),
            "wc": int(r["word_count"]) if "word_count" in r.keys() else 0,
        })

    labels = []
    # Language labels at the outer perimeter
    for i, lang in enumerate(langs):
        sector_center = i * sector_arc - math.pi / 2
        labels.append({
            "text": lang,
            "x": round(math.cos(sector_center) * R_TYPES_LABEL_LANG, 1),
            "y": round(math.sin(sector_center) * R_TYPES_LABEL_LANG, 1),
            "angle_deg": round(math.degrees(sector_center), 1),
            "color": "#cfd8c8",
            "kind": "language",
        })

    # Site-type ring labels along the +X axis (inside the rings, north of center)
    for i, (st, color) in enumerate(SITE_TYPE_RINGS):
        ring_radius = R_TYPES_INNER + (i + 0.5) * type_step
        labels.append({
            "text": st,
            "x": 0,
            "y": -ring_radius,  # canvas y grows downward; negative is "north"
            "angle_deg": -90,
            "color": color,
            "kind": "site_type",
        })

    rings = [
        {"radius": R_CENTER_PLAZA, "color": "rgba(255,255,255,0.05)", "label": "central plaza"},
    ]
    for i, (st, color) in enumerate(SITE_TYPE_RINGS):
        rings.append({
            "radius": R_TYPES_INNER + (i + 1) * type_step,
            "color": color + "33" if not color.startswith("rgba") else color,  # alpha 0x33 ~ 20%
            "label": st,
        })

    return {
        "mode": "types",
        "labels": labels,
        "rings": rings,
        "center": [0, 0],
        "extent": R_TYPES_LABEL_LANG + 80,
        "cards": cards_out,
        "languages": langs,
        "site_type_order": [st for st, _ in SITE_TYPE_RINGS],
        "site_type_colors": {st: c for st, c in SITE_TYPE_RINGS},
        "sector_arc_deg": round(math.degrees(sector_arc), 1),
    }


def layout_categories_flat(conn: sqlite3.Connection, max_cards: int) -> dict:
    """Mirror of layout_types but sectored by topic CATEGORY instead of language.

    12 outer category sectors (30° each) × 6 site_type concentric rings within
    each. Era as small radial offset within each ring. Score-DESC ordering for
    placement stability. Fixed bounds (R_TYPES_INNER=220, R_TYPES_OUTER=1500)
    so the whole layout fits the canvas at moderate zoom — unlike the variable-
    arm pinwheel `layout_categories()` which spreads to 60K extent for /world.

    Used by /game's "categories" toggle so the 2D map looks evenly distributed,
    not sparse-with-mega-arms. Independent of `layout_categories.json` — that
    file stays as-is for /world."""
    cats = [c[0] for c in CATEGORIES]
    cat_color = dict(CATEGORIES)
    sector_count = len(cats)
    sector_arc = 2 * math.pi / sector_count  # 30° each for 12 cats

    type_index = {st: i for i, (st, _) in enumerate(SITE_TYPE_RINGS)}

    rows = conn.execute(
        """
        SELECT id, host, title, era, composite_score, language, category, site_type, site_subtype,
               COALESCE(word_count, 0) AS word_count
        FROM cards
        WHERE is_useful_content = 1 AND category IS NOT NULL AND site_type IS NOT NULL
        ORDER BY composite_score DESC
        LIMIT ?
        """,
        (max_cards,),
    ).fetchall()

    type_step = (R_TYPES_OUTER - R_TYPES_INNER) / max(1, len(SITE_TYPE_RINGS))

    cards_out = []
    for r in rows:
        cat = r["category"]
        st = r["site_type"]
        if cat not in cats or st not in type_index:
            # Misc / unknown site_type → central plaza, jittered
            jx, jy = _jitter(f"misc-cat-flat:{r['id']}")
            x = jx * R_CENTER_PLAZA * 0.7
            y = jy * R_CENTER_PLAZA * 0.7
        else:
            sector_idx = cats.index(cat)
            sector_center = sector_idx * sector_arc - math.pi / 2
            ring_idx = type_index[st]
            base_radius = R_TYPES_INNER + ring_idx * type_step
            jx, jy = _jitter(f"cat-flat:{r['id']}:{cat}:{st}")
            angle = sector_center + jx * (sector_arc * 0.42)
            era_idx = ERA_TO_BAND.get(r["era"] or "midweb", 1)
            era_off = (era_idx - 2) * 22.0  # -44..+44 across 5 eras
            radius = base_radius + (type_step * 0.32) * jy + era_off
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius

        cards_out.append({
            "id": r["id"],
            "host": r["host"] or "",
            "score": round(float(r["composite_score"]), 2),
            "era": r["era"] or "midweb",
            "language": r["language"] or "und",
            "category": cat or "misc",
            "site_type": st or "unknown",
            "x": round(x, 0),
            "y": round(y, 0),
            "wc": int(r["word_count"]) if "word_count" in r.keys() else 0,
        })

    labels = []
    # Category labels at the outer perimeter (cat-specific colors).
    # angle_deg is set so the renderer can curve text along the arc (D3 polish).
    for i, (cat, color) in enumerate(CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        labels.append({
            "text": cat,
            "x": round(math.cos(sector_center) * R_TYPES_LABEL_LANG, 1),
            "y": round(math.sin(sector_center) * R_TYPES_LABEL_LANG, 1),
            "angle_deg": round(math.degrees(sector_center), 1),
            "color": color,
            "kind": "category",
        })

    # Sub-sector labels per category — same scheme as the pinwheel layout, lets
    # users see the 4 site_type districts within each cat.
    sub_color = {
        "hobbyist":   "#d8a050",
        "blog":       "#a8a298",
        "business":   "#9aa9bc",
        "other":      "#6a7480",
    }
    sub_label_radius = (R_TYPES_INNER + R_TYPES_OUTER) / 2
    for i, (cat, _color) in enumerate(CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        for sub_name, sub_off in SUB_SECTORS:
            angle = sector_center + sub_off * sector_arc
            labels.append({
                "text": sub_name,
                "x": round(math.cos(angle) * sub_label_radius, 1),
                "y": round(math.sin(angle) * sub_label_radius, 1),
                "angle_deg": round(math.degrees(angle), 1),
                "color": sub_color[sub_name],
                "kind": "district",
                "parent": cat,
            })

    # Site-type ring labels along the +X axis (north of center).
    for i, (st, color) in enumerate(SITE_TYPE_RINGS):
        ring_radius = R_TYPES_INNER + (i + 0.5) * type_step
        labels.append({
            "text": st,
            "x": 0,
            "y": -ring_radius,
            "angle_deg": -90,
            "color": color,
            "kind": "site_type",
        })

    rings = [
        {"radius": R_CENTER_PLAZA, "color": "rgba(255,255,255,0.05)", "label": "central plaza"},
    ]
    for i, (st, color) in enumerate(SITE_TYPE_RINGS):
        rings.append({
            "radius": R_TYPES_INNER + (i + 1) * type_step,
            "color": color + "33" if not color.startswith("rgba") else color,
            "label": st,
        })

    # Sector spokes for the 12 categories
    trails = []
    for i, (cat, color) in enumerate(CATEGORIES):
        sector_center = i * sector_arc - math.pi / 2
        trails.append({
            "sector": cat,
            "color": color,
            "points": [
                [round(math.cos(sector_center) * R_TYPES_INNER, 1),
                 round(math.sin(sector_center) * R_TYPES_INNER, 1)],
                [round(math.cos(sector_center) * R_TYPES_OUTER, 1),
                 round(math.sin(sector_center) * R_TYPES_OUTER, 1)],
            ],
        })

    return {
        "mode": "categories_flat",
        "labels": labels,
        "rings": rings,
        "trails": trails,
        "center": [0, 0],
        "extent": R_TYPES_LABEL_LANG + 80,
        "cards": cards_out,
        "category_colors": {k: v for k, v in CATEGORIES},
        "site_type_order": [st for st, _ in SITE_TYPE_RINGS],
        "site_type_colors": {st: c for st, c in SITE_TYPE_RINGS},
        "sector_arc_deg": round(math.degrees(sector_arc), 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/wander.db")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--max-cards", type=int, default=200000,
                    help="cap on cards in each layout (game requests up to 60K; layout JSON streams them all)")
    ap.add_argument("--rendered-only", action="store_true",
                    help="filter to cards with thumb_path IS NOT NULL; output layout_rendered.json with tighter packing")
    ap.add_argument("--categories-flat", action="store_true",
                    help="output layout_categories_flat.json (12 cat sectors x 6 site_type rings, fixed bounds — for /game's categories mode)")
    args = ap.parse_args()

    if args.rendered_only:
        # Tighter packing for the small (~17K) rendered subset → compact pinwheel.
        _LAYOUT_OPTS["where_extra"] = " AND thumb_path IS NOT NULL AND thumb_path != ''"
        _LAYOUT_OPTS["r_outer_buffer"] = 1.5  # was 5.0; small corpus → no hot-cell buffer needed
        _LAYOUT_OPTS["r_outer_min"] = 2200.0  # was 5500.0; let dense cats stay tight

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect(Path(args.db))

    t0 = time.time()

    # --categories-flat is a standalone build — skip the variable-arm pinwheel
    # and types layouts to save time. Used by refresh_layouts.sh on the same
    # cadence so /game's flat layout stays current.
    if args.categories_flat:
        flat_layout = layout_categories_flat(conn, args.max_cards)
        flat_path = out_dir / "layout_categories_flat.json"
        flat_path.write_text(json.dumps(flat_layout, separators=(",", ":")))
        print(f"[done] {flat_path}: {len(flat_layout['cards']):,} cards, {len(flat_layout['labels'])} labels, "
              f"{(flat_path.stat().st_size / 1024):.1f}KB", flush=True)
        print(f"\n[total] {time.time() - t0:.1f}s", flush=True)
        return

    cat_layout = layout_categories(conn, args.max_cards)
    cat_filename = "layout_rendered.json" if args.rendered_only else "layout_categories.json"
    cat_path = out_dir / cat_filename
    cat_path.write_text(json.dumps(cat_layout, separators=(",", ":")))
    print(f"[done] {cat_path}: {len(cat_layout['cards']):,} cards, {len(cat_layout['labels'])} labels, "
          f"{(cat_path.stat().st_size / 1024):.1f}KB", flush=True)

    if not args.rendered_only:
        type_layout = layout_types(conn, args.max_cards)
        type_path = out_dir / "layout_types.json"
        type_path.write_text(json.dumps(type_layout, separators=(",", ":")))
        print(f"[done] {type_path}: {len(type_layout['cards']):,} cards, {len(type_layout['labels'])} labels, "
              f"{(type_path.stat().st_size / 1024):.1f}KB", flush=True)

    # Real overlap audit — flag any two cards closer than (w_a + w_b)/2 (true geometric overlap)
    print("\n[audit] checking for building-to-building overlap...", flush=True)
    cards = cat_layout["cards"]
    GRID = 50  # spatial bucket size in world units
    buckets = defaultdict(list)
    def building_w(c):
        return 10 + (c.get("score", 0) + 6) * 0.30  # matches /world rendering formula
    for c in cards:
        bx = int(c["x"] // GRID)
        by = int(c["y"] // GRID)
        buckets[(bx, by)].append(c)
    overlaps = 0
    worst = 0.0
    for (bx, by), bucket in buckets.items():
        # Check against bucket + neighbors
        nearby = list(bucket)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0: continue
                nearby.extend(buckets.get((bx + dx, by + dy), []))
        for i, a in enumerate(bucket):
            wa = building_w(a)
            for b in nearby:
                if a is b or a["id"] >= b["id"]: continue
                dx = a["x"] - b["x"]; dy = a["y"] - b["y"]
                d = math.sqrt(dx*dx + dy*dy)
                wb = building_w(b)
                min_dist = (wa + wb) / 2
                if d < min_dist:
                    overlaps += 1
                    overlap_amt = min_dist - d
                    if overlap_amt > worst:
                        worst = overlap_amt
    pct = overlaps / len(cards) * 100 if cards else 0
    print(f"[audit] overlapping pairs: {overlaps}/{len(cards):,} cards ({pct:.3f}%); worst overlap: {worst:.2f} units", flush=True)

    print(f"\n[total] {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
