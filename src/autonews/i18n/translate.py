"""ترجمة/تعريب المقالات الأجنبية قبل دخولها خطّ الإنتاج.

لماذا مكوّن منفصل؟
------------------
لو تُرجم الخبر داخل كاتب المنشور لاختلطت المهمّتان: الترجمة تحتاج أمانة،
والصياغة تحتاج تحريراً. نفصلهما: أولاً **نقل أمين** للمعنى والأرقام،
ثم الكاتب العربي يبني المنشور من النص المعرَّب.

السلامة:
- الأرقام تُقارن بعد الترجمة (بوّابة الجودة تلتقط أي اختلاق).
- الأسماء اللاتينية تبقى كما هي بين قوسين عند أول ذكر.
- بلا مفاتيح AI: ترجمة احتياطية = العنوان الأصلي + تنويه (لا نختلق).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ingest import Article
from ..utils import get_logger, normalize_arabic

log = get_logger("autonews.i18n")

# لغات المصادر الشائعة → اسم عربي
SOURCE_LANG_HINTS: dict[str, str] = {
    "en": "الإنجليزية", "fr": "الفرنسية", "tr": "التركية",
    "es": "الإسبانية", "de": "الألمانية", "ru": "الروسية",
    "ja": "اليابانية", "ko": "الكورية", "zh": "الصينية",
    "ja-jp": "اليابانية", "ko-kr": "الكورية",
    "zh-cn": "الصينية المبسطة", "zh-hans": "الصينية المبسطة",
    "zh-tw": "الصينية التقليدية", "zh-hant": "الصينية التقليدية",
}

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_RE = re.compile(r"[A-Za-z]{3,}")


def detect_lang(text: str, fallback: str = "ar") -> str:
    """كشف اللغة تقريبياً: نسبة الحروف العربية مقابل اللاتينية."""
    if not text:
        return fallback
    ar = len(_ARABIC_RE.findall(text))
    la = len(_LATIN_RE.findall(text))
    if ar == 0 and la == 0:
        return fallback
    if ar >= la * 2:
        return "ar"
    if la > ar:
        return "en"
    return fallback


def needs_translation(article: Article, *, target: str = "ar") -> bool:
    """هل يحتاج الخبر تعريباً قبل النشر؟"""
    source_lang = (article.lang or "").strip().lower().replace("_", "-")
    target_lang = (target or "ar").strip().lower()
    if source_lang == target_lang:
        return False
    if source_lang not in SOURCE_LANG_HINTS:
        return False
    # أسماء الأعمال والعلامات اللاتينية شائعة داخل الأخبار اليابانية والكورية
    # والصينية؛ لا نسمح لها بتغيير لغة المصدر المعلنة إلى الإنجليزية.
    if source_lang.startswith(("ja", "ko", "zh")):
        return True
    sample = f"{article.title} {article.summary[:200]}"
    return detect_lang(sample, fallback=source_lang) != target_lang


@dataclass(slots=True)
class TranslationResult:
    """نتيجة التعريب."""
    ok: bool
    title: str = ""
    summary: str = ""
    lang_source: str = "en"
    model: str = ""
    reason: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.title)


TRANSLATE_SYSTEM = """أنت مترجم صحفي عربي محترف.
قواعدك:
1) انقل المعنى بأمانة كاملة — لا تُضف معلومة ولا رأياً.
2) الأرقام تُنقل كما هي بالضبط (بلا تقريب ولا تحويل).
3) أسماء الأشخاص والأماكن الأجنبية: عرّبها ثم ضع الأصل بين قوسين أول مرة.
4) أسلوب عربي صحفي مباشر، جمل قصيرة، بلا حشو.
5) لا تبدأ بـ«قال» أو «أعلن» إن أمكن؛ ابدأ بالفعل أو الأثر.
أخرج JSON فقط: {"title": "...", "summary": "..."}"""


def build_translate_prompt(article: Article, target: str = "ar") -> str:
    lang = SOURCE_LANG_HINTS.get((article.lang or "").lower(), article.lang)
    return f"""اعرّب الخبر التالي من {lang} إلى العربية.

العنوان: {article.title}
المتن: {(article.summary or '')[:900]}
{(chr(10) + 'تفاصيل: ' + article.content[:600]) if article.content else ''}

أخرج JSON فقط: {{"title": "عنوان عربي ≤ 14 كلمة", "summary": "ملخّص عربي 2–4 جمل"}}"""


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text or "")


def _numbers_preserved(original: str, translated: str) -> bool:
    """هل بقي جوهر الأرقام؟ (الأرقام الكبيرة فقط: سنوات وتواريخ وأعداد)"""
    src = [n for n in _numbers(original) if len(n.replace(".", "").replace(",", "")) >= 2]
    if not src:
        return True
    dst = set(_numbers(translated))
    src_set = set(src)
    kept = len(src_set & dst)
    return kept >= max(1, int(len(src_set) * 0.6))


def translation_fingerprint(original_title: str) -> str:
    """بصمة موضوعية للعنوان الأصلي — تمنع ازدواج نفس الخبر بلغتين.

    الفكرة: الخبر يصل إنجليزياً من BBC وعربياً من الجزيرة. بعد التعريب
    يصير النصّان متشابهين، لكن البصمة القديمة (عنوان إنجليزي) مختلفة.
    نحفظ بصمة الأصل فنرفض النسخة الثانية قبل صرف حصة AI عليها.
    """
    from ..utils import fingerprint, normalize_arabic

    return "tr:" + fingerprint(normalize_arabic(original_title))


def translate_article(article: Article, engine, *, target: str = "ar",
                      max_tokens: int = 700) -> TranslationResult:
    """تعريب مقال أجنبي. عند فشل كل شيء يعيد نتيجة غير ناجحة (لا اختلاق)."""
    if not needs_translation(article, target=target):
        return TranslationResult(ok=True, lang_source=article.lang, reason="لا يحتاج تعريباً")

    if engine is None or not getattr(engine, "keys", []):
        return TranslationResult(ok=False, lang_source=article.lang,
                                 reason="بلا مفاتيح AI — لا تعريب (نُبقي الأصل)")

    from ..ai.writer import extract_json

    try:
        result = engine.generate(
            build_translate_prompt(article, target), system=TRANSLATE_SYSTEM,
            temperature=0.2, max_output_tokens=max_tokens, json_response=True)
        data = extract_json(result.get("text", "")) or {}
    except Exception as exc:  # noqa: BLE001 — الترجمة لا تُسقط التشغيل
        log.warning("تعذّر تعريب المقال (%s): %s", article.source_id, exc)
        return TranslationResult(ok=False, lang_source=article.lang, reason=str(exc)[:120])

    title = " ".join(str(data.get("title") or "").split())
    summary = " ".join(str(data.get("summary") or "").split())
    if not title or len(title) < 8:
        return TranslationResult(ok=False, lang_source=article.lang, reason="مخرجات ناقصة")

    # سلامة الأرقام: أي اختلاق واضح ⇒ نرفض الترجمة (نُبقي الأصل بدل نشر رقم خطأ)
    if not _numbers_preserved(f"{article.title} {article.summary}", f"{title} {summary}"):
        log.warning("🚧 رُفض التعريب: أرقام غير محفوظة (%s)", article.title[:50])
        return TranslationResult(ok=False, lang_source=article.lang,
                                 reason="أرقام غير مطابقة")

    # حماية من الهلوسة: لا نص عربي = فشل
    if len(_ARABIC_RE.findall(title)) < 5:
        return TranslationResult(ok=False, lang_source=article.lang, reason="مخرجات غير عربية")

    return TranslationResult(ok=True, title=title, summary=summary,
                             lang_source=article.lang, model=str(result.get("model", "")))


def apply_translation(article: Article, tr: TranslationResult) -> Article:
    """إعادة مقال جديد معرَّب (بلا تعديل الأصل — الحالة لا تُفسَد)."""
    if not tr.ok or not tr.changed:
        return article
    return Article(
        source_id=article.source_id, source_name=article.source_name,
        title=tr.title, summary=tr.summary or article.summary,
        url=article.url, published=article.published, lang="ar",
        tags=list(article.tags), content=article.content,
        image_url=article.image_url, enriched=article.enriched,
        translated_from=tr.lang_source,
    )


def is_already_covered(original_title: str, seen: list[str], *,
                       similarity: float = 0.55) -> bool:
    """هل عُرِّب هذا العنوان سابقاً (نفس الخبر من مصدر آخر بلغة أخرى)؟"""
    fp = translation_fingerprint(original_title)
    if fp in seen:
        return True
    from ..utils import jaccard

    return any(jaccard(original_title, t) >= similarity for t in seen if t)


def translation_note(tr: TranslationResult) -> str:
    """تنويه شفافية يُلحق بالمنشور."""
    if not tr.changed:
        return ""
    lang = SOURCE_LANG_HINTS.get(tr.lang_source.lower(), tr.lang_source)
    return f"🌐 مُعرَّب من {lang} — الاسم الأصلي محفوظ في النص"
