"""الأعياد والمناسبات الرسمية + ذكاء «التحديث مقابل التكرار».

المشكلة التي يحلّها هذا الملف:
--------------------------------
في الأعياد والعطل الرسمية تتكرّر الأخبار المتشابهة بشكل مشروع تماماً:
    «عطلة رسمية غداً»  ثم  «تمديد العطلة»  ثم  «تفاصيل العطلة والخدمات».
النظام القديم كان يعتبرها تكراراً فيحجبها — وهذا خطأ لفظي ووظيفي.

الحل:
1) `OccasionCalendar` يعرف اليوم أي مناسبة/موسم/عطلة.
2) المناسبة تُغيّر سلوك النظام: نبرة أدفأ، رفع أولوية الأخبار الخدمية،
   و**تساهل في منع التكرار** ضمن نافذة زمنية محدودة.
3) `is_update_not_duplicate()` تميّز «تحديثاً على نفس الموضوع» من «تكراراً حرفياً»
   عبر: تشابه الموضوع + إشارات تحديث (تمديد/تفاصيل/حصيلة…) + نافذة زمنية.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .utils import get_logger, normalize_arabic, parse_dt, utcnow

log = get_logger("autonews.occasions")


# --------------------------------------------------------------------------- #
#  النموذج
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Occasion:
    id: str
    name: str
    kind: str = "national"          # national | religious | global | season
    annual: list[str] = field(default_factory=list)
    ranges: list[tuple[date, date]] = field(default_factory=list)
    season: tuple[str, str] | None = None
    relax_dedup: bool = False
    update_window_hours: int = 24
    max_updates: int = 2
    tone: str = ""
    style_hint: str = ""
    greeting: str = ""
    priority_boost: bool = True

    def matches(self, day: date) -> bool:
        """هل اليوم داخل هذه المناسبة؟"""
        month_day = day.strftime("%m-%d")
        if month_day in self.annual:
            return True
        for start, end in self.ranges:
            if start <= day <= end:
                return True
        if self.season:
            start_md, end_md = self.season
            if start_md <= end_md:
                return start_md <= month_day <= end_md
            # موسم يعبر رأس السنة (مثل موسم الأمطار 11-01 → 03-15)
            return month_day >= start_md or month_day <= end_md
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "kind": self.kind,
            "tone": self.tone, "style_hint": self.style_hint,
            "relax_dedup": self.relax_dedup,
            "update_window_hours": self.update_window_hours,
            "max_updates": self.max_updates,
        }


# --------------------------------------------------------------------------- #
#  التقويم
# --------------------------------------------------------------------------- #
class OccasionCalendar:
    """تقويم المناسبات مع أساليب مساعدة للسياق التحريري."""

    def __init__(self, occasions: list[Occasion], *, update_keywords: list[str] | None = None,
                 greetings_enabled: bool = False) -> None:
        self.occasions = occasions
        self.update_keywords = [normalize_arabic(k.lower()) for k in (update_keywords or [])]
        self.greetings_enabled = greetings_enabled
        log.info("تقويم المناسبات: %d مناسبة/موسم", len(occasions))

    # ------------------------------------------------------------ التحميل
    @classmethod
    def from_config(cls, path: str | Path = "config/occasions.yml") -> "OccasionCalendar":
        p = Path(path)
        if not p.exists():
            log.info("لا يوجد ملف مناسبات (%s) — سيعمل النظام بلا سياق مناسبات", p)
            return cls([])
        try:
            import yaml

            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 — ملف اختياري لا يُسقط التشغيل
            log.warning("تعذّر قراءة ملف المناسبات: %s", exc)
            return cls([])

        occasions: list[Occasion] = []
        for item in data.get("occasions") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            ranges: list[tuple[date, date]] = []
            for spec in item.get("ranges") or []:
                try:
                    start_s, end_s = str(spec).split("..")
                    ranges.append((date.fromisoformat(start_s.strip()), date.fromisoformat(end_s.strip())))
                except (ValueError, AttributeError):
                    log.warning("نطاق تاريخ غير صالح في %s: %s", item.get("id"), spec)
            season = None
            if isinstance(item.get("season"), dict):
                season = (str(item["season"].get("from")), str(item["season"].get("to")))
            occasions.append(Occasion(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                kind=str(item.get("kind", "national")),
                annual=[str(a) for a in (item.get("annual") or [])],
                ranges=ranges,
                season=season,
                relax_dedup=bool(item.get("relax_dedup", False)),
                update_window_hours=int(item.get("update_window_hours", 24)),
                max_updates=int(item.get("max_updates", 2)),
                tone=str(item.get("tone", "")),
                style_hint=str(item.get("style_hint", "")),
                greeting=str(item.get("greeting", "")),
                priority_boost=bool(item.get("priority_boost", True)),
            ))
        greetings = bool((data.get("greetings") or {}).get("enabled", False))
        return cls(occasions, update_keywords=data.get("update_keywords"), greetings_enabled=greetings)

    # ------------------------------------------------------------ الاستعلام
    def today(self, *, tz_offset: int = 3) -> list[Occasion]:
        """المناسبات الفعّالة اليوم (بتوقيت المستخدم لا UTC)."""
        local_day = (utcnow() + timedelta(hours=tz_offset)).date()
        return [o for o in self.occasions if o.matches(local_day)]

    def context_hint(self, *, tz_offset: int = 3) -> str:
        """سطر سياق يُمرَّر للنموذج ليعرف «أين نحن من السنة»."""
        active = self.today(tz_offset=tz_offset)
        if not active:
            return ""
        hints = []
        for o in active:
            bit = f"{o.name} ({o.tone})"
            if o.style_hint:
                bit += f" — {o.style_hint}"
            hints.append(bit)
        return " · ".join(hints)

    def dedup_policy(self, *, tz_offset: int = 3) -> dict[str, Any]:
        """سياسة منع التكرار الفعّالة اليوم.

        القيمة الافتراضية صارمة؛ وأي مناسبة `relax_dedup` تفتح نافذة تساهل.
        """
        active = [o for o in self.today(tz_offset=tz_offset) if o.relax_dedup]
        if not active:
            return {"relaxed": False, "window_hours": 0, "max_updates": 0,
                    "occasions": [], "similarity": 0.95}
        best = max(active, key=lambda o: o.update_window_hours)
        return {
            "relaxed": True,
            "window_hours": best.update_window_hours,
            "max_updates": max(o.max_updates for o in active),
            "occasions": [o.name for o in active],
            "similarity": 0.55,      # نافذة أوسع: نسمح بعنوان مشابه إن كان تحديثاً
        }

    def active_greetings(self, *, tz_offset: int = 3) -> list[Occasion]:
        if not self.greetings_enabled:
            return []
        return [o for o in self.today(tz_offset=tz_offset) if o.greeting]


# --------------------------------------------------------------------------- #
#  ذكاء التحديث مقابل التكرار
# --------------------------------------------------------------------------- #
def has_update_signal(text: str, keywords: list[str]) -> str:
    """إرجاع أول إشارة تحديث موجودة في النص (أو '')."""
    haystack = normalize_arabic(f" {text} ").lower()
    for kw in keywords:
        if not kw:
            continue
        if re.search(rf"\b{re.escape(kw)}\b", haystack) or kw in haystack:
            return kw
    return ""


_OFFICIAL_TOPIC_WORDS = (
    "عطله", "عطلة", "تعطيل", "دوام", "الدوام", "اجازه", "اجازة", "اجازات",
    "مناسبه", "عيد", "اعياد", "احتفال", "رسمي", "الحكومه", "مجلس الوزراء",
    "التربيه", "الجامعات", "المدارس", "الدوائر", "المؤسسات",
)


def is_official_topic(text: str) -> bool:
    """هل الموضوع «رسمي/عطلة/مناسبة»؟ (نستخدمه لتساهل خاص)"""
    haystack = normalize_arabic(text or "").lower()
    return any(w in haystack for w in (normalize_arabic(w) for w in _OFFICIAL_TOPIC_WORDS))


def similarity_of(a: str, b: str) -> float:
    from .utils import jaccard

    return jaccard(a, b)


def topic_similarity(a: str, b: str) -> float:
    """تشابه موضوعي (يتجاوز اختلاف الصيغ الصرفية العربية)."""
    from .utils import topic_overlap

    return topic_overlap(a, b)


@dataclass
class DuplicateDecision:
    """نتيجة قرار «تكرار أم تحديث؟»."""
    duplicate: bool
    is_update: bool = False
    reason: str = ""
    signal: str = ""
    similarity: float = 0.0

    def __bool__(self) -> bool:
        return self.duplicate


def decide(
    *,
    new_title: str,
    new_summary: str,
    past_titles: list[str],
    published_at: datetime | None,
    update_keywords: list[str],
    policy: dict[str, Any],
    strict_similarity: float = 0.62,
    min_gap_hours: float = 3.0,
) -> DuplicateDecision:
    """القرار المركزي: تكرار يُحجب، أم تحديث مشروع يُنشر؟

    ترتيب القرار (مهم):
    1) موضوع مختلف تماماً ⇒ يُنشر (لا علاقة له بالسجلّ).
    2) **نفس العنوان حرفياً وخبر حديث (< 3 ساعات)** ⇒ تكرار مأخوذ من مصدر آخر ⇒ يُحجب.
       (لا تُغريه المناسبة: نفس الخبر من مصدرين ليس «تحديثاً»).
    3) تشابه موضوعي + إشارة تحديث (تمديد/تفاصيل/حصيلة…) ⇒ **تحديث يُنشر**.
    4) داخل موسم/مناسبة مع تساهل، وخبر سابق أقدم من 3 ساعات، والموضوع رسمي/عطلة
       ⇒ **تحديث يُنشر** (هذه هي الحالة التي طلبها المستخدم).
    5) خارج ذلك: تشابه عالٍ ⇒ تكرار.
    """
    if not past_titles:
        return DuplicateDecision(duplicate=False, reason="لا سجلّ سابق")

    best_sim, best_title = 0.0, ""
    best_topic = 0.0
    for title in past_titles:
        sim = similarity_of(new_title, title)
        if sim > best_sim:
            best_sim, best_title = sim, title
        best_topic = max(best_topic, topic_similarity(new_title, title))

    relaxed = bool(policy.get("relaxed"))
    threshold = float(policy.get("similarity", 0.95)) if relaxed else strict_similarity
    signal = has_update_signal(f"{new_title} {new_summary}", update_keywords)
    age_hours = None
    if published_at:
        age_hours = max(0.0, (utcnow() - published_at).total_seconds() / 3600)
    fresh_duplicate = age_hours is not None and age_hours < min_gap_hours

    # (1) موضوع مختلف
    if best_sim < 0.35 and best_topic < 0.30:
        return DuplicateDecision(
            duplicate=False,
            reason=f"موضوع مختلف (حرفي {best_sim:.0%} · موضوعي {best_topic:.0%})",
            similarity=best_sim)

    # (2) نفس الخبر خلال دقائق/ساعات قليلة ⇒ تكرار حقيقي (من مصدر آخر)
    if best_sim >= 0.92 and fresh_duplicate:
        return DuplicateDecision(
            True, False,
            f"تكرار خلال {age_hours:.1f} ساعة (تشابه {best_sim:.0%}) مع: {best_title[:55]}",
            signal, best_sim)

    # (3) إشارة تحديث على نفس الموضوع ⇒ تحديث مشروع
    if signal and best_topic >= 0.30:
        return DuplicateDecision(
            False, True,
            f"تحديث على نفس الموضوع بإشارة «{signal}» (تشابه موضوعي {best_topic:.0%})",
            signal, max(best_sim, best_topic))

    # (4) تساهل المناسبات: خبر عطلة/رسمي أقدم من min_gap ⇒ تطوّر طبيعي
    if relaxed and not fresh_duplicate and (
            is_official_topic(new_title) or is_official_topic(best_title)):
        return DuplicateDecision(
            False, True,
            f"موضوع رسمي/عطلة داخل موسم ({', '.join(policy.get('occasions', []))}) — "
            f"الخبر السابق عمره {age_hours if age_hours is not None else '?'} ساعة",
            signal, max(best_sim, best_topic))

    # (4ب) خارج المناسبات: خبر مطابق بعد > 6 ساعات = تطوّر لا تكرار
    if not relaxed and age_hours is not None and age_hours > 6 and best_topic >= 0.5:
        return DuplicateDecision(
            False, True, f"تطوّر بعد {age_hours:.0f} ساعة على خبر مشابه", signal,
            max(best_sim, best_topic))

    # (5) ما تبقّى: تشابه عالٍ ⇒ تكرار
    if max(best_sim, best_topic) >= min(threshold, 0.9):
        return DuplicateDecision(
            True, False,
            f"تكرار (حرفي {best_sim:.0%} · موضوعي {best_topic:.0%}) مع: {best_title[:55]}",
            signal, max(best_sim, best_topic))

    return DuplicateDecision(
        duplicate=False,
        reason=f"تشابه دون الحدّ (حرفي {best_sim:.0%} · موضوعي {best_topic:.0%})",
        similarity=best_sim)
