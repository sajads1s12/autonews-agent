"""موجّه المحتوى: يحدّد أي مقال يذهب إلى أي قناة ولماذا.

منطق التوجيه (بالترتيب):
1) مطابقة المصدر: channel.sources تحتوي معرّف المصدر أو "*".
2) مطابقة الوسم: تقاطع بين وسوم المقال ووسوم القناة (إن حُدّدت وسوم).
3) مطابقة الكلمة المفتاحية: أي كلمة من channel.keywords تظهر في العنوان/الملخص.
4) إن لم تُحدَّد وسوم ولا كلمات => القناة تستقبل كل ما يطابق المصدر.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config_loader import Channel
from .ingest import Article
from .utils import get_logger, normalize_arabic

log = get_logger("autonews.router")


@dataclass(slots=True)
class Route:
    channel: Channel
    score: float
    reasons: list[str]


class ContentRouter:
    """يوجّه المقالات إلى القنوات المناسبة."""

    def __init__(self, channels: list[Channel]) -> None:
        self.channels = [c for c in channels if c.enabled]
        for ch in self.channels:
            if ch.platform == "console" and not ch.enabled:
                continue
        log.info("الموجّه جاهز | قنوات مفعّلة=%d", len(self.channels))

    def route(self, article: Article) -> list[Route]:
        """إرجاع القنوات المستهدفة مرتّبة بالأولوية."""
        routes: list[Route] = []
        haystack = normalize_arabic(f"{article.title} {article.summary} {' '.join(article.tags)}".lower())

        for channel in self.channels:
            reasons: list[str] = []
            score = 0.0

            # (1) المصدر
            source_ok = "*" in channel.sources or article.source_id in channel.sources
            if not source_ok:
                continue
            reasons.append(f"مصدر:{article.source_id}" if article.source_id in channel.sources else "كل المصادر")
            score += 1.0

            # (2أ) استثناء صريح — فصل القطاعات
            if channel.exclude_tags:
                blocked = {t.lower() for t in article.tags} & {t.lower() for t in channel.exclude_tags}
                if blocked:
                    continue

            # (2) الوسوم
            if channel.tags:
                overlap = {t.lower() for t in article.tags} & {t.lower() for t in channel.tags}
                if not overlap:
                    continue
                reasons.append("وسم:" + ",".join(sorted(overlap))[:60])
                score += 1.5 * len(overlap)

            # (3) الكلمات المفتاحية
            if channel.keywords:
                hits = [k for k in channel.keywords if normalize_arabic(k.lower()) in haystack]
                if not hits:
                    continue
                reasons.append("كلمة:" + ",".join(hits[:3]))
                score += 2.0 * len(hits)

            # (4) عامل ثقة المصدر
            score *= max(0.5, article_weight(article))
            routes.append(Route(channel=channel, score=score, reasons=reasons))

        routes.sort(key=lambda r: (-r.score, r.channel.id))
        return routes

    def targets(self, article: Article) -> list[Channel]:
        return [r.channel for r in self.route(article)]


_SOURCE_TRUST: dict[str, float] = {}
_OFFICIAL: set[str] = set()


def register_sources(sources) -> None:
    """تسجيل مصداقية المصادر و«رسميتها» (يُستدعى من الإعدادات)."""
    for src in sources:
        _SOURCE_TRUST[src.id] = float(getattr(src, "trust", 1.0))
        if getattr(src, "official", False):
            _OFFICIAL.add(src.id)


def article_weight(article: Article) -> float:
    """وزن تقريبي حسب حداثة الخبر ومصداقية المصدر ووجود ملخّص غني."""
    weight = _SOURCE_TRUST.get(article.source_id, 1.0)
    if article.source_id in _OFFICIAL:
        weight += 0.5        # مصدر رسمي: يُنشر أولاً عند تعارض الأنباء
    if article.published:
        from .utils import utcnow

        hours = max(0.0, (utcnow() - article.published).total_seconds() / 3600)
        weight += max(0.0, 0.5 - hours / 96)  # أخبار أحدث = وزن أعلى قليلاً
    if len(article.summary) > 400:
        weight += 0.1
    return weight
