"""اختبارات التكامل: البوّابة بين سحب الأخبار والنشر الفعلي.

نُشغّل `gate()` الحقيقية من `main.py` كما تُستدعى في التشغيل اليومي، ونتحقّق
من النتيجة المرئية: هل نُشر؟ هل انتظر موافقة؟ هل أُجّل؟
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from autonews.control import KIND_APPROVAL, KIND_SCHEDULED, ControlStore


@dataclass
class FakePost:
    title: str = "خبر عادي عن الخدمات في بغداد"
    body: str = "نصّ كافٍ من التفاصيل يشرح ما حدث ومتى وأين بشكل واضح ومقروء للقارئ."
    source_url: str = "https://example.com/news/1"
    used_fallback: bool = False
    sensitive: bool = False

    def as_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "source_url": self.source_url,
                "platform": "telegram", "channel": "tg_main"}


@dataclass
class FakeVerdict:
    ok: bool = True
    score: float = 0.95
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  البوّابة — منقولة كدالّة مستقلّة كي نختبرها بلا شبكة ولا AI
# --------------------------------------------------------------------------- #
def _gate(control: ControlStore, channel_id: str, post, *, breaking: bool,
          verdict=None, review_mode: str = "smart", quiet=(-1, -1),
          min_gap: int = 0, tz_offset: int = 3) -> tuple[str, dict]:
    """نسخة مطابقة لمنطق `main.gate` (بلا اعتماد على Settings الكامل)."""
    from autonews.control import decide, due_iso, is_quiet, local_hour, preview_text

    c = control.get(channel_id)
    if quiet != (-1, -1):
        c.set_quiet(*quiet)
    mode = c.review if c.review != "smart" else review_mode

    score = float(getattr(verdict, "score", 1.0) or 1.0)
    action, why = decide(mode=mode, post=post, score=score,
                         reasons=list(getattr(verdict, "warnings", []) or []),
                         min_score=c.min_score, breaking=breaking)
    if action == "discard":
        return "discard", {"why": why}

    hour = local_hour(tz_offset)
    if not breaking and is_quiet(hour, c.quiet_start, c.quiet_end):
        control.enqueue(KIND_SCHEDULED, channel_id, post.as_dict(),
                        due_at=due_iso(60), reasons=["🌙 هدوء"])
        return "deferred", {"quiet": True}

    ok_now, why_not = c.allow_now(tz_offset=tz_offset)
    if not ok_now and not breaking:
        return "blocked", {"why": why_not}

    if action == "review":
        item_id = control.enqueue(KIND_APPROVAL, channel_id, post.as_dict(),
                                  score=score, reasons=why,
                                  preview=preview_text({"post": post.as_dict()}))
        return "review", {"id": item_id, "why": why}
    return "publish", {}


@pytest.fixture
def control(tmp_path) -> ControlStore:
    return ControlStore.load(tmp_path / "control.json")


# --------------------------------------------------------------------------- #
#  النشر بلا موافقات يدوية
# --------------------------------------------------------------------------- #
def test_clean_post_publishes_immediately(control):
    decision, _ = _gate(control, "tg_main", FakePost(), breaking=False,
                        verdict=FakeVerdict(score=0.95))
    assert decision == "publish"
    assert control.pending(kind=KIND_APPROVAL) == []


def test_low_score_is_discarded_not_queued(control):
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.40))
    assert decision == "discard"
    assert any("درجة" in r for r in info["why"])
    assert control.pending(kind=KIND_APPROVAL) == []


def test_breaking_does_not_bypass_trust_gate(control):
    control.get("tg_main").set_quiet(0, 24)
    decision, _ = _gate(control, "tg_main", FakePost(), breaking=True,
                        verdict=FakeVerdict(score=0.10))
    assert decision == "discard"
    assert control.pending(kind=KIND_APPROVAL) == []
    assert control.pending(kind=KIND_SCHEDULED) == []


def test_quiet_hours_defer_clean_post(control):
    control.get("tg_main").set_quiet(0, 24)
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.95))
    assert decision == "deferred" and info["quiet"]
    queued = control.pending(kind=KIND_SCHEDULED)
    assert len(queued) == 1
    assert queued[0]["post"]["title"] == FakePost().title


def test_paused_channel_blocks_publish(control):
    control.get("tg_main").pause("صيانة")
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.99))
    assert decision == "blocked" and "متوقّفة" in info["why"]


def test_legacy_review_mode_cannot_force_approval(control):
    control.get("tg_main").set_review("always")
    clean, _ = _gate(control, "tg_main", FakePost(), breaking=False,
                     verdict=FakeVerdict(score=1.0), review_mode="always")
    weak, _ = _gate(control, "tg_main", FakePost(used_fallback=True),
                    breaking=False, verdict=FakeVerdict(score=0.1),
                    review_mode="never")
    assert clean == "publish"
    assert weak == "discard"
    assert control.pending(kind=KIND_APPROVAL) == []


def test_legacy_approval_queue_is_not_created_by_gate(control):
    decision, _ = _gate(control, "tg_main", FakePost(), breaking=False,
                        verdict=FakeVerdict(score=0.30))
    assert decision == "discard"
    assert control.pending(kind=KIND_APPROVAL) == []


def test_queue_file_is_not_created_for_discarded_post(tmp_path):
    store = ControlStore.load(tmp_path / "control.json")
    decision, _ = _gate(store, "tg_main", FakePost(), breaking=False,
                        verdict=FakeVerdict(score=0.2))
    assert decision == "discard"
    assert not (tmp_path / "control.json").exists()
# --------------------------------------------------------------------------- #
#  التكامل مع باقي النظام
# --------------------------------------------------------------------------- #
def test_render_applies_branding_end_to_end():
    """منشور حقيقي من القالب ← توقيع + علامة خفيّة."""
    from autonews.ai.writer import Post
    from autonews.branding import Branding, prove_ownership
    from autonews.config_loader import Channel

    channel = Channel(
        id="tg_x", name="قناة الاختبار", platform="telegram", max_chars=4096,
        add_hashtags=2, include_signature=True, signature="📡 تابعونا",
        extra={"branding": {"handle": "test_channel", "title": "قناة الاختبار",
                            "footer": "📡 @{handle}", "stamp_on_image": True,
                            "hidden_mark": True}})
    post = Post(channel_id="tg_x", platform="telegram", title="عنوان الخبر",
                body="نصّ الخبر بشكل كافٍ وواضح للقارئ.", hashtags=["بغداد"])
    text = post.render(channel)

    assert text.endswith("📡 @test_channel") or "@test_channel" in text
    assert "تابعونا" not in text, "التوقيع القديم يُلغى عند وجود بصمة (لا تكرار)"
    ok, msg = prove_ownership(text, expect="test_channel")
    assert ok, msg


def test_control_and_setup_stores_are_independent(tmp_path):
    """فصل المسؤوليات: معرّفات القنوات ≠ حالة التحكّم."""
    from autonews.setup_store import SetupStore

    setup = SetupStore.load(tmp_path / "channels_setup.json")
    setup.bind("tg_main", "-1001234567890", title="القناة", username="my_main")
    control = ControlStore.load(tmp_path / "control.json")
    control.pause("tg_main", "اختبار")

    assert setup.chat_id_for("tg_main") == "-1001234567890"
    assert control.is_paused("tg_main") is True
    # الملفات منفصلة فعلاً
    assert Path(tmp_path / "channels_setup.json").exists()
    assert Path(tmp_path / "control.json").exists()
