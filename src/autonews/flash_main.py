"""نقطة دخول المسار السريع — دورة قصيرة جداً للأخبار العاجلة فقط.

الاستخدام:
    python -m autonews.flash_main                 # دورة واحدة سريعة (~10-20ث)
    python -m autonews.flash_main --dry-run       # معاينة بلا نشر
    python -m autonews.flash_main --interval 60   # تشغيل مستمر كل 60 ثانية (VPS/Docker)

الفرق عن `python -m autonews.main`:
    main       : الدورة الكاملة (سحب كل المصادر، صياغة، جودة، صور، تقارير) — كل ساعتين.
    flash_main : فقط «هل ظهر عاجل في آخر 45 دقيقة؟» — كل 5 دقائق (أو 30 ثانية على VPS).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "autonews"

from .ai import ContentWriter, KeyRotator  # noqa: E402
from .config_loader import ConfigError, Settings, load_config  # noqa: E402
from .flash import FlashLane, ScopedPublisher, classify, is_fresh  # noqa: E402
from .ingest import fetch_all_with_status  # noqa: E402
from .notify import Notifier  # noqa: E402
from .occasions import OccasionCalendar  # noqa: E402
from .state import StateStore  # noqa: E402
from .utils import get_logger, setup_logging, truncate  # noqa: E402

log = get_logger("autonews.flash_main")


def build_engine(settings: Settings) -> KeyRotator | None:
    if not settings.ai.keys:
        log.warning("⚠️ لا مفاتيح Gemini — المسار السريع سيعمل بالقالب الفوري")
        return None
    return KeyRotator(
        keys=settings.ai.keys,
        models=settings.ai.models,
        cooldown_quota=settings.ai.cooldown_quota_seconds,
        cooldown_error=settings.ai.cooldown_error_seconds,
        request_timeout=min(30, settings.ai.request_timeout),   # مهلة قصيرة = سرعة
        max_retries_per_pair=0,                                 # لا إعادة محاولة: ننتقل فوراً
        health_path="state/ai_health.json",
    )


def run_flash_cycle(settings: Settings, *, verbose: bool = True) -> int:
    """دورة سريعة واحدة. تعيد عدد المنشورات الناجحة."""
    store = StateStore(settings.state_file)
    state = store.load()

    from .router import register_sources

    register_sources(settings.sources)
    engine = build_engine(settings)
    writer = ContentWriter(engine, settings, state)
    calendar = (OccasionCalendar.from_config(settings.occasions.config_file)
                if settings.occasions.enabled else OccasionCalendar([]))
    notifier = Notifier(settings.alerts, dry_run=settings.dry_run) \
        if settings.flash.notify_telegram else None

    # 1) النشر الفوري اليدوي من قناة الوكالة (إن كانت مهيّأة)
    scoped_sent: list[str] = []
    if settings.flash.scoped_chat_id and settings.alerts.bot_token:
        scoped = ScopedPublisher(
            bot_token=settings.alerts.bot_token,
            scoped_chat_id=settings.flash.scoped_chat_id,
            target_channels=settings.channels, settings=settings, state=state,
            writer=writer, dry_run=settings.dry_run,
        )
        scoped_sent = scoped.flush(max_messages=3, max_age_minutes=90)

    # 2) سحب سريع للمصادر
    t0 = time.time()
    articles, failed = fetch_all_with_status(settings)
    fetch_s = time.time() - t0

    # 3) تصنيف مختصر (للتقرير)
    verdicts = [(a, classify(a, settings.flash, breaking=settings.breaking)) for a in articles]
    urgent = [(a, v) for a, v in verdicts if v.urgent]
    fresh_urgent = [(a, v) for a, v in urgent if is_fresh(a, settings.flash)[0]]

    if verbose:
        log.info("سحب %d مقالاً في %.1fث | عاجل مرشّح=%d | طازج=%d",
                 len(articles), fetch_s, len(urgent), len(fresh_urgent))
        for a, v in urgent[:6]:
            ok, minutes, why = is_fresh(a, settings.flash)
            tag = "✅" if ok else "⏳"
            log.info("  %s %s [%s] %.0fد | %s", tag, v.label(), v.category,
                     max(0, minutes), truncate(a.title, 65))

    # 4) النشر الفوري
    lane = FlashLane(settings, state, engine=engine, writer=writer,
                     calendar=calendar, notifier=notifier)
    result = lane.run(articles)

    state.begin_run()
    state.finish_run(result.published + len(scoped_sent))
    store.save(state)

    for line in result.details:
        log.info("  • %s", line)
    for line in scoped_sent:
        log.info("  📲 %s", line)

    log.info("⚡ انتهت الدورة السريعة | %s | مدة كلية=%.1fث",
             result.summary(), time.time() - t0)
    return result.published + len(scoped_sent)


def loop(settings_factory, interval: int, *, dry_run: bool) -> int:
    """تشغيل مستمر بفاصل قصير — الأسرع فعلياً (VPS/Docker)."""
    cycle = 0
    while True:
        cycle += 1
        try:
            settings = settings_factory()
            run_flash_cycle(settings)
        except Exception as exc:  # noqa: BLE001 — المشغّل لا يتوقّف
            log.exception("خطأ في الدورة السريعة #%d: %s", cycle, exc)
        sleep_for = max(15, interval)
        log.info("⏱ الدورة القادمة بعد %d ثانية", sleep_for)
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            log.info("أُوقف يدوياً")
            return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autonews-flash",
                                description="المسار السريع للأخبار العاجلة")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--interval", type=int, default=0,
                   help="تشغيل مستمر كل N ثانية (0 = دورة واحدة)")
    p.add_argument("--config", default="config")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    def factory() -> Settings:
        s = load_config(args.config, override_dry_run=True if args.dry_run else None)
        return s

    try:
        settings = factory()
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("❌ خطأ في الإعدادات: %s", exc)
        return 2

    setup_logging(args.log_level or settings.log_level)
    if not settings.flash.enabled:
        log.warning("المسار السريع معطّل في الإعدادات (flash.enabled=false)")
        return 0

    if args.interval > 0:
        return loop(factory, args.interval, dry_run=args.dry_run)
    published = run_flash_cycle(settings)
    return 0 if published >= 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
