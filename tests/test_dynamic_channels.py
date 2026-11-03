"""Regression tests for dynamic channel profiles and independent reporting."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autonews.config_loader import Channel
from autonews.facts import has_independent_reports
from autonews.router import ContentRouter
from autonews.sectors import (
    _source_langs, detect, detect_custom_profile, sources_for, suggest_sources,
)


def test_unknown_channel_gets_a_private_topic_profile():
    profile = detect("عالم الفضاء")
    assert profile is not None
    assert profile.key.startswith("custom_")
    assert profile.tags
    assert profile.queries


def test_custom_description_defines_topic():
    profile = detect_custom_profile("قناتي", "أخبار الفضاء والاكتشافات الفلكية")
    assert profile is not None
    assert profile.keywords
    assert profile.tags


def test_missing_topic_sources_never_become_wildcard():
    profile = detect("عالم الفضاء")
    selected, _ = sources_for(profile, [{"id": "general", "tags": ["أخبار"]}])
    assert selected == []
    assert "*" not in selected


def test_independent_reports_require_distinct_publishers():
    first = SimpleNamespace(
        source_id="one", title="NASA announces discovery of a new planet",
        url="https://one.example/news/planet", origin_url="")
    same_publisher = SimpleNamespace(
        source_id="one", title="NASA announces discovery of a new planet",
        url="https://one.example/another", origin_url="")
    independent = SimpleNamespace(
        source_id="two", title="NASA announces discovery of a new planet",
        url="https://two.example/story/planet", origin_url="")
    sources = {
        "one": SimpleNamespace(official=False),
        "two": SimpleNamespace(official=False),
    }
    assert not has_independent_reports(first, [first, same_publisher], sources)
    assert has_independent_reports(first, [first, independent], sources)



def test_chinese_manhua_gets_its_own_profile_and_chinese_feeds():
    profile = detect("قناة المانهوا الصينية")
    assert profile is not None
    assert profile.key == "manhua"
    sources = suggest_sources(profile)
    assert {item["lang"] for item in sources} == {"ar", "en", "zh"}
    assert all("مانهوا صينية" in item["tags"] for item in sources)


def test_manhwa_and_manga_include_original_language_feeds():
    manhwa = detect("أخبار المانهوا الكورية")
    manga = detect("آخر أخبار المانغا اليابانية")
    assert manhwa is not None and manhwa.key == "manhwa"
    assert manga is not None and manga.key == "manga"
    assert "ko" in {item["lang"] for item in suggest_sources(manhwa)}
    assert "ja" in {item["lang"] for item in suggest_sources(manga)}


def test_google_news_language_detection_handles_non_english_languages():
    assert _source_langs({"url": "https://news.google.com/rss/search?q=x&hl=ja&gl=JP&ceid=JP:ja"}) == {"ja"}
    assert _source_langs({"url": "https://news.google.com/rss/search?q=x&hl=ko&gl=KR&ceid=KR:ko"}) == {"ko"}
    assert _source_langs({"url": "https://news.google.com/rss/search?q=x&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"}) == {"zh"}



def test_specialized_channel_with_no_matching_feed_stays_silent():
    channel = Channel(
        id="tg_space", name="أخبار الفضاء", platform="telegram",
        sources=[], tags=["custom_space"], enabled=True,
    )
    article = SimpleNamespace(
        source_id="general", title="خبر عام", summary="",
        tags=["أخبار"], published=None,
    )
    assert ContentRouter([channel]).targets(article) == []
