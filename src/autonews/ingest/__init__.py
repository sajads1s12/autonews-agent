"""حزمة سحب المحتوى والتصفّح الآلي."""
from .autodiscover import Discovered, discover, first_feed  # noqa: F401
from .extract import Extracted, extract_article_text  # noqa: F401
from .fetch import Fetcher, decode_bytes  # noqa: F401
from .rss import (Article, dedupe_against_history, fetch_all,  # noqa: F401
                  fetch_all_with_status, fetch_source)

__all__ = [
    "Article", "fetch_all", "fetch_all_with_status", "fetch_source",
    "dedupe_against_history", "Fetcher", "decode_bytes", "extract_article_text",
    "Extracted", "discover", "first_feed", "Discovered",
]
