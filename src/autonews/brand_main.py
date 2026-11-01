"""أداة البصمة — إعدادها، معاينتها، وإثبات الملكية.

    python -m autonews.brand_main --show                    # بصمات كل القنوات
    python -m autonews.brand_main --set tg_main --handle my_channel
    python -m autonews.brand_main --preview tg_main         # نموذج منشور + صورة
    python -m autonews.brand_main --prove "نصّ منشور مسروق"  # لمن تعود هذه العلامة؟
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .branding import Branding, apply_footer, hidden_encode, prove_ownership, stamp_image
from .config_loader import load_config
from .utils import setup_logging, write_branding


def show_brandings(config_dir: str = "config") -> int:
    settings = load_config(config_dir)
    print("\n🖋 بصمات الملكية\n" + "─" * 62)
    print(f"{'القناة':<16}{'المعرّف':<18}{'توقيع النصّ':<28}{'الصورة':<8}{'خفية'}")
    for ch in settings.channels:
        b = Branding.from_channel(ch)
        footer = b.footer_for(ch.platform)[:26] or "—"
        print(f"{ch.id:<16}{('@' + b.handle) if b.handle else '—':<18}"
              f"{footer:<28}{'✅' if b.stamp_on_image else '⛔':<8}"
              f"{'✅' if b.hidden_mark else '⛔'}")
    missing = [c.id for c in settings.channels if not Branding.from_channel(c).handle]
    if missing:
        print(f"\n💡 بلا معرّف ({len(missing)}): {'، '.join(missing)}")
        print("   اضبطها من البوت: زر «🖋 البصمة» — أو:")
        print(f"   python -m autonews.brand_main --set {missing[0]} --handle @your_channel")
    return 0


def set_branding(channel_id: str, handle: str, config_dir: str = "config") -> int:
    ok, msg = write_branding(channel_id, handle=handle.lstrip("@"), config_dir=config_dir)
    print(("✅ " if ok else "❌ ") + msg)
    return 0 if ok else 2


def preview(channel_id: str, config_dir: str = "config") -> int:
    """نموذج كامل: نصّ مختوم + صورة موسومة (تُحفظ في مجلد العمل للمعاينة)."""
    settings = load_config(config_dir)
    channel = next((c for c in settings.channels if c.id == channel_id), None)
    if channel is None:
        print(f"❌ لا توجد قناة «{channel_id}»")
        return 2
    brand = Branding.from_channel(channel)
    if not brand.ready:
        print("⚠️ لا معرّف بعد — اضبطه أولاً: --handle @your_channel")
        return 2

    sample = ("خبر تجريبي: انطلاق مشروع الخدمات في بغداد\n\n"
              "أعلنت الجهات المختصّة بدء العمل في 12 موقعاً، مع جدول زمني يمتدّ 90 يوماً.")
    text = apply_footer(sample, brand, platform=channel.platform, max_chars=channel.max_chars)
    text = hidden_encode(text, brand.payload())
    print("\n" + "═" * 62)
    print(text)
    print("═" * 62)
    ok, msg = prove_ownership(text, expect=brand.payload())
    print(f"🕵️ العلامة الخفيّة: {'✅ ' if ok else '⚠️ '}{msg}")

    # صورة نموذجية
    try:
        from PIL import Image  # noqa: PLC0415

        tmp = Path(f"./brand_preview_{channel_id}.png")
        Image.new("RGB", (1200, 640), (26, 34, 54)).save(tmp)
        if brand.stamp_on_image:
            stamped = stamp_image(str(tmp), brand)
            out = Path(f"./brand_preview_{channel_id}.jpg")
            if stamped and stamped != str(tmp):
                import shutil

                shutil.move(stamped, out)      # /tmp قد يكون على قرص آخر
            print(f"🖼 صورة موسومة: {out.resolve()}")
        tmp.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ تعذّرت معاينة الصورة: {exc}")
    return 0


def prove(text: str) -> int:
    ok, msg = prove_ownership(text)
    print(("✅ " if ok else "❌ ") + msg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(prog="autonews-brand",
                                     description="بصمة الملكية: توقيع + علامة مائية + إثبات")
    parser.add_argument("--show", action="store_true", help="عرض بصمات كل القنوات")
    parser.add_argument("--set", metavar="CHANNEL", help="ضبط قناة (مع --handle)")
    parser.add_argument("--handle", default="", help="@اسم_القناة")
    parser.add_argument("--preview", metavar="CHANNEL", help="نموذج منشور + صورة")
    parser.add_argument("--prove", metavar="TEXT", help="إثبات ملكية نصّ")
    parser.add_argument("--config", default="config")
    args = parser.parse_args(argv)

    if args.set:
        if not args.handle:
            parser.error("--set يتطلّب --handle")
        return set_branding(args.set, args.handle, args.config)
    if args.preview:
        return preview(args.preview, args.config)
    if args.prove is not None:
        return prove(args.prove)
    return show_brandings(args.config)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
