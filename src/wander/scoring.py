"""Page scoring: anti-SEO + hand-made signature feature extraction.

Composite score is hand_made_signature minus anti_seo. Higher means more long-tail-worthy
(weird, hand-crafted, dormant). Lower means more SEO-optimized (what Google rewards).

Used by:
  - Day 5-6 render pipeline to score each rendered page
  - Day 7-8 link graph for filtering before clustering
  - Day 11+ API for ranking the feed and laying out the game city
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import tldextract


# ---------------------------------------------------------------------------
# Heuristic indicator sets

ANALYTICS_HINTS = re.compile(
    r"(google-analytics|googletagmanager|gtag\(|gtm\.js|"
    r"hotjar|mixpanel|segment\.com|amplitude|heap\.io|"
    r"facebook\.net/.*fbevents|connect\.facebook\.net|"
    r"plausible\.io|fathom|matomo|piwik|mouseflow|fullstory|"
    r"clarity\.ms|datadog|sentry-cdn)",
    re.I,
)

AD_NETWORK_HINTS = re.compile(
    r"(googlesyndication|doubleclick|adsense|adnxs|adform|"
    r"taboola|outbrain|criteo|pubmatic|rubiconproject|"
    r"adsystem|adservice|advertising\.com|amazon-adsystem|"
    r"openx|sovrn|indexexchange|appnexus|mopub)",
    re.I,
)

CONSENT_BANNER_HINTS = re.compile(
    r"(cookiebot|onetrust|trustarc|cookie-?law|gdpr-?consent|"
    r"didomi|usercentrics|sourcepoint|quantcast)",
    re.I,
)

CDN_HINTS = re.compile(
    r"(cloudflare|cloudfront|fastly|akamai|cdn77|maxcdn|"
    r"jsdelivr|unpkg|cdnjs|bunny\.net|netlify|vercel)",
    re.I,
)

# Templates / page builders / CMSes that indicate template-driven (lower hand-made).
TEMPLATE_HINTS = re.compile(
    r"(wp-content|wordpress|squarespace|wix|webflow|"
    r"shopify|bigcommerce|medium\.com|substack|ghost\.io|"
    r"hubspot|mailchimp|notion\.site|carrd\.co)",
    re.I,
)

# Old-web / hand-coded indicators (90s-era tags, ancient practices).
OLD_HTML_TAGS = {"font", "center", "marquee", "blink", "frame", "frameset", "applet", "basefont"}

# Free-host / personal-page indicators (positive for hand-made cluster).
PERSONAL_HOST_HINTS = re.compile(
    r"(neocities\.org|geocities|tilde\.|sdf\.org|"
    r"\.tk$|\.cf$|\.ml$|"   # free TLDs (not strictly personal but often)
    r"angelfire|tripod|fortunecity|"
    r"github\.io|gitlab\.io|sourcehut\.site|"
    r"\.glitch\.me$|\.repl\.co$|\.netlify\.app$|\.vercel\.app$)",
    re.I,
)

# Big-platform subdomains (negative for "hand-made" — even though content is personal,
# the surface is platform-templated).
PLATFORM_SUBHOST_HINTS = re.compile(
    r"(\.medium\.com$|\.substack\.com$|\.wordpress\.com$|"
    r"\.blogspot\.com$|\.tumblr\.com$|\.wixsite\.com$|"
    r"\.squarespace\.com$|\.weebly\.com$)",
    re.I,
)

# Title patterns indicating an error/block/empty page. Multilingual.
# Matched anywhere in the title. Each language is a separate alternation.
ERROR_TITLE_PATTERNS = re.compile(
    r"("
    # ── English ───────────────────────────────────────────────────
    r"\b(404|403|500|502|503)\b"
    r"|not\s+found|site\s+not\s+configured|forbidden|access\s+denied"
    r"|page\s+(not\s+available|unavailable|removed|missing)"
    r"|connection\s+denied|geo(location)?\s*(blocked|denied|restricted)"
    r"|site\s+(unavailable|maintenance|under\s+construction)"
    r"|under\s+construction|coming\s+soon"
    r"|cloudflare|attention\s+required|just\s+a\s+moment|checking\s+your\s+browser"
    # ── Parked-domain marketplaces (catch all common phrasings) ───
    r"|(?:this\s+)?domain\s+(?:is\s+|name\s+)?for\s+sale|domain\s+parking"
    r"|premium\s+(?:short\s+)?domain|old\s+domain\s+for\s+sale"
    r"|domain\s+(?:has\s+already\s+been\s+|already\s+)?registered"
    r"|domain\s+(?:registered\s+at|management|registration)"
    r"|domain\s+(?:is\s+)?expired|expired\s+domain"
    r"|is\s+a\s+(?:custom\s+)?short\s+domain"
    r"|interested\s+(?:party|buyer)\s+(?:can|may)\s+(?:contact|reach)"
    r"|spaceship\.com|epik\.com|safenames|godaddy.*(?:auctions|listing)"
    r"|isimtescil|domain\s+ve\s+hosting|hosting\s+lideri"
    r"|sedo\s+(?:domain|listing)|name\.com|namecheap.*(?:listing|park)"
    r"|buy\s+(?:it\s+now|this\s+domain)|own\s+this\s+domain|make\s+(?:an\s+)?offer"
    # ── Generic CDN/server/error pages ────────────────────────────
    r"|directory\s+listing|index\s+of\s*/"
    r"|account\s+suspended|website\s+(suspended|expired|disabled)"
    r"|default\s+web\s+page|welcome\s+to\s+nginx|apache2\s+ubuntu\s+default"
    r"|server\s+error|application\s+error|runtime\s+error|compile\s+error|fatal\s+error"
    r"|web\s+server\s+is\s+returning|520:|521:|522:|523:|524:|525:|526:|527:|530:"
    r"|bad\s+gateway|gateway\s+timeout|service\s+unavailable"
    r"|request\s+could\s+not\s+be\s+satisfied|unable\s+to\s+satisfy"
    r"|^error$|^error\s*[:!]|page\s+error"
    r"|object\s+moved|untitled\s+(?:document|page)?$"
    # ── Chinese (Simplified + Traditional) ──────────────────────────
    r"|错误|錯誤"                                  # error
    r"|未找到|找不到|找不著"                       # not found
    r"|拒绝访问|拒絕存取"                          # access denied
    r"|服务器错误|伺服器錯誤"                      # server error
    r"|应用程序错误|應用程式錯誤"                  # application error
    r"|编译错误|編譯錯誤"                          # compile error
    r"|页面不存在|頁面不存在"                      # page does not exist
    r"|网站维护|網站維護"                          # site maintenance
    r"|无法访问|無法存取"                          # cannot access
    r"|敬请期待"                                   # coming soon
    # ── Japanese ───────────────────────────────────────────────────
    r"|エラー"                                     # error
    r"|見つかりません|見つからない"                # not found
    r"|アクセス\s*(?:拒否|できません)"             # access denied / can't access
    r"|お探しのページ"                             # the page you're looking for
    r"|ページが存在しません"                       # page doesn't exist
    r"|メンテナンス中"                             # under maintenance
    # ── Korean ─────────────────────────────────────────────────────
    r"|오류|에러"                                  # error
    r"|찾을\s*수\s*없"                             # cannot find
    r"|접근\s*(?:거부|할\s*수\s*없)"               # access denied
    r"|페이지를\s*찾을\s*수\s*없"                  # page not found
    # ── Russian ────────────────────────────────────────────────────
    r"|ошибка"                                     # error
    r"|не\s+найден"                                # not found
    r"|страница\s+не\s+(?:найдена|существует)"     # page not found / doesn't exist
    r"|доступ\s+(?:запрещ|отклон)"                 # access denied
    r"|сервис\s+недоступен"                        # service unavailable
    # ── German ─────────────────────────────────────────────────────
    r"|seite\s+nicht\s+gefunden"                   # page not found
    r"|nicht\s+gefunden"                           # not found
    r"|zugriff\s+verweigert"                       # access denied
    r"|wartungsarbeiten|im\s+aufbau"               # under maintenance / construction
    # ── French ─────────────────────────────────────────────────────
    r"|page\s+(?:non\s+trouv|introuvable)"         # page not found
    r"|acc[èe]s\s+refus[èe]"                       # access denied
    r"|en\s+construction|bient[ôo]t\s+disponible"  # under construction / coming soon
    # ── Spanish / Portuguese ───────────────────────────────────────
    r"|p[áa]gina\s+no\s+encontrada"                # page not found (es)
    r"|p[áa]gina\s+n[ãa]o\s+encontrada"            # page not found (pt)
    r"|acceso\s+denegado|acesso\s+negado"          # access denied
    r"|sitio\s+en\s+mantenimiento"                 # site under maintenance
    # ── Italian ────────────────────────────────────────────────────
    r"|pagina\s+non\s+trovata"                     # page not found
    r"|accesso\s+negato"                           # access denied
    r"|sito\s+in\s+manutenzione"                   # site under maintenance
    # ── Arabic ─────────────────────────────────────────────────────
    r"|الصفحة\s+غير\s+موجودة"                    # page not found
    r"|خطأ"                                        # error
    # ── Vietnamese ─────────────────────────────────────────────────
    r"|không\s+tìm\s+thấy"                         # not found
    r"|trang\s+không\s+tồn\s+tại"                  # page doesn't exist
    r")",
    re.I,
)

MIN_USEFUL_WORD_COUNT = 50


# ---------------------------------------------------------------------------
# Feature container

@dataclass
class PageFeatures:
    # Anti-SEO indicators (each True = +weight to anti_seo)
    has_schema_org: bool = False
    has_opengraph: bool = False
    has_twittercard: bool = False
    has_canonical: bool = False
    has_amp: bool = False
    has_analytics: bool = False
    has_ad_network: bool = False
    has_consent_banner: bool = False
    has_template_cms: bool = False
    has_cdn: bool = False
    is_platform_subhost: bool = False

    # Continuous SEO-load measurements
    js_payload_kb: float = 0.0
    n_external_scripts: int = 0
    n_meta_tags: int = 0

    # Hand-made signature indicators
    has_old_html_tags: bool = False
    has_inline_styles: bool = False         # <style> in head, not external CSS framework
    is_personal_host: bool = False
    has_email_contact: bool = False
    has_handwritten_css: bool = False        # custom (not Bootstrap/Tailwind/MUI signatures)
    n_external_stylesheets: int = 0          # fewer = simpler hand-coded
    text_to_html_ratio: float = 0.0          # higher = content over markup

    # Bookkeeping (not scored)
    title: str = ""
    meta_description: str = ""
    h1_count: int = 0
    word_count: int = 0
    image_count: int = 0
    iframe_count: int = 0

    # Content-validity gate: True only if the page has real content worth showing.
    # False for error pages, geo-blocks, parked domains, near-empty pages.
    is_useful_content: bool = True
    invalid_reason: str = ""

    # Outbound link hosts (deduplicated, registrable domain). Used by Day 7-8 link
    # graph to lay out the city. Limited to 256 to keep JSON rows small.
    outbound_hosts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Extraction

def extract_features(html: str, url: str) -> PageFeatures:
    """Parse HTML and extract scoring features."""
    f = PageFeatures()
    if not html:
        return f

    soup = BeautifulSoup(html, "lxml")

    # --- Title / meta basics ---
    title_tag = soup.find("title")
    if title_tag:
        f.title = (title_tag.string or "").strip()[:512]

    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        f.meta_description = md["content"].strip()[:1024]

    metas = soup.find_all("meta")
    f.n_meta_tags = len(metas)

    # --- Anti-SEO meta signals ---
    for m in metas:
        prop = (m.get("property") or m.get("name") or "").lower()
        if prop.startswith("og:"):
            f.has_opengraph = True
        if prop.startswith("twitter:"):
            f.has_twittercard = True

    if soup.find("link", rel="canonical"):
        f.has_canonical = True
    if soup.find("link", rel="amphtml") or soup.find("html", attrs={"amp": True}) or soup.find("html", attrs={"⚡": True}):
        f.has_amp = True
    if soup.find("script", attrs={"type": "application/ld+json"}):
        f.has_schema_org = True

    # --- Script analysis ---
    scripts = soup.find_all("script")
    external_scripts = [s for s in scripts if s.get("src")]
    f.n_external_scripts = len(external_scripts)

    inline_js_size = sum(len(s.string or "") for s in scripts if not s.get("src"))
    f.js_payload_kb = inline_js_size / 1024.0  # inline JS only here; external est'd at render time

    src_blob = " ".join(s.get("src", "") for s in external_scripts)
    inline_blob = " ".join((s.string or "") for s in scripts if not s.get("src"))[:50_000]
    src_and_inline = src_blob + " " + inline_blob

    if ANALYTICS_HINTS.search(src_and_inline):
        f.has_analytics = True
    if AD_NETWORK_HINTS.search(src_and_inline):
        f.has_ad_network = True
    if CONSENT_BANNER_HINTS.search(src_and_inline):
        f.has_consent_banner = True
    if TEMPLATE_HINTS.search(html[:200_000]):
        f.has_template_cms = True
    if CDN_HINTS.search(src_blob):
        f.has_cdn = True

    # --- Stylesheet analysis ---
    stylesheets = soup.find_all("link", rel="stylesheet")
    f.n_external_stylesheets = len(stylesheets)
    style_blob = " ".join(s.get("href", "") for s in stylesheets)
    f.has_handwritten_css = (
        f.n_external_stylesheets <= 2
        and not re.search(r"(bootstrap|tailwind|materialize|bulma|foundation|semantic-ui)", style_blob, re.I)
    )
    if soup.find("style"):
        f.has_inline_styles = True

    # --- Hand-made / 90s indicators ---
    for tag in OLD_HTML_TAGS:
        if soup.find(tag):
            f.has_old_html_tags = True
            break

    host = urlparse(url).netloc.lower()
    if PERSONAL_HOST_HINTS.search(host):
        f.is_personal_host = True
    if PLATFORM_SUBHOST_HINTS.search(host):
        f.is_platform_subhost = True

    # --- Content analysis ---
    for h1 in soup.find_all("h1"):
        f.h1_count += 1

    text = soup.get_text(" ", strip=True)
    f.word_count = len(text.split())

    if len(html) > 0:
        f.text_to_html_ratio = len(text) / len(html)

    f.image_count = len(soup.find_all("img"))
    f.iframe_count = len(soup.find_all("iframe"))

    body_text = text.lower()
    if re.search(r"(mailto:|contact me|email me|reach me|drop me a line|@\w+\.\w+)", body_text):
        f.has_email_contact = True

    # --- NSFW + gambling detection (multi-layer) ---
    # Lazy import to avoid a hard dependency cycle for tests that don't need blocklists.
    try:
        from .blocklist import host_in_blocklist, text_looks_nsfw, url_looks_nsfw, text_looks_gambling
        host_for_check = urlparse(url).netloc.lower()
        body_excerpt = (f.title + " " + f.meta_description + " " + text[:3000]).strip()
        if host_in_blocklist(host_for_check):
            f.is_useful_content = False
            f.invalid_reason = "nsfw_domain_blocklist"
        elif url_looks_nsfw(url):
            f.is_useful_content = False
            f.invalid_reason = "nsfw_url_pattern"
        elif text_looks_nsfw(body_excerpt):
            f.is_useful_content = False
            f.invalid_reason = "nsfw_body_pattern"
        elif text_looks_gambling(body_excerpt):
            f.is_useful_content = False
            f.invalid_reason = "gambling_body_pattern"
    except ImportError:
        pass

    # --- Outbound link extraction ---
    self_host = urlparse(url).netloc.lower()
    self_root = ""
    if self_host:
        ext = tldextract.extract(self_host)
        if ext.suffix and ext.domain:
            self_root = f"{ext.domain}.{ext.suffix}".lower()

    out_roots: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            absolute = urljoin(url, href)
            link_host = urlparse(absolute).netloc.lower()
        except Exception:
            continue
        if not link_host:
            continue
        ext = tldextract.extract(link_host)
        if not (ext.suffix and ext.domain):
            continue
        link_root = f"{ext.domain}.{ext.suffix}".lower()
        if link_root == self_root:
            continue  # skip self-links
        out_roots.add(link_root)
        if len(out_roots) >= 256:
            break
    f.outbound_hosts = sorted(out_roots)

    # --- Content-validity gate ---
    if f.word_count < MIN_USEFUL_WORD_COUNT:
        f.is_useful_content = False
        f.invalid_reason = f"too_few_words ({f.word_count})"
    elif f.title and ERROR_TITLE_PATTERNS.search(f.title):
        f.is_useful_content = False
        f.invalid_reason = "error_title_pattern"
    elif PARKED_HINT_BODY := re.search(r"(this\s+domain\s+(is\s+)?for\s+sale|buy\s+this\s+domain|domain\s+parking)", body_text[:5000], re.I):
        f.is_useful_content = False
        f.invalid_reason = "parked_body"

    return f


# ---------------------------------------------------------------------------
# Scoring

# Weights are deliberately simple — this is v0. Tune empirically once we have
# rendered samples and can eyeball score-vs-aesthetic alignment.
ANTI_SEO_WEIGHTS = {
    "has_schema_org":        1.5,
    "has_opengraph":         0.5,
    "has_twittercard":       0.5,
    "has_canonical":         0.3,
    "has_amp":               1.0,
    "has_analytics":         1.5,
    "has_ad_network":        2.5,
    "has_consent_banner":    1.0,
    "has_template_cms":      1.5,
    "has_cdn":               0.3,
    "is_platform_subhost":   1.0,
}

HAND_MADE_WEIGHTS = {
    "has_old_html_tags":     2.0,
    "has_inline_styles":     0.5,
    "is_personal_host":      2.0,
    "has_email_contact":     0.5,
    "has_handwritten_css":   1.0,
}


def composite_score(f: PageFeatures) -> dict:
    # Hard kill: if the page failed the content-validity gate, return a sentinel
    # composite that will sort it to the very bottom regardless of other features.
    if not f.is_useful_content:
        return {
            "anti_seo": 0.0,
            "hand_made": 0.0,
            "composite": -100.0,
            "killed": True,
            "kill_reason": f.invalid_reason,
        }

    anti = sum(w for k, w in ANTI_SEO_WEIGHTS.items() if getattr(f, k, False))

    # Continuous penalties for SEO-load.
    if f.n_external_scripts > 10:
        anti += min((f.n_external_scripts - 10) * 0.05, 2.0)
    if f.n_meta_tags > 20:
        anti += min((f.n_meta_tags - 20) * 0.02, 1.0)
    if f.js_payload_kb > 50:
        anti += min((f.js_payload_kb - 50) * 0.005, 1.5)

    hand = sum(w for k, w in HAND_MADE_WEIGHTS.items() if getattr(f, k, False))
    if f.text_to_html_ratio > 0.3:
        hand += min((f.text_to_html_ratio - 0.3) * 2, 1.0)
    if f.n_external_stylesheets <= 1:
        hand += 0.5

    composite = hand - anti
    return {
        "anti_seo": round(anti, 3),
        "hand_made": round(hand, 3),
        "composite": round(composite, 3),
    }


def score_page(html: str, url: str) -> dict:
    """Convenience: extract features + score in one call. Returns a dict suitable for storage."""
    feats = extract_features(html, url)
    scores = composite_score(feats)
    return {
        "url": url,
        "features": asdict(feats),
        "scores": scores,
    }
