"""نمط «النتائج» — لا تنشر الحادث، بل ما نتج عنه.

لماذا؟
------
نشر «انفجار في منطقة X» هو خبر خام لا قيمة معلوماتية دائمة له، وقد يكون غير مؤكد
أو مؤلماً بلا فائدة. القيمة الحقيقية في **النتائج**:

    ١) ماذا صار للشخص؟      → حالة المصابين، أسماء الضحايا، التوقيف، الإفراج، الإحالة، العزل
    ٢) ماذا صار للمحافظات؟  → أي محافظة عطّلت الدوام وأي محافظة لم تعطّل
    ٣) ماذا صار للناس؟      → قرار، تعويض، صرف، إعادة فتح طريق، تعطيل مؤسسة

هذا الملف يحوّل الخبر إلى بطاقة نتائج منظمة `📋 النتائج` بدل سرد الحادث.

السياسات المتاحة (`flash.content_policy`):
    outcomes_only : لا يُنشر إلا ما يحمل نتائج (الافتراضي المطلوب)
    hybrid        : تُنشر النتائج دائماً + الحدث الخام إذا كان كبيراً (عدد ضحايا/طوارئ)
    raw           : السلوك القديم (نشر كل عاجل)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config_loader import OutcomeSettings
from .ingest import Article
from .utils import get_logger, normalize_arabic

log = get_logger("autonews.outcomes")

# --------------------------------------------------------------------------- #
#  المحافظات العراقية + بدائل كتابتها
# --------------------------------------------------------------------------- #
GOVERNORATES: dict[str, list[str]] = {
    "بغداد": ["بغداد", "العاصمة"],
    "البصرة": ["البصرة", "البصره"],
    "نينوى": ["نينوى", "الموصل", "نينوه"],
    "أربيل": ["أربيل", "اربيل", "هولير"],
    "النجف": ["النجف", "النجف الأشرف"],
    "كربلاء": ["كربلاء", "كربلا"],
    "الأنبار": ["الأنبار", "الانبار", "الرمادي", "الفلوجة", "هيت", "حديثة"],
    "ديالى": ["ديالى", "بعقوبة", "المقدادية", "الخالص"],
    "ذي قار": ["ذي قار", "الناصرية", "الشطرة"],
    "السليمانية": ["السليمانية", "السليمانيه", "سلێمانی"],
    "صلاح الدين": ["صلاح الدين", "تكريت", "سامراء", "بلد", "الدجيل", "بيجي"],
    "بابل": ["بابل", "الحلة", "المحاويل", "الهاشمية"],
    "واسط": ["واسط", "الكوت", "الحي", "النعمانية"],
    "ميسان": ["ميسان", "العمارة", "المجر الكبير"],
    "المثنى": ["المثنى", "السماوة", "الرميثة", "الخضر"],
    "القادسية": ["القادسية", "الديوانية", "الديوانيه", "عفك", "الشامية"],
    "كركوك": ["كركوك", "الحويجة"],
    "دهوك": ["دهوك", "زاخو", "سيميل"],
    "حلبجة": ["حلبجة", "حلبجه"],
    "كردستان": ["كردستان", "إقليم كردستان"],
    "المناطق الكردستانية": ["اقليم كردستان", "حكومة الاقليم"],
}

# --------------------------------------------------------------------------- #
#  إشارات النتائج (وجودها يجعل الخبر «نتيجة» لا «حدثاً»)
# --------------------------------------------------------------------------- #
OUTCOME_SIGNALS: dict[str, list[str]] = {
    "person": [
        "حالة", "حالته", "حالتها", "المصابين", "إصابة", "إصابات", "شفاء", "خرج من المستشفى",
        "حصيلة", "الحصيلة", "وفيات", "وفاة", "قتيل", "قتيلان", "إصابات حرجة", "حرجة",
        "أسماء", "اسم", "الضحايا", "الشهداء", "جثمان", "جثامين", "تسليم", "تشييع",
        "توقيف", "اعتقال", "إفراج", "إخلاء سبيل", "إحالة", "استدعاء", "عزل", "إقالة",
        "استقالة", "تعيين", "تكليف", "ترقية", "محاكمة", "حكم", "غرامة", "تعويض",
        "مفقود", "إنقاذ", "انتشال", "نقل", "علاج", "بتر",
    ],
    "governorate": [
        "محافظة", "محافظات", "المحافظات", "بغداد", "البصرة", "أربيل", "النجف", "كربلاء",
        "الأنبار", "ديالى", "ذي قار", "السليمانية", "صلاح الدين", "بابل", "واسط",
        "ميسان", "المثنى", "القادسية", "كركوك", "دهوك", "حلبجة", "نينوى",
        "استثناء", "استثنت", "شملت", "تشمل", "تستثني", "جميع المحافظات", "عدا",
    ],
    "service": [
        "تعطيل", "تعليق الدوام", "الدوام", "استئناف", "إعادة فتح", "فتح الطريق", "إغلاق",
        "صرف", "توزيع", "تعويض", "راتب", "مستحقات", "إيصال", "كهرباء", "ماء", "بنزين",
        "قرار", "توجيه", "تخصيص", "مناقصة", "استلام", "إنجاز", "افتتاح",
    ],
}

# ما يجعل الخبر «حدثاً خاماً» بلا نتيجة
INCIDENT_SIGNALS = [
    "انفجار", "تفجير", "قصف", "غارة", "غارات", "صاروخ", "اشتباكات", "معارك",
    "هجوم", "إطلاق نار", "حريق", "زلزال", "سيول", "فيضان", "حادث سير", "تصادم",
    "انهيار", "سقوط", "مقتل", "جرح", "شهداء", "قتلى",
]

# كلمات تدلّ على «نتيجة كبيرة» تُنشر حتى في سياسة الحدث الكبير
# «حدث كبير» في سياسة hybrid: ما لا يمكن تجاهله حتى لو لم تصدر نتائجه بعد.
ESCALATION_SIGNALS = [
    "حالة طوارئ", "حداد عام", "تعطيل شامل", "إخلاء", "نزوح", "كارثة",
    "حصيلة", "حالة وفاة", "عشرات", "مئات", "آلاف",
    # حصيلة أولية (كلمة + رقم): «قتلى» وحدها إشارة أن الحدث صار كبيراً
    "قتلى", "قتيل", "وفيات", "ضحايا", "إصابات حرجة", "حالات حرجة",
]


# --------------------------------------------------------------------------- #
#  النموذج
# --------------------------------------------------------------------------- #
@dataclass
class OutcomeAnalysis:
    """تحليل الخبر: هل هو نتيجة أم حدث خام؟ وما هي النتائج المستخرجة؟"""
    is_incident: bool = False
    has_outcome: bool = False
    kinds: list[str] = field(default_factory=list)     # person / governorate / service
    signals: list[str] = field(default_factory=list)
    governorates_on: list[str] = field(default_factory=list)     # مشمولة بالعطلة/القرار
    governorates_off: list[str] = field(default_factory=list)    # مستثناة
    escalate: bool = False

    def summary(self) -> str:
        parts = []
        if self.kinds:
            parts.append("نتائج:" + "/".join(self.kinds))
        if self.governorates_on:
            parts.append("مشمولة:" + "،".join(self.governorates_on[:5]))
        if self.governorates_off:
            parts.append("مستثناة:" + "،".join(self.governorates_off[:5]))
        return " · ".join(parts) or "بلا نتائج"


def norm(text: str) -> str:
    return normalize_arabic(text or "")


def _find(text: str, words: list[str]) -> list[str]:
    hay = norm(text)
    hits = []
    for w in words:
        if norm(w) in hay:
            hits.append(w)
    return hits


def detect_governorates(text: str) -> tuple[list[str], list[str]]:
    """استخراج المحافظات المذكورة، ومحاولة تمييز المشمولة من المستثناة.

    القاعدة التبسيطية: إن ظهرت «استثنى/عدا/باستثناء» قبل المحافظة بفاصل قصير
    نعدّها مستثناة؛ وإلا فهي مشمولة.
    """
    hay = norm(text)
    excluded_markers = ["استثنى", "استثنت", "استثناء", "عدا", "باستثناء", "ما عدا",
                        "فيما لم تشمل", "لم تشمل"]
    on: list[str] = []
    off: list[str] = []

    for gov, variants in GOVERNORATES.items():
        positions = [hay.find(norm(v)) for v in variants]
        positions = [p for p in positions if p >= 0]
        if not positions:
            continue
        pos = min(positions)
        window = hay[max(0, pos - 70):pos]
        is_excluded = any(norm(m) in window for m in excluded_markers)
        (off if is_excluded else on).append(gov)

    # إزالة التكرار مع الحفاظ على الترتيب
    on = list(dict.fromkeys(on))
    off = [g for g in dict.fromkeys(off) if g not in on]
    return on, off


# عبارات تعني صراحة «لا توجد نتائج بعد»
_NO_RESULT_YET = (
    "لم ترد معلومات", "لم ترد أنباء", "لا معلومات", "لم يُعرف", "لم يعرف",
    "حتى الآن لا", "لم تتضح", "لم تُعلن", "لم تعلن", "لا توجد معلومات",
    "لم يصدر", "لم يكشف", "تحت التحقيق", "قيد التحقيق", "جاري التحقق",
)


_CASUALTY_WORDS = ("وفيات", "وفاة", "قتلى", "قتيل", "مصاب", "مصابين", "إصابات",
                   "جرحى", "شهداء", "ضحايا")
_NUM_RE = __import__("re").compile(r"\d+")


def _has_casualty_numbers(text: str) -> bool:
    """رقم مطابق لأي كلمة حصيلة (عربي/هندي) => نتيجة صريحة."""
    from .utils import normalize_arabic as _norm

    hay = _norm(text)
    if not any(_norm(w) in hay for w in _CASUALTY_WORDS):
        return False
    digits = _NUM_RE.findall(hay.translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")))
    return any(int(d) >= 1 for d in digits if d.isdigit())


def has_explicit_no_result(text: str) -> bool:
    """هل ينصّ الخبر صراحةً على عدم توفر النتائج بعد؟"""
    hay = norm(text)
    return any(norm(p) in hay for p in _NO_RESULT_YET)


def analyze(article: Article) -> OutcomeAnalysis:
    """تحليل خبر لمعرفة إن كان نتيجة قابلة للنشر أم حدثاً خاماً."""
    text = f"{article.title} {article.summary} {article.content}"
    result = OutcomeAnalysis()

    incident_hits = _find(text, INCIDENT_SIGNALS)
    result.is_incident = bool(incident_hits)

    for kind, words in OUTCOME_SIGNALS.items():
        hits = _find(text, words)
        if hits:
            result.kinds.append(kind)
            result.signals.extend(hits[:4])
    result.has_outcome = bool(result.kinds)
    result.signals = list(dict.fromkeys(result.signals))[:8]

    on, off = detect_governorates(text)
    result.governorates_on, result.governorates_off = on, off
    # ⚠️ ذكر المحافظة وحده (مكان الحادث) ليس «نتيجة» — نحتاج إشارة قرار/خدمة
    #    (عطلة، تعطيل، استثناء، صرف…) لتكون هناك «مصفوفة محافظات» فعلية.
    service_hits = _find(text, OUTCOME_SIGNALS["service"])
    if (on or off) and service_hits:
        if "governorate" not in result.kinds:
            result.kinds.append("governorate")
        result.has_outcome = True

    # «حصيلة 5 وفيات و23 مصاباً» — رقم + كلمة نتيجة صريحة = نتيجة بلا لبس
    if not result.has_outcome and _has_casualty_numbers(text):
        result.kinds.append("person")
        result.has_outcome = True
        result.signals.append("أرقام حصيلة")

    # «لم ترد معلومات عن ضحايا» ليست تصعيداً — النفي يلغي الإشارتين معاً
    no_result = has_explicit_no_result(text)
    result.escalate = bool(_find(text, ESCALATION_SIGNALS)) and not no_result
    # نفي صريح للنتائج («لم يُعلن عن إصابات»، «لم تتضح») يلغي أي إشارة سطحية:
    # كلمة «إصابات» بعد نفي ليست نتيجة — وهذا يُمنع نشر الحدث الخام باسم «نتيجة».
    if no_result:
        result.has_outcome = False
    return result


def should_publish(article: Article, settings: OutcomeSettings) -> tuple[bool, str, OutcomeAnalysis]:
    """قرار النشر وفق سياسة المحتوى. يعيد (يُنشر؟، السبب، التحليل)."""
    analysis = analyze(article)

    if not settings.enabled:
        return True, "نمط النتائج معطّل — نشر عادي", analysis

    policy = (settings.policy or "outcomes_only").lower()

    if policy == "raw":
        return True, "سياسة raw: نشر كل عاجل", analysis

    # نتائج واضحة ⇒ تُنشر
    if analysis.has_outcome and (analysis.kinds or analysis.governorates_on or analysis.governorates_off):
        # إن كان الخبر «حدثاً» و«نتيجة» معاً (مثل: انفجار + حصيلة) فهو نتيجة مقبولة
        return True, f"يحمل نتائج ({analysis.summary()})", analysis

    # حدث خام بلا نتائج
    if analysis.is_incident:
        if policy == "hybrid" and analysis.escalate:
            return True, "حدث كبير (سياسة hybrid) — يُنشر مع تفاصيل النطاق", analysis
        if not settings.allow_incident_headlines:
            return False, "حدث خام بلا نتائج — يُؤجّل حتى تصدر النتائج", analysis
        return True, "حدث خام (allow_incident_headlines=true)", analysis

    # خبر عادي غير حادث: يُنشر كالمعتاد
    return True, "خبر عادي", analysis


# --------------------------------------------------------------------------- #
#  بناء بطاقة النتائج (بدون AI كخط احتياطي)
# --------------------------------------------------------------------------- #
def build_outcome_body(article: Article, analysis: OutcomeAnalysis) -> str:
    """بطاقة نتائج منظمة من النص نفسه (قالب احتياطي يعمل دائماً)."""
    lines: list[str] = []

    if analysis.governorates_on or analysis.governorates_off:
        if analysis.governorates_on:
            lines.append("✅ مشمولة: " + "، ".join(analysis.governorates_on[:8]))
        if analysis.governorates_off:
            lines.append("◻️ مستثناة: " + "، ".join(analysis.governorates_off[:8]))
        if analysis.governorates_on and not analysis.governorates_off:
            lines.append("ℹ️ لم يُذكر استثناء صريح — يُستحسن متابعة البيان الرسمي.")

    # جملة الحالة / القرار من النص
    text = " ".join((article.summary or article.content or "").split())
    if text:
        sentences = re.split(r"(?<=[.!؟?])\s+", text)
        picked = [s for s in sentences
                  if _find(s, OUTCOME_SIGNALS["person"] + OUTCOME_SIGNALS["service"])][:3]
        for s in picked:
            lines.append("• " + s.strip()[:180])

    if not lines:
        lines.append("• " + (article.summary[:200] or article.title))

    return "\n".join(lines)


OUTCOME_SYSTEM = """أنت محرّر «نتائج» في غرفة أخبار عراقية.
مهمتك: تلخيص **ما نتج** عن الحدث، لا وصف الحدث نفسه.

قواعد صارمة:
1) لا تختلق شيئاً. لا أسماء ولا أرقام ولا محافظات لم تُذكر في النص.
2) لا تبدأ بوصف الانفجار/الهجوم/الحادث. ابدأ بالنتيجة.
3) إن لم تكن هناك نتيجة واضحة، أخرج "body" فارغاً وكلمة "skip": true.
4) رتّب النتائج: أولاً ما يخصّ الأشخاص، ثم المحافظات، ثم القرارات والخدمات.
5) أرقام عربية واضحة، بلا مبالغة ولا صفات درامية.

أخرج JSON فقط:
{"title": "...", "people": "...", "governorates_on": ["..."], "governorates_off": ["..."],
 "decisions": "...", "body": "...", "skip": false}
"""


def build_outcome_prompt(article: Article, analysis: OutcomeAnalysis) -> str:
    gov_hint = ""
    if analysis.governorates_on or analysis.governorates_off:
        gov_hint = (f"\n- محافظات رصدها النظام مبدئياً: مشمولة={analysis.governorates_on} · "
                    f"مستثناة={analysis.governorates_off} (تحقّق من النص، لا تُضف غيرها).")
    return f"""لخّص نتائج الخبر التالي في بطاقة قصيرة.

الخبر: {article.title}
النص: {article.summary[:700]}
{('- تفاصيل إضافية: ' + article.content[:500]) if article.content else ''}
{gov_hint}

المطلوب:
- people: ماذا صار للأشخاص (حالة، إصابات، وفيات، توقيف، إفراج، إحالة، تعويض) — أو "" إن لم يُذكر.
- governorates_on / governorates_off: المحافظات المشمولة والمستثناة (فقط ما ورد بالنص).
- decisions: القرارات أو الإجراءات (تعطيل دوام، فتح طريق، صرف مبالغ…).
- title: عنوان يبدأ بالنتيجة، ≤ 12 كلمة.
- body: 3–5 أسطر منظمة بالعربية، كل سطر معلومة واحدة.
- skip: true إذا لم يكن في الخبر أي نتيجة فعلية.

أخرج JSON فقط.
"""
