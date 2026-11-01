"""التصفح الآلي الذكي: إيجاد خلاصة الموقع تلقائياً إن غاب الرابط أو تغيّر.

لماذا؟ روابط RSS تتغيّر أو تُحذف (رأينا 404 فعلياً في مصادر حقيقية)، والموقع
الذي لا يعرض RSS في واجهته قد يوفّره في `<link rel="alternate">`.

الطبقات:
1) ملفات شائعة: /feed, /rss, /rss.xml, /feed.xml, /atom.xml, /index.xml …
2) وسوم `<link rel="alternate" type="application/rss+xml|atom+xml">` في الصفحة.
3) روابط RSS ظاهرة في الصفحة (href يحتوي rss/feed/atom).
4) خلاصات WordPress / sitemap كخط أخير.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable

from ..utils import get_logger

log = get_logger("autonews.discover")

# قائمة قصيرة ومجرّبة: كل مسار إضافي = طلب شبكة قد يُحجب (رأينا 48 طلباً محجوباً)
COMMON_PATHS = (
    "/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml",
    "/feeds/posts/default", "/?feed=rss2",
)
_ALT_LINK_RE = re.compile(
    r'<link[^>]+type=["\'](application/rss\+xml|application/atom\+xml|application/feed\+json)["\'][^>]*>',
    re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_RSS_HINT_RE = re.compile(r'href=["\']([^"\']*(?:rss|feed|atom)[^"\']*)["\']', re.I)


@dataclass
class Discovered:
    url: str
    method: str          # common_path | link_tag | page_hint
    title: str = ""
    score: float = 0.0


def _looks_like_feed(raw: bytes) -> bool:
    """فحص سريع: هل المحتوى خلاصة فعلية؟"""
    head = raw[:4000].lower()
    if b"<rss" in head or b"<feed" in head:
        return True
    if b"<rdf" in head:            # RSS 1.0
        return True
    return b"<item" in head or b"<entry" in head


def _feed_title(raw: bytes) -> str:
    try:
        text = raw[:20_000].decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    match = _TITLE_RE.search(text)
    return " ".join(match.group(1).split())[:120] if match else ""


def probe_url(fetcher, url: str) -> tuple[bool, str]:
    """يفتح الرابط ويعيد (هل هو خلاصة، عنوانها)."""
    try:
        raw = fetcher(url)
    except Exception as exc:  # noqa: BLE001
        log.debug("فشل فحص %s: %s", url, exc)
        return False, ""
    if not raw or not _looks_like_feed(raw):
        return False, ""
    return True, _feed_title(raw)


def discover(site_url: str, fetcher, *, max_tries: int = 5,
             stop_after_first: bool = True) -> list[Discovered]:
    """محاولة إيجاد خلاصات لموقع ما.

    Args:
        site_url: رابط الموقع أو الصفحة (ليس الخلاصة).
        fetcher: دالة `fetcher(url) -> bytes` (تُرفع الاستثناءات).
    """
    results: list[Discovered] = []
    parsed = urllib.parse.urlparse(site_url)
    if not parsed.scheme:
        site_url = "https://" + site_url
        parsed = urllib.parse.urlparse(site_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # ---- 1) فحص الرابط نفسه (قد يكون خلاصة أصلاً) ----
    ok, title = probe_url(fetcher, site_url)
    if ok:
        results.append(Discovered(site_url, "direct", title, 1.0))
        return results

    # ---- 2) ملفات شائعة ----
    tries = 0
    for path in COMMON_PATHS:
        if tries >= max_tries:
            break
        tries += 1
        candidate = origin + path
        ok, title = probe_url(fetcher, candidate)
        if ok:
            results.append(Discovered(candidate, "common_path", title, 0.9))
            if stop_after_first:
                return results

    # ---- 3) وسوم <link rel=alternate> في الصفحة ----
    try:
        page = fetcher(site_url)
    except Exception:  # noqa: BLE001
        page = b""
    if page:
        text = page[:200_000].decode("utf-8", "replace")
        for tag_match in list(_ALT_LINK_RE.finditer(text))[:4]:
            tag = tag_match.group(0)
            href = _HREF_RE.search(tag)
            if not href:
                continue
            candidate = urllib.parse.urljoin(site_url, href.group(1))
            ok, title = probe_url(fetcher, candidate)
            if ok:
                results.append(Discovered(candidate, "link_tag", title, 1.0))
                if stop_after_first:
                    return results

        # ---- 4) روابط خلاصة ظاهرة في الصفحة ----
        for href in _RSS_HINT_RE.findall(text)[:4]:
            candidate = urllib.parse.urljoin(site_url, href)
            if any(r.url == candidate for r in results):
                continue
            ok, title = probe_url(fetcher, candidate)
            if ok:
                results.append(Discovered(candidate, "page_hint", title, 0.7))

    # إزالة التكرار وترتيب حسب الجودة
    unique: dict[str, Discovered] = {}
    for item in results:
        unique.setdefault(item.url, item)
    ranked = sorted(unique.values(), key=lambda d: -d.score)
    if ranked:
        log.info("اكتُشفت %d خلاصة لـ %s | الأفضل: %s (%s)",
                 len(ranked), origin, ranked[0].url, ranked[0].method)
    else:
        log.warning("لم تُعثر على خلاصة لـ %s", origin)
    return ranked


def first_feed(site_url: str, fetcher) -> str:
    """أقرب خلاصة صالحة (أو '' إن لم توجد)."""
    found = discover(site_url, fetcher)
    return found[0].url if found else ""
