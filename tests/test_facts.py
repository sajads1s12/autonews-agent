import unittest
from types import SimpleNamespace

from autonews.facts import has_independent_reports
from autonews.sectors import detect, detect_custom_profile, suggest_sources, sources_for


def article(source_id, url, title):
    return SimpleNamespace(source_id=source_id, url=url, title=title)


class IndependentReportTests(unittest.TestCase):
    def test_two_publishers_cover_same_story(self):
        first = article("feed_a", "https://site-a.example/news/1",
                        "Studio announces a new animated series for 2027")
        second = article("feed_b", "https://site-b.example/story/9",
                         "Studio announces a new animated series for 2027")
        sources = {"feed_a": SimpleNamespace(official=False),
                   "feed_b": SimpleNamespace(official=False)}
        self.assertTrue(has_independent_reports(first, [first, second], sources))

    def test_same_publisher_does_not_count_twice(self):
        first = article("feed_a", "https://site-a.example/news/1", "Major update announced today")
        second = article("feed_b", "https://www.site-a.example/story/9", "Major update announced today")
        sources = {"feed_a": SimpleNamespace(official=False),
                   "feed_b": SimpleNamespace(official=False)}
        self.assertFalse(has_independent_reports(first, [first, second], sources))

    def test_unrelated_headline_is_not_confirmation(self):
        first = article("feed_a", "https://site-a.example/news/1", "New console announced by company")
        second = article("feed_b", "https://site-b.example/story/9", "Heavy rain expected across the country")
        sources = {"feed_a": SimpleNamespace(official=False),
                   "feed_b": SimpleNamespace(official=False)}
        self.assertFalse(has_independent_reports(first, [first, second], sources))


    def test_google_news_links_use_original_publisher_domains(self):
        first = article("gnews_a", "https://news.google.com/rss/articles/a",
                        "Studio announces a new animated series for 2027")
        second = article("gnews_b", "https://news.google.com/rss/articles/b",
                         "Studio announces a new animated series for 2027")
        first.origin_url = "https://publisher-one.example"
        second.origin_url = "https://publisher-two.example"
        sources = {"gnews_a": SimpleNamespace(official=False),
                   "gnews_b": SimpleNamespace(official=False)}
        self.assertTrue(has_independent_reports(first, [first, second], sources))

    def test_official_source_can_stand_alone(self):
        first = article("official", "https://agency.example/report/1", "Official statement released")
        sources = {"official": SimpleNamespace(official=True)}
        self.assertTrue(has_independent_reports(first, [first], sources))



class DynamicTopicTests(unittest.TestCase):
    def test_mixed_anime_and_manga(self):
        profile = detect("anime and manga")
        self.assertEqual(profile.key, "anime_manga")
        self.assertIn("أنمي", profile.tags)
        self.assertIn("مانغا", profile.tags)

    def test_manhwa_is_separate(self):
        self.assertEqual(detect("مانهوا وويب تون").key, "manhwa")

    def test_custom_description_creates_scoped_feeds(self):
        profile = detect_custom_profile("My channel", "Roblox updates and patch announcements")
        self.assertTrue(profile.key.startswith("custom_"))
        self.assertIn("roblox", profile.queries[0].lower())

    def test_custom_anime_topic_adds_japanese_feeds(self):
        profile = detect_custom_profile("new channel", "anime release schedules")
        self.assertEqual({x["lang"] for x in suggest_sources(profile)}, {"ar", "en", "ja"})

    def test_custom_manhwa_topic_adds_korean_feeds(self):
        profile = detect_custom_profile("new channel", "manhwa chapter updates")
        self.assertEqual({x["lang"] for x in suggest_sources(profile)}, {"ar", "en", "ko"})

    def test_unmatched_topic_has_no_wildcard(self):
        profile = detect_custom_profile("my channel", "rare xylophone astronomy")
        ids, _reason = sources_for(profile, [{"id": "general", "tags": ["news"]}])
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main()
