"""قطاعات محتوى متخصّصة (عمود الأنمي وأمثاله).

المبدأ: **لا نُكرّر النظام**. محرّك الأخبار (سحب → نتيجة/خبر → نشر → قياس)
يبقى واحداً. القطاع الجديد يضيف ثلاث طبقات فقط:

    ١) كاشف  (Detector) : هل هذا الخبر يخصّ القطاع؟ وما نوعه؟
    ٢) مفردات (Lexicon) : أسماء المواسم/الاستوديوهات/الأنواع لتصنيف أدق
    ٣) موجّه  (Router)  : توجيه للقناة المتخصّصة عبر الوسوم تلقائياً

كل ذلك بلا شبكة وبلا AI إلزامي — يعمل ويُختبَر وحده.
"""
from .anime import (
    ANIME_FORMATS, ANIME_GENRES, ANIME_SEASONS, KNOWN_WORKS, STUDIOS, AnimeVerdict,
    ANIME_SYSTEM, build_anime_prompt, classify_anime, extract_titles, is_anime,
    name_glossary, should_publish_anime, translate_names,
)

__all__ = [
    "ANIME_FORMATS", "ANIME_GENRES", "ANIME_SEASONS", "KNOWN_WORKS", "STUDIOS",
    "AnimeVerdict", "classify_anime", "extract_titles", "is_anime",
    "name_glossary", "should_publish_anime", "translate_names",
    "ANIME_SYSTEM", "build_anime_prompt",
]
