"""مخزن إعداد القنوات — المعرّفات تُحفظ هنا، لا في ملفات الإعداد.

لماذا ملف منفصل؟
----------------
`config/channels.yml` يُرفع للمستودع ويُشارَك. معرّفات القنوات قد تكون خاصّة
(قناة غير معلنة، مجموعة إدارة…). لذلك:

    config/channels.yml        ← البنية (منصّة، وسوم، أسلوب) — آمن للمشاركة
    state/channels_setup.json  ← المعرّفات الفعلية — يُديرها البوت وحده

والنظام يقرأ المعرّف بهذا الترتيب:
    ١) state/channels_setup.json   (ما ضبطته من البوت — الأعلى أولوية)
    ٢) extra.ENV_CHAT_ID           (ما تكتبه أداة chatid)
    ٣) متغيّر البيئة               (GitHub Secrets)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .utils import get_logger, iso_now, parse_dt, utcnow

log = get_logger("autonews.setup")

DEFAULT_PATH = "state/channels_setup.json"


@dataclass(slots=True)
class ChannelBinding:
    """ربط قناة بمعرّف حقيقي، مع حالة التحقّق."""
    channel_id: str
    chat_id: str = ""
    title: str = ""
    username: str = ""
    chat_type: str = ""
    verified: bool = False        # هل تأكّدنا أن البوت يستطيع النشر؟
    is_admin: bool = False
    error: str = ""
    handle: str = ""              # 🖋 @اسم_القناة (بصمة الملكية)
    updated_at: str = ""

    def set_ok(self, chat_id: str, *, title: str = "", username: str = "",
               chat_type: str = "", is_admin: bool = True) -> None:
        self.chat_id = str(chat_id)
        self.title = title
        self.username = username
        self.chat_type = chat_type
        self.is_admin = is_admin
        self.verified = True
        self.error = ""
        self.updated_at = iso_now()

    def set_error(self, message: str) -> None:
        self.verified = False
        self.error = message[:300]
        self.updated_at = iso_now()

    def status_icon(self) -> str:
        if self.verified:
            return "✅"
        return "⚠️" if self.chat_id else "⬜"


@dataclass
class SetupStore:
    """كل الروابط + حالة أي عملية جارية (نظام الحالة لتفاعل البوت)."""
    path: Path = field(default_factory=lambda: Path(DEFAULT_PATH))
    bindings: dict[str, ChannelBinding] = field(default_factory=dict)
    # تفاعل جارٍ: {"action": "await_id", "channel_id": "tg_anime", "at": "..."}
    pending: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # ------------------------------------------------------------------ قراءة
    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "SetupStore":
        p = Path(path)
        store = cls(path=p)
        if not p.exists():
            return store
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("تعذّرت قراءة %s (%s) — بدء جديد", p, exc)
            return store
        for cid, raw in (data.get("bindings") or {}).items():
            try:
                store.bindings[cid] = ChannelBinding(**raw)
            except TypeError:
                store.bindings[cid] = ChannelBinding(channel_id=cid, chat_id=str(raw))
        store.pending = dict(data.get("pending") or {})
        return store

    # ------------------------------------------------------------------ كتابة
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": iso_now(),
            "pending": self.pending,
            "bindings": {k: asdict(v) for k, v in self.bindings.items()},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ عمليات
    def get(self, channel_id: str) -> ChannelBinding:
        b = self.bindings.get(channel_id)
        if b is None:
            b = self.bindings[channel_id] = ChannelBinding(channel_id=channel_id)
        return b

    def chat_id_for(self, channel_id: str) -> str:
        b = self.bindings.get(channel_id)
        return b.chat_id if b and b.verified else ""

    def bind(self, channel_id: str, chat_id: str, **meta: Any) -> ChannelBinding:
        b = self.get(channel_id)
        b.set_ok(chat_id, **meta)
        self.save()
        log.info("🔗 رُبطت القناة %s ← %s", channel_id, chat_id)
        return b

    def unbind(self, channel_id: str) -> bool:
        if channel_id in self.bindings:
            del self.bindings[channel_id]
            self.save()
            return True
        return False

    def configured(self) -> list[str]:
        return [k for k, v in self.bindings.items() if v.verified]

    def missing(self, known: list[str]) -> list[str]:
        return [c for c in known if c not in self.configured()]

    # --------------------------------------------------- حالة التفاعل الجاري
    def start_awaiting(self, channel_id: str, *, telegram_user: int = 0,
                       kind: str = "id") -> None:
        """بدء انتظار مدخل من المستخدم.

        kind = "id"    → ينتظر معرّف القناة (الربط)
        kind = "brand" → ينتظر اسم البصمة @handle (التوقيع/العلامة المائية)
        """
        self.pending = {"action": "await_id", "channel_id": channel_id,
                        "kind": kind, "user": telegram_user, "at": iso_now()}
        self.save()

    def awaiting(self, *, telegram_user: int = 0, max_minutes: int = 30) -> str:
        """القناة التي ننتظر مدخلاً لها (أو ""). تنتهي صلاحيتها تلقائياً.

        ⚠️ عمليات بلا قناة (قناة جديدة/مصدر جديد) لها `channel_id` فارغ —
        استخدم `pending_task()` لها، لأن "" تعني «لا انتظار» هنا.
        """
        task = self.pending_task(telegram_user=telegram_user, max_minutes=max_minutes)
        return str(task.get("channel_id") or "") if task else ""

    def pending_task(self, *, telegram_user: int = 0,
                     max_minutes: int = 30) -> dict[str, Any]:
        """كل تفاصيل الانتظار الجاري: {kind, channel_id, user} أو {} إن لا شيء."""
        if self.pending.get("action") != "await_id":
            return {}
        if telegram_user and self.pending.get("user") not in (0, telegram_user):
            return {}
        dt = parse_dt(self.pending.get("at"))
        if dt and (utcnow() - dt).total_seconds() > max_minutes * 60:
            self.clear_pending()
            return {}
        return {"kind": str(self.pending.get("kind") or "id"),
                "channel_id": str(self.pending.get("channel_id") or ""),
                "user": int(self.pending.get("user") or 0)}

    def clear_pending(self) -> None:
        if self.pending:
            self.pending = {}
            self.save()

    # ------------------------------------------------------------------ تقرير
    def summary(self, known: list[tuple[str, str]]) -> str:
        """ملخّص نصّي: أي قناة جاهزة وأي ناقصة."""
        lines = []
        for cid, title in known:
            b = self.bindings.get(cid)
            icon = b.status_icon() if b else "⬜"
            detail = (b.chat_id if b and b.verified else "—")
            lines.append(f"{icon} {title} — {detail}")
        ready = len(self.configured())
        return f"🔗 {ready}/{len(known)} قناة جاهزة\n\n" + "\n".join(lines)
