"""Lightweight matching of the same report across independent publishers."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit


def _host(url: str) -> str:
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host == "news.google.com" or host.endswith(".google.com"):
            return ""
        return host
    except Exception:
        return ""


def _words(value: str) -> set[str]:
    value = re.sub(r"https?://\S+", " ", str(value or "").lower())
    value = re.sub(r"[^\w\s\u0600-\u06ff]", " ", value)
    ignored = {"the", "and", "for", "with", "من", "في", "على", "عن", "الى", "إلى", "هذا", "هذه"}
    return {w for w in value.split() if len(w) > 2 and w not in ignored}


def _matching_headline(a, b) -> bool:
    left = str(getattr(a, "title", "") or "").strip().lower()
    right = str(getattr(b, "title", "") or "").strip().lower()
    if not left or not right:
        return False
    one, two = _words(left), _words(right)
    shared = len(one & two) / max(1, len(one | two))
    return SequenceMatcher(None, left, right).ratio() >= 0.72 and shared >= 0.30 or shared >= 0.68


def has_independent_reports(article, articles, sources_by_id) -> bool:
    source = sources_by_id.get(str(getattr(article, "source_id", "")))
    if source is not None and bool(getattr(source, "official", False)):
        return True
    first_host = _host(getattr(article, "url", ""))
    first_source = str(getattr(article, "source_id", ""))
    if not first_host:
        return False
    for other in articles:
        if other is article or str(getattr(other, "source_id", "")) == first_source:
            continue
        second_host = _host(getattr(other, "url", ""))
        if second_host and second_host != first_host and _matching_headline(article, other):
            return True
    return False


__all__ = ["has_independent_reports"]
