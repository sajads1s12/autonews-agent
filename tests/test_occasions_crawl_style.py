"""اختبارات: المناسبات والعطل، تكرار-أم-تحديث، التصفّح الآلي، والأسلوب المحفّز."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from autonews.ai.writer import ContentWriter, Post, ensure_engaging, polish_body
from autonews.ai.prompts import BANNED_PHRASES, SYSTEM_PROMPT, build_user_prompt
from autonews.config_loader import Channel, Settings, Source
from autonews.ingest import Article
from autonews.ingest.autodiscover import discover, first_feed
from autonews.ingest.extract import extract_article_text
from autonews.ingest.fetch import Fetcher, decode_bytes
from autonews.occasions import (
    Occasion, OccasionCalendar, decide, has_update_signal, is_official_topic,
)
from autonews.utils import utcnow


# --------------------------------------------------------------------------- #
#  أدوات
# --------------------------------------------------------------------------- #
def art(title: str, summary: str = "", url: str = "https://e.co/1", **kw) -> Article:
    return Article(source_id="s", source_name="مصدر", title=title,
                   summary=summary or "ملخص الخبر " * 12, url=url, **kw)


def ch(**kw) -> Channel:
    d = dict(id="tg", name="قناة", platform="telegram", sources=["*"],
             max_chars=4096, add_hashtags=3)
    d.update(kw)
    return Channel(**d)


# --------------------------------------------------------------------------- #
#  1) المناسبات: المطابقة والتقويم
# --------------------------------------------------------------------------- #
def test_occasion_annual_match():
    o = Occasion(id="x", name="عيد", annual=["10-03"])
    assert o.matches(date(2026, 10, 3))
    assert not o.matches(date(2026, 10, 4))


def test_occasion_range_match():
    o = Occasion(id="ramadan", name="رمضان",
                 ranges=[(date(2026, 2, 18), date(2026, 3, 19))])
    assert o.matches(date(2026, 3, 1))
    assert not o.matches(date(2026, 4, 1))


def test_occasion_season_crosses_new_year():
    """موسم الأمطار 11-01 → 03-15 يعبر رأس السنة."""
    o = Occasion(id="rain", name="أمطار", season=("11-01", "03-15"))
    assert o.matches(date(2026, 12, 20))
    assert o.matches(date(2026, 2, 1))
    assert not o.matches(date(2026, 6, 1))


def test_calendar_from_repo_config():
    cal = OccasionCalendar.from_config("config/occasions.yml")
    assert len(cal.occasions) >= 10
    assert any(o.id == "ramadan" for o in cal.occasions)
    assert cal.update_keywords, "كلمات إشارات التحديث يجب أن تُحمّل"


def test_calendar_context_and_policy_on_holiday(monkeypatch):
    """محاكاة يوم عطلة: يجب أن يُفتح تساهل التكرار ويظهر سياق المناسبة."""
    cal = OccasionCalendar.from_config("config/occasions.yml")

    class FakeDT:
        pass

    eid = Occasion(id="eid", name="عيد الفطر", annual=["03-20"], relax_dedup=True,
                   update_window_hours=48, max_updates=3, tone="احتفالي",
                   style_hint="أخبار الصلاة والعطلة")
    cal.occasions = [eid]
    # نجبر «اليوم» ليكون 20 مارس
    import autonews.occasions as occ_mod

    fixed = utcnow().replace(month=3, day=20, hour=9)
    monkeypatch.setattr(occ_mod, "utcnow", lambda: fixed)
    hint = cal.context_hint(tz_offset=3)
    policy = cal.dedup_policy(tz_offset=3)
    assert "عيد الفطر" in hint and "احتفالي" in hint
    assert policy["relaxed"] is True
    assert policy["window_hours"] == 48
    assert policy["max_updates"] == 3


def test_calendar_no_occasion_no_relax(monkeypatch):
    cal = OccasionCalendar([Occasion(id="x", name="عيد", annual=["10-03"])])
    import autonews.occasions as occ_mod

    monkeypatch.setattr(occ_mod, "utcnow", lambda: utcnow().replace(month=6, day=15))
    assert cal.dedup_policy(tz_offset=3)["relaxed"] is False
    assert cal.context_hint(tz_offset=3) == ""


# --------------------------------------------------------------------------- #
#  2) تكرار أم تحديث؟ (المشكلة الأساسية التي طلبها المستخدم)
# --------------------------------------------------------------------------- #
UPDATE_KW = ["تمديد", "تفاصيل", "حصيلة", "قرار جديد", "استئناف", "إلغاء"]


def test_holiday_tomorrow_then_extension_is_published():
    """عطلة اليوم → تمديد العطلة غداً: **خبر جديد** لا تكرار."""
    old = "عطلة رسمية يوم الخميس في جميع الدوائر الحكومية"
    new = "تمديد العطلة الرسمية لتشمل يوم الأحد المقبل"
    verdict = decide(
        new_title=new, new_summary="أعلنت الأمانة العامة تمديد العطلة",
        past_titles=[old], published_at=utcnow() - timedelta(hours=18),
        update_keywords=UPDATE_KW,
        policy={"relaxed": True, "window_hours": 48, "similarity": 0.5, "occasions": ["عيد"]},
    )
    assert verdict.duplicate is False, verdict.reason
    assert verdict.is_update is True
    assert verdict.signal == "تمديد"


def test_same_headline_within_minutes_is_duplicate():
    """نفس العنوان من مصدر آخر بعد دقائق = تكرار حقيقي يُحجب."""
    old = "عطلة رسمية يوم الخميس في جميع الدوائر الحكومية"
    new = "عطلة رسمية يوم الخميس في جميع الدوائر الحكومية"
    verdict = decide(
        new_title=new, new_summary="", past_titles=[old],
        published_at=utcnow() - timedelta(minutes=20),
        update_keywords=UPDATE_KW,
        policy={"relaxed": True, "window_hours": 48, "similarity": 0.5, "occasions": ["عيد"]},
    )
    assert verdict.duplicate is True


def test_official_topic_relaxed_without_signal():
    """خبر عطلة/دوام مشابه داخل موسم عطلة يُعدّ تحديثاً حتى بلا إشارة صريحة."""
    verdict = decide(
        new_title="تفاصيل الدوام الرسمي خلال أيام العيد في الدوائر الحكومية",
        new_summary="أعلنت الوزارة جداول الدوام",
        past_titles=["الدوام الرسمي في الدوائر الحكومية أيام العيد"],
        published_at=utcnow() - timedelta(hours=10),
        update_keywords=UPDATE_KW,
        policy={"relaxed": True, "window_hours": 48, "similarity": 0.5, "occasions": ["عيد"]},
    )
    assert verdict.duplicate is False


def test_outside_occasion_similar_headline_is_duplicate():
    """بدون مناسبة: نفس الخبر بعد ساعات قليلة = تكرار."""
    verdict = decide(
        new_title="ارتفاع أسعار الذهب في الأسواق المحلية اليوم",
        new_summary="",
        past_titles=["ارتفاع أسعار الذهب في الأسواق المحلية اليوم"],
        published_at=utcnow() - timedelta(hours=2),
        update_keywords=UPDATE_KW,
        policy={"relaxed": False, "window_hours": 0, "similarity": 0.95, "occasions": []},
        strict_similarity=0.62,
    )
    assert verdict.duplicate is True


def test_unrelated_news_never_blocked():
    verdict = decide(
        new_title="افتتاح معرض الكتاب السنوي في العاصمة",
        new_summary="", past_titles=["عطلة رسمية يوم الخميس"],
        published_at=utcnow(), update_keywords=UPDATE_KW,
        policy={"relaxed": True, "window_hours": 48, "similarity": 0.5, "occasions": ["عيد"]},
    )
    assert verdict.duplicate is False


def test_update_signal_detection():
    assert has_update_signal("تمديد العطلة ليوم الأحد", UPDATE_KW) == "تمديد"
    assert has_update_signal("نشر تفاصيل جديدة عن الحادث", UPDATE_KW) == "تفاصيل"
    assert has_update_signal("خبر عادي بلا إشارات", UPDATE_KW) == ""


def test_official_topic_detection():
    assert is_official_topic("عطلة رسمية لجميع الدوائر")
    assert is_official_topic("مجلس الوزراء يعلن الدوام")
    assert not is_official_topic("مباراة كرة قدم في الدوري")


# --------------------------------------------------------------------------- #
#  3) التصفّح الآلي: الترميز والاستخراج والاكتشاف
# --------------------------------------------------------------------------- #
def test_decode_windows_1256():
    """مواقع عربية قديمة بلا charset صحيح — يجب ألّا يظهر النص مشوّهاً."""
    text = "أعلنت الحكومة عن عطلة رسمية يوم الخميس"
    raw = text.encode("windows-1256")
    assert decode_bytes(raw) == text


def test_decode_utf8_with_meta_charset():
    raw = '<meta charset="windows-1256">'.encode() + "عطلة رسمية".encode("windows-1256")
    assert "عطلة رسمية" in decode_bytes(raw)


def test_extract_json_ld():
    page = """
    <html><head><script type="application/ld+json">
    {"@type":"NewsArticle","headline":"عطلة رسمية",
     "articleBody":"أعلنت الأمانة العامة لمجلس الوزراء أن يوم الخميس عطلة رسمية في جميع الدوائر،
      باستثناء الدوائر الخدمية والصحية التي تعمل بنظام المناوبات. وأشار البيان إلى أن الدوام
      يعود يوم الأحد المقبل في المواعيد المعتادة."}
    </script></head><body></body></html>
    """
    result = extract_article_text(page)
    assert result.method == "json_ld"
    assert "عطلة رسمية" in result.text
    assert result.ok


def test_extract_open_graph_fallback():
    page = ('<meta property="og:description" content="أعلنت الحكومة تمديد العطلة الرسمية '
            'لتشمل يوم الأحد المقبل، مع استمرار عمل الدوائر الخدمية والصحية خلال أيام العطلة '
            'وفق نظام المناوبات المعتمد في الوزارات.">')
    result = extract_article_text(page)
    assert result.method == "open_graph"
    assert "تمديد" in result.text


def test_extract_readability_paragraphs():
    paras = "".join(
        f"<p>هذا نص الخبر رقم {i} ويحتوي تفاصيل مهمة عن العطلة الرسمية والخدمات البلدية في المحافظة.</p>"
        for i in range(6))
    page = f"<html><body><nav>قائمة التنقل</nav>{paras}<footer>تذييل</footer></body></html>"
    result = extract_article_text(page)
    assert result.ok
    assert "قائمة التنقل" not in result.text
    assert "تذييل" not in result.text


def test_extract_returns_empty_on_garbage():
    assert not extract_article_text("<html><body></body></html>").ok


def test_discover_finds_common_path():
    """موقع بلا إعلان خلاصة، لكن /rss.xml يعمل."""
    feed = '<?xml version="1.0"?><rss><channel><title>موقع</title><item><title>x</title></item></channel></rss>'.encode()

    def fetcher(url: str) -> bytes:
        if url.endswith("/rss.xml"):
            return feed
        raise OSError("404")

    found = discover("https://example.com", fetcher)
    assert found and found[0].url == "https://example.com/rss.xml"
    assert found[0].method == "common_path"


def test_discover_reads_link_tag():
    page = b'<html><head><link rel="alternate" type="application/rss+xml" href="/feeds/news"></head></html>'
    feed = '<?xml version="1.0"?><rss><channel><title>خلاصة</title></channel></rss>'.encode()

    def fetcher(url: str) -> bytes:
        if url.endswith("/feeds/news"):
            return feed
        if url == "https://example.com/":
            return page
        raise OSError("no")

    found = discover("https://example.com/", fetcher)
    assert any(f.url == "https://example.com/feeds/news" for f in found)


def test_discover_returns_empty_when_nothing_found():
    def fetcher(url: str) -> bytes:
        raise OSError("blocked")

    assert discover("https://example.com", fetcher) == []
    assert first_feed("https://example.com", fetcher) == ""


def test_fetcher_handles_network_failure_gracefully():
    f = Fetcher(timeout=1, retries=1)
    result = f.get("https://127.0.0.1:1/definitely-not-there")
    assert not result.ok and result.error
    assert "طلبات=" in f.report()


def test_article_thin_detection():
    assert art("عنوان طويل بما يكفي للفحص", summary="سطر").is_thin
    long_summary = "جملة تفصيلية طويلة " * 20
    assert not art("عنوان", summary=long_summary).is_thin


# --------------------------------------------------------------------------- #
#  4) الأسلوب المحفّز (لا ممل)
# --------------------------------------------------------------------------- #
def test_polish_removes_dead_phrases():
    raw = ("جدير بالذكر أن الوزارة أعلنت، وعلى صعيد متصل، عن تمديد العطلة. "
           "في سياق متصل أكد المسؤول أن الدوام يعود الأحد.")
    out = polish_body(raw)
    for phrase in ("جدير بالذكر", "على صعيد متصل", "في سياق متصل"):
        assert phrase not in out


def test_polish_removes_title_repetition():
    title = "تمديد العطلة الرسمية"
    out = polish_body(f"{title} أعلنت الوزارة اليوم قرار التمديد.", title=title)
    assert not out.startswith(title)


def test_polish_splits_overlong_sentences():
    long_sentence = ("أعلنت الوزارة اليوم عن قرار مهم يتعلق بالدوام الرسمي في جميع المحافظات، "
                     "وأشارت إلى أن القرار سيطبّق ابتداءً من الأسبوع المقبل في كل الدوائر "
                     "باستثناء الخدمية منها، مع استمرار المناوبات في المراكز الصحية.")
    out = polish_body(long_sentence, max_words=26)
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) >= 2, "الجملة الطويلة يجب أن تُقسَّم إلى أسطر"
    assert all(len(l.split()) <= 26 for l in lines)


def test_ensure_engaging_adds_question_for_social():
    out = ensure_engaging("خبر عن تمديد العطلة", platform="facebook")
    assert out.rstrip().endswith("؟")
    # لا يضيف سؤالاً إن وُجد
    already = "خبر عن التمديد. ما رأيك؟"
    assert ensure_engaging(already, platform="facebook") == already
    # لا سؤال في تيليجرام
    assert ensure_engaging("خبر", platform="telegram") == "خبر"


def test_prompt_contains_engagement_rules_and_context():
    prompt = build_user_prompt(
        art("عطلة رسمية غداً"), ch(), breaking=False,
        context_hint="عيد الفطر (احتفالي) — أخبار الصلاة والعطلة",
        is_update=True, previous_title="عطلة رسمية يوم الخميس")
    assert "الخطّاف" in prompt
    assert "عيد الفطر" in prompt
    assert "تحديث على خبر سابق" in prompt
    assert "الخطّاف أولاً" in SYSTEM_PROMPT


def test_system_prompt_forbids_clickbait():
    assert "لا تختلق" in SYSTEM_PROMPT
    assert "هل تعلم" in SYSTEM_PROMPT       # مذكورة كمنع
    assert BANNED_PHRASES


def test_writer_output_has_hook_and_polish(tmp_path):
    payload = json.dumps({
        "title": "تمديد العطلة ليشمل الأحد",
        "body": "جدير بالذكر أن الأمانة العامة أعلنت تمديد العطلة الرسمية لتشمل يوم الأحد المقبل، "
                "مع استمرار عمل الدوائر الخدمية والصحية وفق نظام المناوبات.",
        "hook": "تمديد العطلة ليشمل يوم الأحد",
        "reader_value": "الدوام يعود الأحد في المواعيد المعتادة",
        "hashtags": ["العراق", "عطلة"], "category": "مجتمع",
        "key_points": ["استثناء الدوائر الخدمية"], "sensitive": False,
    })
    body = json.dumps({"candidates": [{"content": {"parts": [{"text": payload}]}}]})
    from autonews.ai.engine import KeyRotator

    engine = KeyRotator(keys=["K"], models=["m"], health_path=tmp_path / "h.json",
                        post_fn=lambda *a: (200, body))
    writer = ContentWriter(engine, Settings())
    post = writer.write(art("عطلة رسمية يوم الخميس"), ch(platform="facebook"),
                        is_update=True, context_hint="عيد")
    assert post.hook
    assert post.reader_value
    assert post.is_update is True
    assert "جدير بالذكر" not in post.body
    rendered = post.render(ch(platform="facebook"))
    assert "🔄 تحديث" in rendered
    assert "💡" not in rendered  # لا نضيف سطر قيمة للقارئ إذا لم ينتجه النموذج


def test_update_post_gets_update_badge():
    p = Post(channel_id="tg", platform="telegram", title="تمديد العطلة", body="نص",
             is_update=True)
    assert p.render(ch()).splitlines()[0].startswith("🗞 🔄 تحديث")


def test_breaking_and_update_badges_do_not_collide():
    p = Post(channel_id="tg", platform="telegram", title="انفجار", body="نص",
             breaking=True, is_update=True)
    first = p.render(ch()).splitlines()[0]
    assert "🔴 عاجل" in first and "🔄" not in first
