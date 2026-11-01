"""لوحة التحكّم: إيقاف القنوات · ساعات الهدوء · المراجعة الذكية · الطابور المؤجّل.

لماذا وحدة منفصلة عن `setup_store`؟
-----------------------------------
`setup_store` يجيب سؤالاً واحداً: **أين ننشر؟** (المعرّفات).
هذه الوحدة تجيب: **هل ننشر الآن؟ ومن يوافق؟** — وهي أسئلة تخصّ التشغيل لا الربط،
وتُقرأ في كل دورة، فتستحقّ ملفاً ومساراً مستقلّين.

    state/control.json

وهو ملف آمن للمشاركة (لا أسرار): حالة القنوات + طابور الانتظار.

المنطق الأساسي هنا **دالّات نقيّة** (بلا شبكة ولا حالة عالمية) كي تُختبر بسهولة،
ويستهلكها المشغّل (`main.py`) والمسار السريع (`flash.py`) والبوت (`settings_bot.py`).
"""
from __future__ import annotations

import hashlib
import json
import time
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .utils import get_logger, iso_now, parse_dt, utcnow

log = get_logger("autonews.control")

DEFAULT_PATH = "state/control.json"

# أنماط المراجعة لكل قناة
REVIEW_SMART = "smart"     # ⭐ التلقائي: يُعرض لك ما يشكّ فيه النظام فقط
REVIEW_ALWAYS = "always"   # كل منشور يحتاج موافقتك
REVIEW_NEVER = "never"     # نشر مباشر بلا أسئلة

REVIEW_MODES = (REVIEW_SMART, REVIEW_ALWAYS, REVIEW_NEVER)
MODE_TITLES = {
    REVIEW_SMART: "🤖 ذكية — ما يشكّ فيه فقط",
    REVIEW_ALWAYS: "👁 كل منشور",
    REVIEW_NEVER: "🚀 بلا مراجعة",
}

# نوعا عناصر الطابور
KIND_APPROVAL = "approval"   # ينتظر ضغطة منك
KIND_SCHEDULED = "scheduled" # مؤجّل تلقائياً (ساعات هدوء / فاصل زمني)


# --------------------------------------------------------------------------- #
#  أدوات وقت
# --------------------------------------------------------------------------- #
def local_hour(tz_offset: int = 3, *, now: datetime | None = None) -> int:
    """الساعة المحلية (0–23) حسب فرق التوقيت في الإعدادات."""
    base = now or utcnow()
    return int((base + timedelta(hours=tz_offset)).hour)


def _wrap_hour(hour: int, start: int, end: int) -> bool:
    """هل الساعة داخل نافذة [start, end) مع احتمال عبور منتصف الليل؟"""
    if start < 0 or end < 0 or start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end          # 22 ← 6 مثلاً


def is_quiet(hour: int, start: int, end: int) -> bool:
    """هل نحن في ساعات الهدوء؟ (الفارغ = لا)"""
    return _wrap_hour(hour, start, end)


def minutes_until_open(hour: int, minute: int, start: int, end: int) -> int:
    """كم دقيقة تبقّى حتى تُفتح النافذة؟ (0 إن لم نكن في الهدوء)"""
    if not is_quiet(hour, start, end):
        return 0
    delta = (end - hour) % 24
    if delta == 0 and minute:
        delta = 24
    return max(1, delta * 60 - minute)


def due_iso(minutes: int, *, now: datetime | None = None) -> str:
    """وقت الاستحقاق بصيغة ISO (UTC). القيم السالبة = «حان الآن» (مفيدة للاختبار)."""
    base = now or utcnow()
    return (base + timedelta(minutes=int(minutes))).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# --------------------------------------------------------------------------- #
#  إعداد قناة واحدة
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ChannelControl:
    """تحكّم قناة واحدة — كل حقل مستقلّ حتى يبقى التعديل بنقرة واحدة."""

    channel_id: str
    paused: bool = False
    pause_reason: str = ""
    quiet_start: int = -1        # -1 = بلا هدوء
    quiet_end: int = -1
    review: str = REVIEW_SMART
    min_score: float = 0.85      # تحت هذه الدرجة → مراجعة (في النمط الذكي)
    max_per_hour: int = 0        # 0 = بلا سقف
    hour_key: str = ""           # مثل "2026-11-01T14" — لعدّ السقف الساعي
    hour_count: int = 0
    last_post_at: str = ""
    posts_total: int = 0
    updated_at: str = ""

    # ---------------------------------------------------------------- عرض
    def quiet_label(self) -> str:
        if self.quiet_start < 0 or self.quiet_end < 0:
            return "بلا هدوء"
        return f"🌙 {self.quiet_start:02d}:00 ← {self.quiet_end:02d}:00"

    def status_icon(self) -> str:
        return "⏸" if self.paused else "▶️"

    def summary_line(self) -> str:
        # أيقونة الحالة تُعرض بجانب اسم القناة — لا نُكرّرها هنا
        bits: list[str] = []
        bits.append(self.quiet_label())
        bits.append(MODE_TITLES.get(self.review, self.review))
        if self.max_per_hour:
            bits.append(f"⏱ {self.max_per_hour}/ساعة")
        return " · ".join(bits)

    # ------------------------------------------------------------- استهلاك
    def _touch(self) -> None:
        self.updated_at = iso_now()

    def pause(self, reason: str = "") -> None:
        self.paused = True
        self.pause_reason = reason[:200]
        self._touch()

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = ""
        self._touch()

    def set_quiet(self, start: int, end: int) -> None:
        self.quiet_start, self.quiet_end = int(start), int(end)
        self._touch()

    def clear_quiet(self) -> None:
        self.quiet_start = self.quiet_end = -1
        self._touch()

    def set_review(self, mode: str) -> None:
        if mode in REVIEW_MODES:
            self.review = mode
            self._touch()

    def set_min_score(self, score: float) -> None:
        self.min_score = min(1.0, max(0.0, float(score)))
        self._touch()

    def allow_now(self, *, now: datetime | None = None, tz_offset: int = 3) -> tuple[bool, str]:
        """هل يُسمح بالنشر الآن؟ (إيقاف + ساعات هدوء + سقف ساعي)"""
        if self.paused:
            return False, "متوقّفة يدوياً" + (f" ({self.pause_reason})" if self.pause_reason else "")
        if is_quiet(local_hour(tz_offset, now=now), self.quiet_start, self.quiet_end):
            return False, "ساعات هدوء"
        if self.max_per_hour:
            key = _hour_key(now, tz_offset)
            count = self.hour_count if self.hour_key == key else 0
            if count >= self.max_per_hour:
                return False, f"بلغت سقف {self.max_per_hour} منشوراً في الساعة"
        return True, ""

    def bump(self, *, now: datetime | None = None, tz_offset: int = 3) -> None:
        """تسجيل منشور (لسقف الساعة والإحصاء)."""
        key = _hour_key(now, tz_offset)
        if self.hour_key != key:
            self.hour_key, self.hour_count = key, 0
        self.hour_count += 1
        self.posts_total += 1
        self.last_post_at = iso_now()


def _hour_key(now: datetime | None, tz_offset: int) -> str:
    base = (now or utcnow()) + timedelta(hours=tz_offset)
    return base.strftime("%Y-%m-%dT%H")


# --------------------------------------------------------------------------- #
#  المخزن + الطابور
# --------------------------------------------------------------------------- #
@dataclass
class ControlStore:
    """حالة كل القنوات + الطابور (موافقات + مؤجّلات)."""

    path: Path = field(default_factory=lambda: Path(DEFAULT_PATH))
    channels: dict[str, ChannelControl] = field(default_factory=dict)
    queue: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # ---------------------------------------------------------------- قراءة
    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "ControlStore":
        p = Path(path)
        store = cls(path=p)
        if not p.exists():
            return store
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("تعذّرت قراءة %s (%s) — بدء جديد", p, exc)
            return store
        for cid, raw in (data.get("channels") or {}).items():
            try:
                store.channels[cid] = ChannelControl(**{**raw, "channel_id": cid})
            except TypeError:
                store.channels[cid] = ChannelControl(channel_id=cid)
        store.queue = {k: dict(v) for k, v in (data.get("queue") or {}).items()}
        return store

    # ---------------------------------------------------------------- كتابة
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": iso_now(),
            "channels": {k: asdict(v) for k, v in self.channels.items()},
            "queue": self.queue,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------- الوصول
    def get(self, channel_id: str) -> ChannelControl:
        c = self.channels.get(channel_id)
        if c is None:
            c = self.channels[channel_id] = ChannelControl(channel_id=channel_id)
        return c

    def is_paused(self, channel_id: str) -> bool:
        c = self.channels.get(channel_id)
        return bool(c and c.paused)

    def review_mode(self, channel_id: str) -> str:
        c = self.channels.get(channel_id)
        return c.review if c else REVIEW_SMART

    # ------------------------------------------- عمليات جاهزة (تحفظ تلقائياً)
    # ملاحظة تصميم: تغيير الحقول مباشرةً لا يحفظ؛ لذلك نوفّر هذه الاختصارات
    # كي لا يعتمد الصحّة على تذكّر استدعاء save() في كل موضع.
    def pause(self, channel_id: str, reason: str = "") -> ChannelControl:
        c = self.get(channel_id)
        c.pause(reason)
        self.save()
        return c

    def resume(self, channel_id: str) -> ChannelControl:
        c = self.get(channel_id)
        c.resume()
        self.save()
        return c

    def set_review(self, channel_id: str, mode: str) -> ChannelControl:
        c = self.get(channel_id)
        c.set_review(mode)
        self.save()
        return c

    def set_quiet(self, channel_id: str, start: int, end: int) -> ChannelControl:
        c = self.get(channel_id)
        c.set_quiet(start, end)
        self.save()
        return c

    def clear_quiet(self, channel_id: str) -> ChannelControl:
        c = self.get(channel_id)
        c.clear_quiet()
        self.save()
        return c

    def record_post(self, channel_id: str, *, tz_offset: int = 3) -> ChannelControl:
        """تسجيل منشور ناجح (سقف الساعة + الإحصاء) وحفظ فوري."""
        c = self.get(channel_id)
        c.bump(tz_offset=tz_offset)
        self.save()
        return c

    # --------------------------------------------------------- الطابور
    def enqueue(self, kind: str, channel_id: str, post: dict[str, Any], *,
                due_at: str = "", score: float = 1.0, reasons: list[str] | None = None,
                preview: str = "") -> str:
        """إضافة عنصر للطابور وإرجاع معرّفه."""
        # معرّف فريد: الطابع الزمني بدقّة الميكروثانية يمنع التصادم بين خبرين متشابهين
        stamp = f"{time.time_ns()}"
        item_id = hashlib.sha1(
            f"{kind}:{channel_id}:{post.get('source_url', '')}:{post.get('title', '')}:{stamp}".encode()
        ).hexdigest()[:10]
        self.queue[item_id] = {
            "id": item_id,
            "kind": kind,
            "channel": channel_id,
            "post": post,
            "due_at": due_at,
            "score": round(float(score), 3),
            "reasons": list(reasons or []),
            "preview": preview[:400],
            "created_at": iso_now(),
            "status": "pending",
        }
        self.save()
        return item_id

    def item(self, item_id: str) -> dict[str, Any] | None:
        return self.queue.get(item_id)

    def take(self, item_id: str) -> dict[str, Any] | None:
        """سحب عنصر من الطابور (يُحذف بعد المعالجة)."""
        item = self.queue.pop(item_id, None)
        if item is not None:
            self.save()
        return item

    def pending(self, *, kind: str = "", channel_id: str = "") -> list[dict[str, Any]]:
        items = list(self.queue.values())
        if kind:
            items = [i for i in items if i.get("kind") == kind]
        if channel_id:
            items = [i for i in items if i.get("channel") == channel_id]
        return sorted(items, key=lambda i: str(i.get("created_at", "")))

    def due(self, *, now: datetime | None = None, kind: str = KIND_SCHEDULED) -> list[dict[str, Any]]:
        """العناصر التي حان وقت نشرها (المؤجّلة فقط — الموافقات لا تُنشر بلا ضغطة)."""
        moment = now or utcnow()
        out = []
        for item in self.pending(kind=kind):
            when = parse_dt(item.get("due_at"))
            if when is None or when <= moment:
                out.append(item)
        return out

    def purge(self, *, max_hours: float = 48.0) -> int:
        """حذف ما فات عليه الأوان (يمنع تراكم الطابور للأبد)."""
        moment = utcnow()
        dead = [
            k for k, v in self.queue.items()
            if (dt := parse_dt(v.get("created_at"))) and (moment - dt).total_seconds() > max_hours * 3600
        ]
        for k in dead:
            del self.queue[k]
        if dead:
            self.save()
        return len(dead)

    # ------------------------------------------------------------ تقرير
    def summary(self, known: list[tuple[str, str]], *, tz_offset: int = 3) -> str:
        hour = local_hour(tz_offset)
        lines = [f"🎛 <b>لوحة التحكّم</b> — الساعة عندك {hour:02d}:00", ""]
        paused = [cid for cid in self.channels if self.channels[cid].paused]
        quiet = [cid for cid in self.channels
                 if _wrap_hour(hour, self.channels[cid].quiet_start, self.channels[cid].quiet_end)]
        approvals = len(self.pending(kind=KIND_APPROVAL))
        scheduled = len(self.pending(kind=KIND_SCHEDULED))
        for cid, title in known:
            c = self.channels.get(cid) or ChannelControl(channel_id=cid)
            lines.append(f"{c.status_icon()} <b>{title}</b>\n   {c.summary_line()}")
        lines += [
            "",
            f"⏸ متوقّفة: <b>{len(paused)}</b> · 🌙 في هدوء الآن: <b>{len(quiet)}</b>",
            f"👁 تنتظر موافقتك: <b>{approvals}</b> · 🕒 مؤجّلة: <b>{scheduled}</b>",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  سياسة المراجعة الذكية
# --------------------------------------------------------------------------- #
_SENSITIVE_HINTS = ("وفيات", "قتل", "انفجار", "اعتقال", "تحقيق", "فساد", "تسريح", "أزمة")


def review_reasons(post: Any, *, score: float = 1.0, reasons: list[str] | None = None,
                   min_score: float = 0.85) -> list[str]:
    """لماذا يستحقّ هذا المنشور عينك؟ (قائمة فارغة = انشره فوراً)"""
    out: list[str] = []
    if score < min_score:
        out.append(f"درجة الجودة {score:.2f} أقل من {min_score:.2f}")
    if getattr(post, "used_fallback", False):
        out.append("صياغة احتياطية (تعذّر الذكاء الاصطناعي)")
    if getattr(post, "sensitive", False):
        out.append("محتوى حسّاس")
    blob = f"{getattr(post, 'title', '')} {getattr(post, 'body', '')}"
    hits = [w for w in _SENSITIVE_HINTS if w in blob]
    if hits:
        out.append("كلمات حسّاسة: " + "، ".join(hits[:3]))
    if not str(getattr(post, "source_url", "")).startswith("http"):
        out.append("بلا رابط مصدر")
    for r in reasons or []:
        if r and r not in out:
            out.append(r)
    return out


def decide(*, mode: str, post: Any, score: float = 1.0,
           reasons: list[str] | None = None, min_score: float = 0.85,
           breaking: bool = False) -> tuple[str, list[str]]:
    """القرار النهائي: `publish` فوراً أو `review` أو `scheduled` (يمرّره المستدعي).

    قواعد لا تُخترق:
      • الأخبار العاجلة **لا تنتظر** في النمط الذكي (السرعة شرط أساسي).
      • «بلا مراجعة» تعني بلا مراجعة، حتى لو انخفضت الدرجة.
      • «كل منشور» تعني كل منشور، حتى العاجل.
    """
    if mode == REVIEW_NEVER:
        return "publish", []
    if mode == REVIEW_ALWAYS:
        return "review", ["كل منشور يحتاج موافقة (إعداد القناة)"]
    if breaking:
        return "publish", []
    why = review_reasons(post, score=score, reasons=reasons, min_score=min_score)
    return ("review", why) if why else ("publish", [])


# --------------------------------------------------------------------------- #
#  العرض على تيليجرام
# --------------------------------------------------------------------------- #
def preview_text(item: dict[str, Any], *, limit: int = 700) -> str:
    """نصّ معاينة قصير لعنصر من الطابور (يُرسل للبوت)."""
    post = item.get("post") or {}
    title = str(post.get("title", "")).strip()
    body = _strip_tags(str(post.get("body", ""))).strip()
    score = item.get("score", 1.0)
    head = "👁 <b>يحتاج موافقتك</b>" if item.get("kind") == KIND_APPROVAL else "🕒 <b>مؤجّل</b>"
    lines = [head, f"📊 درجة الجودة: <b>{score:.2f}</b>"]
    if item.get("reasons"):
        lines.append("🔎 " + " · ".join(str(r) for r in item["reasons"][:4]))
    lines += ["", f"🗞 <b>{title}</b>", "", body[:limit]]
    url = str(post.get("source_url", ""))
    if url:
        lines += ["", f"🔗 {url}"]
    return "\n".join(lines)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


__all__ = [
    "ChannelControl", "ControlStore", "DEFAULT_PATH",
    "REVIEW_SMART", "REVIEW_ALWAYS", "REVIEW_NEVER", "REVIEW_MODES", "MODE_TITLES",
    "KIND_APPROVAL", "KIND_SCHEDULED",
    "local_hour", "is_quiet", "minutes_until_open", "due_iso",
    "review_reasons", "decide", "preview_text",
]
