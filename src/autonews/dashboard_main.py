"""أداة اللوحة: بناء `logs/dashboard.html` و«أفضل أسبوع» من الحالة وحدها.

الاستخدام:
    python -m autonews.dashboard_main                # آخر 7 أيام
    python -m autonews.dashboard_main --days 30      # آخر شهر
    python -m autonews.dashboard_main --open         # يطبع المسار لفتحه

لا تصل إلى الشبكة ولا تحتاج مفاتيح — تقرأ state/state.json فقط.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "autonews"

from .config_loader import ConfigError, load_config  # noqa: E402
from .dashboard import collect, write_reports  # noqa: E402
from .state import StateStore  # noqa: E402
from .utils import get_logger, setup_logging  # noqa: E402

log = get_logger("autonews.dashboard_main")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autonews-dashboard",
                                description="لوحة أداء HTML + أفضل أسبوع")
    p.add_argument("--days", type=int, default=0, help="عدد الأيام (0 = من الإعدادات)")
    p.add_argument("--top", type=int, default=0, help="عدد أفضل المنشورات")
    p.add_argument("--config", default="config")
    p.add_argument("--out", default="", help="مسار ملف HTML")
    p.add_argument("--best", default="", help="مسار ملف «أفضل أسبوع»")
    p.add_argument("--dry-run", action="store_true", help="طباعة الملخّص بلا كتابة")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    try:
        settings = load_config(args.config)
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("❌ خطأ في الإعدادات: %s", exc)
        return 2
    setup_logging(args.log_level or settings.log_level)

    store = StateStore(settings.state_file)
    state = store.load()
    days = args.days or settings.dashboard.days
    top_n = args.top or settings.dashboard.top_n

    snap = collect(state, days=days, top_n=top_n)
    if args.dry_run:
        print(f"\n📊 ملخّص آخر {days} أيام")
        print(f"  منشورات: {snap.total_posts}")
        print(f"  الأنواع: {dict(snap.by_kind)}")
        print(f"  المنصّات: {dict(snap.by_platform)}")
        print(f"  أكثر المصادر: {snap.top_sources[:5]}")
        print(f"  ساعات بلا نشر: {snap.quiet_hours[:8] or 'لا يوجد'}")
        print(f"  نتائج: {snap.outcome_share * 100:.0f}% من المنشورات\n")
        return 0

    out, best = write_reports(
        state,
        output_file=args.out or settings.dashboard.output_file,
        best_of_file=args.best or settings.dashboard.best_of_file,
        days=days, top_n=top_n,
    )
    print(f"\n✅ تمّ:\n  📊 {out}\n  ⭐ {best}\n  🗂  {out.with_suffix('.json')}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
