"""طبقة الترجمة: تحويل المصادر الأجنبية إلى منشورات عربية.

المبدأ: لا نُترجم الخبر حرفياً، بل نُعيد صياغته عربياً بنفس معايير بقية النظام
(عنوان يبدأ بالأثر، بلا حشو)، مع الحفاظ على الأرقام والأسماء الأصلية.
"""
from .translate import (
    SOURCE_LANG_HINTS, TranslationResult, detect_lang, needs_translation,
    translate_article,
)

__all__ = [
    "SOURCE_LANG_HINTS", "TranslationResult", "detect_lang", "needs_translation",
    "translate_article",
]
