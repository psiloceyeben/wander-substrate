"""NSFW + spam blocklist loader and matcher.

Multi-layer defense:
  1. Domain blocklist (StevenBlack porn-only hosts file, fetched by 00_fetch_blocklists.py)
  2. URL pattern matcher (Latin + Cyrillic NSFW keywords)
  3. Title/body pattern matcher (Latin + Cyrillic, multilingual)

Used at pull-time (01_pull_urls.py) and render-time (scoring.py validity gate).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import tldextract


DEFAULT_BLOCKLIST_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "blocklists"


# ── URL keyword filter (cheap, runs at pull-time on every URL) ────────
URL_NSFW_PATTERNS = re.compile(
    r"("
    # Latin / English (word-boundary-anchored where the token is short/ambiguous)
    r"\bporn\b|\bxxx\b|\bsex(?!ton|tant|ual-(?:health|abuse|education))\b|"
    r"adult[-_\s]*(?:vid|cam|chat|content|film|movies?)|\bescort\b|"
    r"\bnude\b|\bfuck\b|\bcum(?:shot|swap)?\b|\borgy\b|"
    r"tube8|xvideos|pornhub|redtube|youporn|xhamster|brazzers|chaturbate|"
    r"\bonlyfans\b|stripchat|bongacams|livejasmin|\bcam4\b|myfreecams|"
    r"\bhentai\b|rule34|nhentai|\bshemale\b|\btranny\b|"
    # Romanized Russian / Slavic — word-bounded so "huy" doesn't match "shuhuyiliew"
    r"\bporno\b|\bseks\b|\bpizda\b|\bxuy\b|\bhuy\b|\btrah(?:at|al|nut)\b|\bminet\b|"
    r"anal[-_](?:porn|video|sex)|"
    # Cyrillic (Cyrillic letters provide implicit word boundaries against Latin chars)
    r"порно|"
    r"секс[^ъ]|"
    r"голая|"
    r"эроти"
    r")",
    re.I,
)


# ── Title/content keyword filter (richer patterns at render-time) ─────
NSFW_TITLE_BODY_PATTERNS = re.compile(
    r"("
    # Latin
    r"\bporn\b|\bxxx\b|\bnude\b|\bfuck(?:ing)?\b|\bsex\s+(?:video|tube|cam|chat|tape|story|tonight)|"
    r"\borgy\b|\banal\s+(?:sex|porn|video)|\bblowjob\b|\bhandjob\b|\bcum\s*shot|"
    r"adult\s*(?:video|tube|content|film|movies)|free\s+porn|teen\s+(?:porn|sex|nude|xxx)|"
    r"hentai|shemale|tranny|escort\s+(?:girls|service|agency)|"
    r"\bcam\s*girl|live\s+sex|webcam\s+(?:girls|sex|chat)|"
    # Romanized Russian / common spam English-on-RU
    r"russkoe?\s+porno|pizda|pizdec|trahaem|trahnut|"
    # Cyrillic phrases
    r"порно|"                                  # порно
    r"секс\s+(?:видео|"    # секс видео
    r"чат|"                                              # секс чат
    r"бесплатно)|"         # секс бесплатно
    r"голые?\s+девушк|"  # голые девушки
    r"эротическ|"          # эротический
    r"обнажен|"                     # обнажен(ная)
    r"инцест|"                            # инцест
    r"анальный\s+секс|"  # анальный секс
    r"минет|"                                  # минет
    # Other languages
    r"\bsesso\b|\bnu(?:ovissimo)?\s+porno|"                            # Italian
    r"\bsexe\b\s+(?:gratuit|porno|cam)|"                                # French
    r"sexo\s+(?:gratis|porno|video)|"                                   # Spanish/Portuguese
    # Chinese porn / adult-content SEO patterns
    r"自产拍|"                                                          # self-shot (very specific)
    r"国产\s*(?:精品|视频|在线|自拍|偷拍|无码|人妻)|"                   # domestic + porn term
    r"亚洲\s*(?:精品|无码|av|色情|成人)|"                               # asian + adult
    r"成人\s*(?:视频|电影|网站|在线|片|影院)|"                          # adult + video/movie/site
    r"色情|"                                                            # porn (explicit)
    r"三级片|三級片|"                                                   # level-3 film
    r"av\s*(?:在线|免费|网站)|"                                         # av (porn) + online/free
    r"无码\s*(?:视频|高清|在线)|"                                       # uncensored + video
    r"\s性爱|"                                                          # sex
    # Chinese piracy/streaming aggregators (heavy NSFW overlap)
    r"免费高清\s*(?:在线观看|完整电影|视频|电影)|"
    r"高清电影\s*在线|在线观看\s*(?:完整|高清)|"
    r"影视\s*(?:大全|网|站)|"
    r"牛牛\s*(?:影视|视频)|"
    r"日逼|操逼|约炮|"
    r"黄(?:网|色\s*(?:网|站|片))|"
    r"夜生活|按摩.*(?:服务|包养)"
    r")",
    re.I,
)


# ── Gambling / lottery / scam patterns (separate category, but same kill effect) ──
GAMBLING_TITLE_BODY_PATTERNS = re.compile(
    r"("
    # Indonesian gambling (extremely prevalent in Common Crawl long-tail)
    r"\btogel\s*(?:singapore|sgp|hk|hongkong|sydney|hari|online)?|"
    r"\bslot\s*(?:online|gacor|terpercaya|deposit|pulsa)|"
    r"keluaran\s*(?:sgp|hk|togel|sydney)|"
    r"pengeluaran\s*(?:sgp|hk|togel|sydney)|"
    r"\bdata\s*(?:sgp|hk|togel|sydney)\s*(?:hari|prize)?|"
    r"\bidn\s*(?:slot|togel|poker|live)|"
    r"bandar\s+(?:togel|slot|judi|bola)|"
    r"agen\s+(?:togel|slot|judi|bola)|"
    r"link\s+(?:slot|togel)\s+gacor|"
    r"situs\s+(?:slot|judi|togel)|"
    r"daftar\s+(?:slot|togel|judi)|"
    r"\bjudi\s+(?:online|bola|slot)|"
    # Chinese lottery/gambling
    r"天下\d+\s*(?:天|心水)|"          # "天下246天天心水"
    r"心水\s*(?:资料|论坛|料)|"        # 心水资料
    r"六合彩|特码|开奖结果|跑狗图|"    # mark-six lottery, race-dog chart
    r"码报|赛马会|博彩|老虎机|"        # number reports, gambling, slot machines
    r"澳门(?:威尼斯人|金沙|永利|新葡|银河)|"  # Macau casinos
    r"娱乐城|彩票网|彩票投注|"
    r"BC\.GAME|stake\.com|"             # crypto casinos
    # Korean gambling sites
    r"슬롯\s*사이트|토토\s*사이트|"
    r"먹튀\s*(?:검증|사이트)|"
    r"안전놀이터|메이저놀이터|"
    r"온라인카지노|바카라\s*사이트|"
    # Generic English crypto/online casino spam
    r"\b(?:online|live)\s+casino\s+(?:bonus|deposit|free)|"
    r"crypto\s+casino|"
    r"sports?betting\s+(?:tips|bonus)"
    r")",
    re.I,
)


def text_looks_gambling(text: str) -> bool:
    """Returns True if title/snippet matches gambling/lottery/scam patterns."""
    if not text:
        return False
    return bool(GAMBLING_TITLE_BODY_PATTERNS.search(text))


@lru_cache(maxsize=1)
def load_porn_blocklist(blocklist_dir: Optional[Path] = None) -> frozenset[str]:
    """Load StevenBlack porn-only blocklist as a frozenset of registrable roots.

    Returns empty set (with stderr warning) if file missing — scripts shouldn't crash
    just because the blocklist hasn't been fetched yet."""
    bld = blocklist_dir or DEFAULT_BLOCKLIST_DIR
    path = bld / "porn_domains.txt"
    if not path.exists():
        import sys
        print(f"[blocklist] WARNING: {path} not found — run scripts/00_fetch_blocklists.py", file=sys.stderr)
        return frozenset()
    domains = set()
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Lines from hosts file are "0.0.0.0 domain.com" or just "domain.com"
            parts = line.split()
            host = parts[-1].lower()
            if not host or "." not in host:
                continue
            ext = tldextract.extract(host)
            if ext.suffix and ext.domain:
                root = f"{ext.domain}.{ext.suffix}".lower()
                domains.add(root)
                # Also keep the bare host so subdomains match.
                domains.add(host)
    return frozenset(domains)


def host_in_blocklist(host: str, blocklist: Optional[frozenset[str]] = None) -> bool:
    """Check if host or its registrable root is in the porn blocklist."""
    if blocklist is None:
        blocklist = load_porn_blocklist()
    if not blocklist:
        return False
    host = host.lower().strip()
    if host in blocklist:
        return True
    ext = tldextract.extract(host)
    if ext.suffix and ext.domain:
        root = f"{ext.domain}.{ext.suffix}".lower()
        if root in blocklist:
            return True
    return False


def url_looks_nsfw(url: str) -> bool:
    return bool(URL_NSFW_PATTERNS.search(url))


def text_looks_nsfw(text: str) -> bool:
    """Returns True if title/meta_description/body excerpt matches NSFW patterns.
    Pass the title + meta_description + first ~3KB of body for best signal."""
    if not text:
        return False
    return bool(NSFW_TITLE_BODY_PATTERNS.search(text))
