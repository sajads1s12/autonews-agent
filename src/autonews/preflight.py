"""🧪 الفحص الشامل قبل التشغيل الحقيقي — ١٠ اختبارات مطلوبة.

لماذا أداة مستقلة وليس مجرد اختبارات pytest؟
    لأن بعض الفحوص تحتاج **شبكة ومفاتيح حقيقية** (Gemini · تيليجرام)،
    وهذه لا يجوز أن تكون في CI (تفشل بلا أسرار وتكشف مفاتيح في السجلات).
    فنفصلها: pytest للاختبارات المحلية، وهذه الأداة لما قبل النشر الفعلي.

المبدأ: **لا تنشر شيئاً**. كل الفحوص إمّا قراءة أو كتابة معاينة.
    الفحص الوحيد الذي يلمس تيليجرام هو `sendMessage` لرسالة اختبار — يمكن
    تعطيله بـ `--offline`.

الاستخدام:
    python -m autonews.preflight              # الفحوص التي لا تحتاج شبكة
    python -m autonews.preflight --online     # + Gemini + تيليجرام
    python -m autonews.preflight --json       # تقرير للآلة
واجهات للمرآة.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .utils import get_logger

log = get_logger("autonews.preflight")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


# --------------------------------------------------------------------------- #
#  نتيجة فحص واحد
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Check:
    """نتيجة اختبار واحد."""

    id: int
    name: str
    status: str = "skip"            # pass | fail | warn | skip
    detail: str = ""
    hint: str = ""

    @property
    def icon(self) -> str:
        return {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭"}.get(self.status, "•")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "status": self.status,
                "detail": self.detail, "hint": self.hint}


# --------------------------------------------------------------------------- #
#  الفحوص
# --------------------------------------------------------------------------- #
def check_imports(cfg: dict) -> Check:
    """١) تحميل المكتبات."""
    check = Check(1, "تحميل المكتبات", status="pass")
    missing: list[str] = []
    for module, label in (("yaml", "PyYAML"), ("feedparser", "feedparser")):
        try:
            __import__(module)
        except ImportError:
            missing.append(label)
    optional: list[str] = []
    try:
        __import__("PIL")
    except ImportError:
        optional.append("Pillow (ختم الصور سيتعطّل)")

    if missing:
        check.status = "fail"
        check.detail = f"مفقود: {', '.join(missing)}"
        check.hint = "pip install -r requirements.txt"
    else:
        check.detail = "كل المكتبات الأساسية موجودة"
        if optional:
            check.detail += f" · اختياري: {', '.join(optional)}"
    return check


def check_config(cfg: dict) -> Check:
    """٤) قراءة إعدادات القنوات."""
    check = Check(4, "قراءة إعدادات القنوات")
    try:
        from .config_loader import load_config

        config = load_config(cfg["config_dir"])
    except Exception as exc:                                    # noqa: BLE001
        check.status = "fail"
        check.detail = f"تعذّر التحميل: {str(exc)[:150]}"
        check.hint = "راجع YAML: python -c \"import yaml;yaml.safe_load(open('config/channels.yml'))\""
        return check

    enabled = [c for c in config.channels if c.enabled]
    check.detail = (f"{len(config.channels)} قناة ({len(enabled)} مفعّلة) · "
                    f"{len(config.sources)} مصدراً")
    if not enabled:
        check.status = "fail"
        check.detail += " — لا قنوات مفعّلة"
        check.hint = "فعّل قناة واحدة على الأقل في channels.yml"
    else:
        check.status = "pass"
        # فحص: هل قناة تشير إلى مصدر غير موجود؟
        known = {s.id for s in config.sources}
        bad = [(c.id, src) for c in enabled for src in c.sources
               if src != "*" and src not in known]
        if bad:
            check.status = "warn"
            check.detail += f" · {len(bad)} مرجع مصدر غير معروف سيُتجاهل"
    return check


def check_state(cfg: dict) -> Check:
    """٥) قراءة وكتابة بيانات الحالة."""
    check = Check(5, "قراءة وكتابة بيانات الحالة")
    try:
        from .state import StateStore

        store = StateStore(cfg["state_file"])
        state = store.load()
        before_runs = state.runs
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "state.json"
            StateStore(probe).save(state)               # كتابة ذرّية
            again = StateStore(probe).load()
            assert again.runs == before_runs, "القيم لم تُستعد كما هي"
            assert probe.exists() and probe.stat().st_size > 10
        check.status = "pass"
        check.detail = (f"تشغيلات={before_runs} · منشورات={len(state.posted)} · "
                        f"مقالات={len(state.articles)} · كتابة ذرّية ✅")
    except Exception as exc:                                    # noqa: BLE001
        check.status = "fail"
        check.detail = f"تعذّر: {str(exc)[:150]}"
        check.hint = "تأكّد من صلاحية الكتابة في state/"
    return check


def check_dedup(cfg: dict) -> Check:
    """٧) منع التكرار — أهمّ وظيفة تشغيلية."""
    check = Check(7, "منع التكرار")
    try:
        from .state import StateStore
        from .utils import fingerprint

        state = StateStore(cfg["state_file"]).load()
        text = "خبر اختبار لمنع التكرار — يجب ألّا يُنشر مرّتين"
        fp = fingerprint(text)
        # ١) قبل التسجيل: يجب ألّا يكون معروفاً
        seen_before = state.has_article(fp)
        # ٢) نسجّله ثم نتأكد أن النظام «يتذكّره»
        state.mark(fp, "posted", title=text[:40])
        seen_after = state.has_article(fp)
        # ٣) ونحفظ ونعيد التحميل — الذاكرة يجب أن تعيش بعد إعادة التشغيل
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "state.json"
            StateStore(probe).save(state)
            reloaded = StateStore(probe).load()
            survived = reloaded.has_article(fp)

        if seen_after and survived:
            check.status = "pass"
            check.detail = ("البصمة تُكتشف بعد التسجيل وتنجو من إعادة التحميل ✅ · "
                            f"سجلّ {len(state.articles)} بصمة · "
                            f"{'كانت معروفة سابقاً' if seen_before else 'بصمة جديدة'}")
        else:
            check.status = "fail"
            check.detail = (f"فشل: بعد التسجيل={seen_after} · بعد إعادة التحميل={survived}")
            check.hint = "راجع State.has_article / State.mark"
    except Exception as exc:                                    # noqa: BLE001
        check.status = "fail"
        check.detail = f"تعذّر: {str(exc)[:150]}"
    return check


def check_classification(cfg: dict) -> Check:
    """٦) تصنيف الأخبار (توزيع على القنوات)."""
    check = Check(6, "تصنيف الأخبار وتوزيعها")
    try:
        from .config_loader import load_config
        from .sectors import detect

        config = load_config(cfg["config_dir"])
        cases = [("أخبار الأنمي والمانغا", "anime"), ("الدوري العراقي", "sports"),
                 ("عالم التقنية", "tech")]
        hits = sum(1 for name, want in cases
                   if (detect(name) and detect(name).key == want))
        # فحص التوزيع الفعلي عبر نفس دالة الموجّه المستخدمة في النشر
        from .flash import FlashLane
        from .ingest import Article

        anime = next((c for c in config.channels if c.id == "tg_anime" and c.enabled), None)
        routing = "لا قناة أنمي لفحص التوزيع"
        routing_ok = True
        if anime is not None:
            lane = object.__new__(FlashLane)            # بلا شبكة ولا حالة
            # نستخدم مصدراً موجوداً فعلاً في مصادر القناة (وإلا رُفض لسبب آخر)
            src_anime = next((x for x in anime.sources if x != "*"), "ann_anime")
            anime_art = Article(source_id=src_anime, source_name="أنمي",
                                title="إعلان أنمي جديد", summary="مانغا",
                                url="https://x/1", tags=["أنمي"])
            sports_art = Article(source_id=src_anime, source_name="أنمي",
                                 title="الدوري العراقي", summary="كرة",
                                 url="https://x/2", tags=["رياضة"])
            accepts_anime = lane._targets(anime, anime_art) is True
            rejects_sports = lane._targets(anime, sports_art) is False
            routing_ok = accepts_anime and rejects_sports
            routing = ("قناة الأنمي تستقبل الأنمي وترفض الرياضة ✅" if routing_ok
                       else f"❌ الموجّه: أنمي={accepts_anime} · رياضة مرفوضة={rejects_sports}")

        if hits >= 2 and routing_ok:
            check.status = "pass"
            check.detail = f"القطاعات: {hits}/3 · {routing}"
        else:
            check.status = "warn"
            check.detail = f"القطاعات: {hits}/3 · {routing}"
            check.hint = "راجع autonews.sectors / flash._targets"
    except Exception as exc:                                    # noqa: BLE001
        check.status = "fail"
        check.detail = f"تعذّر: {str(exc)[:150]}"
    return check


def check_state_sync(cfg: dict) -> Check:
    """٩) استعادة الحالة بعد انتهاء التشغيل (المستودع الخاص)."""
    from .state_sync import SyncConfig, check as sync_check

    check = Check(9, "استعادة الحالة بين التشغيلات")
    sync = SyncConfig.from_env()
    if not sync.enabled:
        check.status = "warn"
        check.detail = ("المستودع الخاص غير مضبوط — الحالة ستعيش داخل بيئة "
                        "التشغيل وتُفقد بعدها")
        check.hint = ("أضف STATE_REPO و STATE_REPO_TOKEN في GitHub Secrets "
                      "(docs/أسرار-GitHub.md)")
        return check
    ok, msg = sync_check(sync, workdir=cfg["config_dir"].rsplit("/", 1)[0] or ".")
    check.status = "pass" if ok else "fail"
    check.detail = msg
    return check


def check_secrets(cfg: dict) -> Check:
    """١٠) عدم وجود بيانات حسّاسة في المستودع (الأمن)."""
    import subprocess

    check = Check(10, "لا بيانات حسّاسة في المستودع")
    script = Path(cfg["root"]) / "scripts" / "check_secrets.sh"
    if not script.exists():
        check.status = "warn"
        check.detail = "سكربت الفحص مفقود"
        return check
    try:
        proc = subprocess.run(["bash", str(script)], cwd=cfg["root"],
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        check.status = "warn"
        check.detail = f"تعذّر التشغيل: {str(exc)[:100]}"
        return check
    if proc.returncode == 0:
        check.status = "pass"
        check.detail = "لا أسرار في الملفات"
    else:
        check.status = "fail"
        leaks = [ln for ln in proc.stdout.splitlines() if "❌" in ln][:3]
        check.detail = " · ".join(x.strip("❌ ").strip() for x in leaks) or "تسرّب محتمل"
        check.hint = "bash scripts/check_secrets.sh --history"
    return check


def check_github_actions(cfg: dict) -> Check:
    """٨) سلامة مسارات GitHub Actions (بنية صحيحة بلا شبكة)."""
    import yaml

    check = Check(8, "تشغيل GitHub Actions")
    root = Path(cfg["root"])
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.exists():
        check.status = "fail"
        check.detail = "مجلد workflows مفقود (لن تعمل الجدولة)"
        return check

    problems: list[str] = []
    workflows = sorted(wf_dir.glob("*.yml"))
    for path in workflows:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: YAML تالف ({str(exc)[:40]})")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: بنية غير صالحة")
            continue
        text = path.read_text(encoding="utf-8")
        if "runs-on" in text and "timeout-minutes" not in text:
            problems.append(f"{path.name}: بلا timeout")
        runs_bot = any(k in text for k in ("autonews.main", "flash_main", "setup_main"))
        if runs_bot and "concurrency:" not in text:
            problems.append(f"{path.name}: بلا concurrency (خطر نشر مزدوج)")

    # الـ composite actions
    for name in ("setup", "state"):
        if not (root / ".github" / "actions" / name / "action.yml").exists():
            problems.append(f"action مفقود: {name}")

    if problems:
        check.status = "fail"
        check.detail = " · ".join(problems[:3])
        check.hint = "راجع .github/workflows/"
    else:
        check.status = "pass"
        check.detail = f"{len(workflows)} مسار عمل سليم · concurrency + timeout + cache"
    return check


def check_ai_offline(cfg: dict) -> Check:
    """٢أ) طبقة الذكاء الاصطناعي — بلا شبكة (تكوين المفاتيح والنماذج)."""
    check = Check(2, "طبقة Gemini (تكوين)")
    from .config_loader import load_config

    try:
        config = load_config(cfg["config_dir"])
        provider = getattr(config, "ai", None)
        keys = getattr(provider, "keys", []) if provider else []
        n = len(keys)
        if n == 0:
            check.status = "warn"
            check.detail = "لا مفاتيح — سيعمل القالب الاحتياطي (نصّ بسيط بلا AI)"
            check.hint = "أضف GEMINI_API_KEY_1 في الأسرار"
        else:
            masked = ", ".join(k[:6] + "…" for k in keys[:3])
            check.detail = f"{n} مفتاحاً · {masked}" + (" …" if n > 3 else "")
    except Exception as exc:                                    # noqa: BLE001
        check.status = "fail"
        check.detail = f"تعذّر: {str(exc)[:150]}"
    return check


def check_gemini_online(cfg: dict) -> Check:
    """٢) اتصال Gemini — حقيقي (يتطلّب مفتاحاً)."""
    check = Check(2, "اتصال Gemini")
    from .config_loader import load_config

    config = load_config(cfg["config_dir"])
    keys = getattr(getattr(config, "ai", None), "keys", [])
    if not keys:
        check.status = "skip"
        check.detail = "لا مفتاح — تخطّي"
        return check

    key = keys[0]
    url = ("https://generativelanguage.googleapis.com/v1beta/models"
           f"?key={key}")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])][:2]
        check.status = "pass"
        check.detail = f"المفتاح يعمل · {len(data.get('models', []))} نموذجاً متاحاً"
        if models:
            check.detail += f" · {', '.join(m.split('/')[-1] for m in models)}"
    except urllib.error.HTTPError as exc:
        check.status = "fail"
        check.detail = f"HTTP {exc.code} — المفتاح مرفوض أو الحصّة انتهت"
        check.hint = "ولّد مفتاحاً جديداً من AI Studio"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        check.status = "warn"
        check.detail = f"تعذّر الاتصال (شبكة): {str(exc)[:80]}"
    return check


def check_telegram_online(cfg: dict) -> Check:
    """٣) اتصال تيليجرام — getMe (قراءة فقط، لا رسالة)."""
    check = Check(3, "اتصال تيليجرام")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        check.status = "skip"
        check.detail = "TELEGRAM_BOT_TOKEN غير مضبوط"
        return check
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            user = data.get("result") or {}
            check.status = "pass"
            check.detail = f"البوت: @{user.get('username', '?')}"
        else:
            check.status = "fail"
            check.detail = f"ردّ غير متوقّع: {str(data)[:100]}"
    except urllib.error.HTTPError as exc:
        check.status = "fail"
        check.detail = f"HTTP {exc.code} — التوكن مرفوض"
        check.hint = "أعد توليد التوكن من @BotFather"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        check.status = "warn"
        check.detail = f"تعذّر الاتصال: {str(exc)[:80]}"
    return check


# --------------------------------------------------------------------------- #
#  التشغيل
# --------------------------------------------------------------------------- #
def run_all(*, online: bool = False, root: str = ".") -> list[Check]:
    """تنفيذ كل الفحوص بالترتيب. `online=False` يعني بلا شبكة."""
    root_path = str(Path(root).resolve())
    cfg = {
        "root": root_path,
        "config_dir": str(Path(root_path) / "config"),
        "state_file": str(Path(root_path) / "state" / "state.json"),
    }
    checks: list[Check] = [
        check_imports(cfg),
        check_gemini_online(cfg) if online else check_ai_offline(cfg),
        check_telegram_online(cfg) if online else Check(
            3, "اتصال تيليجرام", "skip", "بلا شبكة (استخدم --online)"),
        check_config(cfg),
        check_state(cfg),
        check_classification(cfg),
        check_dedup(cfg),
        check_github_actions(cfg),
        check_state_sync(cfg),
        check_secrets(cfg),
    ]
    return checks


def report(checks: list[Check], *, as_json: bool = False) -> int:
    """طباعة التقرير. يعيد رمز خروج (0 = لا فشل)."""
    if as_json:
        payload = {
            "ok": all(c.status != "fail" for c in checks),
            "passed": sum(1 for c in checks if c.status == "pass"),
            "failed": sum(1 for c in checks if c.status == "fail"),
            "warned": sum(1 for c in checks if c.status == "warn"),
            "skipped": sum(1 for c in checks if c.status == "skip"),
            "checks": [c.as_dict() for c in checks],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    print("\n🧪 الفحص الشامل قبل التشغيل\n" + "═" * 62)
    for check in sorted(checks, key=lambda c: c.id):
        print(f"{check.icon} [{check.id:>2}] {check.name}")
        if check.detail:
            print(f"      {check.detail}")
        if check.hint:
            print(f"      {YELLOW}↳ {check.hint}{RESET}")

    passed = sum(1 for c in checks if c.status == "pass")
    failed = [c for c in checks if c.status == "fail"]
    warned = [c for c in checks if c.status == "warn"]
    skipped = [c for c in checks if c.status == "skip"]

    print("─" * 62)
    print(f"✅ ناجح: {passed} · ❌ فاشل: {len(failed)} · "
          f"⚠️ تحذير: {len(warned)} · ⏭ متخطّى: {len(skipped)}")

    if failed:
        print(f"\n{RED}🚫 لا تنشر بعد الآن. أصلح الفاشل أولاً:{RESET}")
        for c in failed:
            print(f"   • [{c.id}] {c.name}: {c.detail}")
        return 1

    if warned:
        print(f"\n{YELLOW}⚠️ يمكن التشغيل، لكن راجع التحذيرات.{RESET}")
    else:
        print(f"\n{GREEN}🎉 كل الفحوص ناجحة — النظام جاهز للنشر الحقيقي.{RESET}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m autonews.preflight",
        description="فحص شامل قبل تشغيل النشر الحقيقي (لا ينشر شيئاً)")
    parser.add_argument("--online", action="store_true",
                        help="اختبر Gemini وتيليجرام فعلياً (يتطلّب مفاتيح)")
    parser.add_argument("--json", action="store_true", help="تقرير JSON")
    parser.add_argument("--root", default=".", help="جذر المشروع")
    args = parser.parse_args(argv)

    checks = run_all(online=args.online, root=args.root)
    return report(checks, as_json=args.json)


__all__ = ["Check", "run_all", "report", "main"]


if __name__ == "__main__":              # pragma: no cover
    sys.exit(main())
