"""🧠 أداة سطر الأوامر: التعرّف على قطاع قناة وضبط إعدادها.

الاستخدام:
    python -m autonews.sector_main --guess "أخبار الأنمي والمانغا"
    python -m autonews.sector_main --apply tg_anime "أخبار الأنمي"
    python -m autonews.sector_main --list
"""
from __future__ import annotations

import argparse
import sys

from .sectors import SECTORS, apply_to_channel, detect, score, sources_for
from .utils import get_logger

log = get_logger("autonews.sector_main")


def _sources() -> list[dict]:
    from .sectors import _load_sources

    return _load_sources("config")


def show_guess(name: str) -> int:
    profile = detect(name)
    print(f"\n🔍 الاسم: {name}")
    scores = score(name)
    if not scores:
        print("🤷 لم أتعرّف على قطاع — ستبقى القناة تستقبل كل الأخبار (آمن).")
        return 1
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:4]
    print("   الاحتمالات: " + " · ".join(
        f"{SECTORS[k].icon} {SECTORS[k].label}({v})" for k, v in ranked))
    if profile is None:
        print("   ⚠️ الدلالة ضعيفة — لن أخمّن.")
        return 1
    picked, why = sources_for(profile, _sources())
    print(f"\n✅ القطاع: {profile.icon} {profile.label}")
    print(f"   الوسوم   : {', '.join(profile.tags) or '—'}")
    print(f"   المصادر  : {len(picked) if picked != ['*'] else 'كل'} ({why})")
    if picked != ["*"]:
        print("   " + ", ".join(picked))
    return 0


def show_apply(channel_id: str, name: str) -> int:
    ok, msg = apply_to_channel(channel_id, name, override=True)
    print(("\n" if ok else "\n⚠️ ") + msg.replace("<b>", "").replace("</b>", ""))
    return 0 if ok else 1


def show_list() -> int:
    print(f"\n🧠 القطاعات المعروفة ({len(SECTORS)})\n" + "─" * 60)
    for profile in SECTORS.values():
        picked, _ = sources_for(profile, _sources())
        n = len(picked) if picked != ["*"] else "كل"
        print(f"{profile.icon} {profile.label:16} وسوم: {', '.join(profile.tags) or '—':22} مصادر: {n}")
        print(f"   كلمات: {', '.join(profile.keywords[:6])}…")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m autonews.sector_main",
        description="التعرّف على قطاع القناة من اسمها وضبط الوسوم والمصادر.")
    parser.add_argument("--guess", metavar="NAME", help="اعرض القطاع المتوقّع لاسم")
    parser.add_argument("--apply", nargs=2, metavar=("CHANNEL", "NAME"),
                        help="اضبط القناة من اسمها (وسوم + مصادر)")
    parser.add_argument("--list", action="store_true", help="كل القطاعات المعروفة")
    args = parser.parse_args(argv)

    if args.list:
        return show_list()
    if args.guess:
        return show_guess(args.guess)
    if args.apply:
        return show_apply(args.apply[0], args.apply[1])
    parser.print_help()
    return 0


if __name__ == "__main__":              # pragma: no cover
    sys.exit(main())
