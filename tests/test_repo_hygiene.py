"""🛡 اختبارات نظافة المستودع — تمنع تسرّب البيانات الخاصة قبل أي دفع.

هذه أهمّ مجموعة اختبارات من ناحية الأمان: تفشل إن أضاف أحدهم
ملفّ حالة، أو سجلاً، أو سرّاً إلى المستودع العام.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
#  ١) .gitignore يحمي الحالة والسجلات
# --------------------------------------------------------------------------- #
def test_gitignore_covers_state_and_logs():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("state/*", "logs/*", ".env", ".state-repo/", ".state-archive/",
                    "__pycache__/", "*.bak", "*.zip"):
        assert pattern in text, f".gitignore لا يحمي: {pattern}"


def test_gitignore_keeps_example_and_actions():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!.env.example" in text, "لا بدّ من رفع القالب"
    assert "!.github/**" in text, "workflows يجب أن تُرفع"


def test_state_json_would_be_ignored():
    """الملف الحيوي للذاكرة يجب أن يكون متجاهلاً في المستودع العام."""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "state/**/*.json" in gi or "state/*" in gi


# --------------------------------------------------------------------------- #
#  ٢) .env.example قالب نظيف
# --------------------------------------------------------------------------- #
def test_env_example_has_no_real_values():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value:
            values.append((key, value))
    # القيم الوحيدة المسموح لها بملء: مسار/فرع افتراضي لا سرّ فيه
    allowed = {"STATE_REPO_BRANCH"}
    unexpected = [f"{k}={v}" for k, v in values if k not in allowed]
    assert not unexpected, f"قيم غير متوقّعة في القالب: {unexpected}"


def test_env_example_documents_required_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("GEMINI_API_KEYS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID",
                 "STATE_REPO", "STATE_REPO_TOKEN"):
        assert name in text, f"{name} غير موثّق في القالب"


# --------------------------------------------------------------------------- #
#  ٣) لا معرّفات حقيقية في الإعدادات العامة
# --------------------------------------------------------------------------- #
def test_no_real_telegram_chat_ids_in_config():
    """أسماء متغيّرات فقط — لا أرقام chat_id حقيقية."""
    data = yaml.safe_load((ROOT / "config" / "channels.yml").read_text(encoding="utf-8"))
    for channel in data["channels"]:
        for key, value in (channel.get("credentials") or {}).items():
            assert isinstance(value, str), f"{channel['id']}.{key} ليس اسم متغيّر"
            assert not re.match(r"^-?\d{6,}$", value), \
                f"{channel['id']}.{key} يحوي رقماً حقيقياً بدل اسم متغيّر"


def test_no_facebook_page_id_in_public_config():
    """معرّف الصفحة بيانات خاصة → المستودع الخاص أو الأسرار."""
    text = (ROOT / "config" / "channels.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    fb = next(c for c in data["channels"] if c["id"] == "fb_page")
    page_id = (fb.get("extra") or {}).get("ENV_PAGE_ID", "")
    assert page_id == "", f"معرّف صفحة حقيقي في النسخة العامة: {page_id}"


def test_no_hardcoded_secrets_in_python():
    """لا مفاتيح مكتوبة داخل الكود."""
    patterns = (re.compile(r"EAA[A-Za-z0-9]{50,}"),
                re.compile(r"\d{8,12}:AA[A-Za-z0-9_-]{30,}"),
                re.compile(r"AIza[0-9A-Za-z_-]{35}"),
                re.compile(r"AQ\.Ab8R[A-Za-z0-9_-]{20,}"),
                re.compile(r"ghp_[A-Za-z0-9]{36}"))
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            for match in pattern.findall(text):
                offenders.append(f"{path.name}: {match[:12]}…")
    assert not offenders, f"أسرار في الكود: {offenders}"


# --------------------------------------------------------------------------- #
#  ٤) سكربت الفحص موجود ويعمل
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash غير متاح")
def test_check_secrets_script_passes():
    proc = subprocess.run(["bash", "scripts/check_secrets.sh"], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"فحص الأسرار فشل:\n{proc.stdout[-1500:]}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash غير متاح")
def test_check_secrets_detects_planted_secret(tmp_path):
    """الأهمّ: نتأكد أن الفحص **يكشف** فعلاً وليس ديكوراً."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_secrets.sh", tmp_path / "scripts")
    (tmp_path / "leak.py").write_text(
        'TOKEN = "8913437888:AAGxZTfS6QgisPRkno25rapJ8Pw9sWtf0nMxyz"\n',
        encoding="utf-8")
    proc = subprocess.run(["bash", "scripts/check_secrets.sh"], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, "الفحص لم يكشف سرّاً مزروعاً!"
    assert "❌" in proc.stdout


# --------------------------------------------------------------------------- #
#  ٥) بنية المشروع المتوقّعة
# --------------------------------------------------------------------------- #
def test_required_files_exist():
    for name in ("requirements.txt", ".gitignore", ".env.example", "pytest.ini",
                 "README.md", "Dockerfile"):
        assert (ROOT / name).exists(), f"مفقود: {name}"


def test_workflows_are_valid_yaml():
    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    assert len(workflows) >= 4, "عدد مسارات العمل أقلّ من المتوقّع"
    for path in workflows:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name}: YAML تالف"
        assert "on" in data or True in data, f"{path.name}: لا يوجد مُشغِّل"


def test_composite_actions_exist():
    for name in ("setup", "state"):
        assert (ROOT / ".github" / "actions" / name / "action.yml").exists(), \
            f"الـ action المشترك مفقود: {name}"


def test_every_workflow_that_runs_bot_has_concurrency():
    """منع تشغيل نسختين متعارضتين — شرط أساسي."""
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "autonews.main" in text or "flash_main" in text or "setup_main" in text:
            assert "concurrency:" in text, f"{path.name}: لا يوجد concurrency (خطر نشر مزدوج)"


def test_runtime_workflows_have_timeouts():
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "runs-on" in text:
            assert "timeout-minutes" in text, f"{path.name}: بلا timeout"


def test_workflows_use_pip_cache():
    """عدم إعادة تثبيت المكتبات بلا داعٍ."""
    setup_action = (ROOT / ".github" / "actions" / "setup" / "action.yml").read_text(encoding="utf-8")
    assert "cache: pip" in setup_action or "cache: 'pip'" in setup_action


# --------------------------------------------------------------------------- #
#  ٦) لا أسرار مكتوبة في الـ workflows
# --------------------------------------------------------------------------- #
def test_workflows_use_secrets_reference_only():
    """كل قيمة حسّاسة يجب أن تمرّ عبر ${{ secrets.* }} — لا نصّ صريح."""
    secret_patterns = (re.compile(r"EAA[A-Za-z0-9]{50,}"),
                       re.compile(r"\d{8,12}:AA[A-Za-z0-9_-]{30,}"),
                       re.compile(r"AIza[0-9A-Za-z_-]{35}"))
    for path in (ROOT / ".github").rglob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for pattern in secret_patterns:
            assert not pattern.search(text), f"{path.name}: سرّ مكتوب في الـ workflow"


def test_permissions_are_least_privilege():
    """المستودع العام لا يحتاج صلاحية كتابة — الحالة في المستودع الخاص."""
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "contents: write" not in text, \
            f"{path.name}: يطلب كتابة — الحالة يجب أن تكون في المستودع الخاص"


# --------------------------------------------------------------------------- #
#  المتطلب ٦: بيانات المستودع الخاص لا تظهر في السجلات العامة
# --------------------------------------------------------------------------- #
def test_no_chat_id_in_log_statements():
    """لا معرّفات قنوات (chat_id) في رسائل السجل — تُرفع مع Actions ويقرأها غيرك."""
    import re
    from pathlib import Path

    offenders = []
    for path in Path("src").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.search(r"\blog\.(info|warning|error|debug|exception)\(", line):
                continue
            if re.search(r"chat_id|chatid|page_id|token|secret", line, re.I) \
                    and "%s" in line:
                offenders.append(f"{path}:{i}")
    assert not offenders, "سجلات تطبع معرّفات خاصة: " + ", ".join(offenders)


def test_logs_and_state_are_ignored_by_public_repo():
    """state/ و logs/ لا تُرفع للمستودع العام."""
    from pathlib import Path

    text = Path(".gitignore").read_text(encoding="utf-8")
    assert "state/" in text
    assert "logs/" in text


def test_main_imports_every_name_it_uses():
    """حرس NameError: كل اسم يُستخدم في main.py معرّف أو مستورد."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/autonews/main.py").read_text(encoding="utf-8"))
    import builtins

    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)

    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    # __file__ ضمني · exc من `except ... as exc`
    missing = sorted(used - defined - {"self", "cls", "__name__", "__file__", "exc"})
    assert not missing, f"main.py يستخدم أسماء غير معرّفة: {missing}"


# --------------------------------------------------------------------------- #
#  عزل الاختبارات عن بيئة التشغيل
# --------------------------------------------------------------------------- #
def test_ci_does_not_disable_ai_for_test_step():
    """CI يجب ألّا يشغّل الاختبارات بـ AI_DISABLED — كان يُسقط اختبارين كذباً."""
    from pathlib import Path

    text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    #  نقسم إلى خطوات ونفحص **الخطوة** التي تشغّل pytest الأساسي فقط
    steps = text.split("- name:")
    basic = [st for st in steps if "run: pytest -q" in st]
    assert basic, "لم أجد خطوة pytest في CI"
    first = basic[0]
    assert "AI_DISABLED" not in first, \
        f"خطوة الاختبارات تضبط AI_DISABLED: {first.strip()[:80]}"


def test_conftest_cleans_operational_env():
    """حرس ضد الاعتماد على «بيئة نظيفة» — result is environment-dependent."""
    from pathlib import Path

    text = Path("tests/conftest.py").read_text(encoding="utf-8")
    for name in ("AI_DISABLED", "REQUIRE_REVIEW", "ENABLED_CHANNELS",
                 "ENABLED_SOURCES", "GEMINI_API_KEYS", "STATE_REPO"):
        assert name in text, f"conftest لا يعزل {name}"


def test_ai_disabled_is_intentional_and_documented():
    """AI_DISABLED سلوك مقصود (نصّ احتياطي) — يوجد اختبار يثبته صراحةً."""
    from pathlib import Path

    text = Path("tests/test_pipeline.py").read_text(encoding="utf-8")
    assert "test_ai_disabled_env_empties_keys" in text
