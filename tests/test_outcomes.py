"""اختبارات نمط النتائج: نشر «ما صار» بدل «ما وقع» + مصفوفة المحافظات."""
from __future__ import annotations

import pytest

from autonews.ai import Post
from autonews.config_loader import OutcomeSettings
from autonews.ingest import Article
from autonews.outcomes import (
    GOVERNORATES, analyze, build_outcome_body, detect_governorates, has_explicit_no_result,
    should_publish,
)


def art(title: str, summary: str = "") -> Article:
    return Article(source_id="s", source_name="مصدر", title=title,
                   summary=summary, url="https://e.co/1")


CFG = OutcomeSettings(enabled=True, policy="outcomes_only")


# --------------------------------------------------------------------------- #
#  1) الحدث الخام لا يُنشر
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,summary", [
    ("انفجار في سوق شعبي وسط بغداد", "وقع انفجار في سوق شعبي، ولم ترد معلومات عن ضحايا حتى الآن."),
    ("قصف مدفعي على أطراف المدينة", "تعرضت أطراف المدينة لقصف مدفعي مساء اليوم."),
    ("اشتباكات مسلحة قرب نقطة تفتيش", "اندلعت اشتباكات مسلحة بين مسلحين وقوات الأمن."),
    ("حريق كبير في مستودع", "اندلع حريق كبير في مستودع للمواد الغذائية."),
    ("زلزال يضرب المنطقة", "شعر السكان بهزة أرضية قوية مساء اليوم."),
])
def test_raw_incidents_are_deferred(title, summary):
    ok, why, analysis = should_publish(art(title, summary), CFG)
    assert not ok, f"الحدث الخام يجب أن يُؤجّل: {title} → {why}"
    assert "نتائج" in why or "يُؤجّل" in why


# --------------------------------------------------------------------------- #
#  2) النتائج تُنشر
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,summary,must_have", [
    ("حصيلة انفجار السوق: 5 وفيات و23 مصاباً وحالة 4 حرجة",
     "أعلنت وزارة الصحة أن الحصيلة بلغت 5 وفيات و23 مصاباً أربعة منهم بحالة حرجة.", "person"),
    ("توقيف المتهم بسرقة المصرف وإحالته إلى القضاء",
     "أعلنت الشرطة توقيف المتهم بعد 48 ساعة وإحالته إلى القضاء.", "person"),
    ("إفراج عن الموقوفين بعد تدخل عشائري",
     "أُفرج عن الموقوفين بعد اتفاق عشائري، وأُحيل الملف إلى القضاء.", "person"),
    ("تعطيل الدوام في البصرة وميسان وذي قار غداً",
     "قررت المحافظات تعطيل الدوام الرسمي غداً بسبب سوء الأحوال الجوية.", "governorate"),
    ("صرف رواتب المتقاعدين الخميس",
     "أعلنت هيئة التقاعد صرف الرواتب يوم الخميس في جميع المنافذ.", "service"),
])
def test_outcome_news_is_published(title, summary, must_have):
    ok, why, analysis = should_publish(art(title, summary), CFG)
    assert ok, f"يجب أن يُنشر (نتيجة): {title} → {why}"
    assert must_have in analysis.kinds


def test_explicit_no_result_blocks_publishing():
    a = art("انفجار قرب مصرف",
            "وقع انفجار قرب مصرف في بغداد، ولم تتضح حتى الآن ملابسات الحادث ولم يُعلن عن إصابات.")
    assert has_explicit_no_result(a.summary)
    ok, _, _ = should_publish(a, CFG)
    assert not ok


# --------------------------------------------------------------------------- #
#  3) مصفوفة المحافظات (المطلوب الأساسي)
# --------------------------------------------------------------------------- #
def test_governorates_included_and_excluded():
    on, off = detect_governorates(
        "أعلنت الأمانة العامة تعطيل الدوام غداً في جميع المحافظات "
        "باستثناء أربيل ودهوك، فيما استثنت محافظة السليمانية."
    )
    assert "أربيل" in off and "دهوك" in off
    assert "السليمانية" in off
    assert not set(off) & set(on), "لا يمكن أن تكون المحافظة مشمولة ومستثناة معاً"


def test_governorates_only_included():
    on, off = detect_governorates(
        "تعطيل الدوام الرسمي في البصرة وميسان وذي قار اليوم")
    assert {"البصرة", "ميسان", "ذي قار"} <= set(on)
    assert off == []


def test_all_governorates_named():
    text = ("العطلة تشمل بغداد والبصرة ونينوى وأربيل والنجف وكربلاء والأنبار وديالى "
            "وذي قار والسليمانية وصلاح الدين وبابل وواسط وميسان والمثنى والقادسية وكركوك ودهوك")
    on, _ = detect_governorates(text)
    assert len(on) >= 15, f"يجب رصد معظم المحافظات، رصد {len(on)}: {on}"


def test_governorate_variants_recognized():
    """الموصل = نينوى، الناصرية = ذي قار، الحلة = بابل."""
    on, _ = detect_governorates("تعطيل الدوام في الموصل والناصرية والحلة")
    assert {"نينوى", "ذي قار", "بابل"} <= set(on)


def test_all_governorates_registry_is_complete():
    assert len(GOVERNORATES) >= 18, "قائمة المحافظات العراقية ناقصة"


# --------------------------------------------------------------------------- #
#  4) بطاقة النتائج والقالب الاحتياطي
# --------------------------------------------------------------------------- #
def test_outcome_body_fallback_lists_governorates():
    a = art("تعطيل الدوام غداً",
            "شمل القرار جميع المحافظات باستثناء أربيل ودهوك، وقررت الوزارة تعطيل الدوام.")
    analysis = analyze(a)
    body = build_outcome_body(a, analysis)
    assert "مشمولة" in body or "مستثناة" in body
    assert "أربيل" in body


def test_policy_hybrid_allows_big_incidents():
    cfg = OutcomeSettings(enabled=True, policy="hybrid")
    a = art("انفجار كبير يخلّف عشرات الضحايا وتُعلن حالة طوارئ",
            "أعلنت الجهات حالة طوارئ في المدينة، فيما وردت أنباء عن عشرات الضحايا.")
    ok, why, analysis = should_publish(a, cfg)
    assert ok and ("hybrid" in why or analysis.escalate)


def test_policy_raw_restores_old_behaviour():
    cfg = OutcomeSettings(enabled=True, policy="raw")
    ok, why, _ = should_publish(art("قصف مدفعي على أطراف المدينة", "قصف مدفعي."), cfg)
    assert ok and "raw" in why


def test_disabled_outcomes_publishes_everything():
    cfg = OutcomeSettings(enabled=False)
    ok, _, _ = should_publish(art("انفجار", "انفجار في السوق."), cfg)
    assert ok


def test_allow_incident_headlines_flag():
    cfg = OutcomeSettings(enabled=True, policy="outcomes_only", allow_incident_headlines=True)
    ok, _, _ = should_publish(art("قصف مدفعي على أطراف المدينة", "قصف."), cfg)
    assert ok


# --------------------------------------------------------------------------- #
#  5) عرض المنشور: شارة 📋 وبطاقة النتائج
# --------------------------------------------------------------------------- #
def test_outcome_badge_in_title():
    from autonews.config_loader import Channel

    p = Post(channel_id="tg", platform="telegram", title="عطلة في 15 محافظة",
             body="📋 النتائج:\n✅ مشمولة: بغداد، البصرة", is_outcome=True)
    ch = Channel(id="tg", name="ق", platform="telegram", sources=["*"], max_chars=4096)
    first = p.render(ch).splitlines()[0]
    assert "📋" in first


def test_breaking_overrides_outcome_badge():
    from autonews.config_loader import Channel

    p = Post(channel_id="tg", platform="telegram", title="عنوان", body="نص",
             is_outcome=True, breaking=True)
    ch = Channel(id="tg", name="ق", platform="telegram", sources=["*"], max_chars=4096)
    first = p.render(ch).splitlines()[0]
    assert "🔴" in first and "📋" not in first


def test_post_serialization_keeps_outcome_flag():
    p = Post(channel_id="c", platform="telegram", title="t", body="b", is_outcome=True)
    assert Post.from_dict(p.as_dict()).is_outcome is True
