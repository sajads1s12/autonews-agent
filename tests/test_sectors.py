"""اختبارات 🧠 التعرّف على القطاع من اسم القناة."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from autonews.sectors import (SECTORS, apply_to_channel, clean_name, detect,
                              score, sources_for)


# --------------------------------------------------------------------------- #
#  ١) التعرّف من الاسم
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,expected", [
    ("أخبار الوتين", "news"),
    ("قناة الأنمي والمانغا", "anime"),
    ("عالم الأنمي", "anime"),
    ("أخبار الأنمي والمانجا", "anime"),        # الأخصّ يفوز على «أخبار»
    ("🎌 مانجا لاند", "anime"),
    ("الدوري العراقي", "sports"),
    ("عالم التقنية", "tech"),
    ("أسعار الدولار", "economy"),
    ("قيمنق العرب", "gaming"),
    ("صحتك اليوم", "health"),
    ("الطقس في بغداد", "weather"),
    ("بيتكوين والكريبتو", "crypto"),
    ("أمن العراق", "security"),
    ("فن وثقافة", "culture"),
    ("جامعة بغداد", "education"),
])
def test_detect_known_names(name, expected):
    profile = detect(name)
    assert profile is not None, f"لم يتعرّف على «{name}»"
    assert profile.key == expected


@pytest.mark.parametrize("name", ["قناة دجلة", "السومرية", "كركوك", ""])
def test_detect_returns_none_when_unclear(name):
    """لا تخمين بلا دلالة — القناة تبقى تستقبل كل شيء (أآمن)."""
    assert detect(name) is None


def test_detect_prefers_specialist_over_general():
    """«أخبار الأنمي» أنمي لا أخبار — وإلا صار الأنمي في القناة العامة."""
    assert detect("أخبار الأنمي").key == "anime"


def test_clean_name_strips_emoji_and_punctuation():
    cleaned = clean_name("🎌 قناة الأنمي | Manga 🍥")
    assert "🎌" not in cleaned and "|" not in cleaned
    assert "انمي" in cleaned


def test_score_reports_alternatives():
    scores = score("أخبار الأنمي والمانغا")
    assert scores["anime"] > scores.get("news", 0)


# --------------------------------------------------------------------------- #
#  ٢) اختيار المصادر
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sources():
    from autonews.sectors import _load_sources

    return _load_sources("config")


def test_anime_gets_only_anime_sources(sources):
    picked, why = sources_for(SECTORS["anime"], sources)
    assert picked != ["*"], "قناة الأنمي يجب أن تتخصّص"
    from autonews.sectors import _load_sources  # noqa: F401

    by_id = {s["id"]: s for s in sources}
    for sid in picked:
        assert "أنمي" in (by_id[sid].get("tags") or []), sid


def test_general_news_covers_everything(sources):
    """القناة العامة يجب أن تعرف كل شيء — السرعة تغلب التخصيص هنا.

    الفصل عن الأنمي/الرياضة يحدث بـ `exclude_tags` في القناة نفسها، لا بحجب
    المصادر (وإلا صمتت القناة الرئيسية، وهذا يخالف شرط «الخبر أولاً بأول»).
    """
    picked, _ = sources_for(SECTORS["news"], sources)
    assert len(picked) >= len(sources) - 2
    # والعربية تسبق الإنجليزية في الترتيب
    from autonews.sectors import _source_langs

    by_id = {s["id"]: s for s in sources}
    assert "ar" in _source_langs(by_id[picked[0]])
    eng = [i for i, sid in enumerate(picked) if "en" in _source_langs(by_id[sid])]
    ara = [i for i, sid in enumerate(picked) if "ar" in _source_langs(by_id[sid])]
    assert not eng or not ara or max(ara) < min(eng), "يجب أن تتقدّم المصادر العربية"


def test_every_sector_has_at_least_one_source(sources):
    for key, profile in SECTORS.items():
        picked, why = sources_for(profile, sources)
        assert picked, f"{key}: لا مصادر"
        if picked != ["*"]:
            assert len(picked) >= 1


def test_no_sources_means_publish_everything():
    """الأمان: بلا مصادر لا نصمت — ننشر كل شيء."""
    picked, why = sources_for(SECTORS["sports"], [])
    assert picked == ["*"]


def test_unknown_tag_falls_back_to_all(sources):
    from autonews.sectors import SectorProfile

    made_up = SectorProfile(key="x", label="وهمي", icon="❓",
                            keywords=("وهمي",), tags=("لا_يوجد_وسم",))
    picked, why = sources_for(made_up, sources)
    assert picked == ["*"]


# --------------------------------------------------------------------------- #
#  ٣) التطبيق على channels.yml
# --------------------------------------------------------------------------- #
@pytest.fixture()
def cfgdir(tmp_path):
    dest = tmp_path / "config"
    dest.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, dest / name)
    return dest


def test_apply_writes_tags_and_sources(cfgdir):
    from autonews.config_loader import load_config

    ok, msg = apply_to_channel("tg_sports", "الدوري العراقي",
                               config_dir=str(cfgdir), override=True)
    assert ok, msg
    ch = next(c for c in load_config(str(cfgdir)).channels if c.id == "tg_sports")
    assert "رياضة" in ch.tags
    assert ch.sources != ["*"] and all(s.startswith("gnews_sports") for s in ch.sources)


def test_apply_keeps_yaml_valid_and_files_intact(cfgdir):
    import yaml

    apply_to_channel("tg_tech", "عالم التقنية", config_dir=str(cfgdir), override=True)
    data = yaml.safe_load((cfgdir / "channels.yml").read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["channels"]]
    assert len(ids) == len(set(ids))
    assert "tg_main" in ids


def test_apply_refuses_to_overwrite_manual_sources(cfgdir):
    """لا نفسد ضبطاً يدوياً مقصوداً بلا إذن."""
    assert apply_to_channel("tg_anime", "الأنمي", config_dir=str(cfgdir), override=True)[0]
    ok, msg = apply_to_channel("tg_anime", "الأنمي", config_dir=str(cfgdir))
    assert not ok
    assert "مسبقاً" in msg or "تركتها" in msg


def test_apply_unknown_channel(cfgdir):
    ok, msg = apply_to_channel("tg_nope", "الأنمي", config_dir=str(cfgdir))
    assert not ok and "غير موجودة" in msg


def test_apply_unclear_name_keeps_channel_open(cfgdir):
    ok, msg = apply_to_channel("tg_main", "قناة دجلة", config_dir=str(cfgdir), override=True)
    assert not ok
    assert "لم أتعرّف" in msg


# --------------------------------------------------------------------------- #
#  ٤) التصنيف اليدوي — sector_of · set_sector · catalog
# --------------------------------------------------------------------------- #
def test_sector_of_reads_from_tags(cfgdir):
    from autonews.sectors import sector_of

    assert sector_of("tg_anime", config_dir=str(cfgdir)).key == "anime"
    assert sector_of("tg_main", config_dir=str(cfgdir)).key == "news"


def test_sector_of_unknown_channel(cfgdir):
    from autonews.sectors import sector_of

    assert sector_of("tg_does_not_exist", config_dir=str(cfgdir)) is None


def test_set_sector_switches_tags_and_sources(cfgdir):
    """تغيير الاختصاص يدوياً: الوسوم والمصادر تتبدّل معاً (لا تختلط)."""
    from autonews.config_loader import load_config
    from autonews.sectors import sector_of, set_sector

    ok, msg = set_sector("tg_sports", "tech", config_dir=str(cfgdir))
    assert ok, msg
    ch = next(c for c in load_config(str(cfgdir)).channels if c.id == "tg_sports")
    assert "تقنية" in ch.tags
    assert "رياضة" not in ch.tags, "وسوم الاختصاص القديم يجب أن تُحذف"
    assert sector_of("tg_sports", config_dir=str(cfgdir)).key == "tech"


def test_set_sector_rejects_unknown(cfgdir):
    from autonews.sectors import set_sector

    ok, msg = set_sector("tg_anime", "nope", config_dir=str(cfgdir))
    assert not ok and "غير معروف" in msg


def test_catalog_lists_all_sectors():
    from autonews.sectors import catalog

    items = catalog()
    assert len(items) >= 14
    assert all({"key", "label", "icon", "tags"} <= set(i) for i in items)


# --------------------------------------------------------------------------- #
#  ٥) عدد المنشورات لكل قناة (من البوت)
# --------------------------------------------------------------------------- #
def test_update_channel_setting_changes_cap(cfgdir):
    from autonews.config_loader import load_config
    from autonews.editor import update_channel_setting

    res = update_channel_setting("tg_tech", "max_posts_per_run", 7, config_dir=str(cfgdir))
    assert res.ok, res.message
    ch = next(c for c in load_config(str(cfgdir)).channels if c.id == "tg_tech")
    assert ch.max_posts_per_run == 7


def test_update_channel_setting_rejects_bad_value(cfgdir):
    from autonews.editor import update_channel_setting

    assert not update_channel_setting("tg_tech", "max_posts_per_run", "كثير",
                                      config_dir=str(cfgdir)).ok
    assert not update_channel_setting("tg_tech", "max_posts_per_run", 999,
                                      config_dir=str(cfgdir)).ok
    assert not update_channel_setting("tg_tech", "unknown_key", 1,
                                      config_dir=str(cfgdir)).ok


def _raw_enabled(cfgdir, cid):
    import yaml

    data = yaml.safe_load((cfgdir / "channels.yml").read_text(encoding="utf-8"))
    return next(c for c in data["channels"] if c["id"] == cid)["enabled"]


def test_set_channel_enabled_toggles(cfgdir):
    """تعطيل/تفعيل قناة من البوت: القناة تبقى في الملف (كي تظهر باللوحة) لكن لا تُنشر."""
    from autonews.config_loader import load_config
    from autonews.editor import set_channel_enabled

    assert set_channel_enabled("tg_games", False, config_dir=str(cfgdir)).ok
    assert _raw_enabled(cfgdir, "tg_games") is False
    # التعطيل يمنع النشر فعلاً (لا يظهر ضمن قنوات التشغيل)
    assert "tg_games" not in [c.id for c in load_config(str(cfgdir)).channels]

    assert set_channel_enabled("tg_games", True, config_dir=str(cfgdir)).ok
    assert _raw_enabled(cfgdir, "tg_games") is True
    assert "tg_games" in [c.id for c in load_config(str(cfgdir)).channels]


# --------------------------------------------------------------------------- #
#  ٦) مصادر مقترحة لقطاع بلا مصادر (لا قناة صامتة)
# --------------------------------------------------------------------------- #
def test_suggest_sources_generates_google_news_urls():
    from autonews.sectors import SECTORS, suggest_sources

    items = suggest_sources(SECTORS["anime"])
    assert items and all("news.google.com/rss" in i["url"] for i in items)
    assert all(i["tags"] for i in items)


def test_every_sector_has_queries():
    """كل قطاع يجب أن يعرف كيف يبحث عن أخباره (وإلا صمت عند الحاجة)."""
    from autonews.sectors import SECTORS

    missing = [p.label for p in SECTORS.values() if not p.queries]
    assert not missing, f"قطاعات بلا استعلامات: {missing}"
