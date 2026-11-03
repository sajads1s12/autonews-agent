"""Regression coverage for generated topic feeds."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autonews.sectors import SECTORS, detect, suggest_sources


def test_known_sector_without_custom_queries_gets_generated_feeds():
    profile = SECTORS["gaming"]
    assert not profile.queries
    sources = suggest_sources(profile)
    assert len(sources) >= 4
    assert {item["lang"] for item in sources} == {"ar", "en"}
    assert all(item["tags"] == list(profile.tags) for item in sources)
    assert all(item["url"].startswith("https://news.google.com/rss/search?") for item in sources)


def test_custom_topic_creates_deduplicated_language_feeds():
    profile = detect("فضاء", "أخبار الفضاء والاكتشافات الفلكية")
    assert profile is not None
    sources = suggest_sources(profile)
    ids = [item["id"] for item in sources]
    assert len(ids) == len(set(ids))
    assert {item["lang"] for item in sources} == {"ar", "en"}
    assert all(profile.tags[0] in item["tags"] for item in sources)


def test_generated_feed_ids_are_stable_for_same_profile():
    profile = SECTORS["gaming"]
    first = suggest_sources(profile)
    second = suggest_sources(profile)
    assert [item["id"] for item in first] == [item["id"] for item in second]
