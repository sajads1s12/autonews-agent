"""ميزات النتائج المتقدّمة (v6) — فوق كاشف النتائج الأساسي.

المكوّنات:
  ١) سطر «الأثر عليك»        → `impact_line()`
  ٢) اللاحقة (Aftermath)      → كيف يتحوّل المؤجّل إلى بطاقة نتيجة عند صدورها
  ٣) بطاقة المحافظات اليومية  → `build_governorate_daily()`
  ٥) التحقّق المزدوج          → `verify_result()` + `SourceEvidence`
  ٦) شفافية المصدر            → `source_line()`
  ٧) الحوادث الصامتة          → `silent_incidents()` (تنبيه فقط، لا نشر)
  ٨) التعارض بين المحافظات    → `governorate_contrast()`

كل شيء هنا نقيّ (بلا شبكة) وقابل للاختبار وحده.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .config_loader import OutcomeSettings
from .ingest import Article
from .outcomes import GOVERNORATES, OutcomeAnalysis, analyze, norm
from .utils import get_logger, parse_dt, utcnow

log = get_logger("autonews.outcome_features")


# =========================================================================== #
#  ١) سطر «الأثر عليك» — أهم سطر في البطاقة
# =========================================================================== #
#  كل قاعدة: (نمط، قالب الأثر). نُعيد أول مطابقة فقط — الأثر الأقرب للقارئ.
_IMPACT_RULES: list[tuple[tuple[str, ...], str]] = [
    (("تعطيل الدوام", "تعليق الدوام", "عطلة", "العطلة", "تعطيل", "الدوام"),
     "دوامك الرسمي يتأثر — تحقّق من محافظتك قبل الخروج"),
    (("استئناف الدوام", "استئناف العمل"), "الدوام يعود طبيعياً — راجع مؤسستك للتأكيد"),
    (("سعر الصرف", "سعر الدولار", "الدولار", "الصرافة"),
     "سعر صرف الدولار يمسّ جيبك مباشرة اليوم"),
    (("راتب", "رواتب", "مستحقات", "صرف الرواتب", "المخصصات"),
     "راتبك/مستحقاتك على هذا المسار — تابع موعد الصرف"),
    (("بنزين", "وقود", "محطة"), "وقود سيارتك: التوزيع قد يتأثر في المحافظات المذكورة"),
    (("كهرباء", "الطاقة", "تجهيز"), "ساعات التجهيز الكهربائي قد تتغيّر في مناطقك"),
    (("ماء", "المياه", "إسالة"), "إمداد الماء قد ينقطع جزئياً — خزّن ما تحتاجه"),
    (("إغلاق طريق", "فتح الطريق", "قطع الطريق", "تحويلة", "الطرق"),
     "مسار سفرك يتغيّر — اختر طريقاً بديلاً قبل الانطلاق"),
    (("امتحان", "امتحانات", "الدراسة", "المدارس", "التربية"),
     "جدول الدراسة/الامتحان قد يتغيّر — تابع كتاب المدرسة"),
    (("توقيف", "اعتقال", "إفراج", "إخلاء سبيل", "إحالة", "حكم", "محاكمة"),
     "ملف قانوني قيد المتابعة — النتائج الرسمية تصدر لاحقاً"),
    (("تعويض", "تعويضات"), "تعويض مالي: سجّل طلبك عبر المحافظة قبل انتهاء المهلة"),
    (("إخلاء", "نزوح", "إيواء"), "إن كنت قريباً من المنطقة: جهّز وثائقك واستمع للدفاع المدني"),
    (("حريق", "انهيار"), "سلامتك أولاً — لا تقترب من الموقع واترك الطريق لفرق الإنقاذ"),
]

_IMPACT_MAX = 62   # حدّ الأحرف ليبقى سطراً واحداً على الجوال


def impact_line(article: Article, analysis: OutcomeAnalysis | None = None) -> str:
    """سطر واحد: ماذا يعني هذا الخبر لك أنت (لا للدولة)."""
    text = f"{article.title} {article.summary} {article.content}"
    hay = norm(text)
    for keys, template in _IMPACT_RULES:
        if any(norm(k) in hay for k in keys):
            return template[:_IMPACT_MAX]
    # احتياط: إن ذُكرت محافظة محدّدة نسأل القارئ عن موقعه
    if analysis and (analysis.governorates_on or analysis.governorates_off):
        return "يخصّ محافظتك — راجع التفاصيل قبل أي التزام"
    return ""


# =========================================================================== #
#  ٢) اللاحقة — الحادثة المؤجّلة لا تُنسى
# =========================================================================== #
@dataclass(slots=True)
class Aftermath:
    """حادثة خام مؤجّلة، بانتظار أن تصدر نتائجها."""
    fp: str
    title: str
    url: str
    source_id: str
    source_name: str
    summary: str = ""
    governorates: list[str] = field(default_factory=list)
    first_seen: str = ""
    checks: int = 0
    last_check: str = ""
    score: float = 0.0            # أهمية الحادثة (تحدد أولوية المتابعة)


def register_deferred(analysis: OutcomeAnalysis, article: Article, *,
                      store: list[dict], min_score: float = 0.0) -> bool:
    """تسجيل حادثة مؤجّلة لمتابعتها لاحقاً. يعيد True إن أُضيفت/حُدّثت."""
    if not analysis.is_incident:
        return False
    score = 0.0
    score += 2.0 if analysis.escalate else 0.0
    score += 1.0 * min(len(analysis.governorates_on) + len(analysis.governorates_off), 3)
    score += 1.0 if re.search(r"\d", article.title + article.summary) else 0.0
    score += 1.0 if len(analysis.signals) >= 2 else 0.0
    if score < min_score:
        return False
    for item in store:
        if item.get("fp") == article.fp:
            item["checks"] = int(item.get("checks", 0)) + 1
            item["last_check"] = utcnow().isoformat()
            return True
    store.append({
        "fp": article.fp, "title": article.title[:160], "url": article.url,
        "source_id": article.source_id, "source_name": article.source_name,
        "summary": (article.summary or "")[:400],
        "governorates": list(dict.fromkeys(analysis.governorates_on + analysis.governorates_off)),
        "first_seen": utcnow().isoformat(), "checks": 0, "last_check": "",
        "score": round(score, 2),
    })
    log.info("🕓 رُصدت حادثة بانتظار نتائجها: %s", article.title[:70])
    return True


def resolve_followups(articles: Iterable[Article], store: list[dict], *,
                      min_hours: float = 1.0) -> list[tuple[dict, Article, OutcomeAnalysis]]:
    """مقارنة المقالات الجديدة بالحوادث المؤجّلة: هل صدرت نتيجة تطابقها؟

    يعيد قائمة (العنصر المؤجّل، المقال الجديد، تحليل النتيجة) — ويحذف المطابقات.
    """
    resolved: list[tuple[dict, Article, OutcomeAnalysis]] = []
    if not store:
        return resolved

    for item in list(store):
        item_govs = {g for g in item.get("governorates", []) if g}
        item_tokens = set(norm(item.get("title", "")).split())
        for art in articles:
            if art.url == item.get("url"):
                continue
            a = analyze(art)
            if not a.has_outcome:
                continue
            # المطابقة: نفس المحافظة + تشابه واضح في العنوان (أو إشارة حادثة مشتركة)
            art_govs = set(a.governorates_on + a.governorates_off)
            shared_gov = bool(item_govs & art_govs)
            art_tokens = set(norm(art.title).split())
            overlap = len(item_tokens & art_tokens) / max(1, len(item_tokens))
            if shared_gov or overlap >= 0.45:
                resolved.append((item, art, a))
                log.info("🔄 صدرت نتيجة لحادثة متابَعة: %s → %s",
                         item.get("title", "")[:45], art.title[:45])
                store.remove(item)
                break
    return resolved


def followup_badge(item: dict) -> str:
    """وسم زمني: كم انتظرنا هذه الحادثة حتى صدرت نتيجتها؟"""
    dt = parse_dt(item.get("first_seen"))
    if not dt:
        return ""
    hours = max(0.0, (utcnow() - dt).total_seconds() / 3600)
    if hours < 1:
        return "🔄 تحديث فوري"
    if hours < 24:
        return f"🔄 تطوّر بعد {int(round(hours))} ساعة"
    return f"🔄 تطوّر بعد {int(round(hours / 24))} يوم"


# =========================================================================== #
#  ٣) بطاقة المحافظات اليومية
# =========================================================================== #
@dataclass(slots=True)
class GovStatus:
    """حالة محافظة واحدة اليوم."""
    name: str
    holiday: bool = False        # عطلة/تعطيل دوام
    service: str = ""            # وصف الخدمة المتأثرة
    notes: list[str] = field(default_factory=list)


def build_governorate_daily(items: list[tuple[Article, OutcomeAnalysis]], *,
                            date_label: str = "") -> str:
    """منشور واحد: «خريطة المحافظات اليوم» — عطلة/خدمات لكل محافظة.

    items: أزواج (مقال، تحليله). نستخرج حالة كل محافظة ثم نبني جدولاً نصياً.
    """
    statuses: dict[str, GovStatus] = {}
    holiday_words = ("عطلة", "تعطيل", "تعليق الدوام", "الدوام", "عطّلت", "تعطل")
    service_words = ("كهرباء", "ماء", "الطرق", "إغلاق", "صرف", "بنزين", "وقود", "وقود")

    def touch(name: str) -> GovStatus:
        st = statuses.get(name)
        if st is None:
            st = statuses[name] = GovStatus(name=name)
        return st

    for article, analysis in items:
        hay = norm(f"{article.title} {article.summary}")
        is_holiday = any(norm(w) in hay for w in holiday_words)
        service = next((w for w in service_words if norm(w) in hay), "")
        for gov in analysis.governorates_on:
            st = touch(gov)
            st.holiday = st.holiday or is_holiday
            if service and not st.service:
                st.service = service
            if len(st.notes) < 2:
                st.notes.append(article.title[:70])
        for gov in analysis.governorates_off:
            st = touch(gov)
            st.notes.append("غير مشمولة")

    if not statuses:
        return ""

    # ترتيب بأهمية المحافظة (بغداد ثم حسب الأبجدية)
    order = list(GOVERNORATES.keys())
    rows = sorted(statuses.values(), key=lambda s: (order.index(s.name) if s.name in order else 99, s.name))

    on = [r.name for r in rows if r.holiday]
    off = [r.name for r in rows if not r.holiday and "غير مشمولة" in r.notes]
    quiet = [r.name for r in rows if not r.holiday and "غير مشمولة" not in r.notes]

    lines = [f"🗺️ خريطة المحافظات اليوم{(' — ' + date_label) if date_label else ''}", "━━━━━━━━━━━━━━"]
    if on:
        lines.append("🛑 عطلة/تعطيل دوام: " + "، ".join(on[:12]))
    if quiet:
        lines.append("🟢 دوام طبيعي: " + "، ".join(quiet[:12]))
    if off:
        lines.append("⚪ غير مشمولة صراحةً: " + "، ".join(off[:12]))

    services = [f"{r.name}: {r.service}" for r in rows if r.service]
    if services:
        lines.append("")
        lines.append("🔧 خدمات متأثرة:")
        lines.extend("• " + s for s in services[:6])

    lines.append("")
    lines.append("ℹ️ تُحدَّث عند كل بيان رسمي — لا تعتمد على منشور واحد.")
    return "\n".join(lines)


# =========================================================================== #
#  ٥) التحقّق المزدوج
# =========================================================================== #
@dataclass(slots=True)
class SourceEvidence:
    """دليل مصدر واحد على نتيجة معيّنة."""
    source_id: str
    source_name: str
    official: bool = False
    trust: float = 1.0


@dataclass(slots=True)
class Verification:
    """نتيجة التحقّق: هل النتيجة موثوقة بما يكفي للنشر؟"""
    ok: bool
    reason: str
    sources: list[str] = field(default_factory=list)
    confidence: str = "منخفضة"     # عالية | متوسطة | منخفضة

    def badge(self) -> str:
        icon = {"عالية": "🟢", "متوسطة": "🟡", "منخفضة": "🟠"}.get(self.confidence, "🟠")
        return f"{icon} ثقة {self.confidence}"


def verify_result(evidence: list[SourceEvidence], settings: OutcomeSettings) -> Verification:
    """قاعدة التحقّق المزدوج.

    - مصدر رسمي واحد ⇒ موثوقة (عالية).
    - مصدران مستقلّان ⇒ موثوقة (متوسطة على الأقل).
    - مصدر واحد غير رسمي ⇒ تُنشر فقط إن لم يُشترط التحقّق.
    """
    if not evidence:
        return Verification(ok=not settings.dual_verify, reason="بلا دليل مصدر",
                            confidence="منخفضة")
    official = [e for e in evidence if e.official]
    names = [f"{e.source_name}{' (رسمي)' if e.official else ''}" for e in evidence]

    if official:
        return Verification(True, "مصدر رسمي", names, "عالية")
    if len({e.source_id for e in evidence}) >= 2:
        best = max(e.trust for e in evidence)
        return Verification(True, f"{len(evidence)} مصادر مستقلة", names,
                            "متوسطة" if best < 1.2 else "عالية")
    if settings.dual_verify:
        return Verification(False, "مصدر واحد غير رسمي — ينتظر تأكيداً ثانياً", names, "منخفضة")
    return Verification(True, "مصدر واحد (التحقّق المزدوج معطّل)", names, "منخفضة")


# =========================================================================== #
#  ٦) شفافية المصدر
# =========================================================================== #
def source_line(article: Article, *, official: bool = False, trust: float = 1.0,
                verification: Verification | None = None,
                age_minutes: int | None = None) -> str:
    """سطر شفافية: من قال الخبر، ما وزنه، ومنذ متى."""
    parts = [f"📍 المصدر: {article.source_name}{' — رسمي' if official else ''}"]
    if trust and abs(trust - 1.0) > 0.01:
        parts.append(f"وزن الثقة {trust:.1f}")
    if verification is not None:
        parts.append(verification.badge())
    if age_minutes is not None and 0 <= age_minutes < 24 * 60:
        parts.append(f"قبل {age_minutes} دقيقة" if age_minutes < 120
                     else f"قبل {age_minutes // 60} ساعة")
    return " · ".join(parts)


# =========================================================================== #
#  ٧) الحوادث الصامتة — تنبيه إداري فقط
# =========================================================================== #
def silent_incidents(store: list[dict], *, hours: float = 6.0) -> list[dict]:
    """حوادث كبرى مضت مدّتها بلا أي نتيجة ⇒ ننبّه المحرّر (لا ننشر).

    هذه هي النقطة التي يخطئ فيها الجميع: تُنشر الحادثة أو تُنسى.
    هنا: لا نشر، لكن **لا نسيان**.
    """
    out: list[dict] = []
    now = utcnow()
    for item in store:
        dt = parse_dt(item.get("first_seen"))
        if not dt:
            continue
        age_h = (now - dt).total_seconds() / 3600
        if age_h >= hours and float(item.get("score", 0)) >= 2.0:
            out.append({**item, "age_hours": round(age_h, 1)})
    out.sort(key=lambda i: (-float(i.get("score", 0)), -float(i.get("age_hours", 0))))
    return out


def silent_alert_text(items: list[dict]) -> str:
    """نص تنبيه تيليجرام للحوادث الصامتة (لا يُنشر على القنوات)."""
    if not items:
        return ""
    lines = ["🕵️ <b>حوادث بلا نتائج منشورة</b>",
             "هذه لم تُنشر (وهذا صحيح) — لكن قد تحتاج تدخّلك:"]
    for it in items[:5]:
        lines.append(f"• {it.get('title', '')[:90]}")
        lines.append(f"   ⏱ منذ {it.get('age_hours', 0)} ساعة · أهمية {it.get('score', 0)}"
                     f" · {it.get('source_name', '')}")
    lines.append("")
    lines.append("💡 لو صدرت النتائج لاحقاً فستُنشر تلقائياً.")
    return "\n".join(lines)


# =========================================================================== #
#  ٨) التعارض بين المحافظات
# =========================================================================== #
_CONTRAST_WORDS = ("عطلة", "تعطيل", "الدوام", "دوام", "استثنى", "استثناء", "عدا")


def governorate_contrast(items: list[tuple[Article, OutcomeAnalysis]]) -> str:
    """هل يوجد تعارض يستحق خبراً؟ («البصرة عطّلت وبغداد لا»)."""
    holiday: set[str] = set()
    normal: set[str] = set()
    for article, analysis in items:
        hay = norm(f"{article.title} {article.summary}")
        if not any(norm(w) in hay for w in _CONTRAST_WORDS):
            continue
        holiday.update(analysis.governorates_on)
        normal.update(analysis.governorates_off)

    both = holiday & normal
    holiday -= both
    normal -= both
    if not holiday or not normal:
        return ""
    return (f"⚖️ تفاوت واضح: {('، '.join(sorted(holiday)[:4]))} عطّلت/غيّرت الدوام، "
            f"بينما {('، '.join(sorted(normal)[:4]))} لم تشملها — راجع حالتك قبل الغد.")
