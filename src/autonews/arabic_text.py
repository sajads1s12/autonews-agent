"""تشكيل الحروف العربية للرسم على الصور — تنفيذ مدمج بلا مكتبات خارجية.

المشكلة: مكتبات الرسم (Pillow) لا تُشكّل العربية. ترسم «محمد» حروفاً منفصلة
معكوسة الترتيب، فتبدو مشوّهة. الحلّ المعتاد مكتبتان (`arabic-reshaper` + `python-bidi`)
— لكن كلّ اعتمادية إضافية = نقطة فشل في GitHub Actions وحصة وقت.

لذلك نُشكّل هنا بأنفسنا:
  ١) استبدال كل حرف بشكله الصحيح (ابتدائي/وسطي/نهائي/منفصل) حسب جيرانه.
  ٢) قلب ترتيب المقاطع (RTL) مع الحفاظ على ترتيب الأرقام واللاتينية (LTR).
  ٣) دعم الحركات (تشكيل) واللام-ألف (لا) والألف الممدودة (آ) والهمزات.

الناتج: نصّ «جاهز للرسم» يُمرَّر إلى `ImageDraw.text` مباشرة.
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- #
#  جداول الأشكال — (منفصل، نهائي، ابتدائي، وسطي) — فارغ = لا شكل
# --------------------------------------------------------------------------- #
_FORMS: dict[str, tuple[str, str, str, str]] = {
    "\u0621": ("\u0621", "", "", ""),                                  # ء
    "\u0622": ("\u0622", "\uFE81", "\uFE81", ""),                       # آ
    "\u0623": ("\u0623", "\uFE83", "\uFE83", ""),                       # أ
    "\u0624": ("\u0624", "\uFE85", "\uFE85", ""),                       # ؤ
    "\u0625": ("\u0625", "\uFE87", "\uFE87", ""),                       # إ
    "\u0626": ("\u0626", "\uFE89", "\uFE8B", "\uFE8C"),                 # ئ
    "\u0627": ("\u0627", "\uFE8D", "\uFE8D", ""),                       # ا
    "\u0628": ("\u0628", "\uFE8F", "\uFE91", "\uFE92"),                 # ب
    "\u0629": ("\u0629", "\uFE93", "\uFE93", ""),                       # ة
    "\u062A": ("\u062A", "\uFE95", "\uFE97", "\uFE98"),                 # ت
    "\u062B": ("\u062B", "\uFE99", "\uFE9B", "\uFE9C"),                 # ث
    "\u062C": ("\u062C", "\uFE9D", "\uFE9F", "\uFEA0"),                 # ج
    "\u062D": ("\u062D", "\uFEA1", "\uFEA3", "\uFEA4"),                 # ح
    "\u062E": ("\u062E", "\uFEA5", "\uFEA7", "\uFEA8"),                 # خ
    "\u062F": ("\u062F", "\uFEA9", "\uFEA9", ""),                       # د
    "\u0630": ("\u0630", "\uFEAB", "\uFEAB", ""),                       # ذ
    "\u0631": ("\u0631", "\uFEAD", "\uFEAD", ""),                       # ر
    "\u0632": ("\u0632", "\uFEAF", "\uFEAF", ""),                       # ز
    "\u0633": ("\u0633", "\uFEB1", "\uFEB3", "\uFEB4"),                 # س
    "\u0634": ("\u0634", "\uFEB5", "\uFEB7", "\uFEB8"),                 # ش
    "\u0635": ("\u0635", "\uFEB9", "\uFEBB", "\uFEBC"),                 # ص
    "\u0636": ("\u0636", "\uFEBD", "\uFEBF", "\uFEC0"),                 # ض
    "\u0637": ("\u0637", "\uFEC1", "\uFEC3", "\uFEC4"),                 # ط
    "\u0638": ("\u0638", "\uFEC5", "\uFEC7", "\uFEC8"),                 # ظ
    "\u0639": ("\u0639", "\uFEC9", "\uFECB", "\uFECC"),                 # ع
    "\u063A": ("\u063A", "\uFECD", "\uFECF", "\uFED0"),                 # غ
    "\u0641": ("\u0641", "\uFED1", "\uFED3", "\uFED4"),                 # ف
    "\u0642": ("\u0642", "\uFED5", "\uFED7", "\uFED8"),                 # ق
    "\u0643": ("\u0643", "\uFED9", "\uFEDB", "\uFEDC"),                 # ك
    "\u0644": ("\u0644", "\uFEDD", "\uFEDF", "\uFEE0"),                 # ل
    "\u0645": ("\u0645", "\uFEE1", "\uFEE3", "\uFEE4"),                 # م
    "\u0646": ("\u0646", "\uFEE5", "\uFEE7", "\uFEE8"),                 # ن
    "\u0647": ("\u0647", "\uFEE9", "\uFEEB", "\uFEEC"),                 # ه
    "\u0648": ("\u0648", "\uFEED", "\uFEED", ""),                       # و
    "\u0649": ("\u0649", "\uFEEF", "\uFEEF", ""),                       # ى
    "\u064A": ("\u064A", "\uFEF1", "\uFEF3", "\uFEF4"),                 # ي
    "\u0640": ("\u0640", "\u0640", "\u0640", "\u0640"),                 # ـ تطويل
    # فارسية/أردية شائعة في أسماء القنوات
    "\u067E": ("\u067E", "\uFB56", "\uFB58", "\uFB59"),                 # پ
    "\u0686": ("\u0686", "\uFB7A", "\uFB7C", "\uFB7D"),                 # چ
    "\u0698": ("\u0698", "\uFB8A", "\uFB8A", ""),                       # ژ
    "\u06AF": ("\u06AF", "\uFB92", "\uFB94", "\uFB95"),                 # گ
}

# اللام-ألف بكل صورها
_LAM_ALEF = {
    "\u0622": ("\uFEF5", "\uFEF6"),   # لآ
    "\u0623": ("\uFEF7", "\uFEF8"),   # لأ
    "\u0625": ("\uFEF9", "\uFEFA"),   # لإ
    "\u0627": ("\uFEFB", "\uFEFC"),   # لا
}

# حروف لا تتّصل بما بعدها
_FORWARD_JOINERS = {chr(code) for code in (
    0x0621, 0x0622, 0x0623, 0x0624, 0x0625, 0x0627, 0x062F, 0x0630, 0x0631,
    0x0632, 0x0648, 0x0649, 0x0629, 0x0698,
)}

_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_ARABIC_RANGE = "\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF"


def _is_arabic(ch: str) -> bool:
    return bool(re.match(f"[{_ARABIC_RANGE}]", ch))


def _joins_forward(ch: str) -> bool:
    """هل يتّصل هذا الحرف بالحرف الذي بعده؟"""
    return ch in _FORMS and ch not in _FORWARD_JOINERS


def shape_arabic(text: str, *, strip_tashkeel: bool = True) -> str:
    """تحويل نصّ عربي إلى صورته المتصلة الجاهزة للرسم (بترتيب LTR بصرياً)."""
    if not text:
        return ""
    if strip_tashkeel:
        text = _DIACRITICS_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()

    # ١) اللام-ألف أولاً (لأنها تحتلّ حرفين)
    merged: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\u0644" and i + 1 < len(text) and text[i + 1] in _LAM_ALEF:
            merged.append("\u0000" + text[i + 1])      # علامة داخلية للام-ألف
            i += 2
            continue
        merged.append(ch)
        i += 1

    # ٢) اختيار الشكل حسب الجوار
    out: list[str] = []
    for idx, ch in enumerate(merged):
        if ch.startswith("\u0000"):
            base = ch[1:]
            prev_ch = merged[idx - 1] if idx > 0 else ""
            prev_joins = _joins_forward(prev_ch) if prev_ch and not prev_ch.startswith("\u0000") else False
            isolated, final = _LAM_ALEF[base]
            out.append(final if prev_joins else isolated)
            continue
        forms = _FORMS.get(ch)
        if forms is None:
            out.append(ch)
            continue
        isolated, final, initial, medial = forms
        prev_ch = merged[idx - 1] if idx > 0 else ""
        next_ch = merged[idx + 1] if idx + 1 < len(merged) else ""
        prev_joins = _joins_forward(prev_ch) if prev_ch and not prev_ch.startswith("\u0000") else False
        if prev_ch.startswith("\u0000"):
            prev_joins = True
        next_joins = bool(next_ch) and next_ch in _FORMS

        if prev_joins and next_joins and medial:
            out.append(medial)
        elif prev_joins and final:
            out.append(final)
        elif next_joins and initial:
            out.append(initial)
        else:
            out.append(isolated)

    # ٣) ترتيب بصري: اقلب المقاطع العربية فقط (الأرقام واللاتينية تبقى كما هي)
    return _visual_order("".join(out))


def _is_ltr_char(ch: str) -> bool:
    """محرف لاتيني/رقم يُقرأ من اليسار (يشمل @ _ . / : -)."""
    return ch.isascii() and (ch.isalnum() or ch in "@_./:-+%&#")


def _split_runs(text: str) -> list[tuple[bool, str]]:
    """تقسيم النصّ إلى مقاطع: (هل هو LTR؟، المقطع).

    المحارف المحايدة (مسافة، ·، —) تُلحق بالمقطع العربي: فصلها يُنتج فوضى
    بصرية («العراق · @x» تصبح قطعاً متناثرة).
    """
    runs: list[tuple[bool, str]] = []
    for ch in text:
        is_ltr = _is_ltr_char(ch)
        if runs and runs[-1][0] == is_ltr:
            runs[-1] = (is_ltr, runs[-1][1] + ch)
        elif runs and not is_ltr and runs[-1][0]:
            runs.append((False, ch))          # محرف محايد بعد لاتيني ⇒ عربي
        else:
            runs.append((is_ltr, ch))
    return runs


def _visual_order(text: str) -> str:
    """ترتيب بصري: كل مقطع عربي يُقلب، واللاتيني يبقى، والمقاطع تُرتَّب من الآخر.

    لماذا هذا التعقيد؟ لأن الرسم يجري من اليسار (Pillow)، والعربية تُقرأ من
    اليمين. الخلط بين الاثنين («@name · نصّ عربي») لا يُحلّ بقلب النصّ كاملاً —
    بل بتقسيمه إلى مقاطع وقلب كل مقطع عربي على حِدة مع عكس ترتيب المقاطع.
    """
    return "".join(run if is_ltr else run[::-1]
                   for is_ltr, run in reversed(_split_runs(text)))


def _looks_ltr(tok: str) -> bool:
    """هل المقطع مقروء من اليسار؟ (رقم/كلمة لاتينية/رابط)"""
    letters = [c for c in tok if not c.isspace()]
    if not letters:
        return True
    latin = sum(1 for c in letters if c.isascii() and c.isalnum())
    return latin / len(letters) > 0.5


def visual_len(text: str) -> int:
    """عدد المحارف بعد التشكيل (لحساب العرض التقريبي)."""
    return len(shape_arabic(text))


__all__ = ["shape_arabic", "visual_len", "unicodedata"]
