"""اختبارات أدوات التحرير من البوت: الوسوم · القناة الجديدة · المصدر الجديد."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from autonews.editor import (
    FeedCheck, add_channel, add_source, all_tags, check_feed, tag_warnings,
    update_channel_tags, _verify_yaml,
)


@pytest.fixture
def cfg(tmp_path) -> Path:
    """نسخة معزولة من مجلد الإعدادات (لا نلمس الإعدادات الحقيقية)."""
    dst = tmp_path / "config"
    dst.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, dst / name)
    return dst


# --------------------------------------------------------------------------- #
#  🏷 الوسوم
# --------------------------------------------------------------------------- #
def test_add_tag_updates_file_and_loader(cfg):
    from autonews.config_loader import load_config

    res = update_channel_tags("tg_tech", add=["عملات رقمية"], config_dir=str(cfg))
    assert res.ok and "عملات رقمية" in res.tags
    ch = next(c for c in load_config(str(cfg)).channels if c.id == "tg_tech")
    assert "عملات رقمية" in ch.tags, "المحمّل يجب أن يرى الوسم الجديد"


def test_add_tag_is_idempotent(cfg):
    update_channel_tags("tg_tech", add=["عملات"], config_dir=str(cfg))
    res = update_channel_tags("tg_tech", add=["عملات"], config_dir=str(cfg))
    assert res.ok and res.tags.count("عملات") == 1


def test_remove_tag(cfg):
    before = update_channel_tags("tg_tech", add=["مؤقت"], config_dir=str(cfg))
    assert "مؤقت" in before.tags
    after = update_channel_tags("tg_tech", remove=["مؤقت"], config_dir=str(cfg))
    assert "مؤقت" not in after.tags


def test_tag_comments_preserved(cfg):
    update_channel_tags("tg_main", add=["سياسة"], config_dir=str(cfg))
    text = (cfg / "channels.yml").read_text(encoding="utf-8")
    assert "🖋 بصمة الملكية" in text, "التعليقات العربية لا تُمحى"
    assert "exclude_tags" in text


def test_unknown_channel_rejected(cfg):
    res = update_channel_tags("tg_ghost", add=["x"], config_dir=str(cfg))
    assert not res.ok and "غير موجودة" in res.message


def test_tag_warning_when_general_channel_exists(cfg):
    """قناة عامة تستقبل كل شيء ⇒ التنبيه يجب أن يظهر (وإلّا ظننتَ الفصل يعمل)."""
    res = update_channel_tags("tg_tech", add=["رياضة"], config_dir=str(cfg))
    assert res.ok
    assert any("عامة" in w or "تشابه" in w for w in res.warnings)


def test_tag_warning_on_overlap_with_other_channel(cfg):
    warnings = tag_warnings("tg_econ", ["رياضة"], config_dir=str(cfg))
    assert any("tg_sports" in w and "تشابه" in w for w in warnings)


def test_tag_warning_on_excluded_tag(cfg):
    warnings = tag_warnings("tg_anime", ["أنمي"], config_dir=str(cfg))
    assert any("tg_main" in w and "تستثني" in w for w in warnings)


def test_all_tags_catalog(cfg):
    catalog = all_tags(str(cfg))
    assert "أنمي" in catalog and catalog["أنمي"] == ["tg_anime"]
    assert "تقنية" in catalog


def test_yaml_guard_detects_broken_text():
    assert _verify_yaml("channels: [\n  broken", key="channels")
    assert _verify_yaml("channels: []", key="channels") == ""


# --------------------------------------------------------------------------- #
#  ➕ قناة جديدة
# --------------------------------------------------------------------------- #
def test_add_channel_creates_full_block(cfg):
    from autonews.branding import Branding
    from autonews.config_loader import load_config

    ok, msg = add_channel("tg_crypto", name="قناة العملات",
                          tags=["عملات", "بيتكوين"], config_dir=str(cfg))
    assert ok, msg
    cfg_loaded = load_config(str(cfg))
    ch = next(c for c in cfg_loaded.channels if c.id == "tg_crypto")
    assert ch.tags == ["عملات", "بيتكوين"]
    assert ch.credentials["chat_id"] == "TELEGRAM_CRYPTO_CHAT_ID"
    assert Branding.from_channel(ch).ready, "البصمة تُنشأ مع القناة"
    assert ch.flash_eligible is True


def test_add_channel_rejects_duplicate(cfg):
    add_channel("tg_crypto", name="أ", config_dir=str(cfg))
    ok, msg = add_channel("tg_crypto", name="ب", config_dir=str(cfg))
    assert not ok and "موجودة" in msg


def test_add_channel_rejects_bad_id(cfg):
    ok, msg = add_channel("قناة عربية", config_dir=str(cfg))
    assert not ok and "غير صالح" in msg


def test_add_channel_leaves_file_valid_yaml(cfg):
    add_channel("tg_games2", name="ألعاب ٢", tags=["ألعاب"], config_dir=str(cfg))
    data = yaml.safe_load((cfg / "channels.yml").read_text(encoding="utf-8"))
    assert isinstance(data["channels"], list)


# --------------------------------------------------------------------------- #
#  📡 المصدر الجديد
# --------------------------------------------------------------------------- #
def test_check_feed_rejects_bad_url():
    res = check_feed("not-a-url")
    assert not res.ok and "http" in res.error


def test_add_source_verifies_before_adding(cfg):
    """مصدر ميت لا يُضاف — هذا أهمّ ضمان (لا نُلوّث الإعدادات بروابط خربانة)."""
    before = len(yaml.safe_load((cfg / "settings.yml").read_text())["sources"])
    ok, msg = add_source("https://definitely-not-a-real-feed-xyz.example/rss",
                         config_dir=str(cfg), verify=True)
    assert not ok
    after = len(yaml.safe_load((cfg / "settings.yml").read_text())["sources"])
    assert after == before, "لا تغيير في الملف عند الفشل"


def test_add_source_without_verify(cfg):
    from autonews.config_loader import load_config

    ok, msg = add_source("https://feeds.example.com/rss", name="مصدر تجريبي",
                         tags=["تقنية"], config_dir=str(cfg), verify=False)
    assert ok, msg
    sources = load_config(str(cfg)).sources
    assert any("مصدر تجريبي" in s.name for s in sources)


def test_add_source_no_duplicates(cfg):
    add_source("https://feeds.example.com/rss", name="أ", config_dir=str(cfg), verify=False)
    ok, msg = add_source("https://feeds.example.com/rss", name="ب",
                         config_dir=str(cfg), verify=False)
    assert not ok and "موجود" in msg


def test_source_block_has_tags_and_enabled(cfg):
    add_source("https://feeds.example.com/rss", name="اختبار", tags=["رياضة"],
               config_dir=str(cfg), verify=False)
    data = yaml.safe_load((cfg / "settings.yml").read_text(encoding="utf-8"))
    new = [s for s in data["sources"] if s.get("name") == "اختبار"][0]
    assert new["tags"] == ["رياضة"] and new["enabled"] is True


def test_feed_summary_shape():
    ok = FeedCheck(True, "https://x", title="قناة", items=10, fresh=4, sample="عنوان")
    assert "قناة" in ok.summary() and "10" in ok.summary()
    bad = FeedCheck(False, "https://x", error="خطأ")
    assert "❌" in bad.summary()
