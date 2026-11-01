"""بصمة الملكية — نُثبت أن المنشور لك، حتى لو سُرِق حرفياً.

ثلاث طبقات متكاملة (كلها اختيارية، ولكل قناة تحكّم مستقلّ):

١) **توقيع مرئي في نصّ المنشور** — `📡 @قناتك` في الأسفل، أو بصيغة منصّة معيّنة
   (`المصدر: @قناتك` لتيليجرام، `via @قناتك` لـ X…). هذا ما يراه القارئ.

٢) **علامة مائية على الصورة** — تُرسم على حافة الصورة داخل برنامج التوليد ذاته
   (لا على الخادم): اسم القناة بخطّ عربي + ظلّ خفيف. أي شخص يحفظ الصورة يراها.
   وتُعاد الصورة بحجم معقول وJPEG لتقليل الوزن.

٣) **علامة مائية خفيّة داخل النص** — يُرمَّز اسم قناتك في محارف صفرية العرض
   (Zero-Width) في نهاية المنشور. لا يراها أحد، لكنها **تنتقل مع النسخ واللصق**،
   فيمكن إثبات السرقة بكود واحد: `python -m autonews.brand_main --prove "النص"`.

لماذا هذا مهمّ عملياً؟
- تيليجرام لا يفهرس `#` كوسم فعلي — فالاعتماد على الهاشتاغ وحده لحماية الحقوق وهم.
- الصور تُنسخ وتُنشر في قنوات أخرى بلا مصدر: العلامة على الصورة تبقى ملتصقة بها.
- العلامة الخفيّة لا تُزال بـ «نسخ ولصق»، وهي أرخص إثبات ملكية ممكن.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("autonews.brand")

# --------------------------------------------------------------------------- #
#  حقول العلامة الخفيّة (محارف صفرية العرض — لا تُرى ولا تُحذف بالنسخ)
# --------------------------------------------------------------------------- #
# أربعة محارف صفرية العرض ⇒ ٢ بِت لكل محرف (نصف الحجم مقارنة بمحرفين)
ZW_ALPHABET = "\u200b\u200c\u200d\u2060"
_ZW_SET = {ch: str(i) for i, ch in enumerate(ZW_ALPHABET)}
_ZW_REV = {str(i): ch for i, ch in enumerate(ZW_ALPHABET)}
HIDDEN_MAX_CHARS = 14          # طول اسم القناة المُرمَّز (يكفي للتمييز)

DEFAULT_POSITION = "bottom-right"
POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center-bottom")


# --------------------------------------------------------------------------- #
#  الإعداد
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Branding:
    """إعداد البصمة لقناة واحدة (يُقرأ من `channels.yml → branding`)."""

    handle: str = ""            # @my_channel (بلا @)
    title: str = ""             # اسم القناة الظاهر (للعرض)
    enabled: bool = True
    # نصّ المنشور
    footer: str = "📡 @{handle}"                 # التوقيع المرئي
    platform_templates: dict[str, str] = field(default_factory=dict)
    # الصورة
    stamp_on_image: bool = True
    image_text: str = "@{handle}"               # ما يُرسم على الصورة
    position: str = DEFAULT_POSITION
    opacity: float = 0.72
    font_size: int = 0                          # 0 = تلقائي حسب عرض الصورة
    chip: bool = True                           # شريط داكن خلف النص (وضوح على أي صورة)
    # العلامة الخفيّة
    hidden_mark: bool = True

    # -------------------------------------------------------------- التوقيع
    def handle_ref(self) -> str:
        """أفضل معرّف متاح: @username وإلّا اسم القناة."""
        return f"@{self.handle}" if self.handle else (self.title or "")

    def footer_for(self, platform: str) -> str:
        """سطر التوقيع المناسب للمنصّة."""
        if not self.enabled:
            return ""
        template = self.platform_templates.get(platform) or self.footer
        if not template:
            return ""
        if not self.handle:
            # بلا معرّف (@username): الاسم الظاهر هو البديل الوحيد المتاح
            template = template.replace("@{handle}", "{title}").replace("{handle}", "{title}")
        return template.replace("{handle}", self.handle).replace(
            "{title}", self.title or self.handle).strip()

    def image_label(self) -> str:
        """نصّ العلامة على الصورة — يتطلّب معرّفاً (@username).

        بلا معرّف نُرجع فراغاً كي لا نطبع «@» وحده أو اسماً عامّاً على صورة
        قد لا تكون لنا. الاسم الظاهر يُستخدم في توقيع النصّ فقط.
        """
        if not self.handle:
            return ""
        return (self.image_text or "@{handle}").replace(
            "{handle}", self.handle).replace("{title}", self.title or self.handle).strip()

    @property
    def ready(self) -> bool:
        return bool(self.enabled and (self.handle or self.title))

    # ------------------------------------------------------------- الترميز
    def payload(self) -> str:
        """الرمز المخفي: اسم القناة (بلا @) — يُستخدم للاستدلال."""
        return re.sub(r"[^0-9A-Za-z_.]", "", self.handle)[:24] or "autonews"

    @classmethod
    def from_channel(cls, channel: Any) -> "Branding":
        """بناء البصمة من كائن القناة (بدون استيراد دائري)."""
        raw: dict[str, Any] = dict(getattr(channel, "extra", {}).get("branding") or {})
        # توافق قديم: القناة كانت تستخدم signature فقط
        if not raw and getattr(channel, "signature", ""):
            raw = {"footer": channel.signature, "enabled": True}
        handle = str(raw.get("handle") or "").lstrip("@")
        return cls(
            handle=handle,
            title=str(raw.get("title") or getattr(channel, "name", "")),
            enabled=bool(raw.get("enabled", True)),
            footer=str(raw.get("footer", "📡 @{handle}")),
            platform_templates={str(k): str(v) for k, v in (raw.get("platform") or {}).items()},
            stamp_on_image=bool(raw.get("stamp_on_image", True)),
            image_text=str(raw.get("image_text", "@{handle}")),
            position=str(raw.get("position", DEFAULT_POSITION)),
            opacity=float(raw.get("opacity", 0.72)),
            font_size=int(raw.get("font_size", 0) or 0),
            chip=bool(raw.get("chip", True)),
            hidden_mark=bool(raw.get("hidden_mark", True)),
        )


# --------------------------------------------------------------------------- #
#  ١) التوقيع المرئي في النصّ
# --------------------------------------------------------------------------- #
def _strip_existing(text: str, footer: str) -> str:
    """إزالة توقيع قديم متطابق كي لا يتكرّر عند إعادة التركيب."""
    core = footer.strip()
    if not core:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip() != core]
    return "\n".join(lines)


def apply_footer(text: str, branding: Branding, *, platform: str = "telegram",
                 max_chars: int = 4096) -> str:
    """إضافة التوقيع المرئي في نهاية المنشور (بلا تكرار وبلا تجاوز الحدّ)."""
    if not branding.ready:
        return text
    footer = branding.footer_for(platform)
    if not footer:
        return text
    text = _strip_existing(text, footer)
    if footer in text:                      # التوقيع موجود داخل النصّ أصلاً
        return text
    room = max_chars - len(footer) - 2
    if room <= 0:
        return text
    body = text if len(text) <= room else text[:room].rstrip()
    sep = "\n" if platform != "x" else "\n"
    return f"{body.rstrip()}{sep}{footer}"


# --------------------------------------------------------------------------- #
#  ٢) العلامة الخفيّة (صفرية العرض)
# --------------------------------------------------------------------------- #
def hidden_encode(text: str, token: str) -> str:
    """إخفاء رمز داخل النصّ بمحارف صفرية العرض (يُنزع أولاً أي رمز سابق)."""
    if not token:
        return text
    clean = hidden_decode(text)          # النصّ منظّفاً من أي علامة سابقة
    data = token[:HIDDEN_MAX_CHARS].encode("utf-8")
    bits = "".join(f"{byte:08b}" for byte in data)
    marks = "".join(_ZW_REV[f"{int(bits[i:i + 2], 2):d}"]
                    for i in range(0, len(bits) - len(bits) % 2, 2))
    return f"{clean.rstrip()}{marks}"


def hidden_decode(text: str, *, keep: bool = False) -> str:
    """استخراج الرمز المخفي (أو تنظيف النصّ منه إن `keep=False`)."""
    marks = "".join(_ZW_SET[ch] for ch in text if ch in _ZW_SET)
    payload = ""
    if len(marks) >= 8:
        bits = "".join(f"{int(d):02b}" for d in marks)
        raw = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8))
        payload = raw.decode("utf-8", "ignore").strip("\x00")
    if keep:
        return payload
    return "".join(ch for ch in text if ch not in _ZW_SET)


def prove_ownership(text: str, *, expect: str = "") -> tuple[bool, str]:
    """إثبات الملكية: هل يحمل النصّ بصمتنا؟ (ولمن؟)"""
    token = hidden_decode(text, keep=True)
    if not token:
        return False, "لا توجد علامة خفيّة في النصّ"
    if expect and token.lower() != expect.lower().lstrip("@"):
        return False, f"العلامة تعود إلى @{token} لا إلى @{expect.lstrip('@')}"
    return True, f"العلامة تعود إلى @{token}"


# --------------------------------------------------------------------------- #
#  ٣) العلامة المائية على الصورة
# --------------------------------------------------------------------------- #
def _bundled_reshaper():
    """مُشكّل عربي مدمج (نحويل الحروف لأشكالها المتصلة)."""
    from .arabic_text import shape_arabic  # noqa: PLC0415 — استيراد مؤجّل

    return shape_arabic


def _load_font(size: int):
    """أفضل خطّ عربي متاح (مع بدائل)."""
    from PIL import ImageFont  # noqa: PLC0415

    candidates = [
        os.environ.get("AUTONEWS_FONT", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:               # إصدارات Pillow القديمة
        return ImageFont.load_default()


def stamp_image(image_path: str, branding: Branding, *,
                output: str = "", max_width: int = 1280) -> str:
    """رسم اسم القناة على الصورة. يعيد مسار الصورة المعالجة (أو الأصل عند الفشل).

    المبدأ: **لا نُفشل النشر أبداً** — إن فشل أي شيء (لا Pillow، صورة تالفة)
    نُعيد المسار الأصلي ونكمل.
    """
    label = branding.image_label()
    if not (branding.enabled and branding.stamp_on_image and label and image_path):
        return image_path
    try:
        from PIL import Image, ImageDraw, ImageFilter  # noqa: PLC0415

        shape = _bundled_reshaper()
    except Exception as exc:  # noqa: BLE001
        log.warning("تعذّر تجهيز العلامة على الصورة (%s) — الصورة الأصلية تُنشر", exc)
        return image_path

    try:
        with Image.open(image_path) as raw:
            img = raw.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)))

            text = shape(label)
            size = branding.font_size or max(18, min(48, img.width // 26))
            font = _load_font(size)
            draw = ImageDraw.Draw(img, "RGBA")
            box = draw.textbbox((0, 0), text, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            pad = max(8, size // 3)
            margin = max(10, img.width // 40)

            pos = branding.position if branding.position in POSITIONS else DEFAULT_POSITION
            x = margin if "left" in pos else img.width - tw - pad * 2 - margin
            if "top" in pos:
                y = margin
            elif pos == "center-bottom":
                x = (img.width - tw - pad * 2) // 2
                y = img.height - th - pad * 2 - margin
            else:
                y = img.height - th - pad * 2 - margin
            x, y = max(0, x), max(0, y)

            if branding.chip:
                # شريط داكن شبه شفّاف + حدّ خفيف = يظهر على الصور الفاتحة والداكنة
                chip = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
                cdraw = ImageDraw.Draw(chip)
                cdraw.rounded_rectangle((0, 0, chip.width - 1, chip.height - 1),
                                        radius=max(6, size // 2), fill=(0, 0, 0, 165))
                cdraw.rounded_rectangle((0, 0, chip.width - 1, chip.height - 1),
                                        radius=max(6, size // 2),
                                        outline=(255, 255, 255, 90), width=max(1, size // 16))
                img.paste(chip, (x, y), chip)

            # ظلّ خفيف ثم النصّ الأبيض
            shadow = img.filter(ImageFilter.GaussianBlur(2))
            sdraw = ImageDraw.Draw(shadow, "RGBA")
            sdraw.text((x + pad + 1, y + pad + 1), text, font=font, fill=(0, 0, 0, 140))
            img = shadow
            draw = ImageDraw.Draw(img, "RGBA")
            draw.text((x + pad, y + pad), text, font=font,
                      fill=(255, 255, 255, int(255 * min(1.0, max(0.2, branding.opacity)))))

            out = output or str(Path(tempfile.gettempdir()) /
                               f"autonews_stamped_{os.getpid()}_{Path(image_path).stem[:20]}.jpg")
            img.save(out, "JPEG", quality=88, optimize=True)
            log.info("🖋 علّمت الصورة بـ %s (%s)", label, pos)
            return out
    except Exception as exc:  # noqa: BLE001 — التوقيع لا يُسقط النشر
        log.warning("تعذّر رسم العلامة على الصورة: %s", exc)
        return image_path


def apply_branding(text: str, branding: Branding, *, platform: str,
                   max_chars: int = 4096) -> str:
    """تركيب النصّ النهائي: توقيع مرئي + علامة خفيّة.

    تُنادى من `Post.render()` — لذلك تعمل تلقائياً على تيليجرام وفيسبوك وX
    وعلى المسار السريع وإعادة المحاولات، بلا تكرار الكود في كل ناشر.
    """
    from .utils import truncate  # noqa: PLC0415

    if not branding.ready:
        return text
    text = apply_footer(text, branding, platform=platform, max_chars=max_chars)
    # X حدّه 280 حرفاً — العلامة الخفيّة هناك تُحسب من الميزانية فنتخطّاها
    if branding.enabled and branding.hidden_mark and branding.handle and platform != "x":
        text = hidden_encode(text, branding.payload())
    return truncate(text, max_chars, ellipsis="…")


def brand_image(image: Any, channel: Any) -> Any:
    """تطبيق العلامة المائية على صورة الخبر (نقطة تكامل واحدة لكل المنصّات).

    تُنادى من `Publisher.publish` — لذلك تعمل على كل المسارات: التشغيل العادي،
    المسار السريع، إعادة المحاولات، والمسوّدات الموافَق عليها… بلا تكرار.
    """
    if image is None or not getattr(image, "ok", False):
        return image
    branding = Branding.from_channel(channel)
    if not branding.ready:
        return image
    stamped_path = stamp_image(image.path, branding)
    if stamped_path == image.path or not stamped_path:
        return image

    from .images import ArticleImage  # noqa: PLC0415 — منع الاستيراد الدائري

    old_path = image.path
    try:
        os.unlink(old_path)           # الأصل لم يبقَ له مستخدم
    except OSError:
        pass
    return ArticleImage(path=stamped_path, url=image.url, alt=image.alt, mime="image/jpeg")


__all__ = [
    "Branding", "DEFAULT_POSITION", "POSITIONS", "brand_image",
    "apply_footer", "apply_branding", "hidden_encode", "hidden_decode",
    "prove_ownership", "stamp_image",
]
