"""بوّابة الجودة: تمنع نشر المحتوى المشكوك فيه أو الرديء.

أهم وظيفة هنا هي **كشف الاختلاق الرقمي**: النماذج اللغوية تُولّد أرقاماً
(نسب، تواريخ، أعداد وفيات) غير موجودة في الخبر الأصلي. لذلك نستخرج كل رقم
من المنشور ونتحقّق من وجوده (أو وجود ما يعادله) في نص المصدر.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ai import Post
from .config_loader import Channel, QualitySettings
from .ingest import Article
from .utils import get_logger, normalize_arabic, truncate

log = get_logger("autonews.quality")

# أرقام عربية-هندية -> لاتينية
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
# ما يجب استثناؤه من فحص الأرقام: الروابط وسطر التوقيت النسبي (ليست معلومات)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_AGO_RE = re.compile(r"[🕒⏱]?\s*قبل\s+\d+\s*(دقيقة|دقيقتين|ساعة|ساعتين|يوم|أيام|شهر|سنة)")
_ID_LIKE_RE = re.compile(r"\b\d{6,}\b")   # معرّفات طويلة في الروابط/الأكواد
_PERCENT_WORDS = ("%", "٪", "بالمئة", "في المئة", "بالمائة")

# حقن/توجيهات مشبوهة يجب ألا تظهر في المخرجات
_INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "تجاهل التعليمات", "تجاهل ما سبق",
    "system prompt", "أنت الآن", "you are now", "developer mode", "</system>",
)
_AI_SELF_TALK = ("كمساعد", "as an ai", "بصفتي نموذجاً", "كنموذج لغوي", "لا أستطيع الوصول")


@dataclass
class QualityResult:
    ok: bool
    score: float = 1.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    invented_numbers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "score": round(self.score, 2),
                "reasons": self.reasons, "warnings": self.warnings,
                "invented_numbers": self.invented_numbers}


def extract_numbers(text: str) -> list[str]:
    """استخراج الأرقام من نص (مع توحيد الأرقام العربية)."""
    return _NUMBER_RE.findall((text or "").translate(_AR_DIGITS))


def extract_claim_numbers(text: str) -> list[str]:
    """أرقام "ادّعائية" فقط: بلا روابط، بلا توقيت نسبي، بلا معرّفات طويلة.

    هذا ما يهمّنا في كشف الاختلاق: النِسب والأعداد والتواريخ، لا أرقام الروابط.
    """
    clean = _URL_RE.sub(" ", text or "")
    clean = _AGO_RE.sub(" ", clean)
    clean = _ID_LIKE_RE.sub(" ", clean)
    return _NUMBER_RE.findall(clean.translate(_AR_DIGITS))


def _number_variants(num: str) -> set[str]:
    """بدائل ممكنة للرقم: 1000 / 1,000 / 1.000 / 1.0."""
    clean = num.replace(",", "").replace(".", "") if num.count(",") + num.count(".") > 1 else num
    variants = {num, num.replace(",", ""), num.replace(".", ""), num.replace(",", ".")}
    if clean.isdigit():
        variants.add(clean)
    # 5.5 -> 55 بسبب حذف الفاصلة؛ أبقِ الأصل أيضاً
    return {v for v in variants if v}


def check_numbers(post: Post, article: Article, *, strict: bool = True) -> list[str]:
    """إرجاع قائمة الأرقام التي وردت في المنشور ولا أصل لها في الخبر."""
    source_text = normalize_arabic(
        f"{article.title} {article.summary} {article.content} {' '.join(article.tags)}"
    ).translate(_AR_DIGITS)
    source_numbers: set[str] = set()
    for num in extract_claim_numbers(source_text):
        source_numbers |= _number_variants(num)

    post_nums = extract_claim_numbers(f"{post.title} {post.body}")
    invented: list[str] = []
    for num in post_nums:
        variants = _number_variants(num)
        if variants & source_numbers:
            continue
        # أرقام صغيرة جداً (1-3) قد تكون ترقيماً/تعداداً عربياً وليست معلومة
        if num.isdigit() and int(num) <= 3 and not strict:
            continue
        invented.append(num)
    return invented


def check_post(post: Post, article: Article, channel: Channel,
               settings: QualitySettings) -> QualityResult:
    """فحص شامل لمنشور واحد قبل السماح بنشره."""
    reasons: list[str] = []
    warnings: list[str] = []

    # 1) الاختلاق الرقمي
    invented = check_numbers(post, article, strict=settings.require_numbers_match)
    if invented and settings.require_numbers_match:
        reasons.append("أرقام غير موجودة في المصدر: " + ", ".join(invented[:6]))

    # 2) الطول
    if len(post.body) < settings.min_body_chars:
        reasons.append(f"جسم المنشور قصير جداً ({len(post.body)} < {settings.min_body_chars})")
    if len(post.title) > settings.max_title_chars:
        warnings.append("العنوان أطول من المعتاد — سيُقتطع")
        post.title = truncate(post.title, settings.max_title_chars)

    # 3) تطابق العنوان مع المصدر (منعاً لعناوين مضلّلة كلياً)
    from .utils import jaccard

    similarity = jaccard(post.title, article.title)
    if similarity < 0.12 and len(article.title) > 20:
        warnings.append(f"العنوان بعيد جداً عن المصدر (تشابه {similarity:.0%})")

    # 4) محتوى حساس
    if post.sensitive and settings.block_sensitive:
        reasons.append("خبر حسّاس — الاستبعاد مفعّل في الإعدادات")

    # 5) حقن تعليمات أو كلام عن الذات
    haystack = normalize_arabic(f"{post.title} {post.body}").lower()
    injection = [m for m in _INJECTION_MARKERS if m in haystack]
    if injection:
        reasons.append("مؤشّرات حقن تعليمات في المخرجات: " + ", ".join(injection[:3]))
    selftalk = [m for m in _AI_SELF_TALK if m in haystack]
    if selftalk:
        reasons.append("النموذج يتحدّث عن نفسه داخل الخبر")

    # 6) هاشتاغات مفرطة / تكرار
    if len(post.hashtags) > settings.max_hashtags:
        post.hashtags = post.hashtags[: settings.max_hashtags]
        warnings.append("تم تقليم الهاشتاغات")

    # 7) فراغات/تكرار حرفي مفرط
    if post.body.count("!") > 3:
        warnings.append("علامات تعجّب مفرطة")
    if re.search(r"(.{15,}?)\1{2,}", post.body):
        warnings.append("تكرار حرفي داخل النص")

    score = 1.0 - 0.25 * len(reasons) - 0.05 * len(warnings)
    ok = not reasons
    result = QualityResult(ok=ok, score=max(0.0, min(1.0, score)), reasons=reasons,
                           warnings=warnings, invented_numbers=invented)
    if not ok:
        log.warning("🚧 رُفض منشور (%s): %s", channel.id, " | ".join(reasons))
    elif warnings:
        log.info("🔎 تحذيرات جودة (%s): %s", channel.id, " | ".join(warnings))
    return result
