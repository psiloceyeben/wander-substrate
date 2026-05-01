"""Render-on-demand pipeline.

One Playwright browser, many contexts, semaphore-bounded concurrency.
Each render returns HTML (for scoring) + WebP thumb bytes (for the card surface).
Full-page PNGs are NOT stored — we keep small thumbs and re-render on cache miss.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


# Mobile-portrait so the rendered card matches the feed viewport.
DEFAULT_VIEWPORT = {"width": 540, "height": 960}
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_THUMB_WIDTH = 540
DEFAULT_THUMB_QUALITY = 75


@dataclass
class RenderResult:
    url: str
    ok: bool
    html: str = ""
    thumb_bytes: bytes = b""
    elapsed_ms: int = 0
    final_url: str = ""           # after redirects
    error: str = ""
    status: int = 0


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def make_thumb(screenshot_bytes: bytes,
               max_width: int = DEFAULT_THUMB_WIDTH,
               quality: int = DEFAULT_THUMB_QUALITY) -> bytes:
    """PNG screenshot bytes -> WebP thumb at max_width, preserving aspect ratio.

    Crops to a max height of 4x width so we don't store giant scrolling pages.
    """
    img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
    max_h = max_width * 4
    if img.height > max_h:
        img = img.crop((0, 0, img.width, max_h))
    if img.width > max_width:
        ratio = max_width / img.width
        new_h = int(img.height * ratio)
        img = img.resize((max_width, new_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WebP", quality=quality, method=4)
    return out.getvalue()


async def render_one(browser: Browser,
                     url: str,
                     viewport: dict = DEFAULT_VIEWPORT,
                     timeout_ms: int = DEFAULT_TIMEOUT_MS) -> RenderResult:
    """Render a single URL in a fresh context. No persistent state, no shared cookies."""
    t0 = time.time()
    context: Optional[BrowserContext] = None
    try:
        context = await browser.new_context(
            viewport=viewport,
            user_agent="wander-discovery-research/0.0.1 (+psiloceyeben@github)",
            ignore_https_errors=True,
        )
        # Block heavyweight third-party trackers/ads that would skew render time and JS payload.
        # Note: this changes what the scoring sees. We block aggressive third-party stuff
        # AFTER scoring (we want to count the calls), so for this v0 we let them through.
        page: Page = await context.new_page()

        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Soft-wait for additional network/render settle, but don't block forever.
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

        html = await page.content()
        screenshot_bytes = await page.screenshot(type="png", full_page=True)
        thumb = make_thumb(screenshot_bytes)

        return RenderResult(
            url=url,
            ok=True,
            html=html,
            thumb_bytes=thumb,
            elapsed_ms=int((time.time() - t0) * 1000),
            final_url=page.url,
            status=response.status if response else 0,
        )
    except Exception as e:
        return RenderResult(
            url=url,
            ok=False,
            elapsed_ms=int((time.time() - t0) * 1000),
            error=f"{type(e).__name__}: {str(e)[:200]}",
        )
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def render_batch(urls: list[str],
                       parallelism: int = 4,
                       viewport: dict = DEFAULT_VIEWPORT,
                       timeout_ms: int = DEFAULT_TIMEOUT_MS,
                       on_result=None) -> list[RenderResult]:
    """Render many URLs with bounded concurrency. on_result fires per completion."""
    sem = asyncio.Semaphore(parallelism)
    results: list[RenderResult] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        async def run(url):
            async with sem:
                r = await render_one(browser, url, viewport, timeout_ms)
                results.append(r)
                if on_result:
                    on_result(r)
                return r
        await asyncio.gather(*[run(u) for u in urls])
        await browser.close()

    return results


def save_thumb(thumb_bytes: bytes, url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{url_hash(url)}.webp"
    path.write_bytes(thumb_bytes)
    return path
