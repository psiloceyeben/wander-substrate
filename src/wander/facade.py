"""Procedural building facade generator.

Given a card's PageFeatures dict, render a single SVG building tile.
The game (Phaser 3) consumes these as in-world buildings; the facade is
deterministic from features so the same URL always produces the same building.

Tile target: 64×96 px (configurable). Era and score drive paint and silhouette.
No screenshot needed — the building IS the parametric expression of the page.
"""

from __future__ import annotations

import hashlib
from typing import Optional

# ── Era palette ────────────────────────────────────────────────────────
# Hand-tuned to match the registers we see in scored data.
ERA_PALETTE = {
    "old_web":       {"wall": "#e8d8b0", "trim": "#7c5a2c", "sign": "#3a2010", "roof": "#a05030"},
    "midweb":        {"wall": "#c8d2c4", "trim": "#5e7068", "sign": "#1a1f1c", "roof": "#7a8a80"},
    "template":      {"wall": "#dbd6d0", "trim": "#a8a298", "sign": "#3a3530", "roof": "#8a847c"},
    "seo_hardened":  {"wall": "#cfd6e0", "trim": "#46566a", "sign": "#0e1622", "roof": "#5a6c80"},
    "modern_spa":    {"wall": "#1e242c", "trim": "#9aa9bc", "sign": "#e6ecf2", "roof": "#0c1014"},
    "outskirts":     {"wall": "#5a4030", "trim": "#3a2818", "sign": "#1e1410", "roof": "#2a1c10"},
}


def _hash_jitter(seed_str: str, span: int = 3) -> int:
    """Deterministic small offset from a string seed."""
    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    return (h[0] % (2 * span + 1)) - span


def building_svg(features: dict,
                 score: float = 0.0,
                 era: Optional[str] = None,
                 width: int = 64,
                 height: int = 96,
                 url_seed: str = "") -> str:
    """Return SVG markup for a single building tile."""
    era = era or "midweb"
    palette = ERA_PALETTE.get(era, ERA_PALETTE["midweb"])

    # ── Silhouette: height proportional to content density ──────────────
    word_count = features.get("word_count", 0) or 0
    n_scripts = features.get("n_external_scripts", 0) or 0
    n_h1 = features.get("h1_count", 0) or 0
    n_images = features.get("image_count", 0) or 0

    # Body fills [4, height-4] vertically; we vary how tall the building reaches.
    density_score = min(1.0, (word_count / 1000.0 + n_scripts / 30.0) / 2.0)
    body_h = int((height - 8) * (0.45 + 0.55 * density_score))
    body_w = width - 8
    body_x = 4
    body_y = height - 4 - body_h

    # ── Roof shape from era ────────────────────────────────────────────
    roof_h = 6 if era in ("old_web", "midweb") else 3
    roof_y = body_y - roof_h

    # ── Door ──────────────────────────────────────────────────────────
    door_w = 8
    door_h = 14
    door_x = body_x + (body_w - door_w) // 2 + _hash_jitter(url_seed + "doorx", 2)
    door_y = height - 4 - door_h

    # ── Windows: rows derived from features ─────────────────────────────
    n_window_rows = max(1, min(int(body_h / 14), 5))
    n_window_cols = max(1, min(2 + n_h1 // 2, 4))
    window_color = palette["sign"]

    windows = []
    for row in range(n_window_rows):
        wy = body_y + 6 + row * 14
        if wy + 6 >= door_y:  # don't overlap door
            continue
        for col in range(n_window_cols):
            spacing = (body_w - 6) / (n_window_cols + 1)
            wx = body_x + 3 + int((col + 1) * spacing) - 3
            # Era-based window pattern.
            if era == "modern_spa":
                # Long horizontal slits.
                windows.append(f'<rect x="{wx-1}" y="{wy}" width="6" height="2" fill="{window_color}" opacity="0.85"/>')
            elif era == "old_web":
                # Tall narrow windows with light from inside (yellow).
                windows.append(f'<rect x="{wx}" y="{wy}" width="4" height="6" fill="#f4d870" opacity="0.9"/>')
            else:
                windows.append(f'<rect x="{wx}" y="{wy}" width="4" height="4" fill="{window_color}" opacity="0.7"/>')

    # ── Score halo: subtle outline tint by composite ────────────────────
    if score > 1.5:
        halo = f'<rect x="{body_x-1}" y="{roof_y-1}" width="{body_w+2}" height="{body_h+roof_h+2}" fill="none" stroke="#9af090" stroke-width="0.6" opacity="0.5"/>'
    elif score < -5:
        halo = f'<rect x="{body_x-1}" y="{roof_y-1}" width="{body_w+2}" height="{body_h+roof_h+2}" fill="none" stroke="#7a3a3a" stroke-width="0.6" opacity="0.4"/>'
    else:
        halo = ""

    # ── Old-web extras (only old_web era) ───────────────────────────────
    extras = ""
    if era == "old_web":
        # Animated scrolling marquee feel: a small banner above the door.
        banner_w = body_w - 10
        bx = body_x + 5
        extras += f'<rect x="{bx}" y="{door_y - 7}" width="{banner_w}" height="4" fill="#f4d870" stroke="#7c5a2c" stroke-width="0.5"/>'

    if features.get("has_email_contact"):
        # A mailbox by the door.
        mx = door_x + door_w + 1
        my = door_y + door_h - 6
        extras += f'<rect x="{mx}" y="{my}" width="3" height="4" fill="{palette["trim"]}"/>'

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" shape-rendering="crispEdges">',
        f'<rect x="0" y="{height-4}" width="{width}" height="4" fill="#2a2018"/>',
        f'<polygon points="{body_x},{body_y} {body_x+body_w},{body_y} {body_x+body_w-2},{roof_y} {body_x+2},{roof_y}" fill="{palette["roof"]}"/>',
        f'<rect x="{body_x}" y="{body_y}" width="{body_w}" height="{body_h}" fill="{palette["wall"]}" stroke="{palette["trim"]}" stroke-width="0.8"/>',
        "".join(windows),
        f'<rect x="{door_x}" y="{door_y}" width="{door_w}" height="{door_h}" fill="{palette["sign"]}" stroke="{palette["trim"]}" stroke-width="0.5"/>',
        f'<circle cx="{door_x + door_w - 2}" cy="{door_y + door_h - 7}" r="0.6" fill="{palette["trim"]}"/>',
        extras,
        halo,
        '</svg>',
    ]
    return "".join(parts)


def rich_site_facade(host: str, category: str, era: str, score: float,
                     title: str = "", width: int = 320, height: int = 480) -> str:
    """Larger, richer 'site impression' SVG for use as a lazy-thumb fallback.
    Encodes host as visible masthead, era-typed typography, category-suggested
    layout. Generated on demand from features alone — no rendering pipeline."""
    palette = ERA_PALETTE.get(era, ERA_PALETTE["midweb"])
    cat_color = {
        "tech": "#7aa9d8", "art": "#e0a060", "music": "#c870c0", "food": "#e07050",
        "gaming": "#9080d0", "science": "#60c0a0", "education": "#a0c068",
        "news": "#d8a8a8", "sports": "#e0c060", "commerce": "#9aa9bc",
        "community": "#80c8d0", "personal": "#d8a050", "misc": "#909098",
    }.get(category or "misc", "#909098")
    # Era-specific font stacks
    era_font = {
        "old_web":      "'Comic Sans MS', 'Marker Felt', cursive",
        "midweb":       "Verdana, Geneva, sans-serif",
        "template":     "Arial, Helvetica, sans-serif",
        "seo_hardened": "'Helvetica Neue', Arial, sans-serif",
        "modern_spa":   "'SF Mono', 'Menlo', monospace",
        "outskirts":    "Courier, monospace",
    }.get(era, "Arial, sans-serif")
    host_short = (host or "untitled")[:28]
    title_short = (title or "")[:48]

    # Background varies subtly by era
    bg = palette["wall"]
    fg = palette["sign"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        # Outer frame (category color)
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{cat_color}"/>',
        # Inner page area
        f'<rect x="6" y="6" width="{width-12}" height="{height-12}" fill="{bg}"/>',
        # Header bar with host
        f'<rect x="6" y="6" width="{width-12}" height="56" fill="{cat_color}"/>',
        f'<text x="{width//2}" y="42" text-anchor="middle" font-family="{era_font}" '
        f'font-size="22" font-weight="bold" fill="#fafafa">{_xml_escape(host_short)}</text>',
    ]

    # Per-category body layout
    cat_lower = (category or "misc").lower()
    body_top = 76
    if cat_lower == "personal":
        # Notebook-style horizontal lines (like a journal/blog)
        parts.append(f'<text x="20" y="{body_top+8}" font-family="{era_font}" font-size="16" fill="{fg}">{_xml_escape(title_short)}</text>')
        for i in range(8):
            y = body_top + 36 + i * 22
            parts.append(f'<line x1="20" y1="{y}" x2="{width-20}" y2="{y}" stroke="{fg}" stroke-width="1" opacity="0.35"/>')
    elif cat_lower == "tech":
        # Code-block pattern
        parts.append(f'<text x="20" y="{body_top+8}" font-family="monospace" font-size="14" fill="{fg}">{_xml_escape(title_short)}</text>')
        lines = ["function init() {", "  const n = 7;", "  return n * 2;", "}", "// hello world", "//", "init();"]
        for i, line in enumerate(lines):
            y = body_top + 36 + i * 18
            parts.append(f'<text x="20" y="{y}" font-family="monospace" font-size="12" fill="{fg}" opacity="0.6">{_xml_escape(line)}</text>')
    elif cat_lower == "art":
        # Image grid placeholders
        parts.append(f'<text x="20" y="{body_top+8}" font-family="{era_font}" font-size="14" fill="{fg}">{_xml_escape(title_short)}</text>')
        cols = 3
        cell_w = (width - 60) // cols
        for i in range(6):
            row = i // cols
            col = i % cols
            x = 20 + col * (cell_w + 10)
            y = body_top + 30 + row * (cell_w + 10)
            tint = ["#e0a060", "#c870c0", "#9080d0", "#60c0a0", "#e07050", "#d8a8a8"][i]
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_w}" fill="{tint}" opacity="0.7"/>')
    elif cat_lower == "news":
        # Multi-column text columns
        parts.append(f'<text x="20" y="{body_top+8}" font-family="serif" font-size="18" font-weight="bold" fill="{fg}">{_xml_escape(title_short)}</text>')
        col_w = (width - 50) // 2
        for col in range(2):
            x = 20 + col * (col_w + 10)
            for i in range(10):
                y = body_top + 36 + i * 18
                parts.append(f'<line x1="{x}" y1="{y}" x2="{x+col_w}" y2="{y}" stroke="{fg}" stroke-width="1.2" opacity="0.45"/>')
    elif cat_lower == "music":
        # Equalizer bars + cover thumbnail
        parts.append(f'<text x="20" y="{body_top+8}" font-family="{era_font}" font-size="16" fill="{fg}">{_xml_escape(title_short)}</text>')
        parts.append(f'<rect x="20" y="{body_top+30}" width="100" height="100" fill="{cat_color}" opacity="0.7"/>')
        for i in range(20):
            bx = 140 + i * 8
            bh = 20 + (i * 13 + len(host)) % 60
            parts.append(f'<rect x="{bx}" y="{body_top+30+100-bh}" width="6" height="{bh}" fill="{cat_color}" opacity="0.8"/>')
    elif cat_lower in ("commerce", "gaming", "food"):
        # Product/icon grid
        parts.append(f'<text x="20" y="{body_top+8}" font-family="{era_font}" font-size="14" fill="{fg}">{_xml_escape(title_short)}</text>')
        cols, rows_n = 3, 3
        cell_w = (width - 60) // cols
        for i in range(cols * rows_n):
            row = i // cols
            col = i % cols
            x = 20 + col * (cell_w + 10)
            y = body_top + 30 + row * (cell_w + 10)
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_w}" fill="{cat_color}" opacity="0.5"/>')
            parts.append(f'<rect x="{x}" y="{y+cell_w-12}" width="{cell_w}" height="12" fill="{fg}" opacity="0.4"/>')
    elif cat_lower == "community":
        # Forum threads
        parts.append(f'<text x="20" y="{body_top+8}" font-family="{era_font}" font-size="14" fill="{fg}">{_xml_escape(title_short)}</text>')
        for i in range(5):
            y = body_top + 30 + i * 56
            parts.append(f'<rect x="20" y="{y}" width="{width-40}" height="48" fill="{cat_color}" opacity="0.15" stroke="{fg}" stroke-width="0.5"/>')
            parts.append(f'<circle cx="40" cy="{y+24}" r="14" fill="{cat_color}" opacity="0.6"/>')
            parts.append(f'<line x1="62" y1="{y+18}" x2="{width-30}" y2="{y+18}" stroke="{fg}" stroke-width="1.2" opacity="0.4"/>')
            parts.append(f'<line x1="62" y1="{y+32}" x2="{width-60}" y2="{y+32}" stroke="{fg}" stroke-width="1" opacity="0.3"/>')
    else:
        # Default: structured text mockup
        parts.append(f'<text x="20" y="{body_top+8}" font-family="{era_font}" font-size="14" fill="{fg}">{_xml_escape(title_short)}</text>')
        for i in range(12):
            y = body_top + 36 + i * 22
            line_len = 0.3 + ((i + len(host)) % 7) * 0.1
            parts.append(f'<line x1="20" y1="{y}" x2="{int(20 + (width-40) * line_len)}" y2="{y}" stroke="{fg}" stroke-width="1.2" opacity="0.4"/>')

    # Footer with score badge + category chip
    score_color = "#aef0a8" if score >= 0 else "#f0a8a8"
    parts.append(f'<rect x="6" y="{height-32}" width="{width-12}" height="26" fill="{cat_color}" opacity="0.9"/>')
    parts.append(f'<text x="14" y="{height-13}" font-family="{era_font}" font-size="13" font-weight="bold" fill="#fafafa">{_xml_escape(category)}</text>')
    sign = "+" if score >= 0 else ""
    parts.append(f'<text x="{width-14}" y="{height-13}" text-anchor="end" font-family="{era_font}" font-size="13" font-weight="bold" fill="{score_color}">score {sign}{score:.1f}</text>')
    parts.append('</svg>')
    return "".join(parts)


def _xml_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def building_data(card: dict) -> dict:
    """Return a compact dict describing the building (for game-engine consumption)."""
    from .graph import infer_era
    feats = card.get("features", {})
    era = infer_era(feats)
    return {
        "url": card["url"],
        "host": card.get("final_url", card["url"]).split("/")[2] if "://" in card.get("final_url", card["url"]) else "",
        "title": (feats.get("title") or "")[:80],
        "era": era,
        "score": card.get("scores", {}).get("composite", 0.0),
        "params": {
            "word_count": feats.get("word_count", 0),
            "n_external_scripts": feats.get("n_external_scripts", 0),
            "h1_count": feats.get("h1_count", 0),
            "image_count": feats.get("image_count", 0),
            "iframe_count": feats.get("iframe_count", 0),
            "has_old_html_tags": feats.get("has_old_html_tags", False),
            "has_email_contact": feats.get("has_email_contact", False),
            "has_template_cms": feats.get("has_template_cms", False),
        },
    }
