"""Regression coverage for generated topic feeds."""

from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autonews.sectors import SECTORS, detect_custom_profile, suggest_sources


def test_known_sector_without_custom_queries_gets_generated_feeds():
    profile = replace(SECTORS["gaming"], queries=())
    assert not profile.queries
    sources = suggest_sources(profile)
    assert len(sources) >= 4
    assert {item["lang"] for item in sources} == {"ar", "en"}
    assert all(item["tags"] == list(profile.tags) for item in sources)
    assert all(item["url"].startswith("https://news.google.com/rss/search?") for item in sources)


def test_custom_topic_creates_deduplicated_language_feeds():
    profile = detect_custom_profile("قناة الفضاء", "أخبار الفضاء والاكتشافات الفلكية")
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



def test_default_translation_languages_cover_japanese_korean_and_chinese():
    from autonews.config_loader import TranslationSettings

    assert {"ja", "ko", "zh"}.issubset(set(TranslationSettings().langs))



def test_japanese_korean_and_chinese_articles_are_eligible_for_translation():
    from types import SimpleNamespace
    from autonews.i18n.translate import build_translate_prompt, needs_translation

    samples = {
        "ja": "日本の新作アニメが発表されました",
        "ko": "새로운 웹툰 작품이 발표되었습니다",
        "zh": "中国漫画发布了新的章节",
    }
    for language, headline in samples.items():
        article = SimpleNamespace(lang=language, title=headline, summary=headline, content="")
        assert needs_translation(article, target="ar")
        mixed_script = SimpleNamespace(
            lang=language, title=f"{headline} Naruto", summary=headline, content=""
        )
        assert needs_translation(mixed_script, target="ar")
        prompt = build_translate_prompt(article, target="ar")
        assert {"ja": "اليابانية", "ko": "الكورية", "zh": "الصينية"}[language] in prompt
