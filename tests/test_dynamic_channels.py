"""Regression tests for dynamic channel profiles and independent reporting."""
from types import SimpleNamespace

from autonews.facts import has_independent_reports
from autonews.sectors import detect, detect_custom_profile, sources_for


def test_unknown_channel_gets_a_private_topic_profile():
    profile = detect("عالم الفضاء")
    assert profile is not None
    assert profile.key.startswith("custom_")
    assert profile.tags
    assert profile.queries


def test_custom_description_defines_topic():
    profile = detect_custom_profile("قناتي", "أخبار الفضاء والاكتشافات الفلكية")
    assert profile is not None
    assert "فضاء" in profile.keywords or "الفضاء" in profile.keywords
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
