"""بوت الإعداد التفاعلي — اضبط القنوات من تيليجرام بلا ملفات ولا حاسوب.

كيف يعمل؟
---------
    /start  → لوحة أزرار: كل قناة بحالتها (✅ جاهزة / ⬜ ناقصة)
    تضغط «🎬 قناة الأنمي»
        → يطلب منك المعرّف، ويشرح كيف تجيبه (٣ طرق)
    تدزّ المعرّف (أو @username أو رابط دعوة)
        → البوت **يتحقّق فعلياً** عبر getChat/getChatMember
        → إن كان البوت مشرفاً: يحفظ ويقول ✅
        → وإلا: يقول السبب بالعربية والعلاج بالضبط

نقاط تصميم متعمّدة:
    • التحقّق قبل الحفظ: لا نحفظ معرّفاً لا يعمل (أشهر سبب لفشل النشر).
    • قائمة بيضاء للمستخدمين: لا أحد يضبط قنواتك غيرك.
    • الحالة تُحفظ في state/channels_setup.json (لا تُرفع في الأسرار).
    • يعمل Pull: لا يحتاج خادماً ولا webhook — أمر واحد من Actions.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from typing import Any

from .chatid import _call, bot_info, check_chat, send_test, to_channel_handle
from .setup_store import SetupStore
from .utils import get_logger

log = get_logger("autonews.setup_bot")

TG = "https://api.telegram.org/bot{token}/{method}"

# القناة ↔ وصف عربي مقروء (يُستخدم في الأزرار والرسائل)
CHANNEL_TITLES: dict[str, str] = {
    "tg_main": "📰 الأخبار العامة",
    "tg_anime": "🎬 الأنمي",
    "tg_sports": "⚽ الرياضة",
    "tg_tech": "💻 التقنية",
    "tg_econ": "💰 الاقتصاد والدولار",
    "tg_games": "🎮 الألعاب",
    "tg_local": "🗺️ المحافظات والخدمات",
    "fb_page": "📘 صفحة فيسبوك",
    "x_main": "🐦 حساب X",
}


def os_environ(key: str) -> str:
    """قراءة متغيّر بيئة (مغلّفة لتفادي استيراد os في كل موضع)."""
    import os as _os

    return _os.environ.get(key, "")


def pathlib_join(base: str, name: str):
    """ضمّ مسار (مستورد هنا لتفادي استيراد pathlib في كل مكان)."""
    from pathlib import Path

    return Path(base) / name


_TITLE_CACHE: dict[str, str] = {}


def channel_title(cid: str, *, config_dir: str = "config") -> str:
    """اسم القناة للعرض: من القائمة الثابتة، وإلّا من channels.yml (قناة أُضيفت من البوت)."""
    if cid in CHANNEL_TITLES:
        return CHANNEL_TITLES[cid]
    if cid in _TITLE_CACHE:
        return _TITLE_CACHE[cid]
    try:
        from .channels_main import load_channels

        for ch in load_channels(config_dir):
            if ch.get("id") == cid:
                _TITLE_CACHE[cid] = str(ch.get("name") or cid)
                break
    except Exception:  # noqa: BLE001 — العرض لا يُسقط البوت
        pass
    return _TITLE_CACHE.get(cid, cid)


# --------------------------------------------------------------------------- #
#  نداءات تيليجرام
# --------------------------------------------------------------------------- #
def _slug(text: str) -> str:
    """معرّف لاتيني قصير من اسم عربي (بلا اعتماد على مكتبات ترجمة)."""
    table = {"ا": "a", "أ": "a", "إ": "a", "ب": "b", "ت": "t", "ث": "th",
             "ج": "j", "ح": "h", "خ": "kh", "د": "d", "ذ": "dh", "ر": "r",
             "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t",
             "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ك": "k",
             "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w", "ي": "y",
             "ى": "a", "ة": "h", "ء": "", "ئ": "y", "ؤ": "w"}
    out = "".join(table.get(ch, ch) for ch in (text or "").lower())
    out = re.sub(r"[^a-z0-9]+", "_", out).strip("_")
    return (out[:18] or "new")


def _api(token: str, method: str, params: dict[str, Any] | None = None) -> tuple[bool, Any]:
    return _call(token, method, params)


def send_message(token: str, chat_id: str | int, text: str, *,
                 keyboard: list[list[dict]] | None = None,
                 parse_mode: str = "HTML") -> tuple[bool, Any]:
    """إرسال رسالة (اختياري: لوحة أزرار)."""
    params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000],
                              "disable_web_page_preview": "true"}
    if parse_mode:
        params["parse_mode"] = parse_mode
    if keyboard is not None:
        import json

        params["reply_markup"] = json.dumps(
            {"inline_keyboard": keyboard}, ensure_ascii=False)
    return _api(token, "sendMessage", params)


def answer_callback(token: str, callback_id: str, text: str = "") -> None:
    """إسكات «ساعة الانتظار» في زرّ تيليجرام."""
    _api(token, "answerCallbackQuery",
         {"callback_query_id": callback_id, "text": text[:200]})


def get_updates(token: str, *, offset: int = 0, timeout: int = 0) -> list[dict]:
    ok, result = _api(token, "getUpdates", {
        "offset": offset, "timeout": timeout, "limit": 50,
        "allowed_updates": '["message","callback_query"]',
    })
    return result if ok else []


# --------------------------------------------------------------------------- #
#  شاشات البوت
# --------------------------------------------------------------------------- #
def main_keyboard(store: SetupStore, known: list[str]) -> list[list[dict]]:
    """لوحة الأزرار الرئيسية: زرّ لكل قناة بحالتها."""
    rows: list[list[dict]] = []
    row: list[dict] = []
    for cid in known:
        b = store.bindings.get(cid)
        icon = b.status_icon() if b else "⬜"
        row.append({"text": f"{icon} {channel_title(cid)}",
                    "callback_data": f"pick:{cid}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "🔄 فحص الجميع", "callback_data": "verify_all"},
        {"text": "📋 حالة القنوات", "callback_data": "status"},
    ])
    rows.append([
        {"text": "🎛 لوحة التحكّم", "callback_data": "ctl"},
        {"text": "📊 الإدارة", "callback_data": "manage"},
    ])
    rows.append([{"text": "📋 القنوات وإدارتها", "callback_data": "chs"},
                 {"text": "🧪 اختبار النشر", "callback_data": "test_all"}])
    return rows


# --------------------------------------------------------------------------- #
#  📋 شاشة القنوات — إدارة كاملة بلا ملفات
# --------------------------------------------------------------------------- #
def channels_keyboard(channels: list[dict], control) -> list[list[dict]]:
    """زرّ لكل قناة: ✅ مفعّلة / ⏸ معطّلة + قطاعها."""
    from .sectors import sector_of

    rows: list[list[dict]] = []
    row: list[dict] = []
    for ch in channels:
        cid = ch["id"]
        profile = sector_of(cid)
        icon = profile.icon if profile else "📌"
        state = "✅" if ch.get("enabled", True) else "⏸"
        c = control.get(cid)
        if getattr(c, "paused", False):
            state = "⏸"
        row.append({"text": f"{state} {icon} {ch.get('name') or cid}"[:38],
                    "callback_data": f"chv:{cid}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([{"text": "➕ قناة جديدة", "callback_data": "newc"},
                 {"text": "🧠 ربط بالاسم", "callback_data": "autolink"}])
    rows.append([{"text": "↩️ الرئيسية", "callback_data": "panel"}])
    return rows


def channel_detail_keyboard(cid: str, *, enabled: bool) -> list[list[dict]]:
    """أزرار قناة واحدة: تفعيل · اختصاص · عدد المنشورات · آخر المنشورات."""
    toggle = ({"text": "⏸ تعطيل النشر", "callback_data": f"choff:{cid}"}
              if enabled else
              {"text": "✅ تفعيل النشر", "callback_data": f"chon:{cid}"})
    return [
        [toggle, {"text": "🧠 تغيير الاختصاص", "callback_data": f"sec:{cid}"}],
        [{"text": "🔢 عدد المنشورات", "callback_data": f"num:{cid}"},
         {"text": "📜 آخر المنشورات", "callback_data": f"last:{cid}"}],
        [{"text": "🏷 الوسوم", "callback_data": f"tags:{cid}"},
         {"text": "🖋 البصمة", "callback_data": f"brd:{cid}"}],
        [{"text": "📈 حالة النشر", "callback_data": f"ctl:{cid}"},
         {"text": "↩️ القنوات", "callback_data": "chs"}],
    ]


def sectors_keyboard(cid: str) -> list[list[dict]]:
    """كل الاختصاصات كأزرار — نقرة واحدة تكفي."""
    from .sectors import catalog

    rows: list[list[dict]] = []
    row: list[dict] = []
    for item in catalog():
        row.append({"text": f"{item['icon']} {item['label']}"[:32],
                    "callback_data": f"secs:{cid}:{item['key']}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([{"text": "↩️ رجوع", "callback_data": f"chv:{cid}"}])
    return rows


def control_keyboard(known: list[str], control) -> list[list[dict]]:
    """🎛 لوحة التحكّم: حالة كل قناة (إيقاف/هدوء/مراجعة) بنقرة."""
    rows: list[list[dict]] = []
    row: list[dict] = []
    for cid in known:
        c = control.get(cid)
        row.append({"text": f"{c.status_icon()} {channel_title(cid)}",
                    "callback_data": f"ctl:{cid}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([{"text": "👁 نمط المراجعة للجميع", "callback_data": "rev:all"},
                 {"text": "📥 ما ينتظرني", "callback_data": "apv:list"}])
    rows.append([{"text": "↩️ الرئيسية", "callback_data": "panel"}])
    return rows


def channel_control_keyboard(cid: str, c) -> list[list[dict]]:
    """أزرار قناة واحدة: إيقاف · هدوء · نمط المراجعة."""
    pause_btn = ({"text": "▶️ تشغيل القناة", "callback_data": f"ctl:{cid}:resume"}
                 if c.paused else
                 {"text": "⏸ إيقاف القناة", "callback_data": f"ctl:{cid}:pause"})
    quiet_btn = ({"text": "🌙 إلغاء الهدوء", "callback_data": f"ctl:{cid}:unquiet"}
                 if c.quiet_start >= 0 else
                 {"text": "🌙 ساعات هدوء (23→6)", "callback_data": f"ctl:{cid}:quiet"})
    return [
        [pause_btn, quiet_btn],
        [{"text": "🤖 مراجعة ذكية", "callback_data": f"rev:{cid}:smart"},
         {"text": "👁 كل منشور", "callback_data": f"rev:{cid}:always"},
         {"text": "🚀 بلا مراجعة", "callback_data": f"rev:{cid}:never"}],
        [{"text": "🖋 ضبط البصمة", "callback_data": f"brd:{cid}"},
         {"text": "↩️ لوحة التحكّم", "callback_data": "ctl"}],
    ]


def manage_keyboard(known: list[str]) -> list[list[dict]]:
    """📊 مركز الإدارة: إحصاءات · وسوم · قناة جديدة · مصدر جديد."""
    return [
        [{"text": "📊 الإحصاءات", "callback_data": "st"},
         {"text": "🩺 الأخطاء", "callback_data": "errors"}],
        [{"text": "🏷 وسوم القناة", "callback_data": "tags"}],
        [{"text": "🧠 اربط بالاسم", "callback_data": "autolink"}],
        [{"text": "➕ قناة جديدة", "callback_data": "newc"},
         {"text": "📡 مصدر جديد", "callback_data": "news"}],
        [{"text": "📘 ربط صفحة فيسبوك", "callback_data": "fb"}],
        [{"text": "↩️ الرئيسية", "callback_data": "panel"}],
    ]


def tags_keyboard(cid: str, tags: list[str], catalog: dict[str, list[str]]) -> list[list[dict]]:
    """أزرار وسوم القناة: ✅ مفعّل (اضغط للحذف) · ➕ متاح من الكتالوج."""
    rows: list[list[dict]] = [[{"text": f"🏷 {channel_title(cid)}", "callback_data": "tags"}]]
    current = {t.lower() for t in tags}
    row: list[dict] = []
    for tag in tags:
        row.append({"text": f"✅ {tag}", "callback_data": f"tagd:{cid}:{tag}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row); row = []

    others = [t for t in catalog if t.lower() not in current]
    for tag in others[:8]:
        where = "، ".join(catalog.get(tag, [])[:2])
        label = f"➕ {tag}" + (f" ({where})" if where else "")
        row.append({"text": label, "callback_data": f"taga:{cid}:{tag}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([{"text": "✏️ وسم جديد (أكتبه)", "callback_data": f"tagx:{cid}"},
                 {"text": "↩️ رجوع", "callback_data": "manage"}])
    return rows


def welcome_text(store: SetupStore, known: list[str], bot_name: str = "") -> str:
    ready = len([c for c in store.configured() if c in known])
    lines = [
        "⚙️ <b>إعداد قنوات AutoNews</b>",
        "",
        f"مرحباً{' ' + html.escape(bot_name) if bot_name else ''}! اضبط قنواتك بنقرتين:",
        "",
        f"🔗 <b>{ready}/{len(known)}</b> قناة جاهزة",
        "",
        "<b>الطريقة:</b> اضغط القناة ← يطلب المعرّف ← أرسله ← تحقّق تلقائي ✅",
        "",
        "🧭 <i>كيف تجيب المعرّف؟</i>",
        "١) أضف البوت <b>مشرفاً</b> في القناة",
        "٢) انشر أي رسالة فيها",
        "٣) أرسل لي هنا: <code>@اسم_قناتك</code> أو رابط الدعوة",
    ]
    return "\n".join(lines)


def pick_text(cid: str) -> str:
    """رسالة طلب المعرّف لقناة معيّنة."""
    title = channel_title(cid)
    return "\n".join([
        f"<b>{title}</b>",
        "",
        "أرسل الآن أحد هذه:",
        "",
        "• <code>@my_channel</code> — إن كانت القناة <b>عامة</b>",
        "• <code>-1001234567890</code> — إن كانت <b>خاصة</b> (من الأداة)",
        "• رابط دعوة <code>https://t.me/+xxxx</code> — سأستخرجه إن أمكن",
        "",
        "⚠️ <b>قبل ذلك</b>: أضف البوت <b>مشرفاً</b> في القناة، وإلّا سأقول لك إنه ليس مشرفاً.",
        "",
        "↩️ للإلغاء: /cancel",
    ])


STATUS_HEADER = "📋 <b>حالة القنوات</b>\n"


def status_text(store: SetupStore, known: list[str]) -> str:
    lines = [STATUS_HEADER]
    for cid in known:
        b = store.bindings.get(cid)
        if b and b.verified:
            lines.append(f"✅ <b>{channel_title(cid)}</b>")
            lines.append(f"   <code>{html.escape(b.chat_id)}</code>"
                         + (f" · @{b.username}" if b.username else ""))
            lines.append(f"   الاسم: {html.escape(b.title or '—')}")
        elif b and b.chat_id:
            lines.append(f"⚠️ <b>{channel_title(cid)}</b> — <code>{html.escape(b.chat_id)}</code>")
            lines.append(f"   ❌ {html.escape(b.error or 'غير متحقّق')}")
        else:
            lines.append(f"⬜ <b>{channel_title(cid)}</b> — لم تُضبط")
    missing = [c for c in known if c not in store.configured()]
    if missing:
        lines += ["", f"💡 باقي {len(missing)} قناة: اضغطها من اللوحة."]
    else:
        lines += ["", "🎉 كل القنوات جاهزة!"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  استخراج/تفسير المعرّف
# --------------------------------------------------------------------------- #
_ID_RE = re.compile(r"^-?\d{5,}$")
_TME_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_+%\-]+)")


def _ago(stamp: str) -> str:
    """«قبل ٣ ساعات» من طابع ISO — للعرض في البوت."""
    from .utils import human_age, parse_dt

    dt = parse_dt(stamp) if stamp else None
    return human_age(dt) if dt else "—"


def parse_target(text: str) -> tuple[str, str]:
    """تفسير ما أرسله المستخدم → (نوع، قيمة).

    الأنواع: id | username | invite | unknown
    """
    raw = (text or "").strip()
    if not raw:
        return "unknown", ""
    if _ID_RE.match(raw):
        return "id", raw
    if raw.startswith("@"):
        return "username", raw
    m = _TME_RE.search(raw)
    if m:
        handle = m.group(1)
        if handle.startswith("+") or handle.startswith("joinchat"):
            return "invite", raw
        return "username", "@" + handle.lstrip("@")
    if re.match(r"^[A-Za-z][A-Za-z0-9_]{3,}$", raw):
        return "username", "@" + raw     # كتب الاسم بلا @
    return "unknown", raw


def resolve_invite(token: str, invite: str) -> tuple[str, str]:
    """محاولة استخراج معرّف من رابط دعوة (ناجحة فقط إن كان البوت عضواً)."""
    ok, result = _api(token, "getChat", {"chat_id": invite})
    if ok and isinstance(result, dict) and result.get("id"):
        return str(result["id"]), ""
    return "", str(result)


# --------------------------------------------------------------------------- #
#  المنطق الرئيسي
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class BotConfig:
    """إعداد البوت (من متغيّرات البيئة)."""
    token: str = ""
    allowed_users: tuple[int, ...] = ()   # فارغ = صاحب التوكن فقط (نمنع الجميع)
    admin_chat_id: str = ""               # حيث تُرسل لوحة الإعداد
    setup_file: str = "state/channels_setup.json"
    timeout_seconds: int = 45

    @property
    def enabled(self) -> bool:
        return bool(self.token)


class SetupBot:
    """بوت إعداد القنوات (Pull-based: نداء واحد يعالج كل الرسائل المعلّقة)."""

    def __init__(self, config: BotConfig, *, known_channels: list[str] | None = None,
                 store: SetupStore | None = None) -> None:
        self.cfg = config
        self.store = store or SetupStore.load(config.setup_file)
        self.known = known_channels or list(CHANNEL_TITLES.keys())
        self.me = bot_info(config.token) or {}
        self._offset_file = self.store.path.with_name("setup_bot_offset.txt")

    # ------------------------------------------------------------- صلاحية
    def _allowed(self, user_id: int, chat_id: str | int) -> bool:
        """الصلاحية: قائمة صريحة، وإلّا المحادثة الإدارية فقط.

        🔒 قاعدة أمان: **لا اسمح للجميع أبداً.** من يملك البوت قد لا يكون
        من يملك القنوات، ومن يستطيع ضبط قناة يستطيع توجيه كل النشر إليها.
        """
        allowed = set(self.cfg.allowed_users)
        if allowed:
            return user_id in allowed
        if self.cfg.admin_chat_id:
            return str(chat_id) == str(self.cfg.admin_chat_id)
        # لا قائمة ولا محادثة إدارية: نرفض (fail-closed) ونطبع سبباً واضحاً
        log.warning("⛔ رُفض تحديث: لا SETUP_ALLOWED_USERS ولا TELEGRAM_ADMIN_CHAT_ID")
        return False

    # ------------------------------------------------------------- الإرسال
    def send(self, chat_id: str | int, text: str, *, keyboard=None) -> bool:
        ok, _ = send_message(self.cfg.token, chat_id, text, keyboard=keyboard)
        return ok

    def show_panel(self, chat_id: str | int) -> bool:
        return self.send(chat_id, welcome_text(self.store, self.known,
                                               self.me.get("first_name", "")),
                         keyboard=main_keyboard(self.store, self.known))

    # ------------------------------------------------------------- المعالجة
    def handle_update(self, upd: dict) -> None:
        """معالجة تحديث واحد (رسالة أو ضغط زر)."""
        if "callback_query" in upd:
            self._on_callback(upd["callback_query"])
            return
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        user_id = int((msg.get("from") or {}).get("id", 0) or 0)
        chat_id = chat.get("id")
        if not text or chat_id is None:
            return
        if not self._allowed(user_id, chat_id):
            self.send(chat_id, "⛔ غير مسموح. أضف معرّفك إلى SETUP_ALLOWED_USERS.")
            return
        self._on_text(chat_id, text)

    def _on_text(self, chat_id: str | int, text: str) -> None:
        low = text.lower()
        early = self.store.pending_task()
        if early and early.get("kind") == "autolink":
            self._autolink(chat_id, text)
            return

        if low in ("/start", "/setup", "ابدأ", "إعداد", "/panel"):
            self.store.clear_pending()
            self.show_panel(chat_id)
            return
        if low in ("/cancel", "الغاء", "إلغاء"):
            self.store.clear_pending()
            self.send(chat_id, "✅ أُلغيت العملية.", keyboard=main_keyboard(self.store, self.known))
            return
        if low == "/status":
            self.send(chat_id, status_text(self.store, self.known),
                      keyboard=main_keyboard(self.store, self.known))
            return

        # هل ننتظر معرّفاً؟
        # هل ننتظر مدخلاً؟ (معرّف · بصمة · وسم · قناة جديدة · مصدر جديد)
        task = self.store.pending_task(
            telegram_user=int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0)
        if task:
            pending_id, kind = task["channel_id"], task["kind"]
            if kind == "brand":
                self._set_brand(chat_id, pending_id, text)
            elif kind == "tags":
                self._add_tag(chat_id, pending_id, text)
            elif kind == "new_channel":
                self._create_channel(chat_id, text)
            elif kind == "new_source":
                self._create_source(chat_id, text)
            elif kind == "fb_page":
                self._save_page_id(chat_id, text)
            elif kind == "max_posts":
                self._set_max_posts(chat_id, pending_id, text)
            elif kind == "autolink":
                self._autolink(chat_id, text)
            else:
                self._bind_channel(chat_id, pending_id, text)
            return

        self.send(chat_id, "أرسل /start لعرض لوحة الإعداد.",
                  keyboard=main_keyboard(self.store, self.known))

    def _on_callback(self, cq: dict) -> None:
        data = (cq.get("data") or "")
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        user_id = int((cq.get("from") or {}).get("id", 0) or 0)
        cb_id = cq.get("id", "")
        answer_callback(self.cfg.token, cb_id)
        if chat_id is None or not self._allowed(user_id, chat_id):
            return

        if data.startswith("pick:"):
            cid = data.split(":", 1)[1]
            self.store.start_awaiting(cid, telegram_user=user_id)
            self.send(chat_id, pick_text(cid),
                      keyboard=[[{"text": "↩️ رجوع", "callback_data": "panel"}]])
            return

        if data == "panel":
            self.store.clear_pending()
            self.show_panel(chat_id)
            return

        if data == "status":
            self.send(chat_id, status_text(self.store, self.known),
                      keyboard=main_keyboard(self.store, self.known))
            return

        if data == "verify_all":
            self._verify_all(chat_id)
            return

        if data == "test_all":
            self._test_all(chat_id)
            return

        # ---------------------------------------------------- 🎛 لوحة التحكّم
        if data == "ctl":
            from .control import ControlStore

            control = ControlStore.load()
            self.send(chat_id, control.summary(
                [(c, channel_title(c)) for c in self.known],
                tz_offset=self.tz_offset),
                keyboard=control_keyboard(self.known, control))
            return

        if data.startswith("ctl:"):
            self._on_control(chat_id, data)
            return

        if data.startswith("rev:"):
            self._on_review_mode(chat_id, data)
            return

        if data.startswith("brd:"):
            self._on_brand(chat_id, data)
            return

        # ------------------------------------------- موافقة/رفض/نشر عنصر
        if data.startswith(("apv:", "rej:", "prv:")):
            self._on_approval(chat_id, data)
            return

        if data == "fb:id":
            self.store.start_awaiting("", telegram_user=0, kind="fb_page")
            self.send(chat_id,
                      "✏️ أرسل <b>معرّف الصفحة</b> (أرقام فقط):\n"
                      "<code>1234567890123456</code>\n\n"
                      "من: الصفحة ← About ← Page transparency ← Page ID")
            return

        if data == "fb:help":
            self.send(chat_id,
                      "🔑 <b>كيف تجيب توكن الصفحة؟</b>\n\n"
                      "١) <a href='https://developers.facebook.com/tools/explorer/'>Graph API "
                      "Explorer</a> ← اختر تطبيقك\n"
                      "٢) Get User Access Token + الثلاث صلاحيات\n"
                      "٣) 🔴 اضغط على <b>اسم صفحتك</b> في النافذة ← Allow\n"
                      "٤) انسخ التوكن ← نفّذ:\n"
                      "<code>python -m autonews.fb_main --token</code>\n"
                      "٥) خُذ <code>FACEBOOK_PAGE_TOKEN</code> وضعه في Secrets\n\n"
                      "⚠️ توكن المستخدم ينتهي بعد ساعة — الصفحة لا ينتهي.")
            return

        # ------------------------------------------------- 📊 مركز الإدارة
        if data == "manage":
            self.send(chat_id,
                      "📊 <b>مركز الإدارة</b>\n\n"
                      "كل ما تحتاجه لتشغيل القنوات — بأزرار:\n"
                      "• إحصاءات: من نشر ماذا ومتى\n"
                      "• وسوم: أي وسم يدخل أي قناة\n"
                      "• قناة جديدة / مصدر جديد",
                      keyboard=manage_keyboard(self.known))
            return

        if data == "st":
            self._show_stats(chat_id)
            return

        if data.startswith(("tags", "taga:", "tagd:", "tagx:")):
            self._on_tags(chat_id, data)
            return

        if data == "chs":
            self._show_channels(chat_id)
            return

        if data.startswith("chv:"):
            self._show_channel(chat_id, data.split(":", 1)[1])
            return

        if data.startswith(("chon:", "choff:")):
            cid = data.split(":", 1)[1]
            enable = data.startswith("chon:")
            from .editor import set_channel_enabled

            res = set_channel_enabled(cid, enable, config_dir=self.config_dir)
            self.send(chat_id, res.message,
                      keyboard=[[{"text": "↩️ القناة", "callback_data": f"chv:{cid}"}]])
            return

        if data.startswith("secs:"):
            _, cid, key = data.split(":", 2)
            from .sectors import ensure_sources, set_sector

            ok, msg = set_sector(cid, key, config_dir=self.config_dir)
            if ok:
                from .sectors import SECTORS

                extra = ensure_sources(SECTORS[key], config_dir=self.config_dir)
                if extra:
                    msg += "\n" + extra
            self.send(chat_id, msg if ok else "⚠️ " + msg,
                      keyboard=[[{"text": "↩️ القناة", "callback_data": f"chv:{cid}"}]])
            return

        if data.startswith("sec:"):
            cid = data.split(":", 1)[1]
            from .sectors import sector_of

            current = sector_of(cid, config_dir=self.config_dir)
            head = (f"🧠 <b>اختصاص القناة</b>\nالحالي: "
                    f"{current.icon} {current.label}\n\n" if current else
                    "🧠 <b>اختصاص القناة</b>\nالحالي: بلا اختصاص\n\n")
            self.send(chat_id, head + "اختر الاختصاص الجديد:",
                      keyboard=sectors_keyboard(cid))
            return

        if data.startswith("num:"):
            cid = data.split(":", 1)[1]
            self.store.start_awaiting(cid, telegram_user=0, kind="max_posts")
            self.send(chat_id,
                      "🔢 <b>عدد المنشورات لكل تشغيل</b>\n\n"
                      "أرسل رقماً من ٠ إلى ٢٠:\n"
                      "• <code>0</code> = بلا حدّ\n"
                      "• <code>3</code> = ثلاث منشورات كحدّ أقصى\n\n"
                      "⚠️ القيمة العالية تستهلك توكنات أكثر وتُغرق القناة.",
                      keyboard=[[{"text": "↩️ رجوع", "callback_data": f"chv:{cid}"}]])
            return

        if data.startswith("last:"):
            self._show_last_posts(chat_id, data.split(":", 1)[1])
            return

        if data == "errors":
            self._show_errors(chat_id)
            return

        if data == "autolink":
            self.store.start_awaiting("", telegram_user=0, kind="autolink")
            self.send(chat_id,
                      "🧠 <b>ربط سريع بالاسم</b>\n\n"
                      "أرسل @username للقناة فقط، أو الصق رابطها العام.\n\n"
                      "مثال: <code>@my_channel</code>\n\n"
                      "سأجلب اسم القناة تلقائياً، وأستنتج اختصاصها وأضبط مصادرها.\n"
                      "تأكد أن البوت مضاف مشرفاً إلى القناة.",
                      keyboard=[[{"text": "↩️ رجوع", "callback_data": "panel"}]])
            return
        if data == "newc":
            self.store.start_awaiting("", telegram_user=0, kind="new_channel")
            self.send(chat_id,
                      "➕ <b>قناة جديدة</b>\n\n"
                      "أرسل <b>اسم القناة | وصف اختصاصها</b> في رسالة واحدة، وأنا أستنتج "
                      "الاختصاص والمصادر تلقائياً.\n\n"
                      "مثال:\n<code>قناة المانهوا | إعلانات المانهوا الكورية والفصول الجديدة ومواعيد العودة</code>\n\n"
                      "• لا معرّف ولا سرّ لكل قناة — الربط يكفي\n"
                      "• بدون وصف: أرسل اسم القناة فقط. لا تكتب معرّفاً ولا قائمة مصادر.",
                      keyboard=[[{"text": "↩️ رجوع", "callback_data": "manage"}]])
            return

        if data == "fb":
            self._on_facebook(chat_id)
            return

        if data == "news":
            self.store.start_awaiting("", telegram_user=0, kind="new_source")
            self.send(chat_id,
                      "📡 <b>مصدر جديد</b>\n\n"
                      "أرسل رابط RSS. سأختبره <b>الآن</b> قبل إضافته:\n"
                      "<code>https://example.com/rss</code>\n\n"
                      "اختياري: أضف الاسم والوسوم بعد الرابط:\n"
                      "<code>https://site.com/rss | اسم الموقع | تقنية</code>",
                      keyboard=[[{"text": "↩️ رجوع", "callback_data": "manage"}]])
            return

    # ------------------------------------------------------------- الربط
    def _bind_channel(self, chat_id: str | int, channel_id: str, raw_text: str) -> None:
        """تفسير المعرّف ثم التحقّق ثم الحفظ (لا نحفظ ما لا يعمل)."""
        kind, value = parse_target(raw_text)
        if kind == "unknown":
            self.send(chat_id, "❓ لم أفهم. أرسل <code>@name</code> أو <code>-100…</code> أو رابط دعوة.")
            return

        target = to_channel_handle(value) if kind == "id" else value
        if kind == "invite":
            resolved, err = resolve_invite(self.cfg.token, value)
            if not resolved:
                self.send(chat_id, "⚠️ تعذّر قراءة رابط الدعوة "
                                   f"(<code>{html.escape(err[:120])}</code>).\n"
                                   "أرسل <code>@name</code> أو المعرّف الرقمي بدلاً منه.")
                return
            target = resolved

        info = check_chat(self.cfg.token, target)
        b = self.store.get(channel_id)

        if not info.ok():
            b.set_error(info.error or "تعذّر التحقّق")
            b.chat_id = str(info.id)
            self.store.save()
            hint = ""
            if "مشرف" in (info.error or ""):
                hint = ("\n\n<b>الحلّ:</b> افتح القناة ← Administrators ← "
                        "Add Admin ← اختر البوت ← احفظ، ثم أعد الإرسال.")
            elif "not found" in (info.error or "").lower():
                hint = ("\n\n<b>السبب الغالب:</b> البوت ليس عضواً في القناة، "
                        "أو المعرّف خطأ. أضفه مشرفاً ثم أعد المحاولة.")
            self.send(chat_id, f"❌ <b>{channel_title(channel_id)}</b>\n"
                               f"<code>{html.escape(str(info.id))}</code>\n"
                               f"{html.escape(info.error)}{hint}",
                      keyboard=[[{"text": "🔁 حاول مجدداً", "callback_data": f"pick:{channel_id}"}],
                                [{"text": "↩️ اللوحة", "callback_data": "panel"}]])
            return

        b.set_ok(str(info.id), title=info.title, username=info.username,
                chat_type=info.type, is_admin=info.is_admin)
        self.store.clear_pending()
        self.store.save()

        # 🧠 التعرّف على القطاع من الاسم وضبط الوسوم والمصادر تلقائياً
        guessed = ""
        try:
            from .sectors import apply_to_channel

            ok_sector, sector_msg = apply_to_channel(
                channel_id, info.title, config_dir=self.config_dir)
            if ok_sector:
                guessed = "\n\n" + sector_msg + "\n⬆️ عرفت هذا من اسم القناة."
            else:
                guessed = "\n\n" + sector_msg
        except Exception as exc:                                    # noqa: BLE001
            log.warning("تعذّر التعرّف على قطاع %s: %s", channel_id, exc)

        self.send(chat_id,
                  "🎉 <b>تمّ الربط بنجاح</b>\n\n"
                  f"القناة: <b>{html.escape(info.title)}</b>\n"
                  f"المعرّف: <code>{html.escape(str(info.id))}</code>\n"
                  f"مشرف: {'نعم ✅' if info.is_admin else 'عضو فقط ⚠️'}"
                  f"{guessed}\n\n"
                  "سيبدأ النشر في التشغيل القادم.",
                  keyboard=[[{"text": "🏷 عدّل الوسوم", "callback_data": f"tags:{channel_id}"},
                             {"text": "🎯 إعداد قناة أخرى", "callback_data": "panel"}]])
        log.info("✅ رُبطت %s ← %s (%s)", channel_id, info.id, info.title)

    # --------------------------------------------------------- التحكّم
    tz_offset: int = 3        # فرق توقيت العرض (بغداد = 3)
    config_dir: str = "config"

    def _on_control(self, chat_id: str | int, data: str) -> None:
        """أزرار قناة واحدة: ctl:<cid>[:pause|resume|quiet|unquiet]"""
        from .control import ControlStore

        parts = data.split(":")
        cid = parts[1] if len(parts) > 1 else ""
        action = parts[2] if len(parts) > 2 else ""
        control = ControlStore.load()

        if not cid:                       # ctl على مستوى اللوحة
            self.send(chat_id, control.summary(
                [(c, channel_title(c)) for c in self.known], tz_offset=self.tz_offset),
                keyboard=control_keyboard(self.known, control))
            return

        if action == "pause":
            c = control.pause(cid, "من البوت")
        elif action == "resume":
            c = control.resume(cid)
        elif action == "quiet":
            c = control.set_quiet(cid, 23, 6)
        elif action == "unquiet":
            c = control.clear_quiet(cid)
        else:
            c = control.get(cid)

        title = channel_title(cid)
        note = {
            "pause": "⏸ أُوقفت — لن يُنشر فيها شيء حتى تُشغّلها.",
            "resume": "▶️ تعمل الآن.",
            "quiet": "🌙 ساعات هدوء 23:00→06:00 — ما يأتي ليلاً يُؤجَّل لا يُلغى.",
            "unquiet": "☀️ أُلغي الهدوء — النشر متاح ٢٤ ساعة.",
        }.get(action, "تمّ التحديث.")
        self.send(chat_id, f"<b>{title}</b>\n{note}\n\n{c.summary_line()}",
                  keyboard=channel_control_keyboard(cid, c))

    def _on_review_mode(self, chat_id: str | int, data: str) -> None:
        """rev:<cid|all>[:smart|always|never] · rev:all بلا وضع = شرح الخيارات."""
        from .control import ControlStore, MODE_TITLES

        parts = data.split(":")
        target = parts[1] if len(parts) > 1 else "all"
        mode = parts[2] if len(parts) > 2 else ""
        control = ControlStore.load()

        if not mode:
            self.send(chat_id,
                      "🤖 <b>نمط المراجعة</b>\n\n"
                      "• <b>ذكية</b> — لا أسألك إلّا عن المنشور المشكوك فيه "
                      "(درجة منخفضة · صياغة احتياطية · كلمات حسّاسة · بلا مصدر). "
                      "🔴 <b>العاجل لا ينتظر أبداً.</b>\n"
                      "• <b>كل منشور</b> — موافقتك قبل كل نشر.\n"
                      "• <b>بلا مراجعة</b> — نشر مباشر.\n\n"
                      "اختر القناة:",
                      keyboard=([[{"text": channel_title(c), "callback_data": f"rev:{c}:smart"}]
                                 for c in (self.known if target == "all" else [target])]
                                + [[{"text": "↩️ لوحة التحكّم", "callback_data": "ctl"}]]))
            return

        targets = self.known if target == "all" else [target]
        for cid in targets:
            control.set_review(cid, mode)
        self.send(chat_id,
                  f"✅ النمط الجديد: <b>{MODE_TITLES.get(mode, mode)}</b>\n"
                  f"على: {'كل القنوات' if target == 'all' else channel_title(target)}",
                  keyboard=[[{"text": "↩️ لوحة التحكّم", "callback_data": "ctl"}]])
        log.info("🎛 نمط المراجعة %s ← %s", target, mode)

    def _on_brand(self, chat_id: str | int, data: str) -> None:
        """brd:<cid> → طلب @handle · brd:<cid>:off/stamp/hidden للتبديل."""
        from .control import ControlStore  # noqa: F401 — التحقّق من التوفّر فقط

        parts = data.split(":")
        cid = parts[1] if len(parts) > 1 else ""
        action = parts[2] if len(parts) > 2 else ""
        if not cid:
            return
        if action:
            self._set_brand(chat_id, cid, action, toggle=True)
            return
        self.store.start_awaiting(cid, telegram_user=0, kind="brand")
        self.send(chat_id,
                  f"🖋 <b>بصمة الملكية — {channel_title(cid)}</b>\n\n"
                  "أرسل اسم قناتك كما يظهر في تيليجرام، مثل: "
                  "<code>@my_channel</code>\n\n"
                  "سنضعها في:\n"
                  "• آخر كل منشور — <code>📡 @my_channel</code>\n"
                  "• على كل صورة — شريط صغير باسمك\n"
                  "• علامة خفيّة في النصّ تُثبت الملكية لو نُسق الخبر\n\n"
                  "↩️ للإلغاء: /cancel",
                  keyboard=[[{"text": "↩️ لوحة التحكّم", "callback_data": "ctl"}]])

    # --------------------------------------------------------- الموافقات
    def _on_approval(self, chat_id: str | int, data: str) -> None:
        """apv:<id> نشر · rej:<id> حذف · prv:<id> معاينة · apv:list قائمة الانتظار."""
        from .control import KIND_APPROVAL, ControlStore, preview_text

        action, _, item_id = data.partition(":")
        control = ControlStore.load()

        if item_id == "list":
            items = control.pending(kind=KIND_APPROVAL)
            if not items:
                self.send(chat_id, "📥 لا شيء ينتظر موافقتك الآن.",
                          keyboard=[[{"text": "↩️ الرئيسية", "callback_data": "panel"}]])
                return
            rows = [[{"text": f"👁 {channel_title(i['channel'])} — {str((i.get('post') or {}).get('title', ''))[:28]}",
                      "callback_data": f"prv:{i['id']}"}] for i in items[:8]]
            rows.append([{"text": "↩️ الرئيسية", "callback_data": "panel"}])
            self.send(chat_id, f"📥 <b>{len(items)}</b> منشور ينتظر موافقتك:", keyboard=rows)
            return

        if action == "apv":
            control.take(item_id)
            self.send(chat_id, "🚫 الموافقات اليدوية معطّلة؛ أُزيل هذا العنصر القديم ولن يُنشر.")
            return

        item = control.item(item_id)
        if not item:
            answer_callback(self.cfg.token, "", "انتهت صلاحية هذا العنصر")
            self.send(chat_id, "⌛ انتهت صلاحية هذا العنصر (نُشر أو حُذف سابقاً).")
            return

        if action == "prv":
            self.send(chat_id, preview_text(item), keyboard=[[
                {"text": "✅ نشر الآن", "callback_data": f"apv:{item_id}"},
                {"text": "🗑 حذف", "callback_data": f"rej:{item_id}"},
            ]])
            return

        if action == "rej":
            control.take(item_id)
            self.send(chat_id, "🗑 حُذف — لن يُنشر.",
                      keyboard=[[{"text": "📥 ما ينتظرني", "callback_data": "apv:list"}]])
            return

        if action == "apv":
            channel = self._channel(item.get("channel", ""))
            if channel is None:
                self.send(chat_id, "⚠️ القناة غير موجودة في الإعدادات — احذف العنصر.")
                return
            self.send(chat_id, "⏳ جارٍ النشر…")
            ok, detail = self._publish_item(channel, item)
            control.take(item_id)
            self.send(chat_id,
                      ("✅ <b>نُشر</b>\n" if ok else "❌ <b>فشل النشر</b>\n"
                       f"{html.escape(str(detail)[:200])}\n"),
                      keyboard=[[{"text": "📥 ما ينتظرني", "callback_data": "apv:list"},
                                 {"text": "↩️ الرئيسية", "callback_data": "panel"}]])

    # ------------------------------------------------------------- أدوات
    def _channel(self, cid: str):
        """قراءة تعريف القناة من channels.yml (بلا تحميل كامل للإعدادات)."""
        try:
            from .config_loader import load_config

            settings = load_config("config")
            return next((c for c in settings.channels if c.id == cid), None)
        except Exception as exc:  # noqa: BLE001
            log.warning("تعذّر تحميل القنوات: %s", exc)
            return None

    def _publish_item(self, channel, item: dict) -> tuple[bool, str]:
        """نشر عنصر من الطابور فعلياً (مع الصورة والختم)."""
        try:
            from .ai.writer import Post
            from .config_loader import load_config
            from .images import fetch_article_image
            from .publishers import build_publisher

            settings = load_config("config", override_dry_run=False)
            post = Post.from_dict(item.get("post") or {})
            image = (fetch_article_image(post.source_url, settings=settings.images)
                     if channel.with_image and settings.images.enabled else None)
            publisher = build_publisher(channel, dry_run=False, timeout=45)
            try:
                result = publisher.publish(post, image)
            finally:
                if image:
                    image.cleanup()
            return result.ok, result.error or result.ref
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ------------------------------------------------------------ البصمة
    def _set_brand(self, chat_id: str | int, channel_id: str, raw: str, *,
                   toggle: bool = False) -> None:
        """حفظ @handle أو تبديل خيارات (off/stamp/hidden)."""
        from .config_loader import load_config
        from .utils import write_branding

        try:
            settings = load_config("config")
        except Exception as exc:  # noqa: BLE001
            self.send(chat_id, f"⚠️ تعذّر تحميل الإعدادات: {html.escape(str(exc)[:120])}")
            return
        channel = next((c for c in settings.channels if c.id == channel_id), None)
        if channel is None:
            self.send(chat_id, "⚠️ القناة غير موجودة.")
            return

        from .branding import Branding

        brand = Branding.from_channel(channel)
        if toggle:
            if raw == "off":
                ok, msg = write_branding(channel_id, enabled=False)
            elif raw == "stamp":
                ok, msg = write_branding(channel_id, stamp_on_image=not brand.stamp_on_image)
            elif raw == "hidden":
                ok, msg = write_branding(channel_id, hidden_mark=not brand.hidden_mark)
            else:
                ok, msg = False, "خيار غير معروف"
        else:
            handle = raw.strip().lstrip("@").split("/")[-1]
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]{3,}$", handle):
                self.send(chat_id, "❓ اسم غير صالح. أرسل مثل <code>@my_channel</code> "
                                   "(أحرف إنجليزية وأرقام و_ فقط).")
                return
            ok, msg = write_branding(channel_id, handle=handle)

        if not ok:
            self.send(chat_id, f"⚠️ {html.escape(msg)}")
            return
        self.store.get(channel_id).handle = "" if toggle else raw.strip().lstrip("@")
        self.store.clear_pending()
        self.store.save()
        refreshed = Branding.from_channel(
            next(c for c in load_config("config").channels if c.id == channel_id))
        self.send(chat_id,
                  f"✅ <b>تمّت البصمة — {channel_title(channel_id)}</b>\n\n"
                  f"المعرّف: <code>{html.escape(refreshed.handle or '—')}</code>\n"
                  f"توقيع النصّ: <code>{html.escape(refreshed.footer_for('telegram'))}</code>\n"
                  f"علامة الصورة: {'مفعّلة 🖼' if refreshed.stamp_on_image else 'معطّلة'}\n"
                  f"العلامة الخفيّة: {'مفعّلة 🕵️' if refreshed.hidden_mark else 'معطّلة'}",
                  keyboard=[[{"text": "🖼 تبديل علامة الصورة", "callback_data": f"brd:{channel_id}:stamp"},
                             {"text": "🕵️ تبديل العلامة الخفيّة", "callback_data": f"brd:{channel_id}:hidden"}],
                            [{"text": "↩️ لوحة التحكّم", "callback_data": "ctl"}]])

    # ------------------------------------------------ 📘 فيسبوك
    def _on_facebook(self, chat_id: str | int) -> None:
        """ربط الصفحة: معرّفها (ليس سرّاً) يُحفظ، والتوكن يبقى في Secrets."""
        from .config_loader import load_config

        current = ""
        try:
            ch = next((c for c in load_config(self.config_dir).channels if c.id == "fb_page"), None)
            current = ch.env("page_id") if ch else ""
        except Exception:  # noqa: BLE001
            pass

        has_token = bool(os_environ("FACEBOOK_PAGE_TOKEN") or os_environ("FACEBOOK_USER_TOKEN"))
        lines = [
            "📘 <b>صفحة فيسبوك</b>",
            "",
            f"الحالة: {'✅ مربوطة' if current else '⬜ لم تُضبط'}",
        ]
        if current:
            lines.append(f"المعرّف: <code>{html.escape(current)}</code>")
        lines += [
            f"التوكن: {'✅ موجود في Secrets' if has_token else '❌ غير موجود'}",
            "",
            "<b>ما تحتاجه:</b>",
            "١) معرّف الصفحة (أرقام) — أرسله هنا وأنسخه لك في الإعدادات",
            "٢) توكن الصفحة الدائم — <b>سرّ</b> يُوضع في GitHub Secrets لا هنا",
            "",
            "🔑 من أين؟ <code>python -m autonews.fb_main --token</code>",
            "   أو Graph API Explorer ← اختر صفحتك ← Generate Token",
            "",
            "⚠️ <b>التوكن لا يُرسل للبوت أبداً</b> — خاصّتك أنت، ولا يُمرَّر في تيليجرام.",
        ]
        self.send(chat_id, "\n".join(lines),
                  keyboard=[[{"text": "✏️ أضف معرّف الصفحة", "callback_data": "fb:id"},
                             {"text": "❓ كيف أجيب التوكن؟", "callback_data": "fb:help"}],
                            [{"text": "↩️ رجوع", "callback_data": "manage"}]])

    def _save_page_id(self, chat_id: str | int, raw: str) -> None:
        from .fb_main import save_page

        candidate = raw.strip()
        if not candidate.isdigit() or len(candidate) < 6:
            self.send(chat_id,
                      "❓ المعرّف أرقام فقط (مثل <code>1234567890123456</code>).\n"
                      "ليس اسم الصفحة — الرقم من: الصفحة ← About ← Page transparency.")
            return
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = save_page(candidate, config_dir=self.config_dir)
        self.store.clear_pending()
        if code != 0:
            self.send(chat_id, "⚠️ تعذّر الحفظ:\n<code>" +
                              html.escape(buffer.getvalue()[-300:]) + "</code>")
            return
        self.send(chat_id,
                  "✅ <b>حُفظ معرّف الصفحة</b>\n\n"
                  f"<code>{html.escape(candidate)}</code>\n\n"
                  "الباقي <b>سرّ واحد</b> في GitHub:\n"
                  "Settings → Secrets → Actions → New repository secret\n"
                  "الاسم: <code>FACEBOOK_PAGE_TOKEN</code>\n"
                  "القيمة: التوكن الدائم من <code>--token</code>\n\n"
                  "ثم: <code>python -m autonews.fb_main --check</code>",
                  keyboard=[[{"text": "📘 حالة فيسبوك", "callback_data": "fb"},
                             {"text": "↩️ الإدارة", "callback_data": "manage"}]])

    # ------------------------------------------------ 📊 الإحصاءات
    def _show_stats(self, chat_id: str | int) -> None:
        from .stats import build_stats

        report = build_stats(known=[(c, channel_title(c)) for c in self.known],
                             tz_offset=self.tz_offset)
        advice = report.advice()
        text = report.text()
        if advice:
            text += "\n\n<b>💡 توصيات:</b>\n" + "\n".join(f"• {a}" for a in advice)
        self.send(chat_id, text,
                  keyboard=[[{"text": "🔄 تحديث", "callback_data": "st"},
                             {"text": "↩️ الإدارة", "callback_data": "manage"}]])

    # ------------------------------------------------- 🏷 محرّر الوسوم
    def _on_tags(self, chat_id: str | int, data: str) -> None:
        """tags  ·  tags:<cid>  ·  taga:<cid>:<tag>  ·  tagd:<cid>:<tag>  ·  tagx:<cid>"""
        from .editor import all_tags, update_channel_tags

        parts = data.split(":")
        action, cid = parts[0], (parts[1] if len(parts) > 1 else "")
        tag = parts[2] if len(parts) > 2 else ""

        if not cid:
            rows = [[{"text": channel_title(c), "callback_data": f"tags:{c}"}]
                    for c in self.known]
            rows.append([{"text": "↩️ رجوع", "callback_data": "manage"}])
            self.send(chat_id,
                      "🏷 <b>وسوم أي قناة؟</b>\n\n"
                      "الوسم يحدّد الأخبار التي تصل للقناة. "
                      "قناة بلا وسوم تستقبل <b>كل شيء</b>.",
                      keyboard=rows)
            return

        if action == "tagx":
            self.store.start_awaiting(cid, telegram_user=0, kind="tags")
            self.send(chat_id, f"✏️ أرسل الوسم الجديد لـ <b>{channel_title(cid)}</b> "
                               "(كلمة واحدة، مثل: <code>عملات</code>)")
            return

        if action in ("taga", "tagd"):
            result = update_channel_tags(cid, add=[tag] if action == "taga" else None,
                                         remove=[tag] if action == "tagd" else None)
            if not result.ok:
                self.send(chat_id, f"⚠️ {html.escape(result.message)}")
                return
            extra = ("\n\n" + "\n".join(f"• {html.escape(w)}" for w in result.warnings)
                     if result.warnings else "")
            self.send(chat_id,
                      f"{html.escape(result.message)}{extra}\n\n"
                      f"<i>يُطبَّق في التشغيل القادم.</i>",
                      keyboard=tags_keyboard(cid, result.tags, all_tags()))
            return

        # عرض وسوم القناة
        from .editor import _read_channel_tags  # noqa: PLC0415

        tags = _read_channel_tags(pathlib_join(self.config_dir, "channels.yml"), cid)
        self.send(chat_id,
                  f"🏷 <b>وسوم {channel_title(cid)}</b>\n\n"
                  f"{'✅ ' + '، '.join(tags) if tags else '⬜ بلا وسوم — تستقبل كل شيء'}\n\n"
                  "اضغط ✅ للحذف، أو ➕ للإضافة:",
                  keyboard=tags_keyboard(cid, tags, all_tags()))

    def _add_tag(self, chat_id: str | int, cid: str, raw: str) -> None:
        from .editor import all_tags, update_channel_tags

        tag = raw.strip().splitlines()[0][:24]
        result = update_channel_tags(cid, add=[tag])
        self.store.clear_pending()
        if not result.ok:
            self.send(chat_id, f"⚠️ {html.escape(result.message)}")
            return
        extra = ("\n\n" + "\n".join(f"• {html.escape(w)}" for w in result.warnings)
                 if result.warnings else "")
        self.send(chat_id, f"{html.escape(result.message)}{extra}",
                  keyboard=tags_keyboard(cid, result.tags, all_tags()))

    # --------------------------------------------- ➕ قناة جديدة
    def _autolink(self, chat_id: str | int, raw: str) -> None:
        """ربط سريع: «المعرّف | الاسم» → تحقّق + حفظ + تعرّف على القطاع."""
        from .sectors import detect, detect_custom_profile

        parts = [p.strip() for p in raw.replace("،", "|").split("|")]
        target = to_channel_handle(parts[0]) if parts else ""
        name = parts[1] if len(parts) > 1 else ""
        supplied_description = parts[2] if len(parts) > 2 else ""
        self.store.clear_pending()

        if not target:
            self.send(chat_id, "❌ لم أفهم المعرّف. أرسل: <code>-100… | اسم القناة</code>")
            return

        info = check_chat(self.cfg.token, target)
        if not info.ok():
            hint = ("\n\n<b>الحلّ:</b> أضف البوت مشرفاً في القناة، أو أرسل "
                    "<code>@username</code> بدل الرقم.")
            self.send(chat_id, f"❌ تعذّر الربط: {html.escape(info.error or '')}{hint}",
                      keyboard=[[{"text": "🔁 حاول مجدداً", "callback_data": "autolink"}]])
            return

        title = name or info.title
        profile = detect(title)
        # نُنشئ تعريف القناة إن لم تكن معروفة
        from .config_loader import load_config
        from .editor import suggest_channel_id
        configured = load_config(self.config_dir).channels
        matched = next((c for c in configured
                        if str(c.name).strip().casefold() == title.strip().casefold()), None)
        description = supplied_description or (matched.description if matched else "")
        existing = {c.id for c in configured}
        cid = matched.id if matched else suggest_channel_id(f"{title} {description}", existing=existing)
        profile = detect(title, description)
        if description and (profile is None or profile.key.startswith("custom_")):
            profile = detect_custom_profile(title, description)
        created = ""
        if cid not in existing:
            from .editor import add_channel

            ok_new, msg_new = add_channel(
                cid, name=title, description=description, tags=list(profile.tags) if profile else [],
                config_dir=self.config_dir)
            if ok_new:
                created = f"\n🆕 أُنشئت قناة جديدة: <code>{cid}</code>"

        b = self.store.get(cid)
        b.set_ok(str(info.id), title=info.title, username=info.username,
                chat_type=info.type, is_admin=info.is_admin)
        self.store.save()

        sector_line = ""
        if profile is not None:
            from .sectors import apply_to_channel, ensure_sources

            ensure_sources(profile, config_dir=self.config_dir)
            ok_a, msg_a = apply_to_channel(cid, title, config_dir=self.config_dir,
                                           override=True)
            #  نتحقّق من الملف فعلياً لا من رسالة الدالة وحدها
            applied = next((c for c in load_channels(self.config_dir) if c.get("id") == cid), {})
            srcs = applied.get("sources") or []
            shown = ("كل المصادر (الرمز *)" if srcs == ["*"]
                     else f"{len(srcs)} مصدراً: {', '.join(map(str, srcs[:3]))}")
            sector_line = (f"\n\n{'✅' if ok_a else '⚠️'} {msg_a}"
                           f"\n📡 المصادر في الملف: {shown}")
        else:
            sector_line = ("\n\n🤷 لم أعرف القطاع من الاسم — ستنشر كل الأخبار "
                           "(وهذا آمن). يمكنك تسميتها «أخبار الأنمي» مثلاً.")

        self.send(chat_id,
                  "🎉 <b>تمّ الربط</b>\n\n"
                  f"القناة: <b>{html.escape(info.title)}</b>\n"
                  f"المعرّف: <code>{html.escape(str(info.id))}</code>"
                  f"{created}{sector_line}",
                  keyboard=[[{"text": "🏷 عدّل الوسوم", "callback_data": f"tags:{cid}"},
                             {"text": "🎯 ربط أخرى", "callback_data": "autolink"}]])
        log.info("🔗 ربط سريع: %s ← %s (%s)", cid, info.id, info.title)

    def _create_channel(self, chat_id: str | int, raw: str) -> None:
        """➕ إضافة قناة بالاسم/الوصف فقط — الاختصاص والمعرّف يُستنتجان تلقائياً."""
        from .editor import add_channel, suggest_channel_id
        from .sectors import apply_to_channel
        from .channels_main import load_channels
        from .sectors import detect, detect_custom_profile, ensure_sources, sector_of

        task = self.store.pending_task() or {}
        sector_key = str(task.get("sector", "") or "")

        description = ""
        # الصيغة القديمة (معرّف | اسم | وسوم) تبقى مدعومة.
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            first = parts[0] if parts else ""
            if first.startswith("tg_") or first.startswith("-100"):
                cid = first
                name = parts[1] if len(parts) > 1 else cid
                tags = [t.strip() for t in (parts[2] if len(parts) > 2 else "").split(",") if t.strip()]
            else:
                name = first
                description = parts[1] if len(parts) > 1 else ""
                tags = []
                existing = {str(c.get("id")) for c in load_channels(self.config_dir)}
                cid = suggest_channel_id(f"{name} {description}", existing=existing)
        else:
            # 🎯 المسار الذكي: الاسم (+ الوصف) فقط
            name = raw.strip()
            profile = detect(name, description)
            tags = list(profile.tags) if profile else []
            existing = {str(c.get("id")) for c in load_channels(self.config_dir)}
            cid = suggest_channel_id(name, existing=existing)
            if sector_key:
                from .sectors import SECTORS
                chosen = SECTORS.get(sector_key)
                if chosen is not None:                       # اختيار صريح يتقدّم على الاستنتاج
                    profile = chosen
                    tags = list(chosen.tags)
                    cid = suggest_channel_id(name, existing=existing)

        profile = detect(name, description)
        if description.strip() and (profile is None or profile.key.startswith("custom_")):
            profile = detect_custom_profile(name, description)
        if sector_key and not description.strip():
            from .sectors import SECTORS
            profile = SECTORS.get(sector_key) or profile
        if profile is not None and not tags:
            tags = list(profile.tags)
        ok, msg = add_channel(cid, name=name, description=description,
                             tags=tags, config_dir=self.config_dir)
        self.store.clear_pending()
        if not ok:
            self.send(chat_id, msg.replace("<", "&lt;"),
                      keyboard=[[{"text": "🔁 حاول مجدداً", "callback_data": "newc"},
                                 {"text": "↩️ رجوع", "callback_data": "manage"}]])
            return
        if cid not in self.known:
            self.known.append(cid)

        # ضبط المصادر حسب الاختصاص (لا قناة صامتة) + ذكر ما جرى
        extra = ""
        if profile is not None:
            # أنشئ مصادر الموضوع أولاً، ثم اربط القناة بها؛ عكس الترتيب
            # كان يترك القناة على ["*"] فتستقبل كل الأخبار رغم اكتشاف اختصاصها.
            note = ensure_sources(profile, config_dir=self.config_dir)
            ok_apply, apply_msg = apply_to_channel(
                cid, name, description=description,
                config_dir=self.config_dir, override=True)
            extra = (f"\n🎯 الاختصاص: <b>{profile.icon} {profile.label}</b>"
                     f"\n🏷 الوسوم: {', '.join(profile.tags) or '—'}")
            if note:
                extra += f"\n{note}"
            if not ok_apply:
                extra += f"\n⚠️ تعذّر ربط المصادر: {apply_msg}"
            if not profile.tags:                    # قطاع عام
                extra += ("\n📡 المصادر: <b>كل المصادر (الرمز *)</b> — "
                          "أي مصدر تضيفه لاحقاً يصلها تلقائياً.")
            extra += "\n\n🔁 تقدر تغيّر الاختصاص في أي وقت من «✏️ الاختصاص»."
            dup = [c.get("id") for c in load_channels(self.config_dir)
                   if c.get("platform") == "telegram" and c.get("id") != cid
                   and sector_of(str(c.get("id")), config_dir=self.config_dir) is not None
                   and sector_of(str(c.get("id")), config_dir=self.config_dir).key == profile.key]
            if dup:
                extra += (f"\n\n⚠️ تنبيه: عندك قناة بنفس الاختصاص (<code>{', '.join(dup)}</code>). "
                          f"القاعدتان ستنشران متشابهة — إمّا تعطّل واحدة أو تغيّر اختصاص هذه.")
        else:
            extra = ("\n🤷 لم أتعرّف على الاختصاص من النص — القناة تستقبل كل الأخبار "
                     "(آمن). اضبط الاختصاص يدوياً من «✏️ الاختصاص».")

        self.send(chat_id, msg + extra,
                  keyboard=[[{"text": "🔗 اربط بالاسم", "callback_data": f"pick:{cid}"},
                             {"text": "🧠 الاختصاص", "callback_data": f"sec:{cid}"}],
                            [{"text": "↩️ القنوات", "callback_data": "chs"}]])

    # --------------------------------------------- 📡 مصدر جديد
    def _create_source(self, chat_id: str | int, raw: str) -> None:
        from .editor import add_source

        parts = [p.strip() for p in raw.replace("،", ",").split("|")]
        url = parts[0] if parts else ""
        name = parts[1] if len(parts) > 1 else ""
        tags = [t.strip() for t in (parts[2] if len(parts) > 2 else "").split(",") if t.strip()]
        self.send(chat_id, "⏳ أختبر الرابط الآن…")
        ok, msg = add_source(url, name=name, tags=tags, config_dir=self.config_dir)
        self.store.clear_pending()
        keyboard = ([[{"text": "📡 آخر", "callback_data": "news"},
                      {"text": "↩️ الإدارة", "callback_data": "manage"}]] if ok else
                    [[{"text": "🔁 حاول مجدداً", "callback_data": "news"},
                      {"text": "↩️ رجوع", "callback_data": "manage"}]])
        self.send(chat_id, msg, keyboard=keyboard)

    # ------------------------------------------------------------- الجماعي
    def _verify_all(self, chat_id: str | int) -> None:
        lines = ["🔄 <b>فحص كل القنوات</b>", ""]
        for cid in self.known:
            b = self.store.bindings.get(cid)
            if not b or not b.chat_id:
                lines.append(f"⬜ {channel_title(cid)} — لم تُضبط")
                continue
            info = check_chat(self.cfg.token, b.chat_id)
            if info.ok():
                b.set_ok(str(info.id), title=info.title, username=info.username,
                        chat_type=info.type, is_admin=info.is_admin)
                lines.append(f"✅ {channel_title(cid)} — {html.escape(info.title)}")
            else:
                b.set_error(info.error)
                lines.append(f"❌ {channel_title(cid)} — {html.escape(info.error)}")
        self.store.save()
        self.send(chat_id, "\n".join(lines),
                  keyboard=[[{"text": "↩️ اللوحة", "callback_data": "panel"}]])

    def _test_all(self, chat_id: str | int) -> None:
        lines = ["🧪 <b>اختبار النشر</b>", ""]
        for cid in self.store.configured():
            b = self.store.bindings[cid]
            ok, detail = send_test(self.cfg.token, b.chat_id,
                                   text=f"🧪 اختبار AutoNews — {channel_title(cid)}\n"
                                        "إن وصلتك هذه، فالنشر سيعمل.")
            lines.append(f"{'✅' if ok else '❌'} {channel_title(cid)}"
                         + ("" if ok else f" — {html.escape(str(detail)[:80])}"))
        if len(lines) == 2:
            lines.append("⬜ لا قنوات جاهزة بعد.")
        self.send(chat_id, "\n".join(lines),
                  keyboard=[[{"text": "↩️ اللوحة", "callback_data": "panel"}]])


    # ------------------------------------------------ 📋 إدارة القنوات
    def _channels(self) -> list[dict]:
        """قنوات تيليجرام من channels.yml (مصدر الحقيقة، لا قائمة ثابتة)."""
        try:
            from .channels_main import load_channels

            return [c for c in load_channels(self.config_dir)
                    if c.get("platform") == "telegram"]
        except Exception as exc:                                # noqa: BLE001
            log.warning("تعذّر قراءة القنوات: %s", exc)
            return []

    def _show_channels(self, chat_id: str | int) -> None:
        """📋 كل القنوات بحالتها واختصاصها."""
        from .control import ControlStore

        channels = self._channels()
        if not channels:
            self.send(chat_id, "📭 لا قنوات تيليجرام بعد.\n\nاضغط «➕ قناة جديدة».",
                      keyboard=[[{"text": "➕ قناة جديدة", "callback_data": "newc"},
                                 {"text": "↩️ الرئيسية", "callback_data": "panel"}]])
            return

        control = ControlStore.load("state/control.json")
        active = len([c for c in channels if c.get("enabled", True)])
        lines = [f"📋 <b>القنوات</b> ({active}/{len(channels)} مفعّلة)", ""]
        from .sectors import sector_of

        for ch in channels:
            cid = ch["id"]
            profile = sector_of(cid, config_dir=self.config_dir)
            state = "✅" if ch.get("enabled", True) else "⏸"
            cap = ch.get("max_posts_per_run", 3)
            icon = profile.icon if profile else "📌"
            label = profile.label if profile else "بلا اختصاص"
            lines.append(f"{state} {icon} <b>{ch.get('name') or cid}</b> · {label} · {cap}/تشغيل")
        lines += ["", "اضغط أي قناة لإدارتها."]
        self.send(chat_id, "\n".join(lines), keyboard=channels_keyboard(channels, control))

    def _show_channel(self, chat_id: str | int, cid: str) -> None:
        """تفاصيل قناة واحدة: الاختصاص · الحالة · آخر منشور · الربط."""
        from .control import ControlStore
        from .sectors import sector_of

        channel = next((c for c in self._channels() if c["id"] == cid), None)
        if channel is None:
            self.send(chat_id, f"❌ القناة «{cid}» غير موجودة.",
                      keyboard=[[{"text": "↩️ القنوات", "callback_data": "chs"}]])
            return

        binding = self.store.get(cid)
        control = ControlStore.load("state/control.json")
        c = control.get(cid)
        profile = sector_of(cid, config_dir=self.config_dir)
        enabled = bool(channel.get("enabled", True))

        lines = [f"<b>{html.escape(str(channel.get('name') or cid))}</b>",
                 f"<code>{cid}</code>", ""]
        lines.append(f"الحالة: {'✅ مفعّلة' if enabled else '⏸ معطّلة'}")
        if getattr(c, "paused", False):
            lines.append("⏸ <b>موقوفة مؤقتاً</b> من لوحة التحكّم")
        lines.append(f"الاختصاص: {profile.icon + ' ' + profile.label if profile else '📌 بلا اختصاص'}")
        lines.append(f"عدد المنشورات/تشغيل: <b>{channel.get('max_posts_per_run', 3)}</b>")
        if binding and binding.verified:
            lines.append(f"الربط: ✅ {html.escape(binding.title or binding.chat_id)}")
        else:
            lines.append("الربط: ⬜ لم تُربط — اضغط «ربط بالاسم»")
        lines.append(f"الوسوم: {', '.join(channel.get('tags') or []) or '—'}")
        n_src = len(channel.get("sources") or [])
        lines.append(f"المصادر: {'كل المصادر' if '*' in (channel.get('sources') or []) else str(n_src)}")
        if getattr(c, "posts_total", 0):
            lines.append(f"إجمالي المنشورات: <b>{c.posts_total}</b> · آخرها: {_ago(c.last_post_at)}")

        self.send(chat_id, "\n".join(lines),
                  keyboard=channel_detail_keyboard(cid, enabled=enabled))

    def _show_last_posts(self, chat_id: str | int, cid: str, limit: int = 5) -> None:
        """📜 آخر المنشورات الفعلية لهذه القناة من state.json."""
        posts: list[dict] = []
        try:
            from .state import StateStore

            state = StateStore("state/state.json").load()
            posts = [p for p in state.posted if p.get("channel") == cid][-limit:]
        except Exception as exc:                                # noqa: BLE001
            log.warning("تعذّر قراءة المنشورات: %s", exc)

        if not posts:
            self.send(chat_id, f"📭 لا منشورات مسجّلة لـ <code>{cid}</code> بعد.",
                      keyboard=[[{"text": "↩️ رجوع", "callback_data": f"chv:{cid}"}]])
            return

        lines = [f"📜 <b>آخر {len(posts)} منشورات</b> · <code>{cid}</code>", ""]
        for post in reversed(posts):
            when = _ago(post.get("at", ""))
            title = str(post.get("title") or post.get("url") or "—")[:70]
            mark = "🧪" if post.get("ref") == "dry-run" else "✅"
            lines.append(f"{mark} {html.escape(title)}\n     🕐 {when}")
        self.send(chat_id, "\n".join(lines),
                  keyboard=[[{"text": "↩️ رجوع", "callback_data": f"chv:{cid}"}]])

    def _show_errors(self, chat_id: str | int) -> None:
        """🩺 الأخطاء والإحصاءات: صحة المصادر + طابور الإعادة + آخر تشغيل."""
        lines = ["🩺 <b>الأخطاء والإحصاءات</b>", ""]
        try:
            from .state import StateStore

            state = StateStore("state/state.json").load()
            lines.append(f"التشغيلات: <b>{state.runs}</b> · "
                         f"منشورات: <b>{len(state.posted)}</b> · "
                         f"مقالات معالَجة: <b>{len(state.articles)}</b>")

            if state.retry_queue:
                lines += ["", f"🔁 <b>طابور الإعادة</b> ({len(state.retry_queue)}):"]
                for item in state.retry_queue[:5]:
                    title = str(item.get("title") or item.get("article_fp") or "—")[:50]
                    tries = item.get("attempts", 0)
                    lines.append(f"   • {html.escape(title)} (محاولة {tries})")

            bad = [(sid, h) for sid, h in (state.source_health or {}).items()
                   if int(h.get("fails", 0)) >= 2]
            if bad:
                lines += ["", f"⚠️ <b>مصادر متعثّرة</b> ({len(bad)}):"]
                for sid, health in sorted(bad, key=lambda x: -int(x[1].get("fails", 0)))[:5]:
                    lines.append(f"   • <code>{sid}</code> — {health.get('fails')} فشل")
            else:
                lines.append("✅ لا مصادر متعثّرة")

            pending = getattr(state, "pending_reviews", {}) or {}
            if pending:
                lines += ["", f"👁 بانتظار مراجعتك: <b>{len(pending)}</b>"]
        except Exception as exc:                                # noqa: BLE001
            lines.append(f"❌ تعذّر قراءة الحالة: {html.escape(str(exc)[:80])}")

        sources = self._read_sources()
        active_src = len([x for x in sources if getattr(x, "enabled", True)])
        lines.append(f"📡 المصادر: {len(sources)} ({active_src} مفعّل)")
        lines.append(f"📋 القنوات: {len(self._channels())}")
        self.send(chat_id, "\n".join(lines),
                  keyboard=[[{"text": "📊 الإحصاءات", "callback_data": "st"},
                             {"text": "↩️ الإدارة", "callback_data": "manage"}]])

    def _set_max_posts(self, chat_id: str | int, cid: str, raw: str) -> None:
        """تعديل عدد المنشورات لكل تشغيل من البوت (رقم فقط)."""
        from .editor import update_channel_setting

        text = (raw or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        self.store.clear_pending()
        if not text.lstrip("-").isdigit():
            self.send(chat_id, "❌ أرسل رقماً فقط (مثل <code>3</code>).",
                      keyboard=[[{"text": "🔁 حاول", "callback_data": f"num:{cid}"}]])
            return
        value = int(text)
        res = update_channel_setting(cid, "max_posts_per_run", value,
                                     config_dir=self.config_dir)
        if not res.ok:
            self.send(chat_id, res.message.replace("<", "&lt;"),
                      keyboard=[[{"text": "🔁 حاول", "callback_data": f"num:{cid}"}]])
            return
        note = "بلا حدّ أقصى" if value == 0 else f"{value} منشوراً كحدّ أقصى"
        self.send(chat_id, f"✅ <b>{channel_title(cid)}</b>: {note}",
                  keyboard=[[{"text": "↩️ القناة", "callback_data": f"chv:{cid}"}]])

    def _read_sources(self) -> list[dict]:
        try:
            from .channels_main import load_channels  # noqa: F401
            from .config_loader import load_config

            return list(load_config(self.config_dir).sources)
        except Exception:                                       # noqa: BLE001
            return []

    # ------------------------------------------------------------- الحلقة
    def _load_offset(self) -> int:
        try:
            return int(self._offset_file.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _save_offset(self, offset: int) -> None:
        try:
            self._offset_file.write_text(str(offset))
        except OSError:
            pass

    def poll(self, *, max_rounds: int = 1, wait: int = 0) -> int:
        """معالجة الرسائل المعلّقة. يعيد عدد التحديثات المعالَجة."""
        handled = 0
        offset = self._load_offset()
        for _ in range(max_rounds):
            updates = get_updates(self.cfg.token, offset=offset, timeout=wait)
            if not updates:
                break
            for upd in updates:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                try:
                    self.handle_update(upd)
                except Exception as exc:  # noqa: BLE001 — لا نُسقط الحلقة
                    log.warning("خطأ في معالجة تحديث: %s", exc)
                handled += 1
            self._save_offset(offset)
        return handled
