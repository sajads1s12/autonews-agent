"""اختبارات الحزمة A: وسوم UTM + لوحة الأداء + أفضل أسبوع."""
from __future__ import annotations

from datetime import timedelta

import pytest

from autonews.dashboard import (
    collect, render_best_of, render_html, write_reports,
)
from autonews.links import (
    UTMSettings, build_utm, campaign_for_post, parse_utm, tag_post_url,
)
from autonews.state import State
from autonews.utils import iso_now, utcnow


# --------------------------------------------------------------------------- #
#  UTM
# --------------------------------------------------------------------------- #
def test_utm_added_with_all_four_tags():
    url = build_utm("https://shafaq.com/ar/news", platform="telegram", campaign="outcome")
    tags = parse_utm(url)
    assert tags["utm_source"] == "telegram"
    assert tags["utm_medium"] == "social"
    assert tags["utm_campaign"] == "autonews-outcome"
    assert tags["utm_content"]


def test_utm_preserves_existing_query():
    url = build_utm("https://youtube.com/watch?v=abc", platform="x")
    assert "v=abc" in url and "utm_source=x" in url


def test_utm_does_not_duplicate_existing_tag():
    once = build_utm("https://a.com/b", platform="telegram")
    twice = build_utm(once, platform="facebook")
    assert twice == once, "لا يجب إضافة وسم ثانٍ لرابط موسوم"


def test_utm_disabled_returns_url_as_is():
    url = "https://a.com/b"
    assert build_utm(url, platform="x", settings=UTMSettings(enabled=False)) == url


def test_utm_ignores_non_http():
    assert build_utm("not-a-url", platform="x") == "not-a-url"
    assert build_utm("", platform="x") == ""


def test_utm_source_override():
    url = build_utm("https://a.com/b", platform="telegram",
                    settings=UTMSettings(source_override="myChannel"))
    assert parse_utm(url)["utm_source"] == "mychannel"


def test_campaign_names_by_kind():
    assert campaign_for_post(is_outcome=True, breaking=False) == "outcome"
    assert campaign_for_post(is_outcome=False, breaking=True) == "flash"
    assert campaign_for_post(is_outcome=False, breaking=False) == "regular"
    assert campaign_for_post(is_outcome=False, breaking=False, kind="matrix") == "matrix"


def test_tag_post_url_mutates_post():
    from autonews.ai.writer import Post

    p = Post(channel_id="tg", platform="telegram", title="خبر", body="نص",
             source_url="https://e.co/a", is_outcome=True)
    tag_post_url(p, platform="telegram")
    assert "utm_campaign=autonews-outcome" in p.source_url


# --------------------------------------------------------------------------- #
#  تجميع الأداء
# --------------------------------------------------------------------------- #
def _state_with_posts() -> State:
    st = State()
    now = utcnow()
    samples = [
        ("🔴 عاجل | خبر أول", "telegram", "tg_main", "shafaq", 1),
        ("📋 نتائج: عطلة البصرة", "telegram", "tg_main", "ina", 2),
        ("🗺️ خريطة المحافظات اليوم", "telegram", "tg_main", "ina", 3),
        ("🔄 تحديث | تمديد العطلة", "facebook", "fb_page", "shafaq", 5),
        ("خبر عادي جداً", "x", "x_main", "bbc_world", 30),
    ]
    for title, platform, channel, source, hours_ago in samples:
        st.posted.append({
            "at": (now - timedelta(hours=hours_ago)).isoformat(),
            "channel": channel, "platform": platform, "title": title,
            "url": "https://e.co/x", "source": source,
        })
    # منشور قديم جداً (خارج النافذة)
    st.posted.append({"at": (now - timedelta(days=20)).isoformat(),
                      "channel": "tg_main", "platform": "telegram",
                      "title": "قديم", "source": "shafaq"})
    return st


def test_collect_counts_only_window():
    snap = collect(_state_with_posts(), days=7)
    assert snap.total_posts == 5, "المنشور الأقدم من 7 أيام لا يُحتسب"


def test_collect_classifies_kinds():
    snap = collect(_state_with_posts(), days=7)
    assert snap.by_kind["نتيجة"] == 1
    assert snap.by_kind["عاجل"] == 1
    assert snap.by_kind["تحديث"] == 1
    assert snap.by_kind["خريطة المحافظات"] == 1
    assert snap.by_kind["عادي"] == 1


def test_collect_outcome_share():
    snap = collect(_state_with_posts(), days=7)
    assert snap.outcome_share == pytest.approx(0.2)


def test_best_posts_prioritize_outcomes():
    snap = collect(_state_with_posts(), days=7)
    assert snap.best_posts[0]["kind"] == "نتيجة"


def test_best_posts_merge_cross_platform():
    st = State()
    now = utcnow()
    for platform in ("telegram", "facebook", "x"):
        st.posted.append({"at": now.isoformat(), "channel": platform + "_ch",
                          "platform": platform, "title": "📋 نتائج موحّدة",
                          "source": "ina"})
    snap = collect(st, days=7)
    assert len(snap.best_posts) == 1, "المنشور الواحد على ٣ منصّات = سطر واحد"
    assert snap.best_posts[0]["count"] == 3
    assert set(snap.best_posts[0]["platforms"]) == {"telegram", "facebook", "x"}


def test_top_sources_from_posted():
    snap = collect(_state_with_posts(), days=7)
    assert snap.top_sources, "يجب أن تُقرأ أسماء المصادر"
    assert snap.top_sources[0][0] in {"shafaq", "ina"}


def test_flash_log_included_in_collect():
    st = State()
    st.flash_log.append({"at": iso_now(), "channel": "tg_main", "title": "🔴 عاجل فوري",
                         "source": "shafaq"})
    snap = collect(st, days=7)
    assert snap.total_posts == 1 and snap.by_kind["عاجل"] == 1


# --------------------------------------------------------------------------- #
#  العرض
# --------------------------------------------------------------------------- #
def test_html_is_standalone_rtl():
    html = render_html(collect(_state_with_posts(), days=7))
    assert 'dir="rtl"' in html and "http://" not in html.replace("http://www.w3.org", "")
    assert "<script" not in html, "لا سكربتات خارجية — لوحة تعمل بلا شبكة"
    assert "بطاقات النتائج" in html


def test_html_contains_metrics():
    html = render_html(collect(_state_with_posts(), days=7))
    assert "إجمالي المنشورات" in html and "النشر حسب الساعة" in html
    assert "أفضل منشورات الأسبوع" in html


def test_html_empty_state_is_safe():
    html = render_html(collect(State(), days=7))
    assert "لا بيانات" in html


def test_best_of_markdown_sections():
    md = render_best_of(collect(_state_with_posts(), days=7))
    assert "نتائج تستحقّ التذكير" in md
    assert "من أين جاءت الأخبار" in md


def test_best_of_empty_state():
    md = render_best_of(collect(State(), days=7))
    assert "لا منشورات" in md


def test_write_reports_creates_three_files(tmp_path):
    html_path = tmp_path / "dash.html"
    best_path = tmp_path / "best.md"
    out, best = write_reports(_state_with_posts(), output_file=str(html_path),
                              best_of_file=str(best_path), days=7)
    assert html_path.exists() and best_path.exists()
    assert html_path.with_suffix(".json").exists()
    assert "إجمالي المنشورات" in html_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  أسماء المصادر العربية
# --------------------------------------------------------------------------- #
def test_source_label_translates_ids():
    from autonews.dashboard import source_label

    assert source_label("ina_official") == "واع — محلي"
    assert source_label("shafaq") == "شفق نيوز"
    assert source_label("unknown_src") == "unknown_src"


def test_best_of_uses_arabic_labels():
    st = State()
    st.posted.append({"at": iso_now(), "channel": "tg_main", "platform": "telegram",
                      "title": "📋 نتائج: عطلة", "source": "ina_official"})
    md = render_best_of(collect(st, days=7))
    assert "واع — محلي" in md and "ina_official" not in md
