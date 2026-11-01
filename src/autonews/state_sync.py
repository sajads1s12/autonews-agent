"""🔗 المزامنة مع مستودع الحالة الخاص (`autonews-state`).

لماذا مستودع منفصل؟
-------------------
    المستودع العام (Public)  →  الكود فقط. لا حالة، لا معرّفات قنوات، لا سجلات.
    المستودع الخاص (Private) →  ذاكرة البوت: state/ · logs/ · config/

النتيجة: تعمل على **دقائق مجانية بلا حدّ** (المستودع العام) بينما بياناتك
الشخصية تبقى خاصة. هذا هو سبب الفصل الوحيد المهمّ.

كيف يعمل؟ (دورة واحدة)
----------------------
    ١) `restore`  ← يجلب أحدث نسخة من المستودع الخاص إلى مجلد العمل
    ٢) التشغيل   ← البوت يقرأ ويكتب كما هو (بلا أي تغيير في الكود)
    ٣) `save`     ← يرفع التغييرات + retry ذكي عند التعارض + نسخة أرشيف

الأمان
------
    • لا مفاتيح في الملفات — `STATE_REPO_TOKEN` من البيئة (GitHub Secrets).
    • الأسرار تُحجب من أي مخرجات (`_scrub`).
    • `set -e` غير مستخدم عن قصد: فشل المزامنة **لا يُفشل النشر**.

الاستخدام (سطر أوامر)
---------------------
    python -m autonews.state_sync restore     # قبل التشغيل
    python -m autonews.state_sync save        # بعد التشغيل
    python -m autonews.state_sync status      # ما الذي سيُزامَن؟
    python -m autonews.state_sync check       # فحص الإعداد بلا شبكة
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .utils import get_logger

log = get_logger("autonews.state_sync")

# --------------------------------------------------------------------------- #
#  ما الذي ينتمي إلى المستودع الخاص؟
# --------------------------------------------------------------------------- #
#  المسارات (أو الأنماط) التي تُزامَن. الباقي يبقى في المستودع العام.
SYNC_PATHS: tuple[str, ...] = (
    "state/",          # الذاكرة: منع التكرار · الطابور · التحكّم · صحة المصادر
    "logs/",           # التقارير واللوحة (تحتوي عناوين أخبار ومنشورات)
    "config/",         # معرّفات القنوات + البصمة + معرّف الصفحة تُكتب من البوت
)

#  ما لا يُزامَن أبداً (ملفات مؤقّتة/بايت-كود)
NEVER_SYNC = ("__pycache__", ".pytest_cache", "*.tmp", "*.pyc", ".DS_Store")


@dataclass(slots=True)
class SyncConfig:
    """إعداد المزامنة (من البيئة فقط — لا قيمة سرّية هنا)."""

    token: str = ""                                  # STATE_REPO_TOKEN
    repo: str = ""                                   # STATE_REPO (owner/name)
    branch: str = "main"
    url: str = ""                                    # يُبنى من token+repo
    author_name: str = "autonews-state-bot"
    author_email: str = "autonews-state-bot@users.noreply.github.com"
    max_retries: int = 3
    config_dir: str = "config"

    @classmethod
    def from_env(cls) -> "SyncConfig":
        token = os.environ.get("STATE_REPO_TOKEN", "").strip()
        # ندعم PAT الكلاسيكي و fine-grained (كلاهما يُستخدم بنفس الصيغة)
        repo = (os.environ.get("STATE_REPO", "").strip()
                or os.environ.get("STATE_REPO_FULL", "").strip())
        url = os.environ.get("STATE_REPO_URL", "").strip()
        if not url and repo:
            url = f"https://github.com/{repo}.git"
        return cls(token=token, repo=repo, branch=os.environ.get("STATE_REPO_BRANCH", "main"),
                   url=url,
                   author_name=os.environ.get("STATE_GIT_NAME", "autonews-state-bot"),
                   author_email=os.environ.get("STATE_GIT_EMAIL",
                                               "autonews-state-bot@users.noreply.github.com"))

    # ---------------------------------------------------------------- الفحص
    @property
    def enabled(self) -> bool:
        """هل المزامنة مطلوبة؟ تحتاج رابطاً + توكن."""
        return bool(self.url and self.token)

    def problems(self) -> list[str]:
        """قائمة بما ينقص (بالعربية) — تُستخدم في رسائل التشخيص."""
        out: list[str] = []
        if not self.repo and not self.url:
            out.append("STATE_REPO غير مضبوط (مثل: myname/autonews-state)")
        if not self.token:
            out.append("STATE_REPO_TOKEN غير مضبوط (PAT يقرأ ويكتب مستودع الحالة)")
        elif self.token.startswith("ghp_") and len(self.token) < 30:
            out.append("STATE_REPO_TOKEN يبدو ناقصاً — انسخه كاملاً")
        return out


# --------------------------------------------------------------------------- #
#  أدوات مساعدة
# --------------------------------------------------------------------------- #
_SECRET_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]{10,}|EAA[A-Za-z0-9]{20,}|"
                        r"[0-9]{8,12}:AA[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,})")


def _scrub(text: str, cfg: SyncConfig | None = None) -> str:
    """حجب أي سرّ يمكن أن يظهر في مخرجات git (روابط فيها توكن، رسائل خطأ).

    مهمّ: سجلّات Actions عامّة في المستودع العام — لا يجوز أن يظهر فيها التوكن.
    """
    if not text:
        return ""
    out = _SECRET_RE.sub("***", text)
    if cfg and cfg.token:
        out = out.replace(cfg.token, "***")
    out = re.sub(r"https://[^@\s]+@github\.com", "https://***@github.com", out)
    return out


def _run(cmd: list[str], *, cwd: str | Path, cfg: SyncConfig | None = None,
         timeout: int = 120, safe: bool = True) -> tuple[int, str, str]:
    """تشغيل أمر بلا كشف الأسرار. `safe=True` يعني: لا ترفع استثناء عند الفشل."""
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, check=False,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return proc.returncode, _scrub(proc.stdout, cfg), _scrub(proc.stderr, cfg)
    except subprocess.TimeoutExpired:
        if safe:
            return 124, "", f"انتهت المهلة ({timeout}ث): {' '.join(cmd[:3])}"
        raise
    except FileNotFoundError:
        if safe:
            return 127, "", "الأمر غير موجود (هل git مثبّت؟)"
        raise


def _authenticated_url(cfg: SyncConfig) -> str:
    """رابط HTTPS فيه التوكن — لا يُطبع ولا يُسجَّل أبداً."""
    url = cfg.url
    if cfg.token and "://" in url and "@" not in url.split("://", 1)[1]:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://x-access-token:{cfg.token}@{rest}"
    return url


def _clean_url(cfg: SyncConfig) -> str:
    """الرابط بلا توكن — للعرض في السجلّات."""
    return re.sub(r"//[^@/]+@", "//", cfg.url)


# --------------------------------------------------------------------------- #
#  مجلد مستودع الحالة (منفصل تماماً عن المستودع العام)
# --------------------------------------------------------------------------- #
#  لماذا مجلد منفصل وليس remote في نفس المستودع؟
#    • لا خلط في السجلّات ولا خطر دفع الحالة إلى المستودع العام بالخطأ.
#    • لا حاجة لـ index مؤقّت ولا لـ skip-worktree (وهي هشّة).
#    • مسح المجلد = مسح كل الأثر.
STATE_WORK_DEFAULT = ".state-repo"


def _state_work(workdir: str | Path = ".") -> Path:
    return Path(workdir) / os.environ.get("STATE_WORKDIR", STATE_WORK_DEFAULT)


def _git(args: list[str], *, cwd: Path, cfg: SyncConfig, timeout: int = 180) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=cwd, cfg=cfg, timeout=timeout)


def _clone_or_update(cfg: SyncConfig, sw: Path) -> tuple[bool, str]:
    """تجهيز مجلد مستودع الحالة: استنساخ إن لزم، ثم تحديث."""
    auth = _authenticated_url(cfg)

    if not (sw / ".git").exists():
        sw.mkdir(parents=True, exist_ok=True)
        code, _, err = _git(["clone", "--depth", "1", "--branch", cfg.branch,
                             auth, str(sw)], cwd=sw.parent, cfg=cfg)
        if code != 0:
            # الحالة الشائعة: المستودع فارغ تماماً (بلا أي commit)
            if "not found" in err.lower() or "couldn't find remote ref" in err.lower() \
                    or "remote branch" in err.lower() or "empty" in err.lower():
                code2, _, err2 = _git(["init", "--quiet", "--initial-branch", cfg.branch],
                                      cwd=sw, cfg=cfg)
                if code2 != 0:
                    return False, f"تعذّر تهيئة مجلد الحالة: {err2[:150]}"
                _git(["remote", "add", "origin", _clean_url(cfg)], cwd=sw, cfg=cfg)
                _ensure_git_identity(cfg, sw)
                log.info("ℹ️ المستودع الخاص فارغ — هيّأنا مجلداً جديداً")
                return True, "new"
            return False, f"تعذّر استنساخ المستودع الخاص: {err.strip()[:150]}"
        # لا نخزّن التوكن في .git/config
        _git(["remote", "set-url", "origin", _clean_url(cfg)], cwd=sw, cfg=cfg)
        _ensure_git_identity(cfg, sw)
        log.info("✅ استُنسخ مستودع الحالة")
        return True, "cloned"

    # تحديث: نجلب أحدث حالة (قد تكون فشلت لعدم وجود commits بعد)
    code, _, err = _git(["fetch", "--depth", "1", auth, cfg.branch], cwd=sw, cfg=cfg)
    if code == 0:
        _git(["reset", "--hard", "FETCH_HEAD"], cwd=sw, cfg=cfg)
        _git(["clean", "-fdq", "--", *SYNC_PATHS], cwd=sw, cfg=cfg)
        return True, "updated"
    if "couldn't find remote ref" in err.lower() or "not found" in err.lower():
        return True, "empty"
    return False, f"تعذّر تحديث مجلد الحالة: {err.strip()[:150]}"


def _copy_tree(src_root: Path, dst_root: Path, paths: tuple[str, ...]) -> int:
    """نسخ مسارات الحالة من مجلد إلى آخر. يعيد عدد الملفات المنسوخة."""
    count = 0
    for p in paths:
        src = src_root / p
        if not src.is_dir():
            continue
        dst = dst_root / p
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*NEVER_SYNC))
        count += sum(1 for _ in dst.rglob("*") if _.is_file())
    return count


def init_remote(cfg: SyncConfig, *, workdir: str | Path = ".") -> tuple[bool, str]:
    """تهيئة مجلد المستودع الخاص وربطه (يُستدعى تلقائياً عند أول save)."""
    problems = cfg.problems()
    if problems:
        return False, "❌ " + " · ".join(problems)
    sw = _state_work(workdir)
    ok, note = _clone_or_update(cfg, sw)
    if not ok:
        return False, f"❌ {note}"
    _ensure_git_identity(cfg, sw)
    return True, f"✅ مجلد المستودع الخاص جاهز ({sw}) — {note}"


# --------------------------------------------------------------------------- #
#  ١) الاستعادة
# --------------------------------------------------------------------------- #
def restore(cfg: SyncConfig | None = None, *, workdir: str | Path = ".",
            paths: tuple[str, ...] = SYNC_PATHS,
            reset_config: bool = False) -> tuple[bool, str]:
    """جلب أحدث حالة من المستودع الخاص إلى مجلد العمل.

    `reset_config=False` (الافتراضي): **لا نطمس** `config/` المحلي إلا إن كان
    المستودع الخاص يحمل نسخة أحدث (أي أنه يحتوي الملف فعلاً). هذا يمنع
    أن يمسح أول تشغيل إعداداتك الحالية.

    إن لم يكن المستودع الخاص جاهزاً — **لا نفشل**: نكمل بحالة جديدة.
    """
    cfg = cfg or SyncConfig.from_env()
    work = Path(workdir)
    if not cfg.enabled:
        return False, "ℹ️ المزامنة غير مضبوطة — سيعمل النظام بحالة محلية"

    sw = _state_work(work)
    ok, note = _clone_or_update(cfg, sw)
    if not ok:
        return False, f"⚠️ {note} — سنكمل بالحالة المحلية"

    if note == "new":
        return True, "ℹ️ المستودع الخاص جديد — لا حالة سابقة"

    # ما الذي نستعيده؟ كل شيء موجود فعلاً في المستودع الخاص
    present = tuple(p for p in paths if (sw / p).is_dir())
    if not present:
        return True, "ℹ️ لا مسارات حالة في المستودع الخاص بعد"
    if not reset_config and "config/" in present:
        # config يُستعاد فقط إن كان المستودع الخاص يحتوي channels.yml فعلاً
        if not (sw / "config" / "channels.yml").exists():
            present = tuple(p for p in present if p != "config/")

    count = _copy_tree(sw, work, present)
    n_state = len(list((work / "state").glob("*.json"))) if (work / "state").exists() else 0
    log.info("✅ استُعيدت الحالة (%d ملفاً · %d JSON)", count, n_state)
    return True, f"✅ استُعيدت الحالة ({count} ملفاً · {n_state} JSON)"


# --------------------------------------------------------------------------- #
#  ٢) الحفظ
# --------------------------------------------------------------------------- #
def _init_if_needed(cfg: SyncConfig, sw: Path) -> tuple[bool, str]:
    if (sw / ".git").exists():
        _ensure_git_identity(cfg, sw)
        return True, ""
    return _clone_or_update(cfg, sw)


def _ensure_git_identity(cfg: SyncConfig, workdir: Path) -> None:
    _git(["config", "user.name", cfg.author_name], cwd=workdir, cfg=cfg)
    _git(["config", "user.email", cfg.author_email], cwd=workdir, cfg=cfg)


def save(cfg: SyncConfig | None = None, *, workdir: str | Path = ".",
         message: str = "", paths: tuple[str, ...] = SYNC_PATHS,
         archive: bool = True) -> tuple[bool, str]:
    """رفع الحالة إلى المستودع الخاص مع retry ذكي عند التعارض.

    **لا يُفشل التشغيل أبداً** — النشر أهمّ من الذاكرة.
    """
    cfg = cfg or SyncConfig.from_env()
    work = Path(workdir)
    if not cfg.enabled:
        return False, "ℹ️ المزامنة غير مضبوطة — الحالة محلية فقط"

    sw = _state_work(work)
    ok, note = _init_if_needed(cfg, sw)
    if not ok:
        return False, f"⚠️ {note}"
    _ensure_git_identity(cfg, sw)

    existing = [p for p in paths if (work / p).exists()]
    if not existing:
        return False, "ℹ️ لا مجلدات حالة — لا شيء يُحفظ"

    count = _copy_tree(work, sw, tuple(existing))

    code, out, _ = _git(["status", "--porcelain"], cwd=sw, cfg=cfg)
    if code != 0:
        return False, "⚠️ تعذّر قراءة حالة مجلد الحالة"
    if not out.strip():
        return True, "ℹ️ لا تغييرات في الحالة"

    changed = [ln[3:].strip() for ln in out.splitlines() if ln.strip()]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    msg = message or f"[state] تحديث الحالة {stamp}"

    _git(["add", "-A"], cwd=sw, cfg=cfg)
    code, _, err = _git(["commit", "--no-verify", "-m", msg], cwd=sw, cfg=cfg)
    if code != 0 and "nothing to commit" not in (err or "").lower():
        return False, f"⚠️ تعذّر commit: {err.strip()[:150]}"

    if archive:
        _snapshot(work, existing)

    auth = _authenticated_url(cfg)
    last_err = ""
    for attempt in range(1, cfg.max_retries + 1):
        code, _, err = _git(["push", "--quiet", auth, f"HEAD:{cfg.branch}"],
                            cwd=sw, cfg=cfg)
        if code == 0:
            log.info("✅ حُفظت الحالة في المستودع الخاص (%d ملفاً)", len(changed))
            return True, f"✅ حُفظت الحالة ({len(changed)} ملفاً)"

        last_err = err.strip()[:200]
        log.warning("↻ محاولة %d/%d لدفع الحالة فشلت", attempt, cfg.max_retries)

        # الطرف الآخر دفع قبلك ⇒ non-fast-forward.
        # الحلّ: دمج سجلّاته تحت سجلّنا (ours) — أحدث حالة تفوز بلا تعارض ملفات.
        # ملاحظة: rebase هنا خطأ لأن ملفات JSON تتعارض دائماً سطراً بسطر.
        code_f, _, _ = _git(["fetch", "--depth", "5", auth, cfg.branch], cwd=sw, cfg=cfg)
        if code_f == 0:
            code_m, _, err_m = _git(
                ["merge", "-s", "ours", "--no-edit", "-X", "ours", "FETCH_HEAD"],
                cwd=sw, cfg=cfg)
            if code_m != 0:
                _git(["merge", "--abort"], cwd=sw, cfg=cfg)
                log.debug("تعذّر دمج سجلّ الطرف الآخر: %s", err_m.strip()[:120])
        time.sleep(min(2 ** attempt, 8))

    return False, f"⚠️ تعذّر دفع الحالة بعد {cfg.max_retries} محاولات: {last_err}"


def _snapshot(work: Path, paths: list[str], keep: int = 5) -> Path | None:
    """نسخة أرشيف محلية في `.state-archive/` (تدور آخر ٥ نسخ).

    ليست بديلاً عن Git — لكنها تنقذ الحالة إن تلف ملف أثناء الكتابة.
    """
    try:
        arch = work / ".state-archive"
        arch.mkdir(exist_ok=True)
        target = arch / f"state-{time.strftime('%Y%m%d-%H%M%S')}"
        target.mkdir(exist_ok=True)
        for p in paths:
            src = work / p
            if src.is_dir():
                shutil.copytree(src, target / p, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*NEVER_SYNC))
        olds = sorted(arch.iterdir(), key=lambda q: q.name)
        for old in olds[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
        return target
    except OSError as exc:                      # الأرشيف رفاهية لا شرط
        log.debug("تعذّر إنشاء نسخة أرشيف: %s", exc)
        return None


# --------------------------------------------------------------------------- #
#  ٣) تشخيص
# --------------------------------------------------------------------------- #
def status(cfg: SyncConfig | None = None, *, workdir: str | Path = ".") -> str:
    """ما الذي سيُزامَن الآن؟ (يُطبع في Actions قبل/بعد التشغيل)."""
    cfg = cfg or SyncConfig.from_env()
    work = Path(workdir)
    lines = ["🔗 حالة المزامنة مع المستودع الخاص", "─" * 50]

    if not cfg.enabled:
        lines.append("⚪ غير مضبوطة — النظام يعمل بحالة محلية داخل المستودع العام.")
        lines.append("   للتفعيل: STATE_REPO + STATE_REPO_TOKEN في GitHub Secrets")
        for p in cfg.problems():
            lines.append(f"   • {p}")
    else:
        lines.append(f"✅ مفعّلة → {_clean_url(cfg)} (فرع {cfg.branch})")
        sw = _state_work(work)
        if (sw / ".git").exists():
            code, out, _ = _git(["log", "--oneline", "-1"], cwd=sw, cfg=cfg)
            if code == 0 and out.strip():
                lines.append(f"   آخر حفظ: {out.strip()[:70]}")
            code, out, _ = _git(["status", "--porcelain"], cwd=sw, cfg=cfg)
            pend = len([x for x in out.splitlines() if x.strip()])
            lines.append(f"   ↻ غير مرفوع: {pend} ملفاً")
        else:
            lines.append("   ○ لم يُستنسخ بعد (يحدث في أول تشغيل)")

    for p in SYNC_PATHS:
        d = work / p
        if not d.exists():
            lines.append(f"   ○ {p} — غير موجود")
            continue
        files = [f for f in d.rglob("*") if f.is_file()
                 and not any(x in str(f) for x in NEVER_SYNC)]
        size = sum(f.stat().st_size for f in files)
        lines.append(f"   ● {p} — {len(files)} ملفاً · {size / 1024:.1f} ك.ب")
    return "\n".join(lines)


def check(cfg: SyncConfig | None = None, *, workdir: str | Path = ".") -> tuple[bool, str]:
    """فحص جاهزية المزامنة بلا شبكة (اختبارات + CI)."""
    cfg = cfg or SyncConfig.from_env()
    if not cfg.enabled:
        return False, "المزامنة غير مضبوطة (اختيارية — النظام يعمل بلاها)"
    if not shutil.which("git"):
        return False, "git غير مثبّت في البيئة"
    missing = [p for p in SYNC_PATHS if not (Path(workdir) / p).exists()]
    if len(missing) == len(SYNC_PATHS):
        return False, "لا مسارات حالة في مجلد العمل"
    return True, "جاهزة للمزامنة"


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m autonews.state_sync",
        description="مزامنة حالة البوت مع المستودع الخاص")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["restore", "save", "status", "check", "init"])
    parser.add_argument("--message", default="", help="رسالة الـ commit")
    parser.add_argument("--workdir", default=".", help="مجلد العمل")
    args = parser.parse_args(argv)

    cfg = SyncConfig.from_env()
    if args.action == "restore":
        ok, msg = restore(cfg, workdir=args.workdir)
    elif args.action == "save":
        ok, msg = save(cfg, workdir=args.workdir, message=args.message)
    elif args.action == "init":
        ok, msg = init_remote(cfg, workdir=args.workdir)
    elif args.action == "check":
        ok, msg = check(cfg, workdir=args.workdir)
    else:
        print(status(cfg, workdir=args.workdir))
        return 0

    print(msg)
    # مهمّ: الاستعادة الحقيقية للفشل تُعيد رمزاً غير صفري **فقط** في check/init
    if args.action in ("check", "init"):
        return 0 if ok else 1
    return 0                     # restore/save لا يفشلان التشغيل أبداً


__all__ = ["SyncConfig", "SYNC_PATHS", "restore", "save", "status", "check",
           "init_remote", "main"]


if __name__ == "__main__":              # pragma: no cover
    sys.exit(main())
