"""📊 الإحصاءات من الطرفية (نفس ما يعرضه البوت).

    python -m autonews.stats_main              # كل القنوات
    python -m autonews.stats_main --json       # للاستخدام في سكربت آخر
    python -m autonews.stats_main --advice     # توصيات فقط
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from .stats import build_stats
from .utils import setup_logging


def _known(config_dir: str) -> list[tuple[str, str]]:
    from .setup_main import known_channels
    from .setup_bot import channel_title

    return [(c, channel_title(c, config_dir=config_dir)) for c in known_channels(config_dir)]


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("LOG_LEVEL", "WARNING"))
    p = argparse.ArgumentParser(prog="autonews-stats", description="إحصاءات النشر")
    p.add_argument("--json", action="store_true", help="مخرجات JSON")
    p.add_argument("--advice", action="store_true", help="التوصيات فقط")
    p.add_argument("--config", default="config")
    p.add_argument("--state", default="state/state.json")
    p.add_argument("--control", default="state/control.json")
    args = p.parse_args(argv)

    report = build_stats(known=_known(args.config), state_file=args.state,
                         control_path=args.control)
    if args.json:
        print(json.dumps({**asdict(report), "advice": report.advice()},
                         ensure_ascii=False, indent=2))
        return 0
    if args.advice:
        advice = report.advice()
        print("\n".join(f"• {a}" for a in advice) if advice else "✅ لا ملاحظات — كل شيء سليم")
        return 0
    print(report.text().replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", ""))
    advice = report.advice()
    if advice:
        print("\n💡 توصيات:\n" + "\n".join(f"  • {a}" for a in advice))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
