"""📊 إحصاءات سريعة — «ما الذي نُشر فعلاً، وأين، ومتى؟»

المصدر الوحيد للحقيقة هو `state/state.json` (سجلّ `posted` + صحة المصادر +
الطابور). لا قياس خارجي ولا خدمة تحليلات — كل شيء محلي ومجّاني.

الغرض العملي: تجيب في ثانية على ثلاثة أسئلة:
    • هل تنشر القناة الفلانية أصلاً؟ (قناة صامتة = إعداد خاطئ)
    • أي ساعة تنجح؟ (لضبط ساعات الهدوء بذكاء)
    • أي مصدر يجلب الأخبار وأيها ميت؟ (لتنظيف المصادر)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .control import KIND_APPROVAL, KIND_SCHEDULED, ControlStore
from .state import StateStore
from .utils import get_logger, parse_dt, truncate, utcnow

log = get_logger("autonews.stats")

_SPARK = "▁▂▃▄▅▆▇█"


def _spark(values: list[int]) -> str:
    """رسم شريطي صغير بلا مكتبات (24 خانة = 24 ساعة)."""
    top = max(values) or 1
    return "".join(_SPARK[min(len(_SPARK) - 1, int(v / top * (len(_SPARK) - 1)))]
                   for v in values)


@dataclass(slots=True)
class ChannelStats:
    channel_id: str
    title: str = ""
    total: int = 0
    today: int = 0
    week: int = 0
    with_image: int = 0
    last_at: str = ""
    last_title: str = ""
    by_hour: list[int] = field(default_factory=lambda: [0] * 24)

    def age_label(self) -> str:
        dt = parse_dt(self.last_at)
        if dt is None:
            return "لم تنشر بعد"
        hours = (utcnow() - dt).total_seconds() / 3600
        if hours < 1:
            return f"قبل {int(hours * 60)} دقيقة"
        if hours < 24:
            return f"قبل {int(hours)} ساعة"
        return f"قبل {int(hours / 24)} يوم"

    def status_icon(self) -> str:
        """🚨 القناة الصامتة ٤٨ ساعة = عطل خفيّ يجب أن يُرى."""
        if not self.total:
            return "⬜"
        dt = parse_dt(self.last_at)
        if dt and (utcnow() - dt).total_seconds() > 48 * 3600:
            return "🚨"
        return "✅" if self.today else "😴"


@dataclass(slots=True)
class StatsReport:
    channels: list[ChannelStats] = field(default_factory=list)
    total_posts: int = 0
    today_total: int = 0
    week_total: int = 0
    sources_ok: int = 0
    sources_dead: int = 0
    pending_approvals: int = 0
    scheduled: int = 0
    best_hour: int = -1
    by_hour: list[int] = field(default_factory=lambda: [0] * 24)
    windows: tuple[str, str] = ("", "")

    # -------------------------------------------------------------- العرض
    def text(self) -> str:
        lines = ["📊 <b>إحصاءات النشر</b>", ""]
        lines.append(f"اليوم: <b>{self.today_total}</b> · هذا الأسبوع: <b>{self.week_total}</b> "
                     f"· الإجمالي: <b>{self.total_posts}</b>")
        if self.best_hour >= 0:
            lines.append(f"⏰ أنشط ساعة عندك: <b>{self.best_hour:02d}:00</b>")
        lines.append(f"🕒 آخر ٢٤ ساعة: <code>{_spark(self.by_hour)}</code>")
        lines.append("")
        for c in self.channels:
            if not c.total:
                lines.append(f"⬜ <b>{c.title}</b> — لم تنشر بعد")
                continue
            lines.append(f"{c.status_icon()} <b>{c.title}</b> — "
                         f"اليوم {c.today} · الأسبوع {c.week} · الكل {c.total}")
            if c.last_title:
                lines.append(f"   <i>{truncate(c.last_title, 60)}</i> · {c.age_label()}")
        lines += [
            "",
            f"📡 مصادر تعمل: <b>{self.sources_ok}</b> · ميتة/معطّلة: <b>{self.sources_dead}</b>",
            f"👁 تنتظر موافقتك: <b>{self.pending_approvals}</b> · 🕒 مؤجّلة: <b>{self.scheduled}</b>",
        ]
        if self.windows[0]:
            lines += ["", f"🕒 نافذة النشر الفعلية: <b>{self.windows[0]} — {self.windows[1]}</b>"]
        return "\n".join(lines)

    def advice(self) -> list[str]:
        """توصيات عملية مستخرجة من الأرقام (لا أرقام بلا معنى)."""
        out: list[str] = []
        silent = [c.title for c in self.channels if not c.total]
        if silent:
            out.append(f"⬜ {len(silent)} قناة لم تنشر شيئاً — اربطها أو تحقّق من وسومها.")
        stale = [c.title for c in self.channels
                 if c.total and c.status_icon() == "🚨"]
        if stale:
            out.append(f"🚨 {len(stale)} قناة صامتة أكثر من ٤٨ ساعة: {'، '.join(stale[:3])}")
        if self.best_hour >= 0:
            w = (self.best_hour - 2) % 24, (self.best_hour + 3) % 24
            out.append(f"💡 أنشط نشر بين {w[0]:02d}:00 و{w[1]:02d}:00 — "
                       f"ضع ساعات الهدوء خارجها.")
        if self.sources_dead:
            out.append(f"📡 {self.sources_dead} مصدراً لا يعمل — أخرجه أو أصلحه.")
        if self.pending_approvals > 3:
            out.append(f"👁 {self.pending_approvals} منشوراً ينتظرك — التراكم يُبطئ النشر، "
                       f"جرّب النمط «🤖 ذكية».")
        return out


# --------------------------------------------------------------------------- #
#  الحساب
# --------------------------------------------------------------------------- #
def build_stats(*, known: list[tuple[str, str]] | None = None,
                state_file: str = "state/state.json",
                control_path: str = "state/control.json",
                tz_offset: int = 3) -> StatsReport:
    """جمع الأرقام من الحالة الفعلية (لا نحسب ما لم يُسجَّل)."""
    state = StateStore(state_file).load()
    control = ControlStore.load(control_path)
    report = StatsReport()

    titles = dict(known or [])
    buckets: dict[str, ChannelStats] = {}
    for cid, title in (known or []):
        buckets[cid] = ChannelStats(channel_id=cid, title=title)

    today_start = (utcnow() + timedelta(hours=tz_offset)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)
    now_utc = utcnow()
    today_utc = now_utc - (now_utc + timedelta(hours=tz_offset) -
                           today_start)

    hours_seen: set[int] = set()
    for entry in getattr(state, "posted", []) or []:
        cid = str(entry.get("channel", ""))
        bucket = buckets.get(cid)
        if bucket is None:
            bucket = buckets[cid] = ChannelStats(channel_id=cid, title=titles.get(cid, cid))
        bucket.total += 1
        report.total_posts += 1
        when = parse_dt(entry.get("at"))
        if when:
            local = when + timedelta(hours=tz_offset)
            bucket.by_hour[local.hour] += 1
            report.by_hour[local.hour] += 1
            hours_seen.add(local.hour)
            if when >= today_utc:
                bucket.today += 1
                report.today_total += 1
            if when >= week_start - timedelta(hours=tz_offset):
                bucket.week += 1
                report.week_total += 1
            if not bucket.last_at or str(entry.get("at", "")) > bucket.last_at:
                bucket.last_at = str(entry.get("at", ""))
                bucket.last_title = str(entry.get("title", ""))

    # الصور: لا يُسجَّل وجودها في `posted` — نقدّرها من مجموع الحالة إن وُجد
    for cid, cnt in (getattr(state, "stats", {}) or {}).items():
        if cid in buckets and isinstance(cnt, int):
            buckets[cid].with_image = cnt

    for cid, health in (getattr(state, "source_health", {}) or {}).items():
        if health.get("disabled") or int(health.get("failures", 0)) >= 3:
            report.sources_dead += 1
        else:
            report.sources_ok += 1

    report.channels = sorted(buckets.values(), key=lambda c: -c.total)
    if any(report.by_hour):
        report.best_hour = max(range(24), key=lambda h: report.by_hour[h])
    if hours_seen:
        report.windows = (f"{min(hours_seen):02d}:00", f"{max(hours_seen):02d}:00")

    report.pending_approvals = len(control.pending(kind=KIND_APPROVAL))
    report.scheduled = len(control.pending(kind=KIND_SCHEDULED))
    return report


__all__ = ["ChannelStats", "StatsReport", "build_stats"]
