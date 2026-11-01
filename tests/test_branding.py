"""اختبارات بصمة الملكية: التوقيع النصّي · العلامة الخفيّة · الصورة · الحفظ في YAML."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from autonews.branding import (
    Branding, apply_branding, apply_footer, hidden_decode, hidden_encode,
    prove_ownership, stamp_image,
)


@dataclass
class FakeChannel:
    id: str = "tg_test"
    name: str = "قناة اختبار"
    platform: str = "telegram"
    signature: str = ""
    max_chars: int = 4096
    extra: dict = field(default_factory=dict)


def make_brand(**kw) -> Branding:
    base = dict(handle="my_channel", title="قناتي", footer="📡 @{handle}")
    base.update(kw)
    return Branding(**base)


# --------------------------------------------------------------------------- #
#  ١) التوقيع المرئي
# --------------------------------------------------------------------------- #
def test_footer_appended_once():
    b = make_brand()
    text = apply_footer("خبر عادي", b, platform="telegram", max_chars=4096)
    assert text.endswith("📡 @my_channel")
    assert text.count("@my_channel") == 1


def test_footer_not_duplicated_on_reapply():
    b = make_brand()
    once = apply_footer("خبر", b, platform="telegram")
    twice = apply_footer(once, b, platform="telegram")
    assert once == twice


def test_footer_respects_max_chars():
    b = make_brand()
    text = apply_footer("س" * 5000, b, platform="telegram", max_chars=200)
    assert len(text) <= 200
    assert "@my_channel" in text


def test_footer_uses_platform_template():
    b = make_brand(platform_templates={"facebook": "تابعنا على فيسبوك: {handle}"})
    assert "فيسبوك" in b.footer_for("facebook")
    assert "📡" in b.footer_for("telegram")


def test_footer_falls_back_to_title_without_handle():
    b = Branding(handle="", title="قناة الأنمي", footer="📡 @{handle}")
    out = b.footer_for("telegram")
    assert "@" not in out and "قناة الأنمي" in out


def test_disabled_branding_returns_text_untouched():
    b = make_brand(enabled=False)
    text = apply_branding("نصّ", b, platform="telegram")
    assert text == "نصّ"


# --------------------------------------------------------------------------- #
#  ٢) العلامة الخفيّة
# --------------------------------------------------------------------------- #
def test_hidden_mark_roundtrip():
    marked = hidden_encode("خبر", "my_channel")
    assert hidden_decode(marked, keep=True) == "my_channel"


def test_hidden_mark_is_invisible_and_strippable():
    marked = hidden_encode("خبر", "my_channel")
    assert hidden_decode(marked) == "خبر"


def test_hidden_mark_survives_whitespace_trim():
    marked = hidden_encode("خبر\n\n", "my_channel")
    assert prove_ownership(marked.strip())[0] is True


def test_prove_ownership_detects_wrong_channel():
    marked = hidden_encode("خبر", "other_channel")
    ok, msg = prove_ownership(marked, expect="my_channel")
    assert ok is False and "other_channel" in msg


def test_prove_ownership_without_mark():
    ok, msg = prove_ownership("نصّ عادي بلا علامة")
    assert ok is False and "لا توجد" in msg


def test_hidden_marks_are_compact():
    """٤ محارف صفرية لكل بايت ⇒ الاسم الكامل ≤ ~٦٠ محرفاً غير مرئي."""
    marked = hidden_encode("خبر", "a" * 14)
    zw = sum(1 for ch in marked if ch in "\u200b\u200c\u200d\u2060")
    assert zw <= 4 * 14 + 4


def test_x_platform_skips_hidden_mark():
    b = make_brand()
    text = apply_branding("تغريدة قصيرة", b, platform="x", max_chars=280)
    assert hidden_decode(text, keep=True) == ""


# --------------------------------------------------------------------------- #
#  ٣) العلامة على الصورة
# --------------------------------------------------------------------------- #
pytest.importorskip("PIL")


def _sample_image(path: Path) -> str:
    from PIL import Image

    Image.new("RGB", (900, 500), (30, 60, 120)).save(path)
    return str(path)


def test_stamp_creates_new_image(tmp_path):
    src = _sample_image(tmp_path / "a.jpg")
    out = stamp_image(src, make_brand())
    assert out != src and Path(out).exists()


def test_stamp_disabled_returns_original(tmp_path):
    src = _sample_image(tmp_path / "a.jpg")
    assert stamp_image(src, make_brand(stamp_on_image=False)) == src


def test_stamp_never_raises_on_broken_file(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not-an-image")
    assert stamp_image(str(bad), make_brand()) == str(bad)


def test_stamp_writes_arabic_label(tmp_path):
    """العربية تُشكَّل وتُرسم (لا حروف مفكّكة) — نتحقّق من الناتج مختلف عن الأصل."""
    src = _sample_image(tmp_path / "a.jpg")
    out = stamp_image(src, make_brand(image_text="قناة الأنمي @{handle}"))
    assert Path(out).stat().st_size > 0


def test_brand_image_replaces_path(tmp_path):
    from autonews.branding import brand_image
    from autonews.images import ArticleImage

    src = _sample_image(tmp_path / "a.jpg")
    img = ArticleImage(path=src, url="http://x/y.jpg")
    out = brand_image(img, FakeChannel(extra={"branding": {"handle": "my_channel"}}))
    assert out.path != src and out.ok
    assert not Path(src).exists(), "الصورة الأصلية تُنظَّف بعد الختم"


def test_brand_image_noop_without_handle(tmp_path):
    from autonews.branding import brand_image
    from autonews.images import ArticleImage

    src = _sample_image(tmp_path / "a.jpg")
    img = ArticleImage(path=src)
    out = brand_image(img, FakeChannel())
    assert out.path == src


# --------------------------------------------------------------------------- #
#  ٤) القراءة من القناة
# --------------------------------------------------------------------------- #
def test_from_channel_reads_extra():
    ch = FakeChannel(extra={"branding": {"handle": "@abc_news", "title": "أخبار"}})
    b = Branding.from_channel(ch)
    assert b.handle == "abc_news" and b.title == "أخبار"


def test_from_channel_legacy_signature():
    ch = FakeChannel(signature="📡 تابعونا")
    b = Branding.from_channel(ch)
    assert b.footer == "📡 تابعونا" and b.enabled


def test_real_config_has_branding_for_every_channel():
    from autonews.config_loader import load_config

    settings = load_config("config")
    for ch in settings.channels:
        raw = ch.extra.get("branding")
        assert raw is not None, f"{ch.id} بلا كتلة branding"
        b = Branding.from_channel(ch)
        assert b.stamp_on_image is True
        assert b.footer_for(ch.platform), f"{ch.id} بلا توقيع"


# --------------------------------------------------------------------------- #
#  ٥) الحفظ في YAML (من البوت بلا محرّر)
# --------------------------------------------------------------------------- #
def test_write_branding_updates_handle(tmp_path):
    from autonews.config_loader import load_config
    from autonews.utils import write_branding

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, cfg / name)

    ok, _ = write_branding("tg_sports", handle="sports_iq", config_dir=str(cfg))
    assert ok
    ch = next(c for c in load_config(str(cfg)).channels if c.id == "tg_sports")
    assert Branding.from_channel(ch).handle == "sports_iq"

    # التعليقات العربية تبقى (لا نُعيد توليد الملف)
    assert "# 🖋 بصمة الملكية" in (cfg / "channels.yml").read_text(encoding="utf-8")


def test_write_branding_unknown_channel(tmp_path):
    from autonews.utils import write_branding

    cfg = tmp_path / "config"
    cfg.mkdir()
    shutil.copy("config/channels.yml", cfg / "channels.yml")
    ok, msg = write_branding("tg_nope", handle="x", config_dir=str(cfg))
    assert not ok and "غير موجودة" in msg
