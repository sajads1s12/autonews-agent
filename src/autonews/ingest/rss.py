"""طبقة سحب المحتوى: RSS/Atom مع تصفّح آلي قوي واستخراج المتن الناقص.

المسار الكامل لكل مصدر:
    رابط الخلاصة ──(فشل؟)──► اكتشاف تلقائي للخلاصة ──► تحليل RSS/Atom
    ──► إكمال المقالات بلا مضمون (زيارة الصفحة + JSON-LD/OG/Readability)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable
from xml.etree import ElementTree as ET

from ..config_loader import Settings, Source
from ..utils import (
    fingerprint, get_logger, parse_dt, strip_html, truncate, url_fingerprint, utcnow,
)
from .autodiscover import first_feed
from .extract import extract_article_text
from .fetch import Fetcher

log = get_logger("autonews.ingest")

ATOM_NS = "{http://www.w3.org/2005/Atom}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"


@dataclass(slots=True)
class Article:
    """مقال خام جاهز للمعالجة."""
    source_id: str
    source_name: str
    title: str
    summary: str
    url: str
    published: datetime | None = None
    lang: str = "ar"
    tags: list[str] = field(default_factory=list)
    content: str = ""
    image_url: str = ""          # من الخلاصة/الصفحة (اختياري)
    enriched: bool = False       # هل أُكمل المتن من صفحة المصدر؟
    translated_from: str = ""    # لغة الأصل إن كان النص معرَّباً آلياً (en/fr/tr)
    origin_url: str = ""
    origin_name: str = ""

    @property
    def fp(self) -> str:
        """بصمة ثابتة: العنوان + الرابط (تمنع التكرار عبر التشغيلات)."""
        return f"{fingerprint(self.title)}:{url_fingerprint(self.url)}"

    @property
    def text(self) -> str:
        body = self.content or self.summary
        return f"{self.title}\n\n{body}".strip()

    @property
    def is_thin(self) -> bool:
        """خبر بمضمون ضعيف (عنوان + سطر أو سطرين) — يحتاج استكمالاً."""
        body = (self.content or self.summary or "").strip()
        return len(body) < 220

    def as_dict(self) -> dict:
        return {
            "source": self.source_id, "title": self.title, "url": self.url,
            "published": self.published.isoformat() if self.published else None,
            "summary": truncate(self.summary, 400), "tags": self.tags,
        }


# --------------------------------------------------------------------------- #
#  تحليل الخلاصة
# --------------------------------------------------------------------------- #
def _parse_with_feedparser(raw: bytes) -> list[dict] | None:
    try:
        import feedparser  # type: ignore
    except ImportError:
        return None
    parsed = feedparser.parse(raw)
    entries = []
    for e in parsed.entries or []:
        entries.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "summary": e.get("summary", "") or e.get("description", ""),
            "content": _extract_fp_content(e),
            "published": e.get("published") or e.get("updated") or e.get("created"),
            "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
            "tags": [t.get("term", "") for t in (e.get("tags") or []) if isinstance(t, dict)],
            "image": _extract_fp_image(e),
            "origin_url": (e.get("source") or {}).get("href", "") if isinstance(e.get("source"), dict) else "",
            "origin_name": (e.get("source") or {}).get("title", "") if isinstance(e.get("source"), dict) else "",
        })
    return entries


def _extract_fp_content(entry) -> str:
    blocks = entry.get("content")
    if isinstance(blocks, list) and blocks:
        value = blocks[0].get("value") if isinstance(blocks[0], dict) else None
        if value:
            return strip_html(value)
    return ""


def _extract_fp_image(entry) -> str:
    for key in ("media_content", "media_thumbnail", "links"):
        items = entry.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and str(item.get("type", "")).startswith("image") and item.get("url"):
                return str(item["url"])
            if isinstance(item, dict) and item.get("rel") == "enclosure" \
                    and str(item.get("type", "")).startswith("image"):
                return str(item.get("href") or item.get("url") or "")
    return ""


def _parse_json_feed(raw: bytes) -> list[dict]:
    """دعم JSON Feed (jsonfeed.org) — تستخدمه مواقع حديثة مثل شبكة 964."""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("items") or []
    entries: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        content = it.get("content_text") or it.get("content_html") or ""
        summary = it.get("summary") or content
        image = ""
        for att in (it.get("attachments") or []):
            if isinstance(att, dict) and str(att.get("mime_type", "")).startswith("image"):
                image = str(att.get("url") or "")
                break
        entries.append({
            "title": it.get("title") or "",
            "link": it.get("url") or it.get("external_url") or "",
            "summary": strip_html(str(summary)),
            "content": strip_html(str(content)),
            "published": it.get("date_published") or it.get("date_modified"),
            "tags": [str(t) for t in (it.get("tags") or [])],
            "image": image,
        })
    return entries


def _parse_with_etree(raw: bytes) -> list[dict]:
    """محلّل RSS/Atom احتياطي لا يعتمد على مكتبات خارجية."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log.warning("XML غير صالح: %s", exc)
        return []
    entries: list[dict] = []

    def _text(node, *paths: str) -> str:
        for path in paths:
            found = node.find(path)
            if found is not None:
                if found.text and found.text.strip():
                    return found.text.strip()
                if len(found):
                    return " ".join("".join(found.itertext()).split())
        return ""

    for item in root.iter("item"):          # RSS 2.0
        enclosure = item.find("enclosure")
        image = ""
        if enclosure is not None and str(enclosure.get("type", "")).startswith("image"):
            image = enclosure.get("url", "")
        entries.append({
            "title": _text(item, "title"),
            "link": _text(item, "link") or _text(item, "guid"),
            "summary": _text(item, "description"),
            "content": strip_html(_text(item, f"{CONTENT_NS}encoded")),
            "published": _text(item, "pubDate", f"{DC_NS}date"),
            "tags": [c.text.strip() for c in item.findall("category") if c.text],
            "image": image,
            "origin_url": item.find("source").get("url", "") if item.find("source") is not None else "",
            "origin_name": _text(item, "source"),
        })
    for entry in root.iter(f"{ATOM_NS}entry"):   # Atom
        link = ""
        for link_node in entry.findall(f"{ATOM_NS}link"):
            if link_node.get("rel") in (None, "alternate"):
                link = link_node.get("href", "")
                break
        entries.append({
            "title": _text(entry, f"{ATOM_NS}title"),
            "link": link,
            "summary": _text(entry, f"{ATOM_NS}summary"),
            "content": strip_html(_text(entry, f"{ATOM_NS}content")),
            "published": _text(entry, f"{ATOM_NS}published", f"{ATOM_NS}updated"),
            "tags": [c.get("term", "") for c in entry.findall(f"{ATOM_NS}category")],
            "image": "",
        })
    return entries


def _entry_datetime(entry: dict) -> datetime | None:
    if entry.get("published_parsed"):
        try:
            import calendar

            return datetime.fromtimestamp(calendar.timegm(entry["published_parsed"]),
                                          tz=utcnow().tzinfo)
        except (TypeError, ValueError, OverflowError):
            pass
    return parse_dt(entry.get("published"))


def _clean_title(title: str) -> str:
    title = strip_html(title)
    if title.count("|") or title.count(" - "):
        title = re.sub(r"\s*[-–|]\s*[^-–|]{0,30}$", "", title)
    return " ".join(title.split())


# --------------------------------------------------------------------------- #
#  الجلب
# --------------------------------------------------------------------------- #
def _resolve_feed_url(source: Source, fetcher: Fetcher, state_discoveries: dict | None,
                      *, allow_discover: bool = True) -> str:
    """رابط الخلاصة الفعّال (مع الاكتشاف التلقائي والتخزين المؤقت للنتيجة)."""
    url = source.url
    # خلاصة محفوظة من تشغيل سابق (تجنّب إعادة الاكتشاف كل مرّة)
    if state_discoveries and source.id in state_discoveries:
        cached = state_discoveries[source.id]
        if cached and cached != url:
            log.info("استخدام خلاصة مكتشفة سابقاً للمصدر '%s': %s", source.id, cached)
            return cached

    res = fetcher.get(url, accept="application/rss+xml,application/atom+xml,"
                                  "application/feed+json,application/json,"
                                  "application/xml,text/xml,*/*")
    head = res.raw[:4000].lower() if res.raw else b""
    looks_feedish = (b"<rss" in head or b"<feed" in head or b"<item" in head
                     or b"<entry" in head or b'"items"' in head)
    if res.ok and res.raw and looks_feedish:
        return url

    # محجوب؟ الاكتشاف سيزيد الحجب فقط (كان يُنتج 48 طلباً محجوباً في تشغيل واحد)
    if res.status in (401, 403, 429) or not allow_discover:
        log.warning("تخطّي الاكتشاف التلقائي للمصدر '%s' (%s)", source.id,
                    f"HTTP {res.status}" if res.status else "معطّل")
        return url

    log.warning("الخلاصة غير صالحة (%s) — محاولة اكتشاف تلقائي لـ %s",
                res.error or f"HTTP {res.status}", source.url)
    discovered = first_feed(source.url, fetcher)
    if discovered:
        if state_discoveries is not None:
            state_discoveries[source.id] = discovered
        return discovered
    return url


def fetch_source(source: Source, settings: Settings, *, fetcher: Fetcher | None = None,
                 enrich: bool = True, state_discoveries: dict | None = None,
                 enrich_budget: int = 2) -> list[Article]:
    """جلب وتحليل مصدر واحد وإرجاع مقالات صالحة فقط."""
    fetcher = fetcher or _make_fetcher(settings)
    allow_discover = getattr(settings.crawl, "autodiscover", True)
    feed_url = _resolve_feed_url(source, fetcher, state_discoveries,
                                 allow_discover=allow_discover)

    res = fetcher.get(feed_url, accept="application/rss+xml,application/atom+xml,"
                                       "application/xml,text/xml,*/*")
    if not res.ok or not res.raw:
        log.warning("فشل جلب المصدر %s | %s", feed_url, res.error or res.status)
        return []

    # JSON Feed أو RSS/Atom؟
    is_json = (source.type == "json_feed"
               or res.raw[:300].strip().startswith(b"{")
               or "json" in (res.content_type or "").lower())
    if is_json:
        entries = _parse_json_feed(res.raw)
        if not entries:
            log.warning("JSON Feed غير صالح للمصدر '%s'", source.id)
    else:
        entries = _parse_with_feedparser(res.raw)
        if entries is None:
            log.debug("feedparser غير متاح — استخدام المحلّل الاحتياطي")
            entries = _parse_with_etree(res.raw)
        if not entries:
            entries = _parse_with_etree(res.raw)

    # مصادر رسمية تُعطى أولوية في الترتيب داخل نفس المصدر
    if getattr(source, "official", False) and entries:
        log.debug("مصدر رسمي: %s — أولوية أعلى", source.id)

    now = utcnow()
    articles: list[Article] = []
    seen: set[str] = set()
    for entry in entries:
        title = _clean_title(entry.get("title") or "")
        link = (entry.get("link") or "").strip()
        if len(title) < settings.min_title_chars or not link.startswith("http"):
            continue

        published = _entry_datetime(entry)
        if published and settings.max_age_hours > 0:
            age_hours = (now - published).total_seconds() / 3600
            if age_hours > settings.max_age_hours:
                continue
            if age_hours < -6:
                published = None

        summary = strip_html(entry.get("summary") or "")
        content = entry.get("content") or ""
        if not summary and not content:
            continue

        art = Article(
            source_id=source.id, source_name=source.name, title=title,
            summary=truncate(summary, 1200), url=link, published=published,
            lang=source.lang or "ar",
            tags=[str(t).strip().lower() for t in (entry.get("tags") or []) if t][:8] or list(source.tags),
            content=truncate(content, 3000),
            image_url=str(entry.get("image") or ""),
            origin_url=str(entry.get("origin_url") or ""),
            origin_name=str(entry.get("origin_name") or ""),
        )
        if source.tags:
            art.tags = list(dict.fromkeys(art.tags + list(source.tags)))[:12]
        if art.fp in seen:
            continue
        seen.add(art.fp)
        articles.append(art)

    articles.sort(key=lambda a: a.published or now, reverse=True)
    articles = articles[: max(1, min(source.max_items, settings.max_per_source))]

    # ---- استكمال المتن الناقص (بحدّ أقصى لتوفير الوقت) ----
    if enrich and enrich_budget > 0:
        _enrich_articles(articles, fetcher, settings, budget=enrich_budget)

    filled = sum(1 for a in articles if a.enriched)
    log.info("المصدر '%s': %d مقال جديد%s", source.name, len(articles),
             f" (أُكمل متن {filled} منها من الصفحة)" if filled else "")
    return articles


def _make_fetcher(settings: Settings) -> Fetcher:
    return Fetcher(timeout=settings.request_timeout,
                   user_agent=getattr(settings, "user_agent", "") or "",
                   retries=2)


def _enrich_articles(articles: list[Article], fetcher: Fetcher, settings: Settings,
                     budget: int = 2) -> None:
    """زيارة صفحات المقالات ذات المضمون الضعيف لاستكمال النص (بحدّ أقصى)."""
    used = 0
    for art in articles:
        if used >= budget:
            break
        if not art.is_thin:
            continue
        used += 1
        try:
            page = fetcher.page(art.url)
        except Exception as exc:  # noqa: BLE001 — الاستكمال تحسين لا شرط
            log.debug("تعذّر استكمال %s: %s", art.url, exc)
            continue
        extracted = extract_article_text(page)
        if extracted.ok:
            # نُبقي ملخّص الخلاصة كمقدّمة، ونضيف المتن المستخرج
            if extracted.text and extracted.text not in (art.summary or ""):
                art.content = truncate(extracted.text, 3000)
                art.summary = truncate(art.summary or extracted.text, 1200)
                art.enriched = True
        # صورة احتياطية من الوسوم إن لم تكن الخلاصة قد وفّرتها
        if not art.image_url:
            from ..images import find_image_url

            url, _alt = find_image_url(art.url, timeout=min(8, settings.request_timeout))
            if url:
                art.image_url = url


# --------------------------------------------------------------------------- #
#  الواجهة العامة
# --------------------------------------------------------------------------- #
def fetch_all_with_status(settings: Settings) -> tuple[list[Article], list[str]]:
    """جلب كل المصادر، وإرجاع (المقالات، معرّفات المصادر الفاشلة)."""
    fetcher = _make_fetcher(settings)
    discoveries: dict[str, str] = {}
    all_articles: list[Article] = []
    failed: list[str] = []

    budget_seconds = int(getattr(settings.crawl, "fetch_budget_seconds", 240))
    started = time.time()
    enrich_budget = int(getattr(settings.crawl, "max_enrich_per_source", 2))

    # ⚡ الرسمي أولاً: لو استُهلكت ميزانية السحب نكون قد أخذنا الأهمّ لا الأحدث حظاً
    ordered = sorted(settings.sources,
                     key=lambda src: (not getattr(src, "official", False),
                                      -float(getattr(src, "trust", 1.0))))
    for source in ordered:
        if time.time() - started > budget_seconds:
            log.warning("⏳ استُهلكت ميزانية السحب (%ds) — تخطّي بقية المصادر",
                        budget_seconds)
            break
        try:
            items = fetch_source(source, settings, fetcher=fetcher,
                                 enrich=settings.enrich_articles,
                                 state_discoveries=discoveries,
                                 enrich_budget=enrich_budget)
            if not items:
                failed.append(source.id)
            all_articles.extend(items)
        except Exception as exc:  # noqa: BLE001 — مصدر واحد لا يُسقط التشغيل
            log.error("خطأ غير متوقّع في المصدر '%s': %s", source.id, exc)
            failed.append(source.id)
        time.sleep(0.15)

    if failed:
        log.warning("مصادر بلا نتائج/فاشلة: %s", ", ".join(failed))
    log.info("إحصاء الشبكة: %s", fetcher.report())

    unique: dict[str, Article] = {}
    for art in all_articles:
        key = fingerprint(re.sub(r"\W+", "", art.title))[:16]
        unique.setdefault(key, art)
    result = sorted(unique.values(), key=lambda a: a.published or utcnow(), reverse=True)
    log.info("إجمالي المقالات الفريدة: %d (من %d مصدر)", len(result), len(settings.sources))
    return result, failed


def fetch_all(settings: Settings) -> list[Article]:
    """توافق خلفي: المقالات فقط."""
    return fetch_all_with_status(settings)[0]


def dedupe_against_history(articles: Iterable[Article], seen_fps: set[str]) -> list[Article]:
    return [a for a in articles if a.fp not in seen_fps]
