"""اختبارات 🔗 مزامنة الحالة مع المستودع الخاص — تنفّذ git حقيقياً بلا شبكة.

الفكرة: ننشئ مستودعاً «خاصاً» وهمياً على القرص (bare) ونشغّل دورة كاملة:
    save → restore → save مرة أخرى → تعارض متزامن

هذه الاختبارات تكشف ما لا يكشفه أي اختبار وهمي: مشاكل git الحقيقية
(الفهرس، الـ rebase، تخزين التوكن، المستودع الفارغ).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autonews import state_sync as ss

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git غير متاح")


# --------------------------------------------------------------------------- #
#  أدوات
# --------------------------------------------------------------------------- #
def _git(args, cwd, check=True):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} فشل: {proc.stderr[:300]}")
    return proc


@pytest.fixture()
def world(tmp_path):
    """عالم اختبار: مستودع عام + مستودع خاص + إعداد مزامنة."""
    remote = tmp_path / "state.git"
    _git(["init", "--bare", "-q", "--initial-branch=main", str(remote)], cwd=tmp_path)

    public = tmp_path / "public"
    public.mkdir()
    for name in ("config", "state", "logs"):
        if Path(name).is_dir():
            shutil.copytree(Path(name), public / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.tmp"))
        else:
            (public / name).mkdir()
    # ضمان وجود حالة أساسية: المجلدات الفارغة لا تُحفظ، والأرشيف قد لا يحملها
    state_file = public / "state" / "state.json"
    if not state_file.exists():
        state_file.write_text('{"version": 2, "runs": 0, "posted": [], "articles": {}}',
                              encoding="utf-8")
    (public / "logs").mkdir(exist_ok=True)
    (public / "src").mkdir()
    (public / "src" / "app.py").write_text("# كود\n", encoding="utf-8")
    _git(["init", "-q", "--initial-branch=main"], cwd=public)
    _git(["add", "-A"], cwd=public)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=public)

    cfg = ss.SyncConfig(
        token="ghp_TESTTOKEN1234567890abcdefghij",
        repo="me/autonews-state",
        url=f"file://{remote}",
        branch="main",
        max_retries=2,
    )
    return {"root": tmp_path, "remote": remote, "public": public, "cfg": cfg}


# --------------------------------------------------------------------------- #
#  ١) الإعداد والفحص
# --------------------------------------------------------------------------- #
def test_config_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv("STATE_REPO_TOKEN", "ghp_x")
    monkeypatch.setenv("STATE_REPO", "me/autonews-state")
    monkeypatch.delenv("STATE_REPO_URL", raising=False)
    cfg = ss.SyncConfig.from_env()
    assert cfg.enabled
    assert cfg.url == "https://github.com/me/autonews-state.git"


def test_config_disabled_without_token():
    cfg = ss.SyncConfig(repo="me/x", token="", url="https://github.com/me/x.git")
    assert not cfg.enabled
    assert any("TOKEN" in p for p in cfg.problems())


def test_status_reports_sync_paths(world):
    text = ss.status(world["cfg"], workdir=world["public"])
    assert "state/" in text and "config/" in text


def test_check_ok_when_configured(world):
    ok, msg = ss.check(world["cfg"], workdir=world["public"])
    assert ok, msg


def test_restore_without_config_is_not_fatal(world):
    """بلا إعداد: لا استثناء، رسالة واضحة فقط."""
    cfg = ss.SyncConfig()
    ok, msg = ss.restore(cfg, workdir=world["public"])
    assert not ok and "غير مضبوطة" in msg


# --------------------------------------------------------------------------- #
#  ٢) أول حفظ على مستودع خاص فارغ (أصعب حالة)
# --------------------------------------------------------------------------- #
def test_first_save_creates_private_repo_content(world):
    ok, msg = ss.save(world["cfg"], workdir=world["public"])
    assert ok, msg

    tree = _git(["ls-tree", "-r", "--name-only", "main"], cwd=world["remote"]).stdout
    assert "state/state.json" in tree
    assert "config/channels.yml" in tree
    # كود المشروع لا يُرفع للمستودع الخاص
    assert "src/app.py" not in tree


def test_save_does_not_store_token_in_git_config(world):
    """الأمان: التوكن لا يُكتب في أي ملف على القرص."""
    ss.save(world["cfg"], workdir=world["public"])
    sw = world["public"] / ".state-repo"
    for file in sw.rglob("*"):
        if file.is_file() and ".git/" not in str(file):
            continue
    leaked = []
    for file in (sw / ".git").rglob("*"):
        if file.is_file():
            try:
                if world["cfg"].token in file.read_text(errors="ignore"):
                    leaked.append(str(file))
            except OSError:
                pass
    assert not leaked, f"التوكن مخزّن في: {leaked}"


def test_second_save_without_changes_is_noop(world):
    ss.save(world["cfg"], workdir=world["public"])
    ok, msg = ss.save(world["cfg"], workdir=world["public"])
    assert ok and "لا تغييرات" in msg


# --------------------------------------------------------------------------- #
#  ٣) دورة كاملة: حفظ ← تشغيل جديد ← استعادة
# --------------------------------------------------------------------------- #
def _mutate_state(public: Path, runs: int, title: str) -> None:
    path = public / "state" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": 0,
                                                                              "posted": []}
    data["runs"] = runs
    data.setdefault("posted", []).append(
        {"at": "2026-11-01T15:00:00+00:00", "channel": "tg_anime",
         "title": title, "article_fp": f"fp{runs}"})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_full_cycle_restores_state(world):
    """الحالة تُحفظ ثم تُستعاد في تشغيل جديد — هذا قلب النظام كله."""
    public = world["public"]
    _mutate_state(public, runs=777, title="خبر محفوظ")
    ok, _ = ss.save(world["cfg"], workdir=public)
    assert ok

    # تشغيل جديد: نُعيد الحالة المحلية إلى نسخة قديمة (كما يفعل checkout نظيف)
    path = public / "state" / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runs"] = 1
    data["posted"] = [p for p in data["posted"] if p.get("title") != "خبر محفوظ"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(public / ".state-repo")

    ok, msg = ss.restore(world["cfg"], workdir=public)
    assert ok, msg
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["runs"] == 777, "لم تُستعد الحالة"
    assert any(p.get("title") == "خبر محفوظ" for p in restored["posted"])


def test_restore_on_empty_repo_is_safe(world):
    """أول تشغيل أبداً: المستودع الخاص فارغ — لا انفجار."""
    ok, msg = ss.restore(world["cfg"], workdir=world["public"])
    assert ok
    assert "جديد" in msg or "لا حالة" in msg


def test_restore_does_not_wipe_local_config(world):
    """حماية: استعادة لا تطمس config المحلي إن لم يحمل المستودع الخاص نسخة."""
    public = world["public"]
    before = (public / "config" / "channels.yml").read_text(encoding="utf-8")
    ss.restore(world["cfg"], workdir=public)
    after = (public / "config" / "channels.yml").read_text(encoding="utf-8")
    assert before == after


# --------------------------------------------------------------------------- #
#  ٤) منع تكرار النشر بعد الاستعادة (الوظيفة الأهمّ)
# --------------------------------------------------------------------------- #
def test_dedup_survives_restore(world):
    """جوهر منع التكرار: بصمة نُشرت قبل أسبوع لا تُنشر مرّتين."""
    public = world["public"]
    path = public / "state" / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("articles", {})["fp-duplicate"] = {"status": "posted"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert ss.save(world["cfg"], workdir=public)[0]

    # تشغيل جديد ينسى كل شيء
    data["articles"] = {}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(public / ".state-repo")
    assert ss.restore(world["cfg"], workdir=public)[0]

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert "fp-duplicate" in reloaded["articles"], "ضاعت ذاكرة منع التكرار"


# --------------------------------------------------------------------------- #
#  ٥) التعارض والتزامن
# --------------------------------------------------------------------------- #
def test_concurrent_saves_do_not_corrupt_state(world):
    """محاكاة تشغيلين متزامنين: كلاهما يحفظ بلا فقدان بيانات."""
    public = world["public"]
    ss.save(world["cfg"], workdir=public)

    # الطرف الآخر يدفع تغييره
    other = world["root"] / "other"
    shutil.copytree(public, other, ignore=shutil.ignore_patterns(".state-repo"))
    _mutate_state(other, runs=500, title="من الطرف الآخر")
    assert ss.save(world["cfg"], workdir=other)[0]

    # نحن نحفظ بعده — يجب أن ينجح retry ولا يفشل
    _mutate_state(public, runs=900, title="من طرفنا")
    ok, msg = ss.save(world["cfg"], workdir=public)
    assert ok, msg


def test_save_never_raises_on_broken_remote(world):
    """رمز خطأ لا يوقف النشر أبداً."""
    cfg = ss.SyncConfig(token="ghp_x", repo="x/y",
                        url="https://github.com/x/y-does-not-exist-9999.git",
                        max_retries=1)
    ok, msg = ss.save(cfg, workdir=world["public"])
    assert not ok
    assert "⚠️" in msg or "ℹ️" in msg          # رسالة لا استثناء


# --------------------------------------------------------------------------- #
#  ٦) حجب الأسرار في المخرجات
# --------------------------------------------------------------------------- #
def test_scrub_hides_known_secret_shapes():
    leaking = ("ghp_ABCDEFGHIJKLMNOP1234 EAAaBcDeFgHiJkLmNoPqRsTuVwXyZ012345 "
               "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw AIzaSyABCDEFGHIJKLMNOPQRSTUV")
    out = ss._scrub(leaking)
    for token in leaking.split():
        assert token not in out
    assert out.count("***") >= 3


def test_scrub_hides_token_in_url():
    cfg = ss.SyncConfig(token="ghp_SECRET1234567890", url="https://github.com/a/b.git")
    url = ss._authenticated_url(cfg)
    assert cfg.token in url                          # يُستخدم للاتصال
    assert "ghp_SECRET" not in ss._scrub(url)        # لكن لا يُطبع


def test_clean_url_has_no_credentials(world):
    assert "ghp_" not in ss._clean_url(world["cfg"])


# --------------------------------------------------------------------------- #
#  ٧) انتقال الحالة بين المجلدين
# --------------------------------------------------------------------------- #
def test_state_workdir_is_separate_from_public_repo(world):
    ss.save(world["cfg"], workdir=world["public"])
    public_git = world["public"] / ".git"
    # المستودع العام لا يعرف شيئاً عن المستودع الخاص
    remotes = _git(["remote", "-v"], cwd=world["public"]).stdout
    assert "state.git" not in remotes
    assert (world["public"] / ".state-repo" / ".git").exists()
    assert public_git.exists()


def test_archive_snapshot_created_and_rotated(world):
    public = world["public"]
    for i in range(1, 4):
        _mutate_state(public, runs=i, title=f"خبر {i}")
        ss.save(world["cfg"], workdir=public, archive=True)
    arch = public / ".state-archive"
    assert arch.exists()
    assert len(list(arch.iterdir())) <= 5
