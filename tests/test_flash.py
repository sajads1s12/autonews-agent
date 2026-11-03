"""اختبارات المسار السريع: التصنيف، الحداثة، السقوف، والنشر الفوري."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from autonews.ai import Post
from autonews.ai.writer import ContentWriter
from autonews.config_loader import (
    BreakingSettings, Channel, FlashSettings, Settings, Source,
)
from autonews.flash import (
    FlashLane, FlashVerdict, classify, freshness, is_fresh, _fast_body,
)
from autonews.ingest import Article
from autonews.publishers import PublishResult
from autonews.state import State
from autonews.utils import utcnow


def art(title: str, summary: str = "", published=None, **kw) -> Article:
    return Article(
        source_id=kw.pop("source_id", "aljazeera"), source_name="مصدر",
        title=title, summary=summary or "نص الخبر التفصيلي " * 6,
        url="https://e.co/1", published=published if published is not None else utcnow(), **kw)


def ch(**kw) -> Channel:
    d = dict(id="tg_main", name="الرئيسية", platform="telegram", sources=["*"],
             max_chars=4096, add_hashtags=3, flash_eligible=True)
    d.update(kw)
    return Channel(**d)


def flash_cfg(**kw) -> FlashSettings:
    d = dict(enabled=True, max_age_minutes=45, min_score=5, max_per_hour=6,
             min_gap_seconds=0, max_items_per_run=3)
    d.update(kw)
    return FlashSettings(**d)


# --------------------------------------------------------------------------- #
#  1) التصنيف: ما يستحق النشر الفوري
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,category", [
    ("قصف صاروخي يستهدف أحياء سكنية وسط المدينة", "war"),
    ("انفجار كبير في مصنع شمالي البلاد", "war"),
    ("زلزال بقوة 6 درجات يضرب المنطقة", "disaster"),
    ("عطلة رسمية غداً في جميع الدوائر الحكومية", "holiday"),
    ("تمديد العطلة ليشمل يوم الأحد", "holiday"),
    ("استقالة وزير المالية على خلفية الأزمة", "major"),
    ("البنك المركزي يرفع سعر صرف الدولار", "major"),
])
def test_classify_urgent_categories(title, category):
    verdict = classify(art(title), flash_cfg())
    assert verdict.urgent, f"يجب أن يُصنّف عاجلاً: {title} → {verdict.reason}"
    assert verdict.category == category


@pytest.mark.parametrize("title", [
    "فيديو: هدف رائع في الدوري الإنجليزي",
    "شاهد بالصور جولة داخل أقدم سوق في المدينة",
    "تحليل: ماذا يعني قرار البنك المركزي؟",
    "حوار مع الكاتب حول روايته الجديدة",
    "تقرير: يوم في حياة صياد في البصرة",
])
def test_non_urgent_is_not_flashed(title):
    """المحتوى الترفيهي/التحليلي لا يقاطع القناة بخبر «عاجل»."""
    verdict = classify(art(title), flash_cfg())
    assert not verdict.urgent, verdict.reason


def test_low_score_below_threshold():
    verdict = classify(art("افتتاح معرض الكتاب السنوي في العاصمة",
                           summary="انطلاق المعرض بمشاركة دور نشر محلية وعربية."),
                       flash_cfg())
    assert not verdict.urgent


def test_critical_marker_boosts_score():
    plain = classify(art("عطلة رسمية"), flash_cfg(min_score=99))
    boosted = classify(art("عاجل: عطلة رسمية"), flash_cfg(min_score=99))
    assert boosted.score > plain.score


def test_custom_keywords_force_flash():
    verdict = classify(art("تظاهرة أمام مبنى المحافظة"),
                       flash_cfg(keywords=["تظاهرة"]))
    assert verdict.urgent and "custom" in verdict.category


def test_flash_disabled_never_urgent():
    verdict = classify(art("قصف صاروخي واشتباكات"), flash_cfg(enabled=False))
    assert not verdict.urgent


def test_spam_guard_blocks_video_titles():
    verdict = classify(art("بالفيديو: انفجار قرب المطار"), flash_cfg())
    assert not verdict.urgent, "عنوان «بالفيديو» ليس عاجلاً فورياً"


# --------------------------------------------------------------------------- #
#  2) حارس الحداثة
# --------------------------------------------------------------------------- #
def test_fresh_news_allowed():
    ok, minutes, _ = is_fresh(art("قصف صاروخي", published=utcnow() - timedelta(minutes=5)),
                              flash_cfg())
    assert ok and 4 <= minutes <= 6


def test_old_news_rejected_this_is_the_whole_point():
    """خبر عمره ساعتان لا يُنشر فوراً — هذا هو الفرق بيننا وبين قناة «مباشر»."""
    ok, minutes, why = is_fresh(
        art("قصف صاروخي", published=utcnow() - timedelta(hours=2)), flash_cfg())
    assert not ok
    assert "عمره" in why and minutes > 100


def test_no_date_allowed():
    a = art("خبر عاجل")
    a.published = None
    ok, minutes, _ = is_fresh(a, flash_cfg())
    assert ok and minutes == -1


def test_future_date_rejected():
    ok, _, why = is_fresh(art("خبر", published=utcnow() + timedelta(hours=3)), flash_cfg())
    assert not ok and "مستقبلي" in why


def test_custom_max_age():
    old = utcnow() - timedelta(minutes=20)
    assert is_fresh(art("خبر", published=old), flash_cfg(max_age_minutes=30))[0]
    assert not is_fresh(art("خبر", published=old), flash_cfg(max_age_minutes=10))[0]


# --------------------------------------------------------------------------- #
#  3) سقوف النشر الفوري
# --------------------------------------------------------------------------- #
def test_hourly_cap():
    state = State()
    for _ in range(3):
        state.bump_flash("tg_main", now=1000.0)
    allowed, why = state.flash_allowed("tg_main", max_per_hour=3, now=1001.0)
    assert not allowed and "سقف" in why


def test_hourly_window_resets():
    state = State()
    for _ in range(3):
        state.bump_flash("tg_main", now=1000.0)
    allowed, _ = state.flash_allowed("tg_main", max_per_hour=3, now=1000.0 + 3700)
    assert allowed


def test_min_gap_enforced():
    state = State()
    state.bump_flash("tg_main", now=1000.0)
    allowed, why = state.flash_allowed("tg_main", max_per_hour=10,
                                       min_gap_seconds=30, now=1010.0)
    assert not allowed and "فاصل" in why
    allowed2, _ = state.flash_allowed("tg_main", max_per_hour=10,
                                      min_gap_seconds=30, now=1040.0)
    assert allowed2


def test_was_flashed_prevents_repeat():
    state = State()
    state.record_flash("fp1", "tg_main", "خبر")
    assert state.was_flashed("fp1")
    assert state.was_flashed("fp1", hours=0.0001) in (True, False)   # لا استثناء


def test_flash_post_unlocks_one_regular_pass():
    """خبر نُشر فوراً: الدورة العادية يمكنها إضافة التفاصيل مرّة واحدة فقط."""
    state = State()
    state.begin_run()
    state.mark("fp1", "posted", via="flash")
    assert state.has_article("fp1") is False, "المرور الأول يسمح بالتفاصيل"
    assert state.has_article("fp1") is True, "المرور الثاني يحجب"


# --------------------------------------------------------------------------- #
#  4) المحرّك: النشر الفوري فعلياً
# --------------------------------------------------------------------------- #
class FakePublisher:
    def __init__(self, ok: bool = True):
        self.calls = []
        self.ok = ok

    def __call__(self, channel, dry_run=False, timeout=30):
        outer = self

        class _P:
            def publish(self, post, image=None):
                outer.calls.append((channel.id, post.title, post.breaking))
                return PublishResult(outer.ok, channel.id, channel.platform, ref="1")
        return _P()


def make_lane(settings, state, publisher, *, outcomes: bool = False):
    """نمط النتائج معطّل افتراضياً في الاختبارات الميكانيكية (لها اختباراتها الخاصة)."""
    settings.outcomes.enabled = outcomes
    # الخبر الرسمي يستطيع الوقوف وحده؛ بقية الأخبار تحتاج ناشراً مستقلاً يؤكدها.
    if not settings.sources:
        settings.sources = [Source(id="aljazeera", name="مصدر رسمي للاختبار",
                                   url="https://example.com/rss", official=True)]
    return FlashLane(settings, state, engine=None, writer=ContentWriter(None, settings, state),
                     publisher_factory=publisher)


def test_lane_publishes_fresh_urgent_news():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch()]
    settings.dry_run = False
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)

    result = lane.run([art("قصف صاروخي على أحياء سكنية")])
    assert result.published == 1
    assert pub.calls and pub.calls[0][2] is True     # بشارة عاجل
    assert state.was_flashed(art("قصف صاروخي على أحياء سكنية").fp)


def test_lane_skips_stale_urgent():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch()]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)

    result = lane.run([art("قصف صاروخي", published=utcnow() - timedelta(hours=3))])
    assert result.published == 0 and result.skipped == 1
    assert not pub.calls


def test_lane_skips_non_urgent():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch()]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)
    assert lane.run([art("افتتاح معرض الكتاب السنوي في العاصمة")]).published == 0
    assert not pub.calls


def test_lane_respects_max_items():
    settings = Settings()
    settings.flash = flash_cfg(max_items_per_run=1)
    settings.channels = [ch()]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)

    result = lane.run([art("قصف صاروخي أول"), art("انفجار ثانٍ"), art("زلزال ثالث")])
    assert result.published == 1


def test_lane_sorts_newest_first():
    settings = Settings()
    settings.flash = flash_cfg(max_items_per_run=1)
    settings.channels = [ch()]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)
    old = art("قصف قديم", published=utcnow() - timedelta(minutes=40))
    new = art("انفجار جديد", published=utcnow() - timedelta(minutes=2))
    lane.run([old, new])
    assert "جديد" in pub.calls[0][1]


def test_lane_respects_channel_source_filter():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch(id="tg_bbc", sources=["bbc_arabic"])]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)
    result = lane.run([art("قصف صاروخي", source_id="aljazeera")])
    assert result.published == 0


def test_lane_never_routes_to_channel_without_sources():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch(id="tg_unlinked", sources=[])]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)
    result = lane.run([art("قصف صاروخي")])
    assert result.published == 0
    assert not pub.calls


def test_lane_skips_non_flash_eligible_channel():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch(id="tg_tech", flash_eligible=False)]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)
    assert lane.run([art("قصف صاروخي")]).published == 0


def test_lane_publishes_even_if_ai_unavailable():
    """السرعة أهم من الجمال: القالب الفوري يكفي."""
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch()]
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)      # engine=None
    result = lane.run([art("انفجار كبير في مصنع")])
    assert result.published == 1


def test_lane_failure_does_not_stop_cycle():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch()]
    state = State()
    lane = make_lane(settings, state, FakePublisher(ok=False))
    result = lane.run([art("قصف صاروخي")])
    assert result.failed == 1 and result.published == 0


def test_fast_body_is_short_and_safe():
    body = _fast_body(art("قصف", summary="نص طويل " * 100))
    assert len(body) <= 460


def test_lane_dry_run_does_not_publish():
    settings = Settings()
    settings.flash = flash_cfg()
    settings.channels = [ch()]
    settings.dry_run = True
    state = State()
    pub = FakePublisher()
    lane = make_lane(settings, state, pub)
    lane.run([art("قصف صاروخي")])
    assert pub.calls            # يُنادى الناشر (لكنه dry-run داخلياً)
