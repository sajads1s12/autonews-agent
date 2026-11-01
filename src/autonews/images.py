"""سحب صورة الخبر من صفحة المصدر (Open Graph / Twitter Card).

لماذا؟ منشور بلا صورة يفقد قدراً كبيراً من التفاعل — خصوصاً في تيليجرام
وفيسبوك. نقرأ وسوم og:image من صفحة الخبر نفسها (بلا مكتبات إضافية)،
ونتحقّق من نوع الصورة وحجمها قبل تمريرها لناشر المنصّة.
"""
from __future__ import annotations

import html
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config_loader import ImageSettings
from .utils import get_logger

log = get_logger("autonews.images")

_ALLOWED_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",   # RIFF....WEBP — نتحقّق أدناه
}
_META_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)', re.I),
]
_SKIP_HINTS = ("logo", "icon", "sprite", "avatar", "placeholder", "default", "1x1", "pixel")


@dataclass(slots=True)
class ArticleImage:
    path: str = ""
    url: str = ""
    alt: str = ""
    mime: str = "image/jpeg"

    @property
    def ok(self) -> bool:
        return bool(self.path and Path(self.path).exists())

    def cleanup(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                os.unlink(self.path)
            except OSError:
                pass
        self.path = ""


def _fetch(url: str, *, timeout: int, max_bytes: int, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 (compatible; AutoNewsAgent/1.0)",
        "Accept": "text/html,image/*;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"الملف أكبر من الحد ({max_bytes} بايت)")
        return data


def find_image_url(page_url: str, *, timeout: int = 12, max_bytes: int = 900_000,
                   settings: ImageSettings | None = None) -> tuple[str, str]:
    """استخراج رابط الصورة ووصفها (alt) من صفحة الخبر."""
    try:
        raw = _fetch(page_url, timeout=timeout, max_bytes=max_bytes)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        log.debug("تعذّر تحميل صفحة المصدر %s: %s", page_url, exc)
        return "", ""

    charset = "utf-8"
    match = re.search(rb'charset=["\']?([\w-]+)', raw[:3000], re.I)
    if match:
        charset = match.group(1).decode("ascii", "ignore")
    try:
        page = raw.decode(charset, "replace")
    except LookupError:
        page = raw.decode("utf-8", "replace")

    for pattern in _META_PATTERNS:
        m = pattern.search(page)
        if not m:
            continue
        candidate = html.unescape(m.group(1).strip())
        if not candidate:
            continue
        absolute = urllib.parse.urljoin(page_url, candidate)
        if any(hint in absolute.lower() for hint in _SKIP_HINTS):
            continue
        alt = ""
        alt_match = re.search(r'property=["\']og:image:alt["\'][^>]+content=["\']([^"\']+)', page, re.I)
        if alt_match:
            alt = html.unescape(alt_match.group(1))
        return absolute, alt
    return "", ""


def download_image(url: str, *, timeout: int = 15, max_bytes: int = 5_000_000) -> ArticleImage:
    """تحميل الصورة إلى ملف مؤقّت والتحقّق من نوعها فعلياً (لا نثق بالامتداد)."""
    if not url:
        return ArticleImage()
    try:
        data = _fetch(url, timeout=timeout, max_bytes=max_bytes, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AutoNewsAgent/1.0)",
            "Accept": "image/*",
            "Referer": urllib.parse.urlparse(url)._replace(path="/", params="", query="", fragment="").geturl(),
        })
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        log.debug("تعذّر تحميل الصورة %s: %s", url, exc)
        return ArticleImage(url=url)

    if len(data) < 2000:            # صور صغيرة جداً = أيقونات/بكسلات تتبّع
        log.debug("صورة صغيرة جداً (تجاهل): %d بايت", len(data))
        return ArticleImage(url=url)

    ext, mime = "", ""
    for magic, kind in _ALLOWED_MAGIC.items():
        if data.startswith(magic):
            if kind == "webp" and data[8:12] != b"WEBP":
                continue
            ext = {"jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}[kind]
            mime = f"image/{'jpeg' if kind == 'jpg' else kind}"
            break
    if not ext:
        log.debug("نوع صورة غير مدعوم: %s", url)
        return ArticleImage(url=url)

    tmp = tempfile.NamedTemporaryFile(prefix="autonews_", suffix=f".{ext}", delete=False)
    try:
        tmp.write(data)
        tmp.close()
    except OSError:
        return ArticleImage(url=url)
    return ArticleImage(path=tmp.name, url=url, mime=mime)


def fetch_article_image(page_url: str, *, settings: ImageSettings) -> ArticleImage:
    """الواجهة الكاملة: صفحة → رابط → تنزيل. تفشل بهدوء دائماً."""
    if not settings.enabled or not page_url:
        return ArticleImage()
    url, alt = find_image_url(page_url, timeout=settings.timeout, settings=settings)
    if not url:
        return ArticleImage()
    image = download_image(url, timeout=settings.timeout + 3, max_bytes=settings.max_bytes)
    image.alt = alt
    if image.ok:
        log.info("🖼 صورة الخبر جاهزة (%s, %.0f KB)", image.url[:70], os.path.getsize(image.path) / 1024)
    return image
