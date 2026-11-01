"""كشف الأخبار العاجلة وإعطاؤها أولوية أعلى من سقوف النشر.

المنطق: كلمات مفتاحية في العنوان/الملخص + حداثة عالية => خبر عاجل.
العاجل يُنشر أولاً، ويمكنه تجاوز `max_posts_per_run` (إن سُمح) بحدّ أقصى
مضبوط، كي لا يبتلع سيلُ "العاجل" بقية المحتوى.
"""
from __future__ import annotations

import re

from .config_loader import BreakingSettings
from .ingest import Article
from .utils import get_logger, normalize_arabic, utcnow

log = get_logger("autonews.breaking")

# ملاحظة: لا نضع كلمات عامة مثل "الآن" أو "قبل قليل" — تُنتج إيجابيات كاذبة بكثرة
# (مثال حقيقي: خبر رياضي اعتُبر عاجلاً بسبب كلمة "الآن" في نصه).
DEFAULT_KEYWORDS = [
    "عاجل", "عاجلة", "breaking", "urgent", "خلال دقائق", "الأنباء الأولية",
    "انفجار", "هجوم", "قصف", "غارة", "اشتباكات", "مقتل", "وفيات", "جرحى",
    "استقالة", "إقالة", "اعتقال", "انقلاب", "حالة طوارئ", "تحذير",
    "زلزال", "فيضان", "حريق", "انهيار", "إغلاق", "تعليق", "إعلان الحرب",
    "death", "killed", "explosion", "earthquake", "resigns", "emergency",
]


def keyword_hits(article: Article, keywords: list[str]) -> list[str]:
    haystack = normalize_arabic(f"{article.title} {article.summary[:400]}").lower()
    hits = []
    for kw in keywords:
        k = normalize_arabic(kw.lower()).strip()
        if not k:
            continue
        # حدود كلمة للأحرف اللاتينية، ومطابقة مباشرة للعربية
        if re.search(r"[a-z]", k):
            if re.search(rf"\b{re.escape(k)}\b", haystack):
                hits.append(kw)
        elif k in haystack:
            hits.append(kw)
    return hits


def is_breaking(article: Article, settings: BreakingSettings) -> tuple[bool, list[str]]:
    """تحديد ما إذا كان الخبر عاجلاً، وإرجاع الكلمات التي رجّحت ذلك."""
    if not settings.enabled:
        return False, []
    keywords = settings.keywords or DEFAULT_KEYWORDS
    hits = keyword_hits(article, keywords)

    # شرط الحداثة: العاجل يجب أن يكون طازجاً
    fresh = True
    if article.published:
        age_minutes = (utcnow() - article.published).total_seconds() / 60
        fresh = age_minutes <= 120

    # كلمة واحدة قوية في العنوان تكفي؛ كلمة في الملخص فقط تحتاج دعم حداثة
    title_hits = keyword_hits(Article(
        source_id=article.source_id, source_name=article.source_name,
        title=article.title, summary="", url=article.url,
    ), keywords)
    strong = bool(title_hits) or (len(hits) >= 2 and fresh)
    if strong:
        log.info("🔴 خبر عاجل: %s (كلمات: %s)", article.title[:70], ", ".join(hits[:4]))
    return strong, hits


def priority_of(article: Article, settings: BreakingSettings,
                *, breaking: bool | None = None) -> int:
    """درجة أولوية للترتيب (أعلى = يُنشر أولاً). إن مُرِّرت breaking لا نعيد الفحص."""
    if breaking is None:
        breaking, _ = is_breaking(article, settings)
    priority = settings.priority if breaking else 0
    if article.published:
        age_hours = max(0.0, (utcnow() - article.published).total_seconds() / 3600)
        priority += max(0.0, 5.0 - age_hours)   # الحداثة تضيف حتى 5 نقاط
    return int(priority)
