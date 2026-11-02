"""🧠 التعرّف على قطاع القناة من اسمها — ثم ضبط الوسوم والمصادر تلقائياً.

الفكرة: المستخدم يربط قناة اسمها «أخبار الوتين» أو «عالم الأنمي والمانغا»،
فيعرف النظام القطاع ويضبط الوسوم والمصادر والأسلوب بلا أي إعداد يدوي.

القاعدة الأولى: **لا نحجب الأخبار أبداً**. إن لم نتعارف على القطاع نترك
المصادر `["*"]` فيستمرّ النشر — التخصيص تحسين لا شرط.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .utils import get_logger, normalize_arabic

log = get_logger("autonews.sectors")

# --------------------------------------------------------------------------- #
#  جدول القطاعات
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SectorProfile:
    """ملفّ تعريف قطاع: كلمات دالّة + وسوم المصادر + أسلوب الكتابة."""

    key: str
    label: str
    icon: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]                    # وسوم المصادر التي تخدم هذا القطاع
    hashtags: tuple[str, ...] = ()
    style: str = ""
    lang: str = "ar"
    priority: int = 0
    # استعلامات بحث جاهزة — تُستخدم لتوليد مصادر Google News للقطاع تلقائياً
    queries: tuple[str, ...] = ()                        # عند التعادل: الأعلى يفوز (الأخصّ)


_STYLE_DEFAULT = (
    "أسلوب متخصّص واضح: جملة واحدة قوية، بلا مقدمات ولا حشو، "
    "ابدأ بالمعلومة، واذكر ما يهمّ القارئ عملياً."
)

SECTORS: dict[str, SectorProfile] = {}


def _add(profile: SectorProfile) -> None:
    SECTORS[profile.key] = profile


_add(SectorProfile(
    key="anime", label="أنمي ومانغا", icon="🎌",
    keywords=("انمي", "أنمي", "مانجا", "مانغا", "اوتاكو", "أوتاكو", "شونين",
              "سينين", "انيمي", "كرتون", "خيال_ياباني", "anime", "manga", "otaku"),
    tags=("أنمي", "مانغا"),
    hashtags=("#أنمي", "#مانغا"),
    style=("أسلوب أوتاكو متحمّس لكن دقيق: ابدأ باسم العمل والمصدر، "
           "واذكر الموعد أو الحلقة، ونبّه على الحرق (Spoiler) إن وُجد. "
           "لا تترجم أسماء الأعلام، واستخدم مصطلحات الأنمي المتعارف عليها."),
    priority=30,
))
_add(SectorProfile(
    key="gaming", label="ألعاب وقيمنق", icon="🎮",
    keywords=("العاب", "ألعاب", "قيمنق", "جيمنج", "بلايستيشن", "اكس بوكس",
              "اكسبوكس", "ستيم", "نينتندو", "gaming", "game", "gamer", "playstation", "xbox"),
    tags=("ألعاب", "قيمنق"),
    hashtags=("#ألعاب", "#قيمنق"),
    style=("أسلوب لاعب محترف: اسم اللعبة أولاً، ثم المنصّات المتوفّرة، "
           "ثم الخبر. استخدم مصطلحات اللاعبين بلا مبالغة."),
    priority=25,
))
_add(SectorProfile(
    key="sports", label="رياضة", icon="⚽",
    keywords=("رياضة", "رياضه", "رياضي", "كرة", "كره", "مباراة", "مباريات",
              "دوري", "ملعب", "منتخب", "هدف", "سباق", "اولمبي", "sport", "sports",
              "football", "match", "league"),
    tags=("رياضة", "كرة"),
    hashtags=("#رياضة", "#كرة_القدم"),
    style=("أسلوب تغطية رياضية: النتيجة/الحدث أولاً ثم التفاصيل، "
           "اذكر الفريقين والمسابقة، واقتبس تصريحاً واحداً إن وُجد."),
    priority=20,
))
_add(SectorProfile(
    key="tech", label="تقنية", icon="💻",
    keywords=("تقنية", "تقنيه", "تكنولوجيا", "تك", "موبايل", "هاتف", "هواتف",
              "حاسوب", "كمبيوتر", "برمجة", "تطبيقات", "ذكاء_اصطناعي", "اندرويد",
              "ابل", "ios", "ويندوز", "تقني", "tech", "technology", "software",
              "android", "apple", "microsoft"),
    tags=("تقنية", "علوم", "اتصالات"),
    hashtags=("#تقنية", "#تكنولوجيا"),
    style=("أسلوب تقني مبسّط: ما الجديد؟ لماذا يهمّ المستخدم؟ "
           "تجنّب المصطلحات بلا شرح، واذكر السعر والتوفّر إن ذُكرا."),
    priority=20,
))
_add(SectorProfile(
    key="economy", label="اقتصاد وأسواق", icon="💵",
    keywords=("اقتصاد", "اقتصادي", "دولار", "اسعار", "سعر", "بورصة", "سوق",
              "اسواق", "عملة", "ذهب", "نفط", "استثمار", "استيراد", "تصدير",
              "economy", "market", "stock", "oil", "price"),
    tags=("اقتصاد", "دولار", "أسعار"),
    hashtags=("#اقتصاد", "#أسعار"),
    style=("أسلوب تحليلي موجز: الرقم أولاً، ثم السبب، ثم الأثر على المواطن. "
           "لا نصائح استثمارية."),
    priority=20,
))
_add(SectorProfile(
    key="crypto", label="عملات رقمية", icon="🪙",
    keywords=("عملات_رقمية", "عملة_رقمية", "بيتكوين", "كريبتو", "كريبتو", "اثيريوم",
              "بلوكشين", "تعدين", "crypto", "bitcoin", "btc", "ethereum", "blockchain"),
    tags=("اقتصاد", "عملات رقمية"),
    hashtags=("#كريبتو", "#بيتكوين"),
    style=("أسلوب مالي حذر: السعر والتغيّر أولاً، ثم السبب. "
           "أضف دائماً سطراً أن هذا ليس نصيحة استثمارية."),
    priority=28,
))
_add(SectorProfile(
    key="health", label="صحة وطب", icon="🩺",
    keywords=("صحة", "صحه", "طبي", "طبية", "طب", "مستشفى", "مستشفيات", "دواء",
              "ادوية", "لقاح", "وباء", "فيروس", "health", "medical", "vaccine"),
    tags=("صحة", "طب", "علوم"),
    hashtags=("#صحة", "#طب"),
    style=("أسلوب صحّي موثوق: المعلومة أولاً بلا تهويل، واذكر المصدر الرسمي. "
           "لا تشخيص ولا وصفات."),
    priority=22,
))
_add(SectorProfile(
    key="security", label="أمن ودفاع", icon="🛡",
    keywords=("امن", "أمن", "امني", "عسكري", "عسكرية", "دفاع", "جيش", "حرب",
              "غارات", "قصف", "شرطة", "اعتقال", "security", "military", "defense"),
    tags=("أمن", "عراق"),
    hashtags=("#أمن", "#العراق"),
    style=("أسلوب أمني رصين: ما حدث وأين ومتى، بلا تفاصيل أمنية حساسة. "
           "توخَّ الدقّة وتجنّب الأرقام غير المؤكّدة."),
    priority=22,
))
_add(SectorProfile(
    key="politics", label="سياسة", icon="🏛",
    keywords=("سياسة", "سياسي", "سياسيه", "برلمان", "حكومة", "وزير", "وزارة",
              "انتخابات", "رئيس", "قرار", "دبلوماسي", "politics", "government", "election"),
    tags=("سياسة", "قرارات", "رسمي"),
    hashtags=("#سياسة", "#العراق"),
    style=("أسلوب سياسي محايد: القرار ونصّه أولاً، ثم الجهة، ثم الأثر المتوقّع. "
           "بلا تحليل حزبي."),
    priority=18,
))
_add(SectorProfile(
    key="culture", label="ثقافة وفن", icon="🎭",
    keywords=("ثقافة", "ثقافي", "كتاب", "كتب", "فن", "فني", "مسرح", "شعر",
              "مهرجان", "سينما", "فيلم", "افلام", "culture", "art", "cinema", "book"),
    tags=("ثقافة",),
    hashtags=("#ثقافة", "#فن"),
    style=("أسلوب ثقافي دافئ: اسم العمل/الكاتب أولاً، ثم ما يميّزه، "
           "واقتباس قصير إن وُجد."),
    priority=15,
))
_add(SectorProfile(
    key="weather", label="طقس", icon="🌦",
    keywords=("طقس", "جو", "حرارة", "امطار", "مطر", "ثلوج", "رياح", "عاصفة",
              "weather", "rain", "storm"),
    tags=("خدمات", "عطلة"),
    hashtags=("#طقس", "#العراق"),
    style=("أسلوب خدمي: الحالة الآن + المتوقّع + ما يفعله الناس عملياً. "
           "أرقام الحرارة ورطوبة واضحة."),
    priority=20,
))
_add(SectorProfile(
    key="services", label="خدمات ومحافظات", icon="🏙",
    keywords=("خدمات", "محافظات", "بغداد", "البصرة", "الموصل", "اربيل", "كهرباء",
              "ماء", "مجاري", "طرق", "ازدحام", "تعرفة", "services"),
    tags=("محافظات", "خدمات", "عطلة"),
    hashtags=("#خدمات", "#محافظات"),
    style=("أسلوب خدمي مباشر: المحافظة أولاً، ثم التفصيل، ثم ما يجب أن يعرفه المواطن."),
    priority=16,
))
_add(SectorProfile(
    key="education", label="تعليم", icon="🎓",
    keywords=("تعليم", "جامعة", "جامعات", "مدرسة", "مدارس", "طلاب", "دراسة",
              "امتحان", "امتحانات", "منحة", "بحث_علمي", "education", "university"),
    tags=("تعليم", "ثقافة"),
    hashtags=("#تعليم", "#جامعات"),
    style=("أسلوب إرشادي: القرار أو الموعد أولاً، ثم الفئة المعنيّة، ثم خطوة عملية."),
    priority=18,
))
_add(SectorProfile(
    key="news", label="أخبار عامة", icon="📰",
    keywords=("اخبار", "أخبار", "خبر", "عاجل", "وكالة", "نيوز", "اخبارية",
              "breaking", "news"),
    tags=(),                                  # تعني: كل المصادر ما عدا القطاعات الأخرى
    hashtags=("#أخبار", "#العراق"),
    style=("أسلوب وكالة أنباء سريعة: جملة افتتاحية قوية، لغة فصحى موجزة، "
           "تركيز على ما حدث ومتّى وأين، بلا مبالغات. مناسب للقراءة على الهاتف."),
    priority=10,
))

# كلمات لا تدلّ على شيء — نحذفها قبل المطابقة («قناة»، «الأخبار»… )
_NOISE = ("قناة", "قناه", "ال", "لـ", "الرسمية", "الرسمي", "بوت", "الرئيسية",
          "الرئيسي", "عالم", "عربية", "بالعربي", "الاولى", "الاول",
          "اليوم", "الان", "الآن", "الحياه", "الحياة", "لاند", "نيوز")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]")
_PUNCT_RE = re.compile(r"[^\w\u0600-\u06FF\s]+")


def clean_name(name: str) -> str:
    """تنظيف الاسم: إزالة الإيموجي والترقيم والتطويل، وتوحيد الحروف العربية."""
    text = _EMOJI_RE.sub(" ", name or "")
    text = text.replace("\u0640", "")               # تطويل
    text = _PUNCT_RE.sub(" ", text)
    text = normalize_arabic(text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


_AR_PREFIX = ("وال", "بال", "كال", "فال", "لل", "ال")


def _tokenize(text: str) -> list[str]:
    """كلمات الاسم بعد حذف الأدوات، مع مركّبات ثنائية и الجذوع للعربية."""
    words = [w for w in text.split() if w not in _NOISE and len(w) > 1]
    return words


def _stems(word: str) -> set[str]:
    """جذوع ممكنة للكلمة: بلا «ال» التعريف، وبلا ياء الملكية، إلخ.

    «الأنمي» → الأنمي / أنمي · «صحتك» → صحت / صح / صحة · «الدوري» → دور / دوري
    """
    base = normalize_arabic(word).lower()
    forms = {base}
    changed = True
    while changed:                       # إزالة تكرار «ال»
        changed = False
        for pre in _AR_PREFIX:
            if base.startswith(pre) and len(base) - len(pre) >= 3:
                base = base[len(pre):]
                forms.add(base)
                changed = True
                break
    extra = set()
    for form in forms:
        for suf in ("ات", "ين", "ون", "ها", "هم", "كم", "نا", "ي", "ه", "ك"):
            if form.endswith(suf) and len(form) - len(suf) >= 3:
                extra.add(form[: -len(suf)])
    forms |= extra
    # «صحة/صحت» و«الحياة/الحيات»: تاء مربوطة تُكتب هاءً في التطبيع — نوحّد الطرفين.
    variants = set()
    for form in forms:
        if len(form) >= 3:
            if form.endswith("ه"):
                variants.add(form[:-1] + "ت")
            elif form.endswith("ت"):
                variants.add(form[:-1] + "ه")
    return forms | variants


def score(name: str, username: str = "") -> dict[str, int]:
    """نتيجة كل قطاع لاسم قناة (0 = لا صلة)."""
    text = clean_name(f"{name} {username}")
    if not text:
        return {}
    words = _tokenize(text)
    stems: set[str] = set()
    for w in words:
        stems |= _stems(w)

    result: dict[str, int] = {}
    for profile in SECTORS.values():
        points = 0
        # 🐛 «انمي» و«أنمي» (وكذلك «اخبار» و«أخبار») تتطابقان بعد التطبيع؛ كانت كل صيغة
        #    تُحسب مرّتين فيتضاعف وزن القطاع الذي كتب مفرداته بالهمزتين («أخبار» = ٦)
        #    ويتغلّب على قطاع أخصّ بكلمة واحدة («المانغا» = ٣) ⇒ «أخبار المانغا» تصير أخباراً عامة.
        #    الحلّ: نعدّ كل كلمة مطبَّعة مرّة واحدة فقط.
        normalized = dict.fromkeys(normalize_arabic(kw).lower() for kw in profile.keywords)
        for key in normalized:
            if "_" in key:                          # مركّب: يطابق الجملة كلها
                if key.replace("_", " ") in text.replace("_", " "):
                    points += 4
            elif key in stems:                      # مطابقة الجذع = قوية
                points += 3
            elif len(key) >= 4 and any(key in st for st in stems):
                points += 2                         # داخل جذع أطول
        if points:
            result[profile.key] = points
    return result



def detect_custom_profile(name: str, description: str = "") -> SectorProfile | None:
    """ملف اختصاص مستقل للقنوات ذات المواضيع غير المدرجة في القطاعات العامة."""
    from hashlib import sha1
    clean = clean_name(f"{name} {description}")
    words = [w for w in clean.split() if w not in _NOISE and len(w) > 1]
    if not words:
        return None
    label = " ".join(words[:6])
    slug = sha1(label.encode("utf-8")).hexdigest()[:8]
    return SectorProfile(
        key=f"custom_{slug}", label=label, icon="🧭", keywords=tuple(words),
        tags=(f"custom_{slug}",),
        style="قناة متخصصة: انشر فقط ما يرتبط مباشرة بموضوع القناة، مع ترجمة عربية ورابط مصدر.",
        priority=5, queries=(f'"{label}" when:2d', f"{label} إعلان OR إصدار when:7d"),
    )

def detect(name: str, username: str = "") -> SectorProfile | None:
    """القطاع الأرجح لاسم قناة، أو None إن لم تتّضح أي دلالة.

    عند التعادل نرجّح الأعلى `priority` — أي الأخصّ («أنمي» تتقدّم على «أخبار»).
    """
    scores = score(name, username)
    if not scores:
        # اختصاص مخصّص لأي اسم جديد: لا نفرض قائمة قطاعات ثابتة.
        # نستخدم كلمات الاسم نفسها كاستعلام بحث، وننشئ وسماً خاصاً به
        # حتى لا تختلط مصادره ببقية القنوات.
        return detect_custom_profile(name, username)
    # «أخبار X» تعني X: القطاع الأخصّ يتقدّم دائماً على «الأخبار العامة» متى كانت له دلالة
    # كافية. القناة العامة تستقبل كل شيء أصلاً، فلا خسارة في اختيار الأخصّ، أما العكس
    # فيُسقط الأنمي/الرياضة في القناة العامة (وهو بالضبط ما فصلناه بـ exclude_tags).
    specialists = {k: v for k, v in scores.items() if k != "news" and v >= 3}
    if specialists:
        scores = specialists
    best = max(scores.items(), key=lambda kv: (kv[1], SECTORS[kv[0]].priority))
    key, points = best
    if points < 3:                     # دلالة ضعيفة جداً = لا نخمّن قطاعاً معروفاً
        return detect_custom_profile(name)
    return SECTORS[key]


def detect_many(names: Iterable[str]) -> list[SectorProfile]:
    """تعرّف على عدّة أسماء (يُستخدم لعرض الأزرار والاختبارات)."""
    found = (detect(n) for n in names)
    return [p for p in found if p is not None]


# --------------------------------------------------------------------------- #
#  ربط القطاع بالمصادر
# --------------------------------------------------------------------------- #
def _source_langs(src: dict) -> set[str]:
    """لغات المصدر المخمّنة: من حقل languages إن وُجد، وإلا من رابط Google News."""
    langs = {str(x).lower() for x in (src.get("languages") or [])}
    if langs:
        return langs
    url = str(src.get("url", ""))
    # ceid هو الفيصل: Google News يضع اللغة والبلد فيه معاً («IQ:ar» / «US:en»)
    if "ceid=" in url:
        ceid = url.split("ceid=", 1)[1][:6]
        return {"ar"} if ceid.endswith(":ar") else {"en"}
    if "hl=ar" in url:
        return {"ar"}
    if "hl=en" in url:
        return {"en"}
    return {"ar"}                      # الافتراض: عربي (كل المصادر الثابتة عربية)


def sources_for(sector: SectorProfile,
                sources: Iterable[dict]) -> tuple[list[str], str]:
    """المصادر المناسبة لقطاع → (قائمة المعرّفات، شرح مختصر).

    ثلاث قواعد تحكم الاختيار:
      ١) **الوسوم المطابقة أكثر = أفضل**: مصدر يحمل «تقنية + علوم» أفضل من
         مصدر يحمل «علوم» فقط (الأخير قد يكون زلازل).
      ٢) **اللغة تفضيل لا شرط**: نقدّم العربي، وإن قلّ عن مصدرين نضيف الإنجليزي
         (أخبار الأنمي العالمية مصدرها إنجليزي غالباً).
      ٣) **الأمان أوّلاً**: إن لم نجد شيئاً نرجع `["*"]` — لا قناة صامتة.
    """
    items = [s for s in sources if isinstance(s, dict) and s.get("id")]
    if not items:
        return (["*"], "لا توجد مصادر معرّفة") if not sector.tags else ([], "لا توجد مصادر للاختصاص بعد")

    if not sector.tags:
        # قطاع عام = قناة «الرصاصة الفضية»: كل المصادر.
        #
        #  ⚠️ نُرجع الرمز `*` لا **قائمة** بكل المصادر الحالية.
        #  الفرق جوهري: القائمة تُجمَّد في channels.yml وقت الإضافة، فأي مصدر
        #  جديد يُضاف لاحقاً (من البوت) لا يصل القناة العامة أبداً — وهذا حدث
        #  فعلاً: tg_main قُفل على ٣٩ مصدراً ولم يستقبل ما بعدها.
        #  الرمز `*` يُحلّ وقت التوجيه (`router`)، فيبقى «كل المصادر» صحيحاً دائماً.
        #
        #  الفصل عن القطاعات يحدث بالوسوم (`exclude_tags: [أنمي]`) لا بحجب
        #  المصادر — وإلا صمتت القناة الرئيسية.
        return ["*"], f"كل المصادر (الرمز *) · {len(items)} معرّفاً معرَّفاً الآن"

    want = {str(t) for t in sector.tags}
    scored: list[tuple[float, dict]] = []
    for src in items:
        tags = {str(t) for t in (src.get("tags") or [])}
        hit = want & tags
        if not hit:
            continue
        precision = len(hit) / max(len(tags), 1)
        scored.append((round(len(hit) * 2 + precision, 3), src))
    if not scored:
        return [], "لا يوجد مصدر مطابق للاختصاص"

    scored.sort(key=lambda pair: -pair[0])
    best = scored[0][0]
    chosen = [src for value, src in scored if value >= best * 0.6]

    ar = [src for src in chosen if "ar" in _source_langs(src)]
    if len(ar) >= 2:                          # عربي كافٍ: لا حاجة للإنجليزي
        chosen = ar
    else:                                     # نقدّم العربي ونُبقي الباقي
        chosen.sort(key=lambda src: "ar" not in _source_langs(src))

    return ([str(src["id"]) for src in chosen],
            f"{len(chosen)} مصدراً بهذه الوسوم")


def summary(sector: SectorProfile, sources: list[str], why: str) -> str:
    """رسالة عربية جاهزة للبوت/الطرفية."""
    shown = ", ".join(sources[:6]) + ("…" if len(sources) > 6 else "")
    if sources == ["*"]:
        shown = "كل المصادر"
    return (f"{sector.icon} عرفت القطاع: <b>{sector.label}</b>\n"
            f"• الوسوم: {', '.join(sector.tags) or '—'}\n"
            f"• المصادر: {shown} ({why})")


# --------------------------------------------------------------------------- #
#  التطبيق على ملف الإعدادات (يُستدعى من البوت تلقائياً)
# --------------------------------------------------------------------------- #
def _load_sources(config_dir: str) -> list[dict]:
    """قراءة المصادر من settings.yml بلا استيراد الحمّالة كلها."""
    from pathlib import Path

    import yaml

    path = Path(config_dir) / "settings.yml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [s for s in (data.get("sources") or []) if isinstance(s, dict)]


def wildcard_channels(config: dict) -> list[str]:
    """القنوات العامة (المفترض أنها «كل المصادر») — أي التي intersect_tags فارغة."""
    out = []
    for ch in (config.get("channels") or []):
        if not isinstance(ch, dict):
            continue
        if not (ch.get("tags") or []) and not (ch.get("keywords") or []):
            out.append(str(ch.get("id")))
    return out


def freeze_sources(channel_id: str, sector_key: str, *, config_dir: str = "config") -> bool:
    """هل هذه القناة **مقصودة** بمصادر مجمَّدة؟

    القطاع العام وحده هو الذي يجب أن يبقى `["*"]`. أي قناة قطاعها `news` أو
    بلا اختصاص صريح تُترك كما هي — لا نلمس إعداداً مقصوداً.
    """
    return sector_key not in ("", "news", "general")


def normalize_config(config_dir: str = "config", *, dry_run: bool = False) -> list[str]:
    """يطهّر `channels.yml`: أي قناة عامة مصادرها **قائمة** تُقلَب إلى `["*"]`.

    الفرق الذي يمنع الخطأ: القائمة المجمَّدة تمنع المصادر الجديدة من الوصول،
    والرمز `*` يُحلّ وقت التوجيه فيبقى «كل المصادر» صحيحاً للأبد.

    القنوات المخصّصة (لها وسوم/كلمات) لا تُلمَس — قواعد المشروع تحكمها.
    """
    from pathlib import Path

    import yaml

    from .editor import update_channel_sources

    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fixed: list[str] = []
    for ch in (data.get("channels") or []):
        if not isinstance(ch, dict):
            continue
        cid = str(ch.get("id") or "")
        if not cid:
            continue
        #  «مخصّصة» = لها وسوم أو كلمات توجيه. أمّا `exclude_tags` فهي **أداة
        #  الفصل** نفسها (القناة العامة تستثني الأنمي) — لا دليل على التخصّص.
        #  لو اعتبرناها كذلك لبقي خلل القناة الرئيسية بلا إصلاح.
        if ch.get("tags") or ch.get("keywords"):
            continue
        srcs = ch.get("sources") or []
        if srcs == ["*"]:
            continue
        fixed.append(cid)
        if not dry_run:
            update_channel_sources(cid, ["*"], config_dir=config_dir)
    return fixed


def apply_to_channel(channel_id: str, name: str, *, description: str = "", config_dir: str = "config",
                     override: bool = False) -> tuple[bool, str]:
    """يضبط وسوم القناة ومصادرها من اسمها. لا يفشل أبداً بصمت.

    `override=False` (الافتراضي): لا يلمس قناة لها مصادر مضبوطة مسبقاً —
    حتى لا نفسد إعداداً يدوياً مقصوداً.
    """
    from .editor import _verify_yaml, _write_atomic, update_channel_sources, update_channel_tags
    from pathlib import Path

    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return False, f"لم أجد {path}"

    profile = detect(name, description)
    if profile is None:
        return False, ("🤷 لم أتعرّف على القطاع من الاسم — ستبقى القناة تستقبل "
                       "كل الأخبار (وهذا آمن). يمكنك ضبط الوسوم يدوياً.")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("- id:")
                  and ln.split(":", 1)[1].strip() == channel_id), None)
    if start is None:
        return False, f"القناة «{channel_id}» غير موجودة"
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("- id:")), len(lines))
    block = "".join(lines[start:end])

    already = re.search(r"^\s+sources:\s*\[(.*?)\]", block, re.M)
    current = (already.group(1).strip() if already else "")
    if not override and current not in ('"*"', "*", ""):
        return False, "ℹ️ للقناة مصادر مضبوطة مسبقاً — تركتها كما هي (استخدم override لتغييرها)."

    picked, why = sources_for(profile, _load_sources(config_dir))
    res_s = update_channel_sources(channel_id, picked, config_dir=config_dir)
    res_t = update_channel_tags(channel_id, add=list(profile.tags),
                                config_dir=config_dir) if profile.tags else None

    if not res_s.ok or (res_t is not None and not res_t.ok):
        return False, f"تعذّر الحفظ: {res_s.message if not res_s.ok else res_t.message}"

    path2 = Path(config_dir) / "channels.yml"
    problem = _verify_yaml(path2.read_text(encoding="utf-8"), key="channels")
    if problem:
        return False, problem
    return True, summary(profile, picked, why)


__all__ = ["SectorProfile", "SECTORS", "clean_name", "score", "detect", "detect_custom_profile",
           "detect_many", "sources_for", "summary", "apply_to_channel"]


# --------------------------------------------------------------------------- #
#  التصنيف اليدوي — «فعّل/عطّل» و «غيّر نوع القناة»
# --------------------------------------------------------------------------- #
def sector_of(channel_id: str, *, config_dir: str = "config") -> SectorProfile | None:
    """القطاع المسجَّل للقناة.

    المصدر الأول: وسم القناة المطابق لوسوم قطاع (الوسم يُكتب من `apply_to_channel`).
    لا نضيف حقلاً جديداً في YAML — الوسوم موجودة أصلاً وتعمل في التوزيع.
    """
    from pathlib import Path

    import yaml

    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    channel = next((c for c in (data.get("channels") or [])
                    if str(c.get("id")) == channel_id), None)
    if not channel:
        return None
    tags = {str(t).strip().lower() for t in (channel.get("tags") or [])}
    best: tuple[int, SectorProfile] | None = None
    for profile in SECTORS.values():
        if not profile.tags:
            continue
        hits = len(tags & {t.strip().lower() for t in profile.tags})
        if hits and (best is None or hits > best[0]):
            best = (hits, profile)
    if best:
        return best[1]
    # لا وسم مطابق: جرّب الاسم
    return detect(str(channel.get("name") or ""))


def set_sector(channel_id: str, sector_key: str, *,
               config_dir: str = "config") -> tuple[bool, str]:
    """تغيير قطاع قناة يدوياً: يبدّل وسومها ومصادرها معاً (متّسقان دائماً)."""
    from .editor import update_channel_sources, update_channel_tags

    profile = SECTORS.get(sector_key)
    if profile is None:
        return False, f"❌ قطاع غير معروف: {sector_key}"

    current = sector_of(channel_id, config_dir=config_dir)
    # نحذف وسوم القطاع القديم أولاً حتى لا تختلط الاختصاصات
    remove = list(current.tags) if current and current.key != profile.key else []
    res_t = update_channel_tags(channel_id, add=list(profile.tags), remove=remove,
                                config_dir=config_dir)
    if not res_t.ok:
        return False, res_t.message

    picked, why = sources_for(profile, _load_sources(config_dir))
    res_s = update_channel_sources(channel_id, picked, config_dir=config_dir)
    if not res_s.ok:
        return False, res_s.message

    log_line = f"{profile.icon} صار اختصاص القناة: <b>{profile.label}</b>"
    details = [log_line, f"• الوسوم: {', '.join(profile.tags) or '—'}",
               f"• المصادر: {len(picked) if picked != ['*'] else 'كل'} ({why})"]
    if current and current.key != profile.key:
        details.insert(1, f"• كان: {current.icon} {current.label}")
    return True, "\n".join(details)


QUERIES: dict[str, tuple[str, ...]] = {
    "anime": ("أنمي جديد when:2d", "مانغا إصدار when:3d"),
    "gaming": ("لعبة جديدة إطلاق when:3d", "تحديث لعبة when:2d"),
    "sports": ("الدوري العراقي when:1d", "مباراة اليوم when:1d"),
    "tech": ("ذكاء اصطناعي تقنية when:2d", "هاتف جديد إعلان when:3d"),
    "economy": ("الدولار العراق أسعار when:1d", "الذهب النفط when:1d"),
    "crypto": ("بيتكوين عملات رقمية when:2d",),
    "health": ("صحة دراسة طبية when:2d",),
    "security": ("أمن العراق when:1d",),
    "politics": ("البرلمان العراق قرار when:1d",),
    "culture": ("ثقافة فن مهرجان when:3d",),
    "weather": ("طقس العراق when:1d",),
    "services": ("خدمات محافظات العراق when:1d",),
    "education": ("جامعات تعليم منح when:3d",),
    "news": ("أخبار العراق when:1d",),
}


def _attach_queries() -> None:
    """ربط الاستعلامات بالقطاعات بعد تعريفها كلها."""
    for key, queries in QUERIES.items():
        if key in SECTORS:
            SECTORS[key].queries = tuple(queries)


_attach_queries()


def gnews_url(query: str, language: str = "ar") -> str:
    """Google News RSS بلغة البحث المطلوبة؛ المحتوى يُترجم لاحقاً للعربية."""
    from urllib.parse import quote

    locales = {
        "ar": ("ar", "IQ", "IQ:ar"),
        "en": ("en-US", "US", "US:en"),
        "ja": ("ja", "JP", "JP:ja"),
    }
    hl, gl, ceid = locales.get(language, locales["en"])
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"


def suggest_sources(sector: SectorProfile, *,
                    existing: Iterable[dict] | None = None) -> list[dict]:
    """توليد مصادر بحث متعددة اللغات مع وسم خاص يمنع تسرّبها لقنوات أخرى."""
    have = {str(item.get("id")) for item in (existing or [])}
    out: list[dict] = []
    languages = ["ar", "en"]
    if any(token in sector.key for token in ("anime", "manga", "manhwa")) or any(
            token in clean_name(sector.label) for token in ("انمي", "مانغا", "مانهوا", "مانجا")):
        languages.append("ja")
    tags = list(sector.tags) or ["أخبار"]
    for query_index, query in enumerate(sector.queries, 1):
        for language in languages:
            sid = f"gnews_{sector.key}_{query_index}_{language}"
            if sid in have:
                continue
            out.append({
                "id": sid,
                "name": f"{sector.label} — {language.upper()}",
                "url": gnews_url(query, language),
                "type": "rss",
                "enabled": True,
                "languages": [language],
                "tags": tags,
                "max_items": 8,
            })
    return out


def ensure_sources(sector: SectorProfile, *, config_dir: str = "config") -> str:
    """يضيف مصادر مقترحة للقطاع إن لم يكن له أي مصدر. يعيد ملخّصاً.

    القاعدة: **لا نصمت**. قناة بلا مصادر لا تنشر — وهذا أسوأ من نشر عام.
    """
    from .editor import add_source  # noqa: F401  (نستخدم الإلحاق المباشر أدناه)
    from .utils import get_logger as _gl

    sources = _load_sources(config_dir)
    picked, _ = sources_for(sector, sources)
    if picked and picked != ["*"]:
        return ""                              # له مصادر فعلية

    new_items = suggest_sources(sector, existing=sources)
    if not new_items:
        return f"⚠️ لا توجد مصادر تلقائية متاحة لـ{sector.label}؛ لن تُرسل أخبار غير مرتبطة."

    from pathlib import Path

    import yaml

    path = Path(config_dir) / "settings.yml"
    text = path.read_text(encoding="utf-8")
    block = ""
    for item in new_items:
        tags = ", ".join(f'"{t}"' for t in item["tags"])
        block += (
            f"\n  - id: {item['id']}\n"
            f'    name: "{item["name"]}"\n'
            f'    url: "{item["url"]}"\n'
            f"    type: rss\n    enabled: true\n"
            f"    tags: [{tags}]\n    max_items: {item['max_items']}\n"
        )
    new_text = text.rstrip() + "\n" + block
    data = yaml.safe_load(new_text) or {}
    if not isinstance(data.get("sources"), list):
        _gl("autonews.sectors").warning("تعذّر إضافة مصادر — YAML غير صالح")
        return ""
    path.write_text(new_text, encoding="utf-8")
    return f"➕ أضفت {len(new_items)} مصدراً جديداً لـ{sector.label}"


def catalog(config_dir: str = "config") -> list[dict]:
    """كل القطاعات للعرض في البوت (بعدد مصادرها)."""
    sources = _load_sources(config_dir)
    out = []
    for profile in SECTORS.values():
        picked, _ = sources_for(profile, sources)
        out.append({"key": profile.key, "label": profile.label, "icon": profile.icon,
                    "tags": list(profile.tags),
                    "sources": len(picked) if picked != ["*"] else "كل"})
    return out


__all__ += ["sector_of", "set_sector", "catalog",
            "wildcard_channels", "freeze_sources", "normalize_config"]
