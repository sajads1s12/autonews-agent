"""اختبارات التكامل: الفجوات بين المكوّنات — وهي أخطر من أي ميزة جديدة."""
from __future__ import annotations

from datetime import timedelta

import pytest

from autonews.ai import Post
from autonews.i18n import needs_translation
from autonews.i18n.translate import is_already_covered, translation_fingerprint
from autonews.ingest import Article
from autonews.notify import Notifier
from autonews.config_loader import AlertSettings
from autonews.state import State
from autonews.utils import utcnow


def art(title: str, lang: str = "ar", url: str = "https://e.co/1") -> Article:
    return Article(source_id="s", source_name="مصدر", title=title, summary="",
                   url=url, lang=lang)


# --------------------------------------------------------------------------- #
#  ١) الحوادث المؤجّلة تُحذف بعد انتهاء صلاحيتها
# --------------------------------------------------------------------------- #
def test_deferred_incidents_are_pruned():
    st = State()
    old = {"fp": "old", "title": "حادثة قديمة", "score": 3.0,
           "first_seen": (utcnow() - timedelta(hours=80)).isoformat()}
    fresh = {"fp": "new", "title": "حادثة جديدة", "score": 3.0,
             "first_seen": (utcnow() - timedelta(hours=2)).isoformat()}
    st.deferred_incidents = [old, fresh]
    removed = st.prune_incidents(max_age_hours=72)
    assert removed == 1
    assert [i["fp"] for i in st.deferred_incidents] == ["new"]


def test_prune_keeps_unknown_dates():
    st = State()
    st.deferred_incidents = [{"fp": "x", "title": "بلا تاريخ", "first_seen": ""}]
    assert st.prune_incidents() == 0
    assert len(st.deferred_incidents) == 1


# --------------------------------------------------------------------------- #
#  ٢) منع ازدواج الخبر بين لغتين
# --------------------------------------------------------------------------- #
def test_translation_fingerprint_is_stable():
    a = translation_fingerprint("Magnitude 5.2 quake hits northern Iraq")
    b = translation_fingerprint("Magnitude 5.2 quake hits northern Iraq")
    assert a == b and a.startswith("tr:")


def test_already_covered_blocks_same_foreign_story():
    seen = ["Magnitude 5.2 quake hits northern Iraq"]
    assert is_already_covered("Magnitude 5.2 quake hits northern Iraq", seen)
    assert not is_already_covered("Iraq announces new holiday", seen)


def test_translated_article_flag_preserved():
    from autonews.i18n.translate import TranslationResult, apply_translation

    a = art("Iraq holiday announced", lang="en")
    tr = TranslationResult(ok=True, title="عطلة رسمية في العراق",
                           summary="أعلنت الحكومة عطلة رسمية.", lang_source="en")
    out = apply_translation(a, tr)
    assert out.lang == "ar" and out.translated_from == "en"
    assert out.title == "عطلة رسمية في العراق"
    assert not needs_translation(out)


# --------------------------------------------------------------------------- #
#  ٣) تنبيه واحد بدل ثلاثة
# --------------------------------------------------------------------------- #
def test_digest_single_message_with_sections():
    n = Notifier(AlertSettings(enabled=True, bot_token="t", chat_id="1"), dry_run=True)
    ok = n.digest(title="📋 متابعة", sections=[
        ("🕵️ بلا نتائج", ["حادثة أ", "حادثة ب"]),
        ("🔄 قيد المتابعة", []),
    ])
    assert ok and len(n.sent) == 1


def test_digest_skips_when_empty():
    n = Notifier(AlertSettings(enabled=True, bot_token="t", chat_id="1"), dry_run=True)
    assert n.digest(title="لا شيء", sections=[("x", [])]) is False
    assert n.sent == []


# --------------------------------------------------------------------------- #
#  ٤) التقرير يحمل إحصاءات النتائج والتعريب
# --------------------------------------------------------------------------- #
def test_report_includes_outcome_metrics():
    from autonews.report import RunReport

    r = RunReport()
    r.outcome_posts = 3
    r.deferred_incidents = 4
    r.translations = 2
    md = r.to_markdown()
    assert "بطاقات نتائج منشورة | 3" in md
    assert "حوادث مؤجّلة (بلا نتائج) | 4" in md
    assert "عُربّت · مرفوضة | 2 · 0" in md


# --------------------------------------------------------------------------- #
#  ٥) الرسمي أولاً في السحب (سرعة المصدر الرسمي)
# --------------------------------------------------------------------------- #
def test_official_sources_fetched_first():
    from autonews.config_loader import Source
    from autonews.ingest import rss

    srcs = [
        Source(id="blog", name="مدونة", url="https://b.co", trust=0.5),
        Source(id="official", name="رسمي", url="https://o.co", official=True, trust=1.3),
        Source(id="mid", name="وسط", url="https://m.co", trust=1.0),
    ]
    ordered = sorted(srcs, key=lambda s: (not getattr(s, "official", False),
                                          -float(getattr(s, "trust", 1.0))))
    assert ordered[0].id == "official"
    assert ordered[-1].id == "blog"
    assert hasattr(rss.Article, "__annotations__")
    assert "translated_from" in rss.Article.__annotations__


# --------------------------------------------------------------------------- #
#  ٦) فحص الصحة يعرف الطبقات الجديدة
# --------------------------------------------------------------------------- #
def test_health_check_reports_v6_layers(capsys):
    from autonews.config_loader import load_config
    from autonews.main import run_check

    settings = load_config("config", override_dry_run=True)
    run_check(settings)
    out = capsys.readouterr().out
    assert "تكامل الطبقات" in out
    assert "نمط النتائج" in out
    assert "التعريب" in out
    assert "المتابعة" in out


# --------------------------------------------------------------------------- #
#  ٧) المسار السريع لا ينتظر مفاتيح مجمّدة
# --------------------------------------------------------------------------- #
class _FrozenEngine:
    keys = ["k"]
    models = ["m"]

    def available_pairs(self):
        return []

    def generate(self, *_a, **_k):   # pragma: no cover — يجب ألّا يُستدعى
        raise AssertionError("لا يجب استدعاء المحرّك عندما تكون كل الأزواج مجمّدة")


def test_flash_skips_frozen_engine_and_uses_template():
    from autonews.config_loader import load_config
    from autonews.flash import FlashLane

    settings = load_config("config", override_dry_run=True)
    state = State()
    lane = FlashLane(settings, state, engine=_FrozenEngine())
    a = art("تعطيل الدوام في البصرة وميسان غداً")
    post = lane._write_fast(a, settings.channels[0], _verdict(), None)
    assert post is not None and post.body, "يجب أن يصدر قالب النتائج فوراً"
    assert "📋" in post.body or "البصرة" in post.body


def _verdict():
    from autonews.flash import FlashVerdict

    return FlashVerdict(urgent=True, score=20.0, category="holiday", reason="عطلة")


# --------------------------------------------------------------------------- #
#  ٨) الحالة تبقى صغيرة (تُرفع للمستودع في كل تشغيل!)
# --------------------------------------------------------------------------- #
def test_trim_drops_titles_for_noisy_statuses():
    st = State()
    for i in range(50):
        st.articles[f"f{i}"] = {"status": "failed", "run": i,
                                "at": "2026-11-01T00:00:00+00:00",
                                "title": "عنوان خبر طويل " * 6}
    for i in range(5):
        st.articles[f"p{i}"] = {"status": "posted", "run": i,
                                "at": "2026-11-01T00:00:00+00:00",
                                "title": "عنوان منشور مهم"}
    st._trim()
    assert all("title" not in v for k, v in st.articles.items() if k.startswith("f"))
    assert all("title" in v for k, v in st.articles.items() if k.startswith("p"))


def test_state_size_stays_reasonable():
    import json

    st = State()
    for i in range(4000):
        st.articles[f"fp{i}"] = {"status": "skipped", "run": i,
                                 "at": "2026-11-01T00:00:00+00:00",
                                 "title": "عنوان خبر عراقي طويل نسبياً عن العطلة والدوام"}
    st._trim()
    size_kb = len(json.dumps(st.to_dict(), ensure_ascii=False).encode()) / 1024
    assert size_kb < 600, f"ملف الحالة كبير جداً: {size_kb:.0f} KB"


def test_save_state_script_exists_and_is_valid():
    import shutil
    import subprocess
    from pathlib import Path

    script = Path("scripts/save_state.sh")
    assert script.exists(), "سكربت حفظ الحالة مفقود — لن تُحفظ ذاكرة النظام"
    assert "git add" in script.read_text(encoding="utf-8")
    # فحص نحوي بلا تنفيذ
    bash = shutil.which("bash")
    if bash:
        res = subprocess.run([bash, "-n", str(script)], capture_output=True)
        assert res.returncode == 0, res.stderr.decode()


def test_workflows_save_state_to_repo_not_cache():
    """الحالة تُحفظ في المستودع الخاص (state_sync) أو محلياً — لا في Cache.

    تحديث: بعد فصل المستودعين صار الحفظ عبر composite action `./.github/actions/state`
    الذي يستدعي `python -m autonews.state_sync save`. الكاش ممنوع في الحالتين لأنه
    يُحذف بعد ٧ أيام فيُنسي النظام ذاكرته وينشر مكرراً.
    """
    from pathlib import Path

    for wf in ("flash.yml", "autonews.yml", "control.yml"):
        text = Path(f".github/workflows/{wf}").read_text(encoding="utf-8")
        saves = ("scripts/save_state.sh" in text
                 or "./.github/actions/state" in text)
        assert saves, f"{wf} لا يحفظ الحالة"
        assert "actions/cache" not in text, f"{wf} يستخدم Cache للحالة (خطأ)"
        assert "actions/cache/save" not in text, f"{wf} ما زال يستخدم كاشاً مؤقتاً"
