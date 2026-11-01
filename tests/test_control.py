"""اختبارات لوحة التحكّم: الإيقاف · ساعات الهدوء · المراجعة الذكية · الطابور."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from autonews.control import (
    KIND_APPROVAL, KIND_SCHEDULED, REVIEW_ALWAYS, REVIEW_NEVER, REVIEW_SMART,
    ChannelControl, ControlStore, decide, due_iso, is_quiet, local_hour,
    minutes_until_open, preview_text, review_reasons,
)

UTC = timezone.utc


@dataclass
class FakePost:
    title: str = "خبر عادي عن الخدمات"
    body: str = "تفاصيل الخبر هنا بشكل كافٍ."
    source_url: str = "https://example.com/news"
    used_fallback: bool = False
    sensitive: bool = False

    def as_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "source_url": self.source_url,
                "used_fallback": self.used_fallback, "sensitive": self.sensitive}


# --------------------------------------------------------------------------- #
#  ساعات الهدوء
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hour,start,end,expected", [
    (23, 22, 6, True), (3, 22, 6, True), (5, 22, 6, True),
    (6, 22, 6, False), (12, 22, 6, False), (21, 22, 6, False),
    (10, 9, 17, True), (17, 9, 17, False), (8, 9, 17, False),
    (3, -1, -1, False),          # معطّل
])
def test_is_quiet_windows(hour, start, end, expected):
    assert is_quiet(hour, start, end) is expected


def test_minutes_until_open_midnight_wrap():
    # الساعة 23 وبداية 22 نهاية 6 ⇒ 7 ساعات
    assert minutes_until_open(23, 0, 22, 6) == 7 * 60
    assert minutes_until_open(3, 30, 22, 6) == 150
    assert minutes_until_open(12, 0, 22, 6) == 0


def test_local_hour_uses_offset():
    now = datetime(2026, 11, 1, 21, 0, tzinfo=UTC)
    assert local_hour(3, now=now) == 0        # 21:00 UTC = 00:00 بغداد
    assert local_hour(0, now=now) == 21


# --------------------------------------------------------------------------- #
#  تحكّم القناة
# --------------------------------------------------------------------------- #
def test_pause_blocks_publishing():
    c = ChannelControl(channel_id="tg_main")
    assert c.allow_now()[0] is True
    c.pause("صيانة")
    ok, why = c.allow_now()
    assert ok is False and "صيانة" in why
    c.resume()
    assert c.allow_now()[0] is True


def test_quiet_hours_block_and_label():
    c = ChannelControl(channel_id="tg_main", quiet_start=0, quiet_end=24)
    ok, why = c.allow_now()
    assert ok is False and "هدوء" in why
    assert "🌙" in c.quiet_label()
    c.clear_quiet()
    assert c.quiet_label() == "بلا هدوء"


def test_hourly_cap():
    c = ChannelControl(channel_id="tg_main", max_per_hour=2)
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    c.bump(now=now)
    c.bump(now=now)
    ok, why = c.allow_now(now=now)
    assert ok is False and "سقف" in why


def test_hourly_cap_resets_next_hour():
    c = ChannelControl(channel_id="tg_main", max_per_hour=1)
    c.bump(now=datetime(2026, 11, 1, 12, 5, tzinfo=UTC))
    later = datetime(2026, 11, 1, 13, 5, tzinfo=UTC)
    assert c.allow_now(now=later)[0] is True


# --------------------------------------------------------------------------- #
#  المراجعة الذكية
# --------------------------------------------------------------------------- #
def test_smart_mode_publishes_clean_post():
    action, why = decide(mode=REVIEW_SMART, post=FakePost(), score=0.95)
    assert action == "publish" and why == []


def test_smart_mode_reviews_low_score():
    action, why = decide(mode=REVIEW_SMART, post=FakePost(), score=0.50, min_score=0.85)
    assert action == "review" and any("درجة" in w for w in why)


def test_smart_mode_reviews_fallback_writing():
    action, why = decide(mode=REVIEW_SMART, post=FakePost(used_fallback=True), score=0.95)
    assert action == "review" and any("احتياطية" in w for w in why)


def test_smart_mode_reviews_sensitive_words():
    post = FakePost(title="انفجار قرب سوق", body="سقوط وفيات")
    action, why = decide(mode=REVIEW_SMART, post=post, score=0.95)
    assert action == "review" and any("حسّاسة" in w for w in why)


def test_smart_mode_reviews_missing_source():
    post = FakePost(source_url="")
    action, why = decide(mode=REVIEW_SMART, post=post, score=0.95)
    assert action == "review" and any("مصدر" in w for w in why)


def test_breaking_never_waits_in_smart_mode():
    """السرعة شرط أساسي: العاجل يُنشر حتى لو كان مشكوكاً."""
    post = FakePost(title="انفجار", used_fallback=True, source_url="")
    action, _ = decide(mode=REVIEW_SMART, post=post, score=0.2, breaking=True)
    assert action == "publish"


def test_always_mode_reviews_everything_even_breaking():
    action, why = decide(mode=REVIEW_ALWAYS, post=FakePost(), score=1.0, breaking=True)
    assert action == "review" and why


def test_never_mode_never_reviews():
    action, _ = decide(mode=REVIEW_NEVER, post=FakePost(used_fallback=True), score=0.1)
    assert action == "publish"


def test_extra_reasons_force_review():
    action, why = decide(mode=REVIEW_SMART, post=FakePost(), score=0.99,
                         reasons=["تكرار محتمل"])
    assert action == "review" and "تكرار محتمل" in why


# --------------------------------------------------------------------------- #
#  الطابور
# --------------------------------------------------------------------------- #
def test_enqueue_and_take(tmp_path):
    store = ControlStore.load(tmp_path / "c.json")
    item_id = store.enqueue(KIND_APPROVAL, "tg_main", FakePost().as_dict(), score=0.7,
                            reasons=["درجة منخفضة"])
    assert len(store.pending(kind=KIND_APPROVAL)) == 1
    item = store.take(item_id)
    assert item and item["channel"] == "tg_main"
    assert store.pending(kind=KIND_APPROVAL) == []


def test_queue_persists_between_loads(tmp_path):
    path = tmp_path / "c.json"
    s1 = ControlStore.load(path)
    s1.enqueue(KIND_APPROVAL, "tg_anime", FakePost().as_dict())
    s1.get("tg_main").pause("اختبار")
    s1.save()

    s2 = ControlStore.load(path)
    assert len(s2.pending(kind=KIND_APPROVAL)) == 1
    assert s2.is_paused("tg_main") is True
    assert s2.get("tg_anime").channel_id == "tg_anime"


def test_due_respects_due_at(tmp_path):
    store = ControlStore.load(tmp_path / "c.json")
    # معرّفان مختلفان: نغيّر العنوان كي لا يتصادم الـ hash
    future = store.enqueue(KIND_SCHEDULED, "tg_main",
                           FakePost(title="مؤجّل لاحقاً").as_dict(), due_at=due_iso(60))
    past = store.enqueue(KIND_SCHEDULED, "tg_main",
                         FakePost(title="حان الآن").as_dict(), due_at=due_iso(-5))
    due_ids = {i["id"] for i in store.due(kind=KIND_SCHEDULED)}
    assert past in due_ids and future not in due_ids


def test_approvals_never_auto_publish(tmp_path):
    """عناصر الموافقة لا تُنشر تلقائياً أبداً — حتى لو حان وقتها."""
    store = ControlStore.load(tmp_path / "c.json")
    store.enqueue(KIND_APPROVAL, "tg_main", FakePost().as_dict(), due_at=due_iso(-30))
    assert store.due(kind=KIND_SCHEDULED) == []


def test_purge_drops_old_items(tmp_path):
    store = ControlStore.load(tmp_path / "c.json")
    item_id = store.enqueue(KIND_SCHEDULED, "tg_main", FakePost().as_dict())
    store.queue[item_id]["created_at"] = (
        datetime.now(UTC) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert store.purge(max_hours=48) == 1
    assert store.pending() == []


# --------------------------------------------------------------------------- #
#  العرض
# --------------------------------------------------------------------------- #
def test_preview_text_contains_title_and_buttons_data(tmp_path):
    store = ControlStore.load(tmp_path / "c.json")
    store.enqueue(KIND_APPROVAL, "tg_main", FakePost().as_dict(), score=0.62,
                  reasons=["صياغة احتياطية"])
    item = store.pending()[0]
    text = preview_text(item)
    assert "خبر عادي" in text and "0.62" in text and "احتياطية" in text


def test_summary_counts(tmp_path):
    store = ControlStore.load(tmp_path / "c.json")
    store.get("tg_main").pause()
    store.enqueue(KIND_APPROVAL, "tg_main", FakePost().as_dict())
    text = store.summary([("tg_main", "📰 العامة"), ("tg_anime", "🎬 الأنمي")])
    assert "⏸ متوقّفة: <b>1</b>" in text
    assert "👁 تنتظر موافقتك: <b>1</b>" in text


def test_review_reasons_empty_for_clean_post():
    assert review_reasons(FakePost(), score=0.95) == []
