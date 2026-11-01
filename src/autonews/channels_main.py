"""أداة القنوات: اعرض، أضف، وافحص كل قناة في مكان واحد.

المشكلة العملية: مع نمو النظام تصير القنوات متعدّدة (أخبار · أنمي · تقنية · رياضة).
يصير السؤال: **أي قناة تستقبل ماذا؟** وهل كل قناة جاهزة (توكن + معرّف + بوت مشرف)؟

هذه الأداة تجيب بلا فتح ملفات:

    python -m autonews.channels_main                    # جدول كل القنوات
    python -m autonews.channels_main --check            # + فحص الوصول الحقيقي لكل قناة
    python -m autonews.channels_main --add anime2       # إضافة قناة جديدة بالتفاعل
    python -m autonews.channels_main --matrix           # أي وسم يذهب لأي قناة
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "autonews"

import yaml  # noqa: E402

from .chatid import check_chat  # noqa: E402
from .utils import get_logger, setup_logging  # noqa: E402

log = get_logger("autonews.channels_main")

# القطاعات المعروفة ووسومها (للمصفوفة)
KNOWN_SECTORS = {
    "أخبار عامة": [],
    "أنمي": ["أنمي"],
    "تقنية": ["تقنية", "علوم"],
    "رياضة": ["رياضة"],
    "اقتصاد": ["اقتصاد", "دولار"],
    "ألعاب": ["ألعاب"],
}


# --------------------------------------------------------------------------- #
#  العرض
# --------------------------------------------------------------------------- #
def load_channels(config_dir: str = "config") -> list[dict[str, Any]]:
    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("channels") or []


def _source_of_secret(ch: dict[str, Any], key: str = "chat_id") -> str:
    """من أين يأتي معرّف القناة: extra أم credentials أم مفقود."""
    extra = (ch.get("extra") or {}).get(f"ENV_{key.upper()}")
    if extra:
        return f"extra ▸ {extra}"
    env_name = (ch.get("credentials") or {}).get(key, "")
    return f"env ▸ {env_name}" if env_name else "⚠️ مفقود"


def render_table(channels: list[dict[str, Any]], *, checks: dict[str, Any] | None = None) -> str:
    if not channels:
        return "لا قنوات في config/channels.yml"

    lines = [
        f"{'القناة':16} {'المنصّة':10} {'الحالة':10} {'الوسوم':22} {'المعرّف من':26} فوري",
        "─" * 96,
    ]
    for ch in channels:
        enabled = "✅ مفعّلة" if ch.get("enabled") else "⛔ معطّلة"
        tags = "، ".join(ch.get("tags") or []) or "— (كل شيء)"
        secret = _source_of_secret(ch) if ch.get("platform") == "telegram" else "—"
        flash = "⚡" if ch.get("flash_eligible") else ""
        state = enabled
        if checks is not None and ch.get("platform") == "telegram":
            info = checks.get(ch["id"])
            if info is not None:
                state = "✅ جاهزة" if info.ok() else "❌ غير جاهزة"
        lines.append(f"{ch['id']:16} {ch.get('platform',''):10} {state:10} "
                     f"{tags[:22]:22} {secret[:26]:26} {flash}")

    # تفاصيل مشاكل الوصول
    if checks:
        problems = [(cid, i) for cid, i in checks.items() if i is not None and not i.ok()]
        if problems:
            lines += ["", "🔧 مشاكل الوصول:"]
            for cid, info in problems:
                lines.append(f"  • {cid}: {info.error}")
                lines.append(f"    الحلّ: أضف البوت مشرفاً في القناة ثم:"
                             f" python -m autonews.chatid --chat {info.id} --test")
    return "\n".join(lines)


def render_matrix(channels: list[dict[str, Any]]) -> str:
    """أي قطاع يذهب إلى أي قناة؟ (كشف التداخل والفراغات)"""
    lines = ["🗺 مصفوفة التوجيه: أي وسم → أي قناة", "─" * 62]
    for sector, tags in KNOWN_SECTORS.items():
        hits = []
        sector_tags = {t.lower() for t in tags}
        for ch in channels:
            if not ch.get("enabled"):
                continue
            ch_tags = {t.lower() for t in (ch.get("tags") or [])}
            excl = {t.lower() for t in (ch.get("exclude_tags") or [])}
            # 🚫 الاستثناء يغلب العموم (وإلّا صار الجدول مضلّلاً)
            if sector_tags and sector_tags & excl:
                continue
            if not ch_tags:
                hits.append(f"{ch['id']} (كل شيء)")
            elif ch_tags & sector_tags:
                hits.append(ch["id"])
        mark = "✅" if hits else "⚠️ لا قناة"
        lines.append(f"{sector:14} → {mark} {('، '.join(hits) or '—')}")

    # تحذير مهم: قناة عامة تبتلع كل شيء
    general = [c["id"] for c in channels
               if c.get("enabled") and not (c.get("tags") or [])
               and not (c.get("exclude_tags") or [])]
    if general:
        lines += ["", f"⚠️ قنوات عامة (تستقبل كل شيء): {'، '.join(general)}",
                  "   نصيحة: إن أردت فصلاً كاملاً للقطاعات، أضف لها وسوماً صريحة."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  الإضافة
# --------------------------------------------------------------------------- #
CHANNEL_TEMPLATE = """
  # --------------------------------------------------------------- {sector_icon} {sector}
  - id: {channel_id}
    name: "{name}"
    platform: telegram
    enabled: {enabled}
    sources: ["*"]
    tags: {tags}                 # ← هذا ما يفصلها عن القنوات الأخرى
    style: >
      {style}
    language: ar
    max_chars: 4096
    add_hashtags: 4
    add_emoji: true
    include_source_link: true
    include_signature: false
    max_posts_per_run: 3
    with_image: true
    breaking_boost: false
    review_required: false
    flash_eligible: false
    flash_priority: 0
    credentials:
      bot_token: TELEGRAM_BOT_TOKEN
      chat_id: {secret_name}     # ← سرّ منفصل لهذه القناة
    extra:
      disable_web_page_preview: false
"""


def add_channel(config_dir: str, *, channel_id: str, name: str, tags: list[str],
                sector: str = "", style: str = "", enabled: bool = False,
                sector_icon: str = "📌") -> str:
    """إضافة قناة جديدة إلى channels.yml (إلحاق آمن + تعريف السرّ)."""
    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return f"⚠️ لم أجد {path}"
    text = path.read_text(encoding="utf-8")

    existing = {c.get("id") for c in load_channels(config_dir)}
    if channel_id in existing:
        return f"⚠️ القناة «{channel_id}» موجودة مسبقاً — لن أكرّرها"

    secret_name = f"TELEGRAM_{channel_id.upper().removeprefix('TG_')}_CHAT_ID"
    block = CHANNEL_TEMPLATE.format(
        sector=sector or channel_id, sector_icon=sector_icon, channel_id=channel_id,
        name=name or channel_id, enabled=str(enabled).lower(),
        tags="[" + ", ".join(f'"{t}"' for t in tags) + "]", style=style or
        "أسلوب متخصّص واضح، جمل قصيرة، بلا حشو. ابدأ بالمعلومة لا بالمقدّمة.",
        secret_name=secret_name,
    )
    path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
    return (f"✅ أُضيفت القناة «{channel_id}» إلى {path}\n"
            f"   ١) أضف السرّ في GitHub: {secret_name}\n"
            f"   ٢) اربطها: python -m autonews.chatid --chat @your_channel "
            f"--test --save {channel_id}\n"
            f"   ٣) فعّلها: enabled: true ثم ادفع")


# --------------------------------------------------------------------------- #
#  التنفيذ
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autonews-channels",
                                description="إدارة القنوات: عرض · فحص · إضافة")
    p.add_argument("--check", action="store_true", help="فحص وصول البوت لكل قناة تيليجرام")
    p.add_argument("--matrix", action="store_true", help="مصفوفة: أي قطاع → أي قناة")
    p.add_argument("--add", default="", metavar="ID", help="إضافة قناة جديدة بمعرّف")
    p.add_argument("--name", default="", help="اسم القناة (مع --add)")
    p.add_argument("--tags", default="", help="وسوم مفصولة بفاصلة (مع --add)")
    p.add_argument("--sector", default="", help="اسم القطاع (مع --add)")
    p.add_argument("--token", default="", help="توكن البوت للفحص")
    p.add_argument("--config", default="config")
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args(argv)

    setup_logging(args.log_level)

    # (أ) إضافة
    if args.add:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        print(add_channel(args.config, channel_id=args.add, name=args.name or args.add,
                          tags=tags, sector=args.sector))
        return 0

    channels = load_channels(args.config)
    checks = None
    if args.check:
        import os

        token = (args.token or os.environ.get("TELEGRAM_BOT_TOKEN")
                 or os.environ.get("ALERT_BOT_TOKEN") or "").strip()
        if not token:
            print("⚠️ لا توكن — مرّر --token أو صدّر TELEGRAM_BOT_TOKEN")
        else:
            checks = {}
            for ch in channels:
                if ch.get("platform") != "telegram":
                    continue
                cid = (ch.get("extra") or {}).get("ENV_CHAT_ID")
                if not cid:
                    env_name = (ch.get("credentials") or {}).get("chat_id", "")
                    cid = os.environ.get(env_name, "") if env_name else ""
                if not cid:
                    from .chatid import ChatInfo

                    checks[ch["id"]] = ChatInfo(id="?", error="لا معرّف مضبوط")
                    continue
                checks[ch["id"]] = check_chat(token, cid)

    print(render_table(channels, checks=checks))
    if args.matrix:
        print()
        print(render_matrix(channels))
    print()
    print("💡 أوامر مفيدة:")
    print("   python -m autonews.chatid --chat @your_channel --test --save tg_main")
    print("   python -m autonews.channels_main --add anime2 --name \"أنمي 2\" --tags أنمي")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
