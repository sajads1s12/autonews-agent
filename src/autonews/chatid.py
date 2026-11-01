"""أداة «اعرف معرّف القناة» — تربط البوت بالقناة وتختبر الوصول فعلياً.

المشكلة العملية (أشهر سبب لفشل النشر):
    «chat not found» — لأن معرّف القناة خطأ، أو البوت ليس مشرفاً فيها.
    الناس تظنّ المشكلة في الكود، فتضيّع ساعات.

الحلّ: أداة واحدة تفعل ثلاث خطوات بترتيب صحيح:

    ١) تسأل تيليجرام: من أين جاءت آخر رسالة؟ (`getUpdates`)
       → تكشف معرّفات القنوات/المجموعات التي أضفت البوت إليها للتوّ.
    ٢) تتحقّق من الوصول بنداء حقيقي: (`getChat` + `getChatMember`)
       → تؤكّد أن البوت مشرف فعلاً، ولا تعتمد على الظنّ.
    ٣) ترسل رسالة اختبار واحدة (اختياري) وتُخرج الأوامر الجاهزة.

لا تحفظ أسراراً في المستودع: تطبع القيم لتنقلها إلى GitHub Secrets.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .utils import get_logger

log = get_logger("autonews.chatid")

TG = "https://api.telegram.org/bot{token}/{method}"

# أنواع المحادثات التي يصلح النشر فيها
PUBLISHABLE = {"channel", "supergroup", "group"}


@dataclass(slots=True)
class ChatInfo:
    """محادثة اكتُشفت أو تحقّق منها."""
    id: int | str
    title: str = ""
    type: str = ""
    username: str = ""
    is_admin: bool = False
    can_post: bool = False
    error: str = ""

    @property
    def chat_id_env(self) -> str:
        """الشكل الذي يُوضع في GitHub Secrets (`-100…` للقنوات)."""
        if isinstance(self.id, int):
            return str(self.id)
        return str(self.id)

    @property
    def mention(self) -> str:
        """الصيغة القصيرة إن كان للقناة اسم مستخدم عام."""
        return f"@{self.username}" if self.username else self.chat_id_env

    def ok(self) -> bool:
        return self.can_post and not self.error


def _call(token: str, method: str, params: dict[str, Any] | None = None,
          *, timeout: int = 20) -> tuple[bool, Any]:
    """نداء واجهة تيليجرام. يعيد (نجح؟، البيانات أو وصف الخطأ)."""
    url = TG.format(token=token, method=method)
    data = None
    if params:
        data = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v not in (None, "")}).encode()
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return False, f"HTTP {exc.code}"
        return False, payload.get("description", f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"تعذّر الاتصال: {exc}"
    if not payload.get("ok"):
        return False, payload.get("description", "خطأ غير معروف")
    return True, payload.get("result")


def bot_info(token: str) -> dict[str, Any] | None:
    """معلومات البوت (يثبت أن التوكن صالح)."""
    ok, result = _call(token, "getMe")
    return result if ok else None


def discover_chats(token: str, *, limit: int = 20) -> list[ChatInfo]:
    """اكتشاف المحادثات من `getUpdates`.

    الطريقة: أضف البوت إلى القناة (كمشرف)، ثم انشر رسالة واحدة فيها
    ⇒ تظهر هنا بمعرّفها الكامل. الأكثر موثوقية بلا أي إعداد إضافي.
    """
    ok, result = _call(token, "getUpdates", {"limit": limit, "allowed_updates": '["message","channel_post","my_chat_member"]'})
    if not ok:
        log.warning("getUpdates فشل: %s", result)
        return []

    found: dict[Any, ChatInfo] = {}
    for upd in result or []:
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            obj = upd.get(key) or {}
            chat = obj.get("chat") or {}
            cid = chat.get("id")
            if cid is None:
                continue
            found.setdefault(cid, ChatInfo(
                id=cid, title=chat.get("title") or chat.get("first_name", ""),
                type=chat.get("type", ""), username=chat.get("username", "")))
    return list(found.values())


def check_chat(token: str, chat_id: str | int) -> ChatInfo:
    """التحقّق الحقيقي: هل البوت يستطيع النشر في هذه المحادثة؟

    يفحص: وجود المحادثة · نوعها · كون البوت مشرفاً · صلاحية النشر.
    """
    ok, chat = _call(token, "getChat", {"chat_id": chat_id})
    if not ok:
        return ChatInfo(id=chat_id, error=f"getChat: {chat}")

    info = ChatInfo(id=chat.get("id", chat_id), title=chat.get("title", ""),
                    type=chat.get("type", ""), username=chat.get("username", ""))
    if info.type not in PUBLISHABLE:
        info.error = f"نوع المحادثة «{info.type}» لا يصلح للنشر"
        return info

    ok, me = _call(token, "getMe")
    bot_id = (me or {}).get("id") if ok else None
    if not bot_id:
        info.error = "تعذّر قراءة هوية البوت"
        return info

    ok, member = _call(token, "getChatMember",
                       {"chat_id": info.id, "user_id": bot_id})
    if not ok:
        info.error = f"getChatMember: {member}"
        return info

    status = (member or {}).get("status", "")
    info.is_admin = status in ("administrator", "creator")
    info.can_post = info.is_admin or (
        status == "member" and info.type in ("group", "supergroup")
        and bool((member or {}).get("can_send_messages", True)))
    if info.type == "channel" and not info.is_admin:
        info.error = "البوت ليس مشرفاً في القناة — أضفه مشرفاً ثم أعد المحاولة"
    elif not info.can_post:
        info.error = f"البوت حالته «{status}» ولا يملك صلاحية النشر"
    return info


def send_test(token: str, chat_id: str | int, *, text: str = "") -> tuple[bool, str]:
    """رسالة اختبار واحدة — الدليل القاطع أن النشر سينجح."""
    msg = text or ("✅ <b>AutoNews جاهز</b>\n"
                   "هذه رسالة اختبار: البوت يستطيع النشر في هذه القناة.")
    ok, result = _call(token, "sendMessage", {
        "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if ok:
        return True, str((result or {}).get("message_id", ""))
    return False, str(result)


# --------------------------------------------------------------------------- #
#  الإعدادات الجاهزة للنسخ
# --------------------------------------------------------------------------- #
def env_block(info: ChatInfo) -> str:
    """الأسطر الجاهزة للصق في GitHub Secrets أو ملف .env."""
    return "\n".join([
        f"TELEGRAM_BOT_TOKEN=<توكن البوت>",
        f"TELEGRAM_CHAT_ID={info.chat_id_env}",
        f"# الاسم: {info.title} · النوع: {info.type}",
        f"# الصيغة البديلة: {info.mention}",
    ])


def yml_snippet(info: ChatInfo) -> str:
    """مقتطف channels.yml لاستخدام المعرّف في قناة النشر."""
    cid = str(info.chat_id_env).lstrip("-")
    channel_id = "tg_" + (info.title or "channel")[:12].strip().replace(" ", "_")
    return (f"  - id: {channel_id}\n"
            f"    name: \"{info.title or 'القناة'}\"\n"
            f"    platform: telegram\n"
            f"    chat_id: \"{info.chat_id_env}\"   # ← معرّف حقيقي\n"
            f"    enabled: true\n")


def render_report(bot: dict[str, Any] | None, infos: list[ChatInfo]) -> str:
    """تقرير عربي مقروء (يُطبع في الطرفية)."""
    lines: list[str] = []
    if bot:
        lines.append(f"🤖 البوت: @{bot.get('username', '?')} "
                     f"({bot.get('first_name', '')}) — التوكن صالح ✅")
    else:
        lines.append("❌ التوكن غير صالح أو الشبكة محجوبة")
        return "\n".join(lines)

    if not infos:
        lines += ["", "⚠️ لم أجد أي محادثة.", "",
                  "السبب الغالب: لم تُرسل رسالة في القناة بعد إضافة البوت.",
                  "الحلّ: أضف البوت مشرفاً في القناة، ثم انشر أي رسالة فيها، ثم أعد الأمر."]
        return "\n".join(lines)

    lines += ["", f"🔎 عُثر على {len(infos)} محادثة:", ""]
    for i, info in enumerate(infos, 1):
        icon = "✅" if info.ok() else "❌"
        lines.append(f"{i}) {icon} {info.title or '(بلا اسم)'}")
        lines.append(f"    المعرّف : {info.chat_id_env}")
        lines.append(f"    النوع   : {info.type} · مشرف: {'نعم' if info.is_admin else 'لا'}")
        if info.username:
            lines.append(f"    البديل  : @{info.username}")
        if info.error:
            lines.append(f"    ⚠️ {info.error}")
        lines.append("")

    best = next((i for i in infos if i.ok()), None)
    if best:
        lines += ["─" * 52, "📋 انسخ هذين السطرين إلى GitHub Secrets:", "",
                  env_block(best), "", "─" * 52]
    else:
        lines += ["⚠️ لا توجد قناة جاهزة للنشر. تأكّد من إضافة البوت **مشرفاً**."]
    return "\n".join(lines)


def to_channel_handle(raw: str) -> str:
    """تحويل مُدخل المستخدم إلى ما يفهمه Telegram في `chat_id`.

    Telegram يعرض معرّف القناة في التطبيق كـ «6444047765»، لكن الـ API يحتاج
    الشكل الكامل «-1006444047765». كثيرون ينسخون الرقم القصير فيفشل الربط،
    فنُصلحه تلقائياً بدل أن نطلب من المستخدم فهم الفرق.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("-100"):
        return text
    digits = text.lstrip("-")
    if digits.isdigit() and len(digits) >= 9:
        return "-100" + digits
    return text
