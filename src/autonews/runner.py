"""مشغّل مستمر (اختياري): يعيد الدورة كل N دقيقة بدل GitHub Actions.

متى تستخدمه؟
- على VPS / Docker / Raspberry Pi للعمل دون انتظار جدولة Actions.
- محلياً لمراقبة السلوك أثناء التطوير.

    python -m autonews.runner --every 120          # كل ساعتين
    python -m autonews.runner --every 60 --dry-run
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

from .config_loader import ConfigError, load_config
from .main import run_once
from .utils import get_logger, setup_logging, utcnow

log = get_logger("autonews.runner")


def loop(every_minutes: int, *, dry_run: bool, limit: int | None, config: str,
         flash_every_seconds: int = 0) -> int:
    if every_minutes < 5:
        log.warning("فاصل أقل من 5 دقائق غير مستحسن — تم رفعه إلى 5")
        every_minutes = 5
    cycle = 0
    while True:
        cycle += 1
        started = utcnow()
        log.info("🔁 الدورة #%d بدأت %s (UTC)", cycle, started.strftime("%Y-%m-%d %H:%M"))
        try:
            settings = load_config(config, override_dry_run=True if dry_run else None)
            run_once(settings, limit=limit)
            if flash_every_seconds > 0:
                from .flash_main import run_flash_cycle

                log.info("⚡ تشغيل دورة سريعة بعد الدورة الكاملة")
                run_flash_cycle(settings, verbose=False)
        except ConfigError as exc:
            log.error("خطأ إعدادات — سأكمل الدورة القادمة: %s", exc)
        except Exception as exc:  # noqa: BLE001 — المشغّل لا يتوقّف أبداً
            log.exception("خطأ في الدورة #%d: %s", cycle, exc)

        elapsed = (utcnow() - started).total_seconds()
        sleep_for = max(30, every_minutes * 60 - elapsed)
        nxt = datetime.fromtimestamp(time.time() + sleep_for).strftime("%H:%M")
        log.info("⏰ الدورة القادمة بعد %.0f دقيقة (حوالي %s محلياً)", sleep_for / 60, nxt)
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            log.info("أُوقف يدوياً")
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonews-runner", description="مشغّل دوري مستمر")
    parser.add_argument("--every", type=int, default=120, help="فاصل الدورة بالدقائق (افتراضي 120)")
    parser.add_argument("--dry-run", action="store_true", help="معاينة بلا نشر")
    parser.add_argument("--limit", type=int, default=0, help="حدّ المقالات لكل دورة")
    parser.add_argument("--flash-every", type=int, default=0,
                        help="تشغيل المسار السريع كل N ثانية بين الدورات (0 = بعد كل دورة فقط)")
    parser.add_argument("--config", default="config")
    args = parser.parse_args(argv)

    setup_logging("INFO")
    try:
        load_config(args.config)  # تحقّق مبكر من صحة الإعدادات
    except ConfigError as exc:
        log.error("❌ خطأ في الإعدادات: %s", exc)
        return 2
    return loop(args.every, dry_run=args.dry_run, limit=args.limit or None,
                config=args.config, flash_every_seconds=args.flash_every)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
