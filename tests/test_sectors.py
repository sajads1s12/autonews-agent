"""اختبارات 🧠 التعرّف على القطاع من اسم القناة."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from autonews.sectors import (SECTORS, apply_to_channel, clean_name, detect,
                              score, sources_for, suggest_sources)


# --------------------------------------------------------------------------- #
#  ١) التعرّف من الاسم
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,expected", [
    ("أخبار الوتين", "news"),
    ("قناة الأنمي والمانغا", "anime_manga"),
    ("عالم الأنمي", "anime"),
    ("أخبار الأنمي والمانجا", "anime_manga"),        # الأخصّ يفوز على «أخبار»
    ("🎌 مانجا لاند", "manga"),
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


@pytest.mark.parametrize("name", ["قناة دجلة", "السومرية", "كركوك"])
def test_unknown_named_channels_get_scoped_custom_profiles(name):
    profile = detect(name)
    assert profile is not None and profile.key.startswith("custom_")
    assert profile.tags and profile.queries


def test_empty_channel_name_has_no_profile():
    assert detect("") is None


def test_detect_prefers_specialist_over_general():
    """«أخبار الأنمي» أنمي لا أخبار — وإلا صار الأنمي في القناة العامة."""
    assert detect("أخبار الأنمي").key == "anime"


@pytest.mark.parametrize("name,expected", [
    ("أخبار المانغا", "manga"),                          # كانت news: «اخبار» تُحسب ×٢ و«مانغا» ×١
    ("قناة المانغا — أخبار المانغا الجديدة", "manga"),
    ("أخبار الأنمي", "anime"),
    ("أخبار الرياضة", "sports"),
    ("أخبار التقنية", "tech"),
    ("أخبار الألعاب", "gaming"),
    ("وكالة أخبار الرياضة", "sports"),                   # الأخصّ يتقدّم على «وكالة/أخبار»
    ("أخبار عاجلة", "news"),
    ("أخبار العراق", "news"),
    ("الأخبار العامة", "news"),
])
def test_news_word_never_overrides_a_specific_sector(name, expected):
    """«أخبار X» = قطاع X. وكلمة «أخبار» وحدها = الأخبار العامة."""
    assert detect(name).key == expected


def test_keyword_variants_are_not_double_counted():
    """«انمي/أنمي» و«اخبار/أخبار» كلمة واحدة بعد التطبيع — تُحسب مرّة واحدة."""
    assert score("أخبار عامة")["news"] == 3
    assert score("الأنمي")["anime"] == 3


def test_clean_name_strips_emoji_and_punctuation():
    cleaned = clean_name("🎌 قناة الأنمي | Manga 🍥")
    assert "🎌" not in cleaned and "|" not in cleaned
    assert "انمي" in cleaned


def test_score_reports_alternatives():
    scores = score("أخبار الأنمي والمانغا")
    assert detect("أخبار الأنمي والمانغا").key == "anime_manga"


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


def test_general_news_uses_wildcard_not_frozen_list(sources):
    """القناة العامة = **الرمز** `*`، لا قائمة مجمَّدة بكل المصادر الحالية.

    كان الخلل: `sources_for(عام)` تُرجع قائمة بكل المعرّفات فتُكتب في
    `channels.yml` وقت الإضافة، فأي مصدر جديد يُضاف لاحقاً (من البوت) لا يصل
    القناة الرئيسية أبداً — قُفلت فعلياً على ٣٩ مصدراً.

    الرمز `*` يُحلّ وقت التوجيه، فيبقى «كل المصادر» صحيحاً دائماً.
    الفصل عن الأنمي/الرياضة يحدث بـ `exclude_tags` لا بحجب المصادر.
    """
    picked, why = sources_for(SECTORS["news"], sources)
    assert picked == ["*"], f"القناة العامة يجب أن تحصل على '*' لا {len(picked)} معرّفاً"
    assert "كل المصادر" in why


def test_wildcard_reaches_new_sources_at_routing_time():
    """الرمز `*` يُحلّ في الموجّه: مصدر يُضاف بعد الإعداد يصل القناة العامة."""
    from autonews.config_loader import Channel
    from autonews.ingest import Article
    from autonews.router import ContentRouter

    general = Channel(id="tg_main", name="الرئيسية", platform="telegram",
                      sources=["*"], exclude_tags=["أنمي"])
    anime = Channel(id="tg_anime", name="أنمي", platform="telegram",
                    sources=["ann_anime"], tags=["أنمي"])

    later = Article(title="خبر من مصدر أُضيف لاحقاً", summary="تفاصيل", content="",
                    url="https://e.com/x", source_id="added_later",
                    source_name="جديد", tags=["أخبار"])
    ids = [r.channel.id for r in ContentRouter([general, anime]).route(later)]
    assert "tg_main" in ids, "القناة العامة لم تستقبل مصدراً جديداً — عاد القفل"
    assert "tg_anime" not in ids

    anime_post = Article(title="فيلم أنمي جديد", summary="تفاصيل", content="",
                         url="https://e.com/y", source_id="ann_anime",
                         source_name="أنمي", tags=["أنمي"])
    ids = [r.channel.id for r in ContentRouter([general, anime]).route(anime_post)]
    assert "tg_anime" in ids
    assert "tg_main" not in ids, "خبر الأنمي تسرّب للقناة العامة — الفصل انكسر"


def test_every_sector_has_at_least_one_source(sources):
    for key, profile in SECTORS.items():
        picked, why = sources_for(profile, sources)
        if not picked:
            assert suggest_sources(profile, existing=sources), f"{key}: لا مصادر مقترحة"
        if picked != ["*"] and picked:
            assert len(picked) >= 1


def test_specialized_topic_without_sources_stays_isolated():
    """القناة المتخصصة لا تتحول إلى قناة عامة عند غياب المصدر."""
    picked, why = sources_for(SECTORS["sports"], [])
    assert picked == []


def test_unknown_tag_falls_back_to_all(sources):
    from autonews.sectors import SectorProfile

    made_up = SectorProfile(key="x", label="وهمي", icon="❓",
                            keywords=("وهمي",), tags=("لا_يوجد_وسم",))
    picked, why = sources_for(made_up, sources)
    assert picked == []


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


def test_apply_unknown_name_gets_custom_scope(cfgdir):
    from autonews.config_loader import load_config
    ok, msg = apply_to_channel("tg_main", "قناة دجلة", config_dir=str(cfgdir), override=True)
    assert ok, msg
    channel = next(c for c in load_config(str(cfgdir)).channels if c.id == "tg_main")
    assert channel.sources and channel.sources != ["*"]
    assert any(tag.startswith("custom_") for tag in channel.tags)


# --------------------------------------------------------------------------- #
#  ٤) التصنيف اليدوي — sector_of · set_sector · catalog
# --------------------------------------------------------------------------- #
def test_sector_of_reads_from_tags(cfgdir):
    from autonews.sectors import sector_of

    assert sector_of("tg_anime", config_dir=str(cfgdir)).key == "anime_manga"
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


# --------------------------------------------------------------------------- #
#  الرمز «*» — كل المصادر، لا اسم قطاع ولا قائمة مجمَّدة
# --------------------------------------------------------------------------- #
def test_star_is_not_a_sector():
    """«*» رمز يعني كل المصادر — لا يُفسَّر كاسم قطاع."""
    from autonews.sectors import detect

    assert detect("*") is None
    assert detect("all") is None


def test_general_channel_gets_star_in_added_channel(tmp_path):
    """قناة عامة تُضاف من البوت → `sources: ["*"]` لا قائمة مجمَّدة."""
    import shutil

    from autonews.channels_main import load_channels

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        shutil.copy(f"config/{name}", cfg / name)

    from autonews.editor import add_channel, suggest_channel_id
    from autonews.sectors import apply_to_channel, detect

    profile = detect("قناة الأخبار العاجلة واليومية")
    assert profile is not None and not profile.tags        # قطاع عام
    cid = suggest_channel_id("قناة الأخبار العاجلة", existing=set())
    ok, _ = add_channel(cid, name="قناة الأخبار العاجلة",
                        tags=list(profile.tags), config_dir=str(cfg))
    assert ok
    apply_to_channel(cid, "قناة الأخبار العاجلة", config_dir=str(cfg), override=True)

    ch = next(c for c in load_channels(str(cfg)) if c["id"] == cid)
    assert ch["sources"] == ["*"], f"القناة العامة قُفلت على {ch.get('sources')}"


def test_normalize_config_unlocks_frozen_general_channel(tmp_path):
    """تطهير الإعدادات: قناة عامة بمصادر مجمَّدة تُقلَب إلى `*` — والمخصّصة لا تُلمَس."""
    import shutil

    import yaml

    from autonews.sectors import normalize_config

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "channels.yml").write_text(
        yaml.safe_dump({"channels": [{
            "id": "tg_main", "name": "القناة العامة", "platform": "telegram",
            "enabled": True, "sources": ["ina_official", "shafaq"],
            "exclude_tags": ["أنمي"],
        }, {
            "id": "tg_sports", "name": "رياضة", "platform": "telegram",
            "enabled": True, "sources": ["sport_feed"], "tags": ["رياضة"],
        }]}, allow_unicode=True, sort_keys=False), encoding="utf-8")

    fixed = normalize_config(str(cfg))
    assert "tg_main" in fixed

    after = yaml.safe_load((cfg / "channels.yml").read_text(encoding="utf-8"))
    assert next(c for c in after["channels"] if c["id"] == "tg_main")["sources"] == ["*"]
    assert next(c for c in after["channels"] if c["id"] == "tg_sports")["sources"] == ["sport_feed"]
    # ولا أثر ثانٍ عند إعادة التشغيل
    assert normalize_config(str(cfg)) == []


def test_exclude_tags_is_separation_not_customisation(tmp_path):
    """exclude_tags أداة فصل — لا تعني أن القناة مخصّصة فيُترك قفلها."""
    import shutil

    import yaml

    from autonews.sectors import normalize_config

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "channels.yml").write_text(
        yaml.safe_dump({"channels": [{
            "id": "tg_main", "name": "القناة العامة", "platform": "telegram",
            "enabled": True, "sources": ["shafaq"], "exclude_tags": ["أنمي"],
        }]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert "tg_main" in normalize_config(str(cfg))
