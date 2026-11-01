"""اختبارات ميزات النتائج v6: الأثر، المتابعة، الخريطة، التحقّق، الشفافية، الصمت، التفاوت."""
from __future__ import annotations

import pytest

from autonews.config_loader import OutcomeSettings, TranslationSettings
from autonews.i18n import detect_lang, needs_translation, translate_article
from autonews.ingest import Article
from autonews.outcome_features import (
    SourceEvidence, build_governorate_daily, followup_badge, governorate_contrast,
    impact_line, register_deferred, resolve_followups, silent_alert_text,
    silent_incidents, source_line, verify_result,
)
from autonews.outcomes import OutcomeAnalysis, analyze

CFG = OutcomeSettings(enabled=True, policy="hybrid")


def art(title: str, summary: str = "", url: str = "https://e.co/1", lang: str = "ar",
        source_id: str = "s1", source_name: str = "مصدر") -> Article:
    return Article(source_id=source_id, source_name=source_name, title=title,
                   summary=summary, url=url, lang=lang)


# --------------------------------------------------------------------------- #
#  ١) سطر «الأثر عليك»
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,expect", [
    ("تعطيل الدوام في البصرة غداً", "دوامك"),
    ("تأخير صرف الرواتب لشهر تشرين", "راتبك"),
    ("إغلاق طريق بغداد–كربلاء", "مسار سفرك"),
    ("ارتفاع سعر صرف الدولار في الأسواق", "الدولار"),
    ("إفراج عن موقوفين بقضية تظاهرات", "قانوني"),
])
def test_impact_line_is_reader_centered(title, expect):
    line = impact_line(art(title))
    assert line, f"لا سطر أثر لـ: {title}"
    assert expect in line
    assert len(line) <= 62


def test_impact_line_empty_for_irrelevant():
    assert impact_line(art("مباراة كرة قدم تنتهي بفوز الفريق")) == ""


# --------------------------------------------------------------------------- #
#  ٢) الحوادث المؤجّلة وتطوّرها
# --------------------------------------------------------------------------- #
def test_deferred_incident_is_registered():
    a = art("انفجار في سوق شعبي وسط بغداد", "وقع انفجار ولم ترد معلومات عن ضحايا.")
    store: list[dict] = []
    from autonews.outcomes import analyze as an
    assert register_deferred(an(a), a, store=store)
    assert len(store) == 1 and store[0]["fp"] == a.fp
    # تسجيل ثانٍ لا يضيف نسخة
    register_deferred(an(a), a, store=store)
    assert len(store) == 1 and store[0]["checks"] == 1


def test_followup_resolves_when_outcome_appears():
    incident = art("انفجار في سوق شعبي وسط بغداد", "وقع انفجار بلا معلومات عن ضحايا.",
                   url="https://e.co/incident")
    store: list[dict] = []
    register_deferred(analyze(incident), incident, store=store)
    assert store

    outcome = art("حصيلة انفجار سوق بغداد: 5 وفيات و23 مصاباً",
                  "أعلنت الصحة حصيلة انفجار سوق شعبي في بغداد.", url="https://e.co/outcome")
    resolved = resolve_followups([outcome], store)
    assert resolved, "يجب أن تُطابق النتيجة الحادثة المؤجّلة"
    assert store == [], "الحادثة تُزال بعد صدور نتيجتها"


def test_followup_badge_wording():
    from autonews.utils import iso_now
    from datetime import timedelta
    from autonews.utils import utcnow
    item = {"first_seen": (utcnow() - timedelta(hours=5)).isoformat()}
    assert "ساعة" in followup_badge(item)
    item2 = {"first_seen": (utcnow() - timedelta(days=2)).isoformat()}
    assert "يوم" in followup_badge(item2)
    assert followup_badge({"first_seen": ""}) == ""


# --------------------------------------------------------------------------- #
#  ٣) خريطة المحافظات
# --------------------------------------------------------------------------- #
def test_daily_matrix_lists_holiday_and_normal():
    items = [
        (art("تعطيل الدوام في البصرة وميسان غداً"),
         OutcomeAnalysis(has_outcome=True, governorates_on=["البصرة", "ميسان"])),
        (art("دوام طبيعي في أربيل"),
         OutcomeAnalysis(has_outcome=True, governorates_off=["أربيل"])),
    ]
    card = build_governorate_daily(items, date_label="2026-11-01")
    assert "خريطة المحافظات" in card
    assert "البصرة" in card and "ميسان" in card
    assert "غير مشمولة" in card


def test_daily_matrix_empty_without_governorates():
    assert build_governorate_daily([]) == ""


# --------------------------------------------------------------------------- #
#  ٥) التحقّق المزدوج
# --------------------------------------------------------------------------- #
def test_official_source_needs_no_second_witness():
    v = verify_result([SourceEvidence("ina", "واع", official=True, trust=1.3)],
                      OutcomeSettings(dual_verify=True))
    assert v.ok and v.confidence == "عالية"


def test_two_independent_sources_pass():
    v = verify_result([SourceEvidence("shafaq", "شفق", trust=1.0),
                       SourceEvidence("baghdad", "بغداد اليوم", trust=0.9)],
                      OutcomeSettings(dual_verify=True))
    assert v.ok and v.confidence in ("متوسطة", "عالية")


def test_single_unofficial_source_blocked_when_dual_verify():
    v = verify_result([SourceEvidence("blog", "مدونة", trust=0.6)],
                      OutcomeSettings(dual_verify=True))
    assert not v.ok and "تأكيد" in v.reason
    # ومعطّل ⇒ يمرّ
    v2 = verify_result([SourceEvidence("blog", "مدونة", trust=0.6)],
                       OutcomeSettings(dual_verify=False))
    assert v2.ok


# --------------------------------------------------------------------------- #
#  ٦) سطر الشفافية
# --------------------------------------------------------------------------- #
def test_source_line_contains_source_and_confidence():
    line = source_line(art("خبر", source_name="شفق نيوز"), official=True, trust=1.3,
                       verification=verify_result([SourceEvidence("a", "شفق", True)],
                                                  OutcomeSettings()), age_minutes=12)
    assert "شفق نيوز" in line and "رسمي" in line and "12 دقيقة" in line


# --------------------------------------------------------------------------- #
#  ٧) الحوادث الصامتة
# --------------------------------------------------------------------------- #
def test_silent_incidents_only_after_threshold():
    from datetime import timedelta
    from autonews.utils import utcnow
    fresh = {"fp": "a", "title": "حادثة جديدة", "score": 4.0,
             "first_seen": (utcnow() - timedelta(hours=1)).isoformat()}
    old = {"fp": "b", "title": "حادثة قديمة", "score": 4.0,
           "first_seen": (utcnow() - timedelta(hours=9)).isoformat()}
    low = {"fp": "c", "title": "حادثة تافهة", "score": 0.5,
           "first_seen": (utcnow() - timedelta(hours=9)).isoformat()}
    out = silent_incidents([fresh, old, low], hours=6.0)
    assert [i["fp"] for i in out] == ["b"]
    assert "بلا نتائج" in silent_alert_text(out)


# --------------------------------------------------------------------------- #
#  ٨) التفاوت بين المحافظات
# --------------------------------------------------------------------------- #
def test_governorate_contrast_detected():
    items = [
        (art("تعطيل الدوام في البصرة غداً"),
         OutcomeAnalysis(has_outcome=True, governorates_on=["البصرة"])),
        (art("العطلة لم تشمل بغداد"),
         OutcomeAnalysis(has_outcome=True, governorates_off=["بغداد"])),
    ]
    line = governorate_contrast(items)
    assert "البصرة" in line and "بغداد" in line


def test_no_contrast_when_all_agree():
    items = [(art("تعطيل الدوام في البصرة وميسان"),
              OutcomeAnalysis(has_outcome=True, governorates_on=["البصرة", "ميسان"]))]
    assert governorate_contrast(items) == ""


# --------------------------------------------------------------------------- #
#  🌐 التعريب
# --------------------------------------------------------------------------- #
def test_detect_lang_arabic_and_english():
    assert detect_lang("هذا خبر عربي واضح عن العراق") == "ar"
    assert detect_lang("Iraq announces a new holiday for all governorates") == "en"


def test_needs_translation_only_for_foreign():
    assert needs_translation(art("Iraq holiday announced", lang="en"))
    assert not needs_translation(art("عطلة رسمية في العراق", lang="ar"))


def test_translate_without_engine_fails_safely():
    res = translate_article(art("Iraq holiday announced", lang="en"), None)
    assert not res.ok and "AI" in res.reason


class _FakeEngine:
    keys = ["k"]

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def generate(self, *_a, **_k):
        return {"text": self.payload, "model": "fake"}


def test_translate_keeps_numbers():
    eng = _FakeEngine('{"title": "زلزال بقوة 5.2 يضرب شمال العراق", '
                      '"summary": "سجّل مرصد الزلازل هزة بقوة 5.2 درجة."}')
    res = translate_article(art("Magnitude 5.2 quake hits northern Iraq",
                                "A 5.2 magnitude earthquake was recorded.", lang="en"), eng)
    assert res.ok and "5.2" in res.title


def test_translate_rejects_invented_numbers():
    eng = _FakeEngine('{"title": "زلزال بقوة 7.9 يضرب العراق", '
                      '"summary": "هزة بقوة 7.9 درجة."}')
    res = translate_article(art("Magnitude 5.2 quake hits northern Iraq",
                                "A 5.2 magnitude earthquake was recorded.", lang="en"), eng)
    assert not res.ok and "أرقام" in res.reason


def test_translate_settings_defaults():
    s = TranslationSettings()
    assert s.enabled and s.target == "ar" and "en" in s.langs


# --------------------------------------------------------------------------- #
#  سياسة hybrid: النتائج دائماً + الحدث الكبير فقط
# --------------------------------------------------------------------------- #
def test_hybrid_publishes_big_incident_only():
    from autonews.outcomes import should_publish
    big = art("قتلى في هجوم على نقطة تفتيش", "سقط عدد من القتلى جراء الهجوم.")
    small = art("انفجار في سوق شعبي", "وقع انفجار ولم ترد معلومات عن ضحايا.")
    hybrid = OutcomeSettings(enabled=True, policy="hybrid")
    only = OutcomeSettings(enabled=True, policy="outcomes_only")
    assert should_publish(big, hybrid)[0] is True
    assert should_publish(big, only)[0] is False, "outcomes_only لا تنشر حتى الكبير"
    assert should_publish(small, hybrid)[0] is False
