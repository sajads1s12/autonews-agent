"""لوحة الأداء (HTML) + «أفضل أسبوع» — قياس بلا خدمات خارجية.

الفكرة: لا تعتمد على أي API تحليلات. كل ما تحتاجه موجود في `state/state.json`:
المنشورات، أوقاتها، قنواتها، أنواعها (نتيجة 📋 / عاجل 🔴 / تحديث 🔄)، ومصادرها.
منها نبني لوحة HTML **مستقلة تماماً** (بلا CDN، بلا شبكة) تُفتح من الملف مباشرة،
وتقرير Markdown «أفضل أسبوع» جاهز للنشر أو للأرشفة.

لماذا HTML ملف واحد؟
- يُعرض داخل GitHub Actions كـ Artifact.
- يعمل بلا إنترنت (الشعارات والرسوم SVG مضمّنة).
- يمكن إرساله لعميل أو رفعه على أي استضافة بلا خادم.
"""
from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .utils import get_logger, iso_now, parse_dt, utcnow

log = get_logger("autonews.dashboard")


# =========================================================================== #
#  التجميع
# =========================================================================== #
@dataclass(slots=True)
class PerfRow:
    """سطر أداء واحد: منشور أو مجموعة."""
    label: str
    count: int
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class PerfSnapshot:
    """صورة كاملة عن فترة زمنية."""
    days: int = 7
    since: str = ""
    total_posts: int = 0
    by_platform: Counter = field(default_factory=Counter)
    by_channel: Counter = field(default_factory=Counter)
    by_kind: Counter = field(default_factory=Counter)
    by_source: Counter = field(default_factory=Counter)
    by_hour: Counter = field(default_factory=Counter)
    by_day: Counter = field(default_factory=Counter)
    top_sources: list[tuple[str, int]] = field(default_factory=list)
    best_posts: list[dict] = field(default_factory=list)
    quiet_hours: list[int] = field(default_factory=list)
    outcome_share: float = 0.0

    def as_dict(self) -> dict:
        return {
            "days": self.days, "since": self.since, "total_posts": self.total_posts,
            "by_platform": dict(self.by_platform), "by_channel": dict(self.by_channel),
            "by_kind": dict(self.by_kind), "by_source": dict(self.by_source),
            "by_hour": dict(self.by_hour), "by_day": dict(self.by_day),
            "outcome_share": round(self.outcome_share, 3),
        }


# أسماء عربية للمصادر في التقارير (المعرّفات التقنية لا تُقرأ)
SOURCE_LABELS: dict[str, str] = {
    "ina_official": "واع — محلي", "ina_politics": "واع — سياسي",
    "ina_world": "واع — دولي", "shafaq": "شفق نيوز", "baghdadtoday": "بغداد اليوم",
    "almadapaper": "المدى", "ultrasawt": "ألترا عراق",
    "gnews_iraq_urgent": "Google News عاجل", "gnews_holiday_iq": "Google News عطل",
    "gnews_transport_iq": "Google News طرق", "gnews_security_cell": "خلية الإعلام الأمني",
    "gnews_gov_decisions": "قرارات حكومية", "gnews_iraq_en": "Google News EN",
    "usgs_quakes": "USGS زلازل", "bbc_world": "BBC World", "aljazeera": "الجزيرة",
    "bbc_arabic": "BBC عربي", "skynewsarabia": "سكاي نيوز", "cnn_arabic": "CNN عربي",
    "dw_arabic": "DW عربية", "iraq_964": "964", "gdacs_alerts": "GDACS",
}


def source_label(source_id: str) -> str:
    """اسم عربي مقروء للمصدر (وإلّا المعرّف كما هو)."""
    return SOURCE_LABELS.get(source_id, source_id)


def _kind_of(title: str, *, is_outcome: bool = False) -> str:
    """تصنيف نوع المنشور من عنوانه (لأن السجلّ النصّي لا يحمل النوع دائماً)."""
    t = title or ""
    if t.startswith("🔄") or "🔄 تحديث" in t:
        return "تحديث"
    if t.startswith("📋") or "بطاقة نتائج" in t:
        return "نتيجة"
    if is_outcome:
        return "نتيجة"
    if t.startswith("🔴") or "عاجل" in t:
        return "عاجل"
    if t.startswith("🗺"):
        return "خريطة المحافظات"
    if t.startswith("⚖"):
        return "تفاوت محافظات"
    return "عادي"


def collect(state, *, days: int = 7, top_n: int = 10) -> PerfSnapshot:
    """بناء صورة الأداء من الحالة (بلا شبكة، بلا تبعيات)."""
    snap = PerfSnapshot(days=days, since=(utcnow() - timedelta(days=days)).isoformat())
    cutoff = utcnow() - timedelta(days=days)

    posts = []
    for p in state.posted:
        dt = parse_dt(p.get("at"))
        if dt and dt >= cutoff:
            posts.append({**p, "_dt": dt})
    # المسار السريع يُسجَّل في flash_log لا في posted — نضمّه
    for f in state.flash_log:
        dt = parse_dt(f.get("at"))
        if dt and dt >= cutoff:
            posts.append({
                "at": f.get("at"), "channel": f.get("channel", ""),
                "platform": "telegram", "title": f.get("title", ""),
                "url": "", "source": f.get("source", ""), "_dt": dt, "_flash": True,
            })

    snap.total_posts = len(posts)
    for p in posts:
        snap.by_platform[p.get("platform", "?")] += 1
        snap.by_channel[p.get("channel", "?")] += 1
        kind = _kind_of(p.get("title", ""))
        snap.by_kind[kind] += 1
        if p.get("source"):
            snap.by_source[p["source"]] += 1
        hour = (p["_dt"].hour + 3) % 24          # توقيت بغداد
        snap.by_hour[hour] += 1
        snap.by_day[p["_dt"].strftime("%Y-%m-%d")] += 1

    snap.top_sources = snap.by_source.most_common(top_n)
    if snap.total_posts:
        snap.outcome_share = snap.by_kind.get("نتيجة", 0) / snap.total_posts

    # أفضل المنشورات: النتائج أولاً ثم العاجل (الأثمن تحريرياً).
    # ⚠️ المنشور الواحد يُرسل لعدّة منصّات — ندمجها في سطر واحد بدل ٣ أسطر مكرّرة.
    priority = {"نتيجة": 3, "تحديث": 2, "خريطة المحافظات": 2, "تفاوت محافظات": 2, "عاجل": 1}
    merged: dict[str, dict] = {}
    for p in posts:
        title = (p.get("title") or "").strip()
        key = title[:80]
        row = merged.get(key)
        if row is None:
            merged[key] = {
                "title": title[:140], "kind": _kind_of(title),
                "channels": [p.get("channel", "")],
                "platforms": {p.get("platform", "")},
                "at": p.get("at", ""), "count": 1,
            }
        else:
            row["count"] += 1
            row["platforms"].add(p.get("platform", ""))
            if p.get("channel") and p["channel"] not in row["channels"]:
                row["channels"].append(p["channel"])

    ranked = sorted(merged.values(),
                    key=lambda r: (priority.get(r["kind"], 0), r["at"]), reverse=True)
    snap.best_posts = [
        {**r, "channel": " + ".join(filter(None, r["channels"]))[:40],
         "platforms": sorted(x for x in r["platforms"] if x)}
        for r in ranked[:top_n]
    ]

    # ساعات الصمت: ساعات لا نُشر فيها شيء خلال الفترة (فرص ضائعة)
    if snap.total_posts >= days * 2:
        busy = {h for h, c in snap.by_hour.items() if c}
        snap.quiet_hours = [h for h in range(24) if h not in busy]
    return snap


# =========================================================================== #
#  لوحة HTML (ملف واحد، بلا شبكة)
# =========================================================================== #
def _bars(data: dict[int, int], *, width: int = 420, height: int = 90) -> str:
    """رسم أعمدة SVG بسيط (بلا مكتبات)."""
    if not data:
        return ""
    keys = sorted(data)
    peak = max(data.values()) or 1
    bar_w = width / max(1, len(keys))
    parts = []
    for i, k in enumerate(keys):
        h = (data[k] / peak) * (height - 14)
        x = i * bar_w + 2
        y = height - h - 10
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 3:.1f}" '
                     f'height="{h:.1f}" rx="2" fill="#3b82f6" opacity="0.85"/>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height}" font-size="7" '
                     f'fill="#64748b" text-anchor="middle">{k}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img">{"".join(parts)}</svg>')


def _card(title: str, value: str, hint: str = "", accent: str = "#3b82f6") -> str:
    return (f'<div style="flex:1;min-width:150px;background:#fff;border-radius:14px;'
            f'padding:16px;border:1px solid #e2e8f0;box-shadow:0 1px 2px rgba(0,0,0,.04)">'
            f'<div style="font-size:12px;color:#64748b">{html.escape(title)}</div>'
            f'<div style="font-size:26px;font-weight:700;color:{accent};margin:4px 0">'
            f'{html.escape(value)}</div>'
            f'<div style="font-size:11px;color:#94a3b8">{html.escape(hint)}</div></div>')


def _table(rows: list[tuple], headers: list[str]) -> str:
    head = "".join(f'<th style="text-align:right;padding:8px 10px;font-size:12px;'
                   f'color:#475569;border-bottom:2px solid #e2e8f0">{html.escape(h)}</th>'
                   for h in headers)
    body = []
    for row in rows:
        cells = "".join(
            f'<td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:13px">'
            f'{html.escape(str(c))}</td>' for c in row)
        body.append(f"<tr>{cells}</tr>")
    return (f'<table style="width:100%;border-collapse:collapse;background:#fff;'
            f'border-radius:12px;overflow:hidden">{("<thead><tr>" + head + "</tr></thead>") if head else ""}'
            f'<tbody>{"".join(body)}</tbody></table>')


def render_html(snap: PerfSnapshot, *, title: str = "لوحة أداء AutoNews",
                generated_at: str = "") -> str:
    """لوحة HTML مستقلة تماماً (RTL، بلا CDN، بلا سكربتات خارجية)."""
    generated_at = generated_at or iso_now()
    kinds = snap.by_kind
    kind_badges = "".join(
        f'<span style="display:inline-block;background:#eff6ff;color:#1d4ed8;'
        f'border-radius:999px;padding:4px 12px;margin:2px;font-size:12px">'
        f'{html.escape(k)} · {c}</span>'
        for k, c in kinds.most_common())

    source_rows = [(source_label(s), c) for s, c in snap.top_sources[:12]]
    best_rows = [(b["title"], b["kind"], b.get("channel", ""),
                  "، ".join(b.get("platforms", [])),
                  b["at"][:16].replace("T", " "))
                 for b in snap.best_posts]
    quiet = "، ".join(f"{h}:00" for h in snap.quiet_hours[:8]) or "لا توجد (تغطية كاملة)"

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title></head>
<body style="margin:0;background:#f8fafc;font-family:system-ui,'Segoe UI',Tahoma,sans-serif;color:#0f172a">
<div style="max-width:960px;margin:0 auto;padding:24px">

  <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px">
    <h1 style="font-size:22px;margin:0">📊 {html.escape(title)}</h1>
    <div style="font-size:12px;color:#64748b">آخر {snap.days} أيام · {html.escape(generated_at[:16].replace("T", " "))}</div>
  </div>

  <div style="display:flex;gap:12px;flex-wrap:wrap;margin:18px 0">
    {_card("إجمالي المنشورات", str(snap.total_posts), f"خلال {snap.days} أيام")}
    {_card("بطاقات النتائج", str(kinds.get("نتيجة", 0)),
           f"{snap.outcome_share * 100:.0f}% من الإجمالي", "#059669")}
    {_card("منشورات عاجلة", str(kinds.get("عاجل", 0)), "المسار السريع", "#dc2626")}
    {_card("تحديثات مشروعة", str(kinds.get("تحديث", 0)), "ليست تكراراً", "#7c3aed")}
  </div>

  <div style="background:#fff;border-radius:14px;padding:16px;border:1px solid #e2e8f0;margin-bottom:16px">
    <div style="font-size:13px;color:#475569;margin-bottom:6px">📈 النشر حسب الساعة (توقيت بغداد)</div>
    {_bars(dict(snap.by_hour)) or '<div style="color:#94a3b8;font-size:13px">لا بيانات بعد</div>'}
    <div style="font-size:12px;color:#94a3b8;margin-top:8px">🕳 ساعات بلا نشر: {html.escape(quiet)}</div>
  </div>

  <div style="background:#fff;border-radius:14px;padding:16px;border:1px solid #e2e8f0;margin-bottom:16px">
    <div style="font-size:13px;color:#475569;margin-bottom:8px">🏷 أنواع المنشورات</div>
    {kind_badges or '<span style="color:#94a3b8;font-size:13px">لا بيانات</span>'}
  </div>

  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px">
    <div style="flex:1;min-width:260px">
      <div style="font-size:13px;color:#475569;margin:0 0 8px">📡 أكثر المصادر إنتاجاً</div>
      {_table(source_rows, ["المصدر", "منشورات"]) or ""}
    </div>
    <div style="flex:1;min-width:260px">
      <div style="font-size:13px;color:#475569;margin:0 0 8px">📱 حسب المنصّة</div>
      {_table([(k, v) for k, v in snap.by_platform.most_common()], ["المنصّة", "منشورات"])}
    </div>
  </div>

  <div style="font-size:13px;color:#475569;margin:0 0 8px">⭐ أفضل منشورات الأسبوع</div>
  {_table(best_rows, ["العنوان", "النوع", "القناة", "الوقت"])}

  <div style="margin-top:20px;font-size:11px;color:#94a3b8;text-align:center">
    تقرير محلي مُولَّد من state.json — بلا خدمات تحليلات خارجية، وبلا شبكة.
  </div>
</div></body></html>"""


# =========================================================================== #
#  «أفضل أسبوع» (Markdown)
# =========================================================================== #
def render_best_of(snap: PerfSnapshot, *, title: str = "أفضل ما نشرناه هذا الأسبوع") -> str:
    """ملخّص Markdown جاهز للنشر (تيليجرام/فيسبوك) أو للأرشفة."""
    if not snap.best_posts:
        return f"# {title}\n\nلا منشورات في الفترة المحدّدة.\n"

    lines = [f"# {title}", "",
             f"> الفترة: آخر {snap.days} أيام · {snap.total_posts} منشوراً "
             f"· {snap.outcome_share * 100:.0f}% منها بطاقات نتائج", ""]

    outcomes = [b for b in snap.best_posts if b["kind"] == "نتيجة"]
    others = [b for b in snap.best_posts if b["kind"] != "نتيجة"]

    if outcomes:
        lines += ["## 📋 نتائج تستحقّ التذكير", ""]
        lines += [f"{i}. **{b['title']}** — {'، '.join(b.get('platforms') or [b.get('channel', '')])}"
                  for i, b in enumerate(outcomes[:5], 1)]
        lines.append("")

    if others:
        lines += ["## 🔥 الأكثر أهمية", ""]
        lines += [f"{i}. **{b['title']}** — {b['kind']}"
                  for i, b in enumerate(others[:5], 1)]
        lines.append("")

    if snap.top_sources:
        lines += ["## 📡 من أين جاءت الأخبار", ""]
        lines += [f"- {source_label(s)}: {c} منشوراً" for s, c in snap.top_sources[:5]]
        lines.append("")

    if snap.quiet_hours:
        lines += ["## 🕳 فجوات التغطية (فرص ضائعة)", "",
                  "- ساعات بلا أي نشر: " + "، ".join(f"{h}:00" for h in snap.quiet_hours[:8]),
                  ""]

    lines.append("---")
    lines.append("_مُولَّد آلياً من سجلّ النشر — اختر الأفضل، لا الأحدث._")
    return "\n".join(lines)


# =========================================================================== #
#  الحفظ
# =========================================================================== #
def write_reports(state, *, output_file: str = "logs/dashboard.html",
                  best_of_file: str = "logs/best_of_week.md", days: int = 7,
                  top_n: int = 10, title: str = "لوحة أداء AutoNews") -> tuple[Path, Path]:
    """بناء اللوحتين وحفظهما. يعيد المسارين."""
    snap = collect(state, days=days, top_n=top_n)
    out = Path(output_file)
    best = Path(best_of_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    best.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(snap, title=title), encoding="utf-8")
    best.write_text(render_best_of(snap), encoding="utf-8")
    # نسخة JSON للاستخدام البرمجي/الروبوتات
    out.with_suffix(".json").write_text(
        json.dumps(snap.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("📊 لوحة الأداء: %s · أفضل أسبوع: %s (%d منشوراً)",
             out, best, snap.total_posts)
    return out, best
