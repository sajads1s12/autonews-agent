"""اختبارات فصل القنوات: كل قطاع لقناته، بلا تكرار ولا تسرّب."""
from __future__ import annotations

import shutil

import pytest
import yaml

from autonews.config_loader import Channel
from autonews.ingest import Article
from autonews.flash import FlashLane
from autonews.state import State
from autonews.utils import utcnow


def art(title: str, tags: list[str]) -> Article:
    return Article(source_id="s", source_name="مصدر", title=title, summary="",
                   url="https://e.co/1", tags=tags, published=utcnow())


def ch(cid: str, *, tags=(), exclude=()) -> Channel:
    return Channel(id=cid, name=cid, platform="telegram",
                   tags=[t.lower() for t in tags],
                   exclude_tags=[t.lower() for t in exclude],
                   sources=["*"], enabled=True)


@pytest.fixture
def lane():
    from autonews.config_loader import load_config

    return FlashLane(load_config("config", override_dry_run=True), State())


# --------------------------------------------------------------------------- #
#  ١) الفصل الحقيقي
# --------------------------------------------------------------------------- #
def test_anime_excluded_from_general(lane):
    a = art("Shiba Inu Rooms Anime Announced", ["أنمي"])
    assert not lane._targets(ch("tg_main", exclude=["أنمي"]), a)
    assert lane._targets(ch("tg_anime", tags=["أنمي"]), a)


def test_news_excluded_from_anime_channel(lane):
    a = art("تعطيل الدوام في البصرة", ["عراق"])
    assert lane._targets(ch("tg_main", exclude=["أنمي"]), a)
    assert not lane._targets(ch("tg_anime", tags=["أنمي"]), a)


def test_exclusion_wins_over_general(lane):
    """القناة العامة (بلا وسوم) + استثناء ⇒ لا تستقبل القطاع."""
    a = art("خبر أنمي", ["أنمي"])
    assert not lane._targets(ch("general", exclude=["أنمي"]), a)


def test_no_exclusion_means_everything(lane):
    a = art("خبر أنمي", ["أنمي"])
    assert lane._targets(ch("general"), a), "بلا استثناء ⇒ تستقبل كل شيء"


def test_exclusion_is_case_insensitive(lane):
    a = art("Anime news", ["أنمي"])
    assert not lane._targets(ch("g", exclude=["أنمي"]), a)


# --------------------------------------------------------------------------- #
#  ٢) القنوات في الملف الحقيقي
# --------------------------------------------------------------------------- #
def test_config_channels_have_correct_routing():
    data = yaml.safe_load(open("config/channels.yml", encoding="utf-8"))
    by_id = {c["id"]: c for c in data["channels"]}
    assert by_id["tg_main"].get("exclude_tags") == ["أنمي"], \
        "الرئيسية يجب أن تستثني الأنمي (له قناته)"
    # قناة الأنمي تستقبل «أنمي» و«مانغا» — كلاهما نفس القطاع (المحرّك يعرف ذلك)
    assert set(by_id["tg_anime"]["tags"]) == {"أنمي", "مانغا"}
    assert by_id["tg_anime"]["sources"] != ["*"], "للأنمي مصادر متخصّصة لا كل شيء"
    assert by_id["tg_anime"]["enabled"] is True, "قناة الأنمي جاهزة (تُربط من البوت)"


def test_anime_channel_has_own_secret():
    data = yaml.safe_load(open("config/channels.yml", encoding="utf-8"))
    anime = next(c for c in data["channels"] if c["id"] == "tg_anime")
    assert anime["credentials"]["chat_id"] == "TELEGRAM_ANIME_CHAT_ID", \
        "كل قناة تحتاج سرّاً منفصلاً"


# --------------------------------------------------------------------------- #
#  ٣) أداة القنوات
# --------------------------------------------------------------------------- #
def test_channels_table_renders():
    from autonews.channels_main import load_channels, render_table

    table = render_table(load_channels("config"))
    assert "tg_main" in table and "tg_anime" in table
    assert "معطّلة" in table and "مفعّلة" in table


def test_matrix_warns_about_general_channels():
    from autonews.channels_main import load_channels, render_matrix

    text = render_matrix(load_channels("config"))
    assert "مصفوفة التوجيه" in text
    assert "قنوات عامة" in text


def test_add_channel_appends_and_is_idempotent(tmp_path):
    from autonews.channels_main import add_channel, load_channels

    cfg = tmp_path
    shutil.copy("config/channels.yml", cfg / "channels.yml")
    before = len(load_channels(str(cfg)))
    # استخدم معرّفاً غير موجود أصلاً
    msg = add_channel(str(cfg), channel_id="tg_crypto", name="قناة العملات",
                      tags=["عملات"], sector="العملات")
    assert msg.startswith("✅"), msg
    after = load_channels(str(cfg))
    assert len(after) == before + 1
    new = next(c for c in after if c["id"] == "tg_crypto")
    assert new["tags"] == ["عملات"]
    assert new["enabled"] is False, "القناة الجديدة تُضاف معطّلة (أمان)"
    assert new["credentials"]["chat_id"] == "TELEGRAM_CRYPTO_CHAT_ID"

    # لا تكرار
    assert "موجودة مسبقاً" in add_channel(str(cfg), channel_id="tg_crypto",
                                          name="x", tags=["عملات"])


def test_matrix_respects_exclude_tags():
    from autonews.channels_main import load_channels, render_matrix

    text = render_matrix(load_channels("config"))
    anime_line = next(l for l in text.splitlines() if l.startswith("أنمي"))
    assert "tg_anime" in anime_line
    assert "tg_main" not in anime_line, "الرئيسية تستثني الأنمي — لا تظهر في المصفوفة"


def test_add_channel_keeps_yaml_valid(tmp_path):
    from autonews.channels_main import add_channel

    shutil.copy("config/channels.yml", tmp_path / "channels.yml")
    add_channel(str(tmp_path), channel_id="tg_games", name="ألعاب", tags=["ألعاب"])
    data = yaml.safe_load((tmp_path / "channels.yml").read_text(encoding="utf-8"))
    assert any(c["id"] == "tg_games" for c in data["channels"])
