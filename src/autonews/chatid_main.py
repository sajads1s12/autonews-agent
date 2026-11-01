"""أمر: اعرف معرّف القناة، تحقّق من وصول البوت، واحفظ الإعداد.

الاستخدام:
    # ١) افحص التوكن واكتشف المحادثات
    export TELEGRAM_BOT_TOKEN=123456:AA...
    python -m autonews.chatid

    # ٢) تحقّق من معرّف بعينه وأرسل رسالة اختبار
    python -m autonews.chatid --chat @my_channel --test

    # ٣) احفظ المعرّف في قناة معيّنة داخل channels.yml تلقائياً
    python -m autonews.chatid --chat @my_channel --save tg_main

    # ٤) اكتب الأسطر في ملف .env (محلياً فقط، وهو مُستثنى من git)
    python -m autonews.chatid --chat @my_channel --save-env
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "autonews"

from .chatid import (  # noqa: E402
    bot_info, check_chat, discover_chats, env_block, render_report, send_test,
)
from .utils import get_logger, setup_logging  # noqa: E402

log = get_logger("autonews.chatid_main")


# --------------------------------------------------------------------------- #
#  الحفظ
# --------------------------------------------------------------------------- #
def save_to_channels(channels_file: Path, channel_id: str, chat_ref: str) -> str:
    """حفظ المعرّف لتصبح القناة جاهزة للنشر في مكان واحد فقط.

    ملاحظة معمارية مهمّة: في هذا النظام السرّ يُقرأ من **متغيّر بيئة**
    (`credentials.chat_id: TELEGRAM_CHAT_ID`)، لا من قيمة مكتوبة في الملف.
    لذا «الحفظ» هنا يعني:

        ١) إضافة/تحديث `ENV_CHAT_ID` في بلوك `extra` (أعلى أولوية — يتجاوز البيئة)
        ٢) إبقاء ربط الاعتماد كما هو (السرّ لا يُكتب في المستودع أبداً)

    هذا التعديل جراحي: يمسّ بلوك القناة المطلوبة فقط.
    """
    if not channels_file.exists():
        return f"⚠️ لم أجد {channels_file}"
    text = channels_file.read_text(encoding="utf-8")

    pattern = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)-[ \t]*id:[ \t]*{re.escape(channel_id)}[ \t]*$"
        rf"(?P<body>(?:\n(?![ \t]*-[ \t]*id:).*)*)")
    m = pattern.search(text)
    if not m:
        return f"⚠️ لم أجد القناة «{channel_id}» في {channels_file}"

    body = m.group("body")
    quoted = f'"{chat_ref}"'

    # (١) استبدل سطر ENV_CHAT_ID إن وُجد
    if re.search(r"(?m)^([ \t]+)ENV_CHAT_ID:", body):
        new_body = re.sub(r"(?m)^([ \t]+)ENV_CHAT_ID:.*$",
                          rf"\1ENV_CHAT_ID: {quoted}", body, count=1)
    elif re.search(r"(?m)^([ \t]+)extra:[ \t]*$", body):
        # أضف السطر كأول عنصر تحت extra:
        new_body = re.sub(r"(?m)^([ \t]+)extra:[ \t]*$",
                          rf"\1extra:\n\1  ENV_CHAT_ID: {quoted}", body, count=1)
    else:
        # لا يوجد بلوك extra — أضفه في نهاية بلوك القناة
        new_body = body.rstrip("\n") + f"\n    extra:\n      ENV_CHAT_ID: {quoted}\n"

    updated = text[: m.start("body")] + new_body + text[m.end("body"):]
    channels_file.write_text(updated, encoding="utf-8")
    return (f"✅ حُفظ معرّف «{channel_id}» = {chat_ref}\n"
            f"   (أُضيف ENV_CHAT_ID في extra — يتجاوز متغيّر البيئة، والسرّ يبقى خارج المستودع)")


def save_to_env(env_file: Path, token: str, chat_id: str) -> str:
    """كتابة/تحديث TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID في .env (مُستثنى من git)."""
    lines: list[str] = []
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()

    def upsert(key: str, value: str) -> None:
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                return
        lines.append(f"{key}={value}")

    upsert("TELEGRAM_BOT_TOKEN", token)
    upsert("TELEGRAM_CHAT_ID", chat_id)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"✅ حُفظ في {env_file} (مُستثنى من git — لا يُرفع)"


# --------------------------------------------------------------------------- #
#  التنفيذ
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="autonews-chatid",
        description="اعرف معرّف قناتك، تحقّق من وصول البوت، واحفظ الإعداد")
    p.add_argument("--token", default="", help="توكن البوت (أو متغيّر TELEGRAM_BOT_TOKEN)")
    p.add_argument("--chat", default="", help="معرّف أو @username القناة للتحقّق")
    p.add_argument("--test", action="store_true", help="أرسل رسالة اختبار واحدة")
    p.add_argument("--save", default="", metavar="CHANNEL_ID",
                   help="احفظ المعرّف داخل config/channels.yml لهذه القناة")
    p.add_argument("--save-env", action="store_true", help="احفظ في ملف .env")
    p.add_argument("--config", default="config")
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args(argv)

    setup_logging(args.log_level)
    token = (args.token or os.environ.get("TELEGRAM_BOT_TOKEN")
             or os.environ.get("ALERT_BOT_TOKEN") or "").strip()
    if not token:
        print("❌ لا توكن. مرّر --token أو صدّر TELEGRAM_BOT_TOKEN.\n"
              "   التوكن تأخذه من @BotFather في تيليجرام.")
        return 2

    bot = bot_info(token)
    if not bot:
        print("❌ التوكن غير صالح — تأكّد من نسخه كاملاً من @BotFather.")
        return 1

    # (أ) تحقّق من معرّف محدّد
    if args.chat:
        info = check_chat(token, args.chat)
        print(render_report(bot, [info]))
        if args.test and info.ok():
            ok, detail = send_test(token, info.id)
            print("🧪 رسالة الاختبار: " + ("✅ وصلت (message_id=" + detail + ")"
                                          if ok else f"❌ فشلت — {detail}"))
            if not ok:
                return 1
        elif args.test:
            print("⏭ لم أُرسل اختباراً: القناة غير جاهزة.")
            return 1

        # (ب) الحفظ
        if args.save:
            if not info.ok():
                print("⛔ لن أحفظ معرّفاً غير صالح — أصلح الوصول أولاً.")
                return 1
            msg = save_to_channels(Path(args.config) / "channels.yml",
                                   args.save, str(info.id))
            print(msg)
            if not msg.startswith("✅"):
                return 1
        if args.save_env:
            if not info.ok():
                print("⛔ لن أحفظ معرّفاً غير صالح.")
                return 1
            print(save_to_env(Path(".env"), token, str(info.id)))
        if not (args.save or args.save_env):
            print("\n💡 للحفظ: أضف --save tg_main (داخل channels.yml) أو --save-env.")
        return 0 if info.ok() else 1

    # (ج) الاكتشاف التلقائي
    infos = discover_chats(token)
    # تحقّق معمّق لكل ما وُجد
    checked = [check_chat(token, i.id) for i in infos]
    print(render_report(bot, checked))

    best = next((i for i in checked if i.ok()), None)
    if best and args.test:
        ok, detail = send_test(token, best.id)
        print("🧪 رسالة الاختبار: " + ("✅ وصلت" if ok else f"❌ {detail}"))
    if best and args.save:
        print(save_to_channels(Path(args.config) / "channels.yml",
                               args.save, str(best.id)))
    if best and args.save_env:
        print(save_to_env(Path(".env"), token, str(best.id)))

    return 0 if best else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
