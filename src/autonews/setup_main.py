"""أمر بوت الإعداد.

الاستخدام:
    python -m autonews.setup_main --panel      # أرسل لوحة الإعداد إلى تيليجرام
    python -m autonews.setup_main --poll       # عالج الرسائل/الأزرار المعلّقة
    python -m autonews.setup_main --status     # اطبع الحالة في الطرفية
    python -m autonews.setup_main --auto       # حلقة مستمرة (VPS)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "autonews"

from .setup_bot import CHANNEL_TITLES, BotConfig, SetupBot  # noqa: E402
from .setup_store import SetupStore  # noqa: E402
from .utils import get_logger, setup_logging  # noqa: E402

log = get_logger("autonews.setup_main")


def build_config() -> BotConfig:
    """الإعداد من متغيّرات البيئة (بلا أسرار في الملفات)."""
    users = os.environ.get("SETUP_ALLOWED_USERS", "").replace(" ", "")
    return BotConfig(
        token=(os.environ.get("TELEGRAM_BOT_TOKEN")
               or os.environ.get("ALERT_BOT_TOKEN") or "").strip(),
        allowed_users=tuple(int(u) for u in users.split(",") if u.isdigit()),
        admin_chat_id=(os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
                       or os.environ.get("ALERT_CHAT_ID")
                       or os.environ.get("TELEGRAM_CHAT_ID") or "").strip(),
        setup_file=os.environ.get("SETUP_FILE", "state/channels_setup.json"),
        timeout_seconds=int(os.environ.get("SETUP_TIMEOUT", "45")),
    )


def known_channels(config_dir: str = "config") -> list[str]:
    """القنوات المعروفة من channels.yml (بالترتيب المعروض في الأزرار)."""
    from .channels_main import load_channels

    ids = [c["id"] for c in load_channels(config_dir)
           if c.get("platform") == "telegram"]
    ordered = [c for c in CHANNEL_TITLES if c in ids]
    return ordered + [c for c in ids if c not in ordered]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autonews-setup",
                                description="بوت إعداد القنوات من تيليجرام")
    p.add_argument("--panel", action="store_true", help="أرسل لوحة الأزرار")
    p.add_argument("--poll", action="store_true", help="عالج الرسائل المعلّقة")
    p.add_argument("--rounds", type=int, default=1, help="عدد دورات السحب")
    p.add_argument("--wait", type=int, default=0, help="ثواني الانتظار لكل دورة")
    p.add_argument("--auto", action="store_true", help="حلقة مستمرة (VPS)")
    p.add_argument("--status", action="store_true", help="اطبع الحالة فقط")
    p.add_argument("--config", default="config")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    setup_logging(args.log_level)
    cfg = build_config()
    known = known_channels(args.config)
    store = SetupStore.load(cfg.setup_file)

    if args.status:
        print(store.summary([(c, CHANNEL_TITLES.get(c, c)) for c in known]))
        return 0

    if not cfg.token:
        log.error("❌ لا توكن. صدّر TELEGRAM_BOT_TOKEN.")
        return 2

    bot = SetupBot(cfg, known_channels=known, store=store)

    if args.panel:
        target = cfg.admin_chat_id
        if not target:
            log.error("❌ لا قناة إدارية. ضع TELEGRAM_ADMIN_CHAT_ID (أو TELEGRAM_CHAT_ID).")
            return 2
        ok = bot.show_panel(target)
        log.info("📤 لوحة الإعداد %s → %s", "أُرسلت" if ok else "فشلت", target)
        return 0 if ok else 1

    if args.auto:
        log.info("🔄 بوت الإعداد يعمل باستمرار (Ctrl+C للإيقاف)")
        while True:
            try:
                bot.poll(max_rounds=1, wait=25)
            except KeyboardInterrupt:
                return 0
            except Exception as exc:  # noqa: BLE001
                log.warning("خطأ في الحلقة: %s", exc)
                time.sleep(3)

    if args.poll:
        n = bot.poll(max_rounds=args.rounds, wait=args.wait)
        log.info("✅ عُولجت %d تحديثات", n)
        return 0

    # افتراضي: الحالة
    print(store.summary([(c, CHANNEL_TITLES.get(c, c)) for c in known]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
