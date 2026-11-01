"""روابط UTM — لتعرف **من أين** يأتي القارئ وأي منشور يعمل.

المشكلة: بلا UTM تفتح إحصاءاتك فترى «زيارات من تيليجرام» فقط، ولا تعرف أي
منشور جلبها. مع UTM يصير كل منشور قابلاً للقياس منفصلاً.

صيغة قصيرة ومقروءة (لا تُقبّح الرابط):
    المصدر (source)  : telegram | facebook | x
    الوسيط (medium)  : social
    الحملة (campaign): flash | regular | outcome | matrix
    المحتوى (content): بصمة قصيرة للمقال + نوع البطاقة
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from .utils import fingerprint


@dataclass(slots=True)
class UTMSettings:
    """إعدادات وسوم الحملة."""
    enabled: bool = True
    source_override: str = ""     # اسم المصدر القسري (مثلاً: mychannel)
    medium: str = "social"
    campaign_prefix: str = "autonews"
    keep_existing: bool = True     # لا تلمس رابطاً فيه utm_source أصلاً


def _short_id(post_url: str) -> str:
    """معرّف قصير ثابت (6 أحرف) — يُستخدم في content."""
    return fingerprint(post_url or "x")[:6]


def build_utm(url: str, *, platform: str, campaign: str = "regular",
              content: str = "", settings: UTMSettings | None = None) -> str:
    """إضافة وسوم UTM إلى رابط دون إفساده.

    - يحترم الروابط التي فيها استعلام مسبق (مثل YouTube `?v=`).
    - لا يضيف وسماً مكرّراً.
    - يعيد الرابط كما هو عند التعطيل أو عند وجود utm_source سابقاً.
    """
    st = settings or UTMSettings()
    if not (st.enabled and url):
        return url
    if not url.lower().startswith(("http://", "https://")):
        return url

    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    if st.keep_existing and "utm_source" in query:
        return url

    query.update({
        "utm_source": (st.source_override or platform or "unknown").lower(),
        "utm_medium": st.medium,
        "utm_campaign": f"{st.campaign_prefix}-{campaign}".replace(" ", "-"),
        "utm_content": (content or _short_id(url))[:32],
    })
    # نُبقي ترتيباً ثابتاً ليبقى الرابط قابلاً للمقارنة بين التشغيلات
    ordered = urllib.parse.urlencode(sorted(query.items()))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, ordered, parsed.fragment))


def campaign_for_post(*, is_outcome: bool, breaking: bool, kind: str = "") -> str:
    """اسم الحملة المناسب لنوع المنشور (للمقارنة بين الأنواع في اللوحة)."""
    if kind:
        return kind
    if is_outcome:
        return "outcome"
    if breaking:
        return "flash"
    return "regular"


def tag_post_url(post, *, platform: str, settings: UTMSettings | None = None,
                 kind: str = "") -> None:
    """تعديل رابط المنشور في مكانه (قبل النشر)."""
    if not getattr(post, "source_url", ""):
        return
    post.source_url = build_utm(
        post.source_url, platform=platform,
        campaign=campaign_for_post(is_outcome=getattr(post, "is_outcome", False),
                                   breaking=getattr(post, "breaking", False), kind=kind),
        settings=settings,
    )


def parse_utm(url: str) -> dict[str, str]:
    """قراءة وسوم UTM من رابط (للاختبار والتحقّق)."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    return {k: v for k, v in query.items() if k.startswith("utm_")}
