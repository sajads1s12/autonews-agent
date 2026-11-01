"""اختبارات تشكيل العربية للرسم — أهمّها ترتيب المقاطع المختلطة (عربي + @لاتيني)."""
from __future__ import annotations

import pytest

from autonews.arabic_text import shape_arabic, visual_len


def test_isolated_letters_are_joined():
    """«سلام» تُشكَّل بحروف متصلة (رموز FE70–FEFF) لا حروفاً منفصلة."""
    shaped = shape_arabic("سلام")
    assert any("\ufe70" <= ch <= "\ufeff" for ch in shaped), shaped
    # لام-ألف تحتلّ حرفين ⇒ الناتج أقصر من الأصل بمقدار واحد
    assert len(shape_arabic("سلام")) == 3
    assert len(shape_arabic("لا")) == 1, "لام-ألف اندمجتا في محرف واحد"


def test_visual_order_is_rtl():
    """أول ما يُرسم على اليسار هو آخر كلمة عربية (كي تُقرأ من اليمين)."""
    shaped = shape_arabic("قناة الأنمي")
    assert shaped.endswith("ﻗ") or shaped[-1].startswith("ﻗ"), shaped


def test_digits_stay_in_order():
    shaped = shape_arabic("أخبار بغداد 24")
    assert "24" in shaped, shaped
    assert "42" not in shaped


def test_latin_handle_kept_intact():
    """@username يجب أن يُرسم كما هو (لا يُقلب) مهما كان موضعه."""
    assert "@my_channel" in shape_arabic("@my_channel")
    assert "@anime_iq" in shape_arabic("@anime_iq · قناة الأنمي")
    assert "@akhbar_iraq" in shape_arabic("أخبار العراق · @akhbar_iraq")


def test_mixed_line_order_is_correct():
    """النصّ العربي يأتي على يمين المعرّف في الترتيب البصري (LTR للرسم)."""
    shaped = shape_arabic("قناة الأنمي · @anime_iq")
    # العربية صارت صوراً متصلة (لا حروف خام) ⇒ نقارن بمؤشّر المعرّف فقط
    assert shaped.lstrip().startswith("@anime_iq"), shaped
    assert shaped.count("@") == 1


@pytest.mark.parametrize("text", ["قناة", "أخبار 24", "@handle", "", "   "])
def test_never_raises(text):
    assert isinstance(shape_arabic(text), str)


def test_lam_alef_ligature():
    """«لا» تصبح حرفاً واحداً (FEFB) لا لام+ألف."""
    shaped = shape_arabic("لا")
    assert "\ufefb" in shaped or "\ufefc" in shaped, shaped


def test_tashkeel_is_stripped():
    assert shape_arabic("مُحَمَّد") == shape_arabic("محمد")


def test_visual_len_matches():
    assert visual_len("قناة") == 4
