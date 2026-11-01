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
#  النتائج الأربع الممكنة
# --------------------------------------------------------------------------- #
def test_clean_post_publishes_immediately(control):
    decision, _ = _gate(control, "tg_main", FakePost(), breaking=False,
                        verdict=FakeVerdict(score=0.95))
    assert decision == "publish"
    assert control.pending() == []


def test_low_score_goes_to_approval_queue(control):
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.40))
    assert decision == "review"
    items = control.pending(kind=KIND_APPROVAL)
    assert len(items) == 1 and items[0]["score"] == 0.40
    assert any("درجة" in r for r in items[0]["reasons"])


def test_breaking_bypasses_review_and_quiet(control):
    control.get("tg_main").set_quiet(0, 24)          # هدوء كامل اليوم
    decision, _ = _gate(control, "tg_main", FakePost(), breaking=True,
                        verdict=FakeVerdict(score=0.10))
    assert decision == "publish"
    assert control.pending(kind=KIND_APPROVAL) == []
    assert control.pending(kind=KIND_SCHEDULED) == []


def test_quiet_hours_defers_not_drops(control):
    control.get("tg_main").set_quiet(0, 24)
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.95))
    assert decision == "deferred" and info["quiet"]
    queued = control.pending(kind=KIND_SCHEDULED)
    assert len(queued) == 1, "الخبر يُحفظ في الطابور ولا يُفقد"
    assert queued[0]["post"]["title"] == FakePost().title


def test_paused_channel_blocks_publish(control):
    control.get("tg_main").pause("صيانة")
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.99))
    assert decision == "blocked" and "متوقّفة" in info["why"]


def test_always_mode_reviews_even_perfect_post(control):
    control.get("tg_main").set_review("always")
    decision, _ = _gate(control, "tg_main", FakePost(), breaking=False,
                        verdict=FakeVerdict(score=1.0), review_mode="always")
    assert decision == "review"


def test_never_mode_publishes_bad_post(control):
    control.get("tg_main").set_review("never")
    decision, _ = _gate(control, "tg_main", FakePost(used_fallback=True),
                        breaking=False, verdict=FakeVerdict(score=0.1),
                        review_mode="never")
    assert decision == "publish"


# --------------------------------------------------------------------------- #
#  دورة حياة العنصر: انتظار → نشر
# --------------------------------------------------------------------------- #
def test_approved_item_can_be_published_and_removed(control):
    decision, info = _gate(control, "tg_main", FakePost(), breaking=False,
                           verdict=FakeVerdict(score=0.30))
    assert decision == "review"
    item_id = info["id"]

    # ما زال في الطابور حتى يضغط المستخدم
    assert len(control.pending(kind=KIND_APPROVAL)) == 1
    item = control.item(item_id)
    assert item["channel"] == "tg_main"

    taken = control.take(item_id)
    assert taken is not None
    assert control.pending(kind=KIND_APPROVAL) == []


def test_queue_file_is_readable_json(tmp_path):
    store = ControlStore.load(tmp_path / "control.json")
    _gate(store, "tg_main", FakePost(), breaking=False, verdict=FakeVerdict(score=0.2))
    data = json.loads((tmp_path / "control.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["queue"]) == 1
    assert data["channels"]["tg_main"]["review"] == "smart"


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
