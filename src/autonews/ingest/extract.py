"""استخراج نص الخبر من صفحة HTML — ثلاث استراتيجيات متتالية.

كثير من الخلاصات تعطي عنواناً فقط (أو مقتطفاً بلا مضمون). هنا نكمل الناقص
بزيارة الصفحة نفسها واستخراج المتن، بثلاث طبقات متدرّجة:

1. **JSON-LD** (Schema.org `Article`/`NewsArticle`): أدقّ مصدر إن وُجد.
2. **Open Graph / Meta**: `og:description`، `article:body`…
3. **Readability خفيف**: إزالة القوائم والهوامش ثم اختيار الكتلة الأكثر نصاً
   عبر كثافة النص العربي (بدل مكتبات ثقيلة).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from ..utils import get_logger, strip_html, truncate

log = get_logger("autonews.extract")

_SCRIPT_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
_META_RE = re.compile(r"<meta[^>]+>", re.I)
_ATTR_RE = re.compile(r'([\w:-]+)=["\']([^"\']*)["\']', re.I)
_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)

# كتل يجب حذفها قبل التحليل
_NOISE_RE = re.compile(
    r"(?is)<(script|style|noscript|nav|aside|footer|header|form|iframe|svg|button)[^>]*>.*?</\1>")
_RELATED_RE = re.compile(
    r'(?is)<(div|section|ul)[^>]+(class|id)=["\'][^"\']*'
    r'(related|more-news|الأخبار\s*ذات|ذات\s*صلة|اقرأ\s*أيضاً|شاهد\s*أيضاً|تعليقات|comments)'
    r'[^"\']*["\'][^>]*>.*?</\1>')

_ARABIC_RANGE = re.compile(r"[\u0600-\u06FF]")
_SENTENCE_END = re.compile(r"[.!؟?۔]")


@dataclass
class Extracted:
    text: str = ""
    method: str = ""        # json_ld | open_graph | readability | ""
    title: str = ""

    @property
    def ok(self) -> bool:
        return len(self.text) >= 80


def _arabic_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _ARABIC_RANGE.match(c)) / len(letters)


def _clean(text: str) -> str:
    text = strip_html(html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# --------------------------------------------------------------------------- #
#  1) JSON-LD
# --------------------------------------------------------------------------- #
def _from_json_ld(page: str) -> Extracted:
    for block in _SCRIPT_LD_RE.findall(page):
        block = block.strip()
        data = None
        # strict=False يسمح بحروف التحكّم (أسطر جديدة) داخل النصوص — شائعة فعلياً
        for candidate_text in (block, re.sub(r",\s*([}\]])", r"\1", block)):
            for strict in (False, True):
                try:
                    data = json.loads(candidate_text, strict=strict)
                    break
                except json.JSONDecodeError:
                    continue
            if data is not None:
                break
        if data is None:
            continue
        nodes = data if isinstance(data, list) else [data]
        # بعض الصفحات تغلّف الكائن في @graph
        expanded: list[dict] = []
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                expanded.extend(n for n in node["@graph"] if isinstance(n, dict))
            elif isinstance(node, dict):
                expanded.append(node)

        for node in expanded:
            node_type = str(node.get("@type") or "").lower()
            if not any(t in node_type for t in ("article", "newsarticle", "blogposting", "report")):
                continue
            body = " ".join(str(node.get(k) or "") for k in
                            ("articleBody", "description", "text"))
            title = _clean(str(node.get("headline") or ""))
            body = _clean(body)
            if len(body) >= 120:
                return Extracted(truncate(body, 3000), "json_ld", title)
    return Extracted()


# --------------------------------------------------------------------------- #
#  2) Open Graph / Meta
# --------------------------------------------------------------------------- #
def _from_meta(page: str) -> Extracted:
    wanted = {"og:description", "article:body", "description",
              "twitter:description", "og:title"}
    found: dict[str, str] = {}
    for tag in _META_RE.findall(page):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in wanted and attrs.get("content"):
            found.setdefault(key, attrs["content"])
    body = found.get("article:body") or found.get("og:description") \
        or found.get("description") or found.get("twitter:description") or ""
    return Extracted(_clean(body), "open_graph", _clean(found.get("og:title", "")))


# --------------------------------------------------------------------------- #
#  3) Readability مبسّط
# --------------------------------------------------------------------------- #
def _from_readability(page: str) -> Extracted:
    cleaned = _NOISE_RE.sub(" ", page)
    cleaned = _RELATED_RE.sub(" ", cleaned)

    paragraphs = [_clean(p) for p in _PARAGRAPH_RE.findall(cleaned)]
    paragraphs = [p for p in paragraphs if len(p) >= 60]

    if not paragraphs:
        return Extracted()

    # نرجّح الفقرات العربية/ذات الجمل المتعددة
    scored = []
    for p in paragraphs:
        score = len(p) + 200 * _arabic_ratio(p) + 40 * len(_SENTENCE_END.findall(p))
        if _arabic_ratio(p) > 0.35 or _arabic_ratio(p) == 0.0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])

    chosen: list[str] = []
    total = 0
    for _score, p in scored:
        chosen.append(p)
        total += len(p)
        if total >= 1200 or len(chosen) >= 6:
            break
    if not chosen:
        return Extracted()
    body = "\n".join(chosen)
    return Extracted(truncate(body, 3000), "readability")


# --------------------------------------------------------------------------- #
#  الواجهة
# --------------------------------------------------------------------------- #
def extract_article_text(page_html: str, *, min_chars: int = 120) -> Extracted:
    """استخراج متن الخبر بأفضل طريقة متاحة."""
    if not page_html:
        return Extracted()
    for strategy in (_from_json_ld, _from_meta, _from_readability):
        try:
            result = strategy(page_html)
        except Exception as exc:  # noqa: BLE001 — استراتيجية تفشل، ننتقل للتالية
            log.debug("فشل الاستخراج بـ %s: %s", strategy.__name__, exc)
            continue
        if len(result.text) >= min_chars:
            log.debug("استُخرج %d حرفاً بطريقة %s", len(result.text), result.method)
            return result
    # قبول أي نتيجة قصيرة بدل لا شيء
    fallback = _from_meta(page_html)
    return fallback if fallback.text else Extracted()
