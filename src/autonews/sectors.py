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

from .utils import normalize_arabic

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
    priority: int = 0                        # عند التعادل: الأعلى يفوز (الأخصّ)


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
        for kw in profile.keywords:
            key = normalize_arabic(kw).lower()
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


def detect(name: str, username: str = "") -> SectorProfile | None:
    """القطاع الأرجح لاسم قناة، أو None إن لم تتّضح أي دلالة.

    عند التعادل نرجّح الأعلى `priority` — أي الأخصّ («أنمي» تتقدّم على «أخبار»).
    """
    scores = score(name, username)
    if not scores:
        return None
    best = max(scores.items(), key=lambda kv: (kv[1], SECTORS[kv[0]].priority))
    key, points = best
    if points < 3:                     # دلالة ضعيفة جداً = لا نخمّن
        return None
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
        return ["*"], "لا توجد مصادر معرّفة"

    if not sector.tags:
        # قطاع عام = قناة «الرصاصة الفضية»: كل المصادر العربية، لأنها قناة الأخبار
        # التي يجب أن تعرف كل شيء أولاً. الفصل عن القطاعات يحدث بالوسوم
        # (`exclude_tags: [أنمي]`) لا بحجب المصادر — وإلا صمتت القناة الرئيسية.
        ordered = sorted(items, key=lambda src: "ar" not in _source_langs(src))
        return ([str(src["id"]) for src in ordered],
                f"{len(ordered)} مصدراً (كل الأخبار)")

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
        return ["*"], "لا مصدر بهذه الوسوم — سأنشر كل شيء"

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


def apply_to_channel(channel_id: str, name: str, *, config_dir: str = "config",
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

    profile = detect(name)
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


__all__ = ["SectorProfile", "SECTORS", "clean_name", "score", "detect",
           "detect_many", "sources_for", "summary", "apply_to_channel"]
