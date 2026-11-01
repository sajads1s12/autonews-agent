"""اختبارات عمود الأنمي: التصنيف، السياسة، الأسماء الأصلية، الصياغة."""
from __future__ import annotations

import pytest

from autonews.ingest import Article
from autonews.verticals import (
    KNOWN_WORKS, STUDIOS, build_anime_prompt, classify_anime, extract_titles,
    is_anime, name_glossary, should_publish_anime, translate_names,
)


def art(title: str, summary: str = "") -> Article:
    return Article(source_id="ann", source_name="ANN", title=title,
                   summary=summary, url="https://e.co/1", tags=["أنمي"])


# --------------------------------------------------------------------------- #
#  ١) الكشف
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,summary", [
    ("Jujutsu Kaisen Reveals New Posters", "MAPPA revealed new visuals for the anime."),
    ("Shiba Inu Rooms Manga Gets TV Anime in Summer 2027", "Kyoto Animation produces it."),
    ("أنمي Mercedes and the Waning Moon يكشف عن مقطع دعائي", "تريلر جديد"),
    ("موعد عرض الحلقة الخامسة من Black Clover", "أنمي شهير"),
    ("تطور مذهل في حبكة الفيلم الضخم بليتش", "معركة الألفية"),
])
def test_detects_anime(title, summary):
    assert is_anime(art(title, summary)), f"لم يُكتشف: {title}"


@pytest.mark.parametrize("title,summary", [
    ("مقتل شخص في حادث سير على طريق بغداد", "الحادث وقع صباحاً"),
    ("المباراة تنتهي بفوز الفريق بهدفين", "الدوري العراقي"),
    ("Studio denies any anime adaptation of the game", "There is no anime adaptation."),
    ("ارتفاع أسعار الدولار في الأسواق", "سعر الصرف"),
])
def test_rejects_non_anime(title, summary):
    assert not is_anime(art(title, summary)), f"صنّفه أنمي خطأً: {title}"


# --------------------------------------------------------------------------- #
#  ٢) تصنيف الحدث
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,event", [
    ("New Anime Adaptation Announced for Shiba Inu Rooms", "إعلان"),
    ("Frieren Season 2 Premieres October 2027", "عرض"),
    ("Manga Ends in Next Issue", "انتهاء"),
    ("Main Cast Revealed for New Series", "طاقم"),
    ("New Trailer Released for the Anime", "تريلر"),
])
def test_event_classification(title, event):
    v = classify_anime(art(title, "anime news"))
    assert v.event == event, f"{title} → {v.event}"


def test_studio_and_season_detected():
    v = classify_anime(art("Shiba Inu Rooms Anime Announced for Summer 2027",
                           "Kyoto Animation will produce the TV anime."))
    assert "Kyoto Animation" in v.studios
    assert v.season == "Summer 2027"


def test_known_work_boost():
    v = classify_anime(art("بليتش يعود بموسم جديد", "خبر عن الأنمي"))
    assert v.is_anime and v.score >= 5.0


# --------------------------------------------------------------------------- #
#  ٣) سياسة النشر (الإعلان خبر بذاته — لا ننتظر «نتائج»)
# --------------------------------------------------------------------------- #
def test_announcement_is_publishable():
    ok, why = should_publish_anime(classify_anime(
        art("Shiba Inu Rooms Anime Announced", "Kyoto Animation")))
    assert ok and "أنمي" in why


def test_vague_anime_news_rejected():
    ok, why = should_publish_anime(classify_anime(
        art("حقق سوق الأنمي الياباني رقماً قياسياً في الإيرادات", "")))
    assert not ok and "بلا معلومة" in why


def test_min_score_filters():
    v = classify_anime(art("أنمي جديد", ""))
    assert not should_publish_anime(v, min_score=99)[0]


# --------------------------------------------------------------------------- #
#  ٤) حماية الأسماء الأصلية
# --------------------------------------------------------------------------- #
def test_extract_title_from_english_headline():
    assert extract_titles("Shiba Inu Rooms Manga Gets TV Anime in Summer 2027")[0] == "Shiba Inu Rooms"


def test_extract_does_not_take_generic_phrases():
    titles = extract_titles("Charisma TV Anime Reveals January 7 Debut in Teaser Video")
    assert "Teaser Video" not in titles
    assert "January 7 Debut" not in titles


def test_extract_handles_apostrophes():
    titles = extract_titles("Frieren: Beyond Journey's End Season 2 Premieres October")
    assert titles and "Frieren" in titles[0]


def test_glossary_no_duplicates():
    g = name_glossary(["Shiba Inu Rooms", "Shiba Inu Rooms", "Madhouse"])
    assert g.count("Shiba Inu Rooms") == 1


def test_translate_names_keeps_original():
    text = "هجوم العملاق يعود بموسم جديد"
    out = translate_names(text, ["Attack on Titan"])
    assert out  # لا يكسر النص


# --------------------------------------------------------------------------- #
#  ٥) الصياغة
# --------------------------------------------------------------------------- #
def test_prompt_contains_facts_and_forbids_invention():
    a = art("Shiba Inu Rooms Anime Announced",
            "Kyoto Animation will produce it for Summer 2027.")
    prompt = build_anime_prompt(a, classify_anime(a))
    assert "Kyoto Animation" in prompt
    assert "لا تترجم" in prompt
    assert "skip" in prompt


def test_known_works_list_is_sane():
    assert "bleach" in KNOWN_WORKS and KNOWN_WORKS["bleach"] == "بليتش"
    assert len(KNOWN_WORKS) >= 15


def test_studios_list_includes_major():
    for s in ("MAPPA", "ufotable", "Kyoto Animation"):
        assert s in STUDIOS
