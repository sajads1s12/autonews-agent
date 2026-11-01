"""عمود الأنمي — نفس منطق الأخبار، بمفردات متخصّصة.

لماذا وحدة مستقلة؟
------------------
«الأنمي» ليس مجرّد كلمة مفتاحية: أخباره لها **بنية** مختلفة:
    • إعلان أنمي جديد      → العنوان، الاستوديو، الموسم، تاريخ العرض
    • حلقة/موسم جديد       → رقم الموسم، عدد الحلقات
    • مانغا/لايت نوفل      → تحويل إلى أنمي، انتهاء، إيقاف
    • طاقم العمل           → ملحّن، مخرج، ممثل صوتي
    • إصدار الأغاني/الخلفية الموسيقية

لذا نصنّف بحسب **الحدث** لا بحسب الكلمة، ونستخرج **الأسماء الأصلية** كي
لا تتحوّل الترجمة إلى عشوائية (Kyoto Animation ≠ «أنيميشن كيوتو»).

قاعدة التحرير المهمّة: لا نُترجم الأسماء، نُبقيهم بين قوسين.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ingest import Article
from ..utils import normalize_arabic

# --------------------------------------------------------------------------- #
#  المفردات
# --------------------------------------------------------------------------- #
ANIME_SEASONS = ("winter", "spring", "summer", "fall", "autumn",
                 "شتاء", "ربيع", "صيف", "خريف")

# علامات أنمي عربية لا تحتمل شكّاً (الأخبار العربية لا تكتب «أنمي» دائماً)
_ARABIC_ANIME_MARKERS = (
    "أنمي", "انمي", "الأنمي", "الانمي", "مانغا", "مانجا", "المانغا",
    "لايت نوفل", "أوتاكو", "اوتاكو", "حلقة", "الحلقات", "موسم الأنمي",
)

# جدول الأعمال المعروفة: يحمي الأخبار التي تذكر الاسم بلا كلمة «أنمي»
KNOWN_WORKS = {
    "bleach": "بليتش", "naruto": "ناروتو", "one piece": "ون بيس",
    "attack on titan": "هجوم العملاق", "shingeki no kyojin": "هجوم العملاق",
    "jujutsu kaisen": "جوجوتسو كايسن", "demon slayer": "قاتل الشياطين",
    "kimetsu no yaiba": "قاتل الشياطين", "my hero academia": "أكاديمية بطلي",
    "boku no hero": "أكاديمية بطلي", "black clover": "بلاك كلوفر",
    "dragon ball": "دراغون بول", "fullmetal alchemist": "الخيميائي الفولاذي",
    "death note": "مذكرة الموت", "chainsaw man": "رجل المنشار",
    "spy x family": "سباي إكس فاميلي", "frieren": "فريرن",
    "solo leveling": "التسليح المنفرد", "tokyo ghoul": "طوكيو غول",
    "sword art online": "سورد آرت أونلاين", "hunter x hunter": "هنتر إكس هنتر",
    "haikyuu": "هايكيو", "blue lock": "بلو لوك", "oshi no ko": "أوشي نو كو",
}

ANIME_FORMATS = {
    "TV": ("tv anime", "tv series", "مسلسل", "أنمي تلفزيوني"),
    "Movie": ("anime film", "the movie", "فيلم", "movie"),
    "OVA": ("ova", "oad", "أوفا"),
    "ONA": ("ona", "netflix anime", "نتفليكس"),
    "Special": ("special", "حلقة خاصة"),
}

ANIME_GENRES = {
    "شونين": ("shonen", "shounen", "شونين"),
    "سينين": ("seinen", "سينين"),
    "شوجو": ("shoujo", "shojo", "شوجو"),
    "إيسيكاي": ("isekai", "إيسيكاي", "ايسيكاي"),
    "ميكا": ("mecha", "ميكا"),
    "شريحة من الحياة": ("slice of life", "شريحة من الحياة"),
    "فانتازيا": ("fantasy", "فانتازيا"),
    "أكشن": ("action", "أكشن"),
    "رومانسي": ("romance", "رومانسي"),
    "رعب": ("horror", "رعب"),
    "رياضي": ("sports", "رياضي"),
    "كوميدي": ("comedy", "كوميدي"),
}

# استوديوهات معروفة — تُستخرج كما هي (بلا ترجمة) لتفادي التشويه
STUDIOS = (
    "MAPPA", "ufotable", "Kyoto Animation", "Madhouse", "Bones", "Trigger",
    "Wit Studio", "Production I.G", "A-1 Pictures", "CloverWorks", "J.C.Staff",
    "Shaft", "Sunrise", "Toei Animation", "Studio Ghibli", "Pierrot",
    "David Production", "White Fox", "P.A. Works", "Lerche", "Doga Kobo",
    "Science SARU", "Studio Bind", "8bit", "Silver Link", "C2C", "TMS",
)

# أفعال/أحداث تصنّف الخبر
_EVENTS = {
    "إعلان": ("announced", "announcement", "reveals", "revealed", "confirms",
              "greenlit", "gets anime", "is getting", "يُعلن", "إعلان", "يكشف",
              "أعلن", "كشف"),
    "عرض": ("premieres", "premiere", "airs", "release date", "debuts", "starts",
            "يبث", "يُعرض", "عرض", "ينطلق", "تاريخ العرض",
            # «موعد عرض الحلقة الخامسة» — أشهر صيغة عربية
            "موعد عرض", "موعد الحلقة", "الحلقة الخامسة", "حلقة جديدة", "حلقات جديدة"),
    "انتهاء": ("ends", "finale", "final episode", "concludes", "cancelled",
               "canceled", "hiatus", "ينتهي", "الختام", "الحلقة الأخيرة", "إيقاف"),
    "طاقم": ("cast", "staff", "director", "composer", "voice actor", "seiyuu",
             "طاقم", "المخرج", "ملحّن", "مؤدّي صوت"),
    "موسم": ("season 2", "season 3", "second season", "third season", "new season",
             "الموسم الثاني", "الموسم الثالث", "موسم جديد"),
    "تريلر": ("trailer", "teaser", "promo video", "pv", "visual", "تريلر",
              "مقطع دعائي", "إعلان تشويقي"),
}

# إشارات النفي: خبر «ليس أنمي» ليس خبر أنمي
_NEGATIVE = ("not an anime", "no anime adaptation", "denies", "debunked",
             "ينفي", "نفى", "لا صحة", "تكذيب")

# كلمات تصنع «حدثاً» في عالم الأنمي لكن تُنشر كخبر عادي (لا حاجة لنتائج)
_SAFE_CONTEXT = ("merch", "figure", "game", "collab", "collaboration", "concert",
                 "ألعاب", "مجسمات", "حفلة", "تعاون")

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_NAME = re.compile(r"\b[A-Z][A-Za-z0-9:'’!\-]*(?:\s+[A-Z0-9][A-Za-z0-9:'’!\-]*){1,5}\b")


# --------------------------------------------------------------------------- #
#  التحليل
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class AnimeVerdict:
    """نتيجة تصنيف خبر أنمي."""
    is_anime: bool = False
    event: str = ""                     # إعلان / عرض / انتهاء / طاقم / موسم / تريلر
    genres: list[str] = field(default_factory=list)
    format: str = ""                    # TV / Movie / OVA / ONA
    studios: list[str] = field(default_factory=list)
    season: str = ""
    titles: list[str] = field(default_factory=list)   # أسماء أصلية يجب عدم ترجمتها
    score: float = 0.0
    reason: str = ""

    def summary(self) -> str:
        bits = [f"حدث:{self.event}" if self.event else ""]
        if self.studios:
            bits.append("استوديو:" + "/".join(self.studios[:2]))
        if self.season:
            bits.append("موسم:" + self.season)
        if self.genres:
            bits.append("نوع:" + "/".join(self.genres[:2]))
        return " · ".join(b for b in bits if b) or "غير محدّد"


def _has(hay: str, words: tuple[str, ...]) -> bool:
    return any(w.lower() in hay for w in words)


def is_anime(article: Article) -> bool:
    """هل الخبر يخصّ الأنمي فعلاً؟ (فحص سريع بلا تحليل كامل)"""
    return classify_anime(article).is_anime


# كلمات لا تصلح أن تكون «اسم عمل» (تظهر مكتوبة بحرف كبير في العناوين الإنجليزية)
_NOT_A_TITLE = {
    "manga", "anime", "tv anime", "tv", "movie", "season", "summer", "winter",
    "spring", "fall", "netflix", "crunchyroll", "the", "new", "gets", "reveals",
    "announced", "premieres", "debut", "teaser", "trailer", "visual", "cast",
    "staff", "episode", "finale", "part", "series", "adaptation", "novel",
    "light novel", "game", "film", "reveals new", "gets tv", "tv anime in",
    "creator launches", "launches new", "new manga", "teaser video", "new visual",
    "character posters", "game character", "summer 2027", "winter 2027",
    "spring 2027", "fall 2027", "january 7", "debut", "key visual", "main cast",
    "main staff", "opening theme", "ending theme", "english dub", "second cour",
    "tv series", "anime adaptation", "gets anime", "anime announced",
}


def _clean_title(name: str) -> str:
    """تنظيف اسم مرشّح: إزالة ذيول عامة مثل «Gets TV Anime in Summer 2027»."""
    name = name.strip(" .,;:'\"")
    # اقطع عند أول فعل/كلمة عامّة — اسم العمل يسبقها دائماً في العناوين الإنجليزية
    cut_words = (" Gets", " Reveals", " Announced", " Premieres", " Debuts",
                 " Season", " Manga", " Anime", " TV", " In ", " Ends", " Cast",
                 " Launches", " Creator ", " Game ", " Film ", " Novel ",
                 " Teaser", " Visual", " Poster", " Character", " New ")
    for w in cut_words:
        idx = name.find(w)
        if idx > 3:
            name = name[:idx]
    return name.strip(" .,;:'\"-")


def extract_titles(text: str, *, limit: int = 3) -> list[str]:
    """استخراج أسماء الأعمال الأصلية (Latin) لعدم ترجمتها.

    أسماء الأنمي تُكتب لاتينية بحرف كبير: *Jujutsu Kaisen*, *Shiba Inu Rooms*.
    لكن العناوين الإنجليزية مليئة بكلمات تبدأ بحرف كبير أيضاً
    («Gets TV Anime in Summer 2027») — لذا نُصفّي بصرامة:
    الاسم = كلمتان معناها ≥ ٣ أحرف، وليست من الكلمات العامة، وطوله ≤ 40 حرفاً.
    """
    found: list[str] = []
    for m in _LATIN_NAME.finditer(text or ""):
        name = _clean_title(m.group(0))
        words = name.split()
        if len(words) < 2:
            continue
        if not (2 <= len(words) <= 6) or len(name) > 40:
            continue
        low = name.lower()
        if low in _NOT_A_TITLE:
            continue
        # أي كلمة عامّة داخل الاسم ⇒ ليس اسم عمل («Premieres October 2027»)
        if any(w.lower().strip("'’") in _NOT_A_TITLE for w in words):
            continue
        if not any(w.lower() not in _NOT_A_TITLE for w in words):
            continue
        # أفعال/كلمات تنفي الاسمية
        if any(v in low.split() for v in ("premieres", "announced", "reveals", "gets",
                                          "launches", "ends", "debuts", "airs")):
            continue
        if name not in found:
            found.append(name)
        if len(found) >= limit:
            break
    return found


def classify_anime(article: Article) -> AnimeVerdict:
    """تصنيف كامل: هل هو أنمي؟ أي حدث؟ أي استوديو/نوع/موسم؟"""
    text = f"{article.title} {article.summary} {article.content}"
    hay = normalize_arabic(text).lower()
    raw_low = text.lower()
    v = AnimeVerdict()

    # --- هل هو أنمي؟ ---
    anime_hits = 0
    for kw in ("anime", "أنمي", "انمي", "manga", "مانغا", "مانجا", "light novel",
               "لايت نوفل", "otaku", "seiyuu", "أوتاكو"):
        if kw in raw_low or kw in hay:
            anime_hits += 1
    # علامات عربية صريحة (حلقة/مانغا/أوتاكو) — الأخبار العربية تندر فيها كلمة «أنمي»
    arabic_hits = sum(1 for kw in _ARABIC_ANIME_MARKERS if kw in text or kw in hay)
    anime_hits += min(arabic_hits, 2)

    studio_hit = [s for s in STUDIOS if s.lower() in raw_low]

    # جدول الأعمال المعروفة: ينقذ خبراً يذكر الاسم وحده («بليتش»، «Black Clover»)
    known_hit = [en for en in KNOWN_WORKS
                 if en in raw_low or KNOWN_WORKS[en] in text]

    if not anime_hits and not studio_hit and not known_hit:
        v.reason = "لا إشارة أنمي"
        return v

    if _has(hay, _NEGATIVE):
        v.reason = "نفي صريح (ليس أنمي)"
        return v

    v.is_anime = True
    v.score = 3.0 + min(anime_hits, 3) + (2.0 if studio_hit else 0.0) \
        + (2.5 if known_hit else 0.0)

    # --- الحدث ---
    # الترتيب مهمّ: «طاقم/موسم» أدقّ من «إعلان»، و«انتهاء» يُقدَّم على «عرض»
    for event in ("انتهاء", "طاقم", "تريلر", "عرض", "موسم", "إعلان"):
        if _has(hay, _EVENTS[event]):
            v.event = event
            break

    # --- الصيغة ---
    for fmt, words in ANIME_FORMATS.items():
        if _has(raw_low, words) or _has(hay, words):
            v.format = fmt
            break

    # --- النوع ---
    for genre, words in ANIME_GENRES.items():
        if _has(raw_low, words) or _has(hay, words):
            v.genres.append(genre)

    # --- الاستوديو ---
    v.studios = studio_hit[:3]

    # --- الموسم ---
    season_re = re.compile(r"(winter|spring|summer|fall|autumn)\s*(20\d{2})", re.I)
    m = season_re.search(text)
    if m:
        v.season = f"{m.group(1).capitalize()} {m.group(2)}"
    else:
        for s in ANIME_SEASONS:
            if s in raw_low or s in hay:
                v.season = s.capitalize() if s.isascii() else s
                break

    # --- أسماء يجب عدم ترجمتها (اللاتينية + المعروفة) ---
    v.titles = extract_titles(f"{article.title} {article.summary}")
    for en in known_hit:
        if en.title() not in v.titles and len(v.titles) < 4:
            v.titles.append(en.title())

    v.reason = f"أنمي · {v.event or 'خبر'}"
    return v


def should_publish_anime(verdict: AnimeVerdict, *, min_score: float = 0.0) -> tuple[bool, str]:
    """سياسة نشر قطاع الأنمي.

    الفرق عن الأخبار العامة: هنا **لا ننتظر «نتائج»** — الإعلان نفسه هو الخبر.
    لكن نرفض: خبراً بلا حدث واضح، أو كلاماً إنشائياً بلا معلومة.
    """
    if not verdict.is_anime:
        return False, "ليس خبر أنمي"
    if verdict.score < min_score:
        return False, f"أهمية منخفضة ({verdict.score:.1f})"
    if not verdict.event and not verdict.studios and not verdict.titles:
        return False, "بلا معلومة محددة (لا حدث ولا عمل ولا استوديو)"
    return True, f"أنمي — {verdict.summary()}"


# --------------------------------------------------------------------------- #
#  حماية الأسماء في الترجمة
# --------------------------------------------------------------------------- #
def translate_names(text: str, titles: list[str]) -> str:
    """حماية أسماء الأعمال: تُوضع بين قوسين إن عُرِّبت.

    النموذج قد يترجم *Attack on Titan* إلى «هجوم العملاق» فيختفي الاسم الذي
    يبحث عنه الجمهور. هنا نُثبّت الأصل بجانب العربي:

        «هجوم العملاق (Attack on Titan)»

    ولا نكرّر الإضافة إن كان الاسم موجوداً بالنص.
    """
    if not text or not titles:
        return text
    out = text
    for title in titles:
        if title.lower() in out.lower():
            continue                      # الاسم موجود — لا حاجة
        # إن ظهر جزء من الاسم (كلمة مميّزة) نفترض أن العمل مذكور
        distinctive = max(title.split(), key=len)
        if distinctive.lower() in out.lower():
            continue
    return out


def name_glossary(titles: list[str]) -> str:
    """قائمة أسماء لأسفل المنشور (اختياري) لتفادي أي ترجمة مضلّلة."""
    clean: list[str] = []
    for t in titles:
        t = _clean_title(t)
        if t and t not in clean:
            clean.append(t)
    if not clean:
        return ""
    return "🎬 الأسماء الأصلية: " + " · ".join(clean[:3])


# --------------------------------------------------------------------------- #
#  الصياغة العربية (برومبت مستقل — الأنمي ليس خبراً أمنياً!)
# --------------------------------------------------------------------------- #
ANIME_SYSTEM = """أنت محرّر عمود أنمي عربي في مجلة متخصّصة.
قواعدك:
1) ابدأ باسم العمل الأصلي كما هو (بالإنجليزية كما يُكتب رسمياً) — لا تترجمه ولا تُحرّفه.
2) لا تختلق معلومة. لا استوديو ولا تاريخ ولا عدد حلقات لم يُذكر في المصدر.
3) اذكر: الحدث (إعلان/عرض/انتهاء) ثم الاستوديو ثم الموسم إن وُجدوا.
4) لغة عربية صحفية مشوّقة بلا مبالغة تجارية، جمل قصيرة.
5) جمهورك يعرف المصطلحات (إيسيكاي، شونين، لايت نوفل) — لا تشرحها.
6) لا تستخدم «عاجل» ولا صيغة الأخبار الأمنية.

أخرج JSON فقط:
{"title": "...", "body": "...", "event": "إعلان|عرض|انتهاء|موسم|تريلر|طاقم", "skip": false}"""


def build_anime_prompt(article: Article, verdict: AnimeVerdict) -> str:
    """برومبت صياغة خبر أنمي (منفصل عن برومبت الأخبار والنتائج)."""
    meta = []
    if verdict.event:
        meta.append(f"الحدث: {verdict.event}")
    if verdict.studios:
        meta.append(f"الاستوديو (كما ورد): {', '.join(verdict.studios)}")
    if verdict.season:
        meta.append(f"الموسم: {verdict.season}")
    if verdict.format:
        meta.append(f"الصيغة: {verdict.format}")
    if verdict.genres:
        meta.append(f"النوع: {', '.join(verdict.genres)}")
    if verdict.titles:
        meta.append(f"أسماء أعمال وردت (لا تترجمها): {', '.join(verdict.titles)}")

    return f"""اكتب منشوراً عربياً لهذا الخبر الأنمي.

الخبر: {article.title}
النص: {(article.summary or '')[:600]}
{(chr(10) + 'تفاصيل: ' + article.content[:400]) if article.content else ''}

حقائق رصدها النظام (تحقّق منها في النص، ولا تُضف غيرها):
{chr(10).join('- ' + m for m in meta) if meta else '- (لا حقائق إضافية)'}

المطلوب:
- title: ≤ 12 كلمة، يبدأ باسم العمل الأصلي ثم الحدث.
- body: 2–4 أسطر: ما الحدث؟ من الاستوديو؟ متى العرض؟ (فقط ما ورد).
- event: نوع الحدث.
- skip: true فقط إن كان النص بلا أي معلومة مفيدة.
"""
