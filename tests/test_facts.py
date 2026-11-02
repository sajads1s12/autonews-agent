import unittest
from types import SimpleNamespace

from autonews.facts import has_independent_reports


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


if __name__ == "__main__":
    unittest.main()
