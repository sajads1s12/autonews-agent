"""المسار السريع (Fast Lane): نشر ما يجب أن يراه الناس **في دقيقته**.

الفكرة
------
قناة تنشر الحرب بعد ساعتين لا تُنافس قناة تنشرها بعد دقيقة. لذلك:

- الدورة العادية (GitHub Actions كل ساعتين) تبقى للتغطية العادية.
- مسار **منفصل ومتكرّر** (كل 5 دقائق) يفحص فقط «هل ظهر خبر عاجل جديد؟»
  وينشر فوراً بلا انتظار الدورة الكاملة.

ما يجعل هذا آمناً وقابلاً للاستمرار:
1. **حارس حداثة صارم**: لا يُنشر إلا خبر عمره < `max_age_minutes` (افتراضي 45 دقيقة).
2. **سقف ساعة + فاصل أدنى** لكل قناة: لا فيضان، ولا حرق للحصة.
3. **صياغة سريعة**: نموذج واحد + توكنات أقل + مهلة قصيرة (2 محاولات كحد أقصى).
4. **حتى لو فشل الذكاء الاصطناعي** يُنشر القالب السريع — السرعة أهم من الجمال هنا.
5. **دعم نشر فوري (scoped)**: قناة تيليجرام يمكن أن تعمل بنموذج «وكالة» يُفتح من الجوال
   (Push-to-Channel)، ثم يفرّغه النظام نفسه فوراً عبر `flush_channel()`.

الحدّ الذي يجب أن تعرفه بصراحة
------------------------------
GitHub Actions المجدولة ليست فورية: قد تتأخّر 1–15 دقيقة عند الازدحام، وأقل فاصل
عملي هو 5 دقائق. للحصول على **ثوانٍ** يلزم مشغّل مستمر (VPS صغير / Docker) —
وهو مدعوم في هذا المشروع عبر `python -m autonews.runner --interval 30`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .config_loader import BreakingSettings, Channel, FlashSettings, OutcomeSettings
from .ingest import Article
from .outcome_features import (
    Aftermath, SourceEvidence, build_governorate_daily, followup_badge,
    governorate_contrast, impact_line, register_deferred, resolve_followups,
    silent_alert_text, silent_incidents, source_line, verify_result,
)
from .outcomes import (OutcomeAnalysis, analyze as analyze_outcome,  # noqa: F401
                       build_outcome_body, build_outcome_prompt, should_publish)
from .utils import topic_overlap, get_logger, normalize_arabic, utcnow

log = get_logger("autonews.flash")


# --------------------------------------------------------------------------- #
#  قواعد التصنيف
# --------------------------------------------------------------------------- #
PRIORITY_RULES: dict[str, list[str]] = {
    # أخطر ما يمكن: حرب/أمن/انفجارات/إصابات جماعية
    "war": [
        "حرب", "غزو", "قصف", "غارة", "غارات", "صاروخ", "صواريخ", "مسيّرة", "مسيرة",
        "انفجار", "تفجير", "هجوم", "اشتباكات", "معارك", "قتال", "إطلاق نار",
        "شهداء", "قتلى", "مقتل", "جرحى", "ضحايا", "إصابات", "حصيلة",
        "استنفار", "طوارئ", "إنذار", "تحذير", "إخلاء", "نزوح",
        "war", "attack", "explosion", "airstrike", "missile", "casualties", "killed",
    ],
    # عطل ومناسبات: يؤثّر مباشرة على حياة الناس اليوم
    "holiday": [
        "عطلة", "عطله", "العطل", "تعطيل", "اجازة", "إجازة", "اجازات", "إجازات",
        "تمديد", "الدوام", "دوام", "تعليق الدوام", "إيقاف الدوام",
        "عيد", "اعياد", "أعياد", "مناسبة", "عطلة رسمية", "رسمي",
    ],
    # كوارث وطقس
    "disaster": [
        "زلزال", "فيضان", "فيضانات", "سيول", "أمطار غزيرة", "عاصفة", "إعصار",
        "حريق", "انهيار", "انفجار كبير", "موجة حر", "موجة برد", "قحط",
        "earthquake", "flood", "storm", "fire", "collapse",
    ],
    # قرارات عليا وتغييرات مفاجئة
    "major": [
        "استقالة", "إقالة", "اعتقال", "انقلاب", "حالة طوارئ", "تعليق",
        "إغلاق", "تحذير", "إلغاء", "تأجيل", "قرار", "رفع", "خفض",
        "البنك المركزي", "الدولار", "سعر الصرف", "الرواتب", "الكهرباء",
        "resign", "emergency", "shutdown", "ban",
    ],
}

# كلمات تُعتبر «وقت حرب» — ترفع كل شيء إلى أعلى أولوية
CRITICAL_MARKERS = ("عاجل", "breaking", "الآن", "فورا", "فوراً", "جاري", "مباشر")

_SPAM_GUARD = (
    "فيديو", "بالفيديو", "شاهد", "صور", "بالأرقام", "تقرير", "حوار", "مقابلة",
    "تحليل", "رأي", "مقال", "كاريكاتير", "بث مباشر لمباراة", "أهداف المباراة",
)


# كلمات «حاسمة»: تكفي وحدها في العنوان (لكل فئة)
_DECISIVE = {
    "war": {"قصف", "غارة", "غارات", "صاروخ", "صواريخ", "انفجار", "تفجير", "هجوم",
            "اشتباكات", "معارك", "شهداء", "قتلى", "مقتل", "جرحى", "ضحايا", "حرب",
            "غزو", "استنفار", "إخلاء", "نزوح",
            "attack", "explosion", "airstrike", "missile", "casualties", "war"},
    "disaster": {"زلزال", "فيضان", "فيضانات", "سيول", "إعصار", "عاصفة", "حريق",
                 "انهيار", "earthquake", "flood", "storm", "collapse"},
    "major": {"استقالة", "إقالة", "اعتقال", "انقلاب", "إغلاق", "حالة طوارئ",
              "إلغاء", "تأجيل", "resign", "emergency", "shutdown"},
    "holiday": {"عطلة", "عطله", "تعطيل", "اجازة", "إجازة", "تمديد"},
}


# كلمات تدلّ على أن الخبر «إعلان عطلة عام بلا تفاصيل» ⇒ يُنتظر صدور النتائج
_BARE_ANNOUNCEMENT = ("عطلة رسمية", "عطلة غدا", "عطلة غداً", "تعطيل الدوام غدا")


@dataclass
class FlashVerdict:
    urgent: bool
    category: str = ""
    score: int = 0
    hits: list[str] = field(default_factory=list)
    reason: str = ""

    def label(self) -> str:
        icons = {"war": "⚔️", "disaster": "🚨", "holiday": "📅", "major": "❗"}
        return icons.get(self.category, "⚡")


def normalize(text: str) -> str:
    return normalize_arabic(text or "").lower()


def classify(article: Article, settings: FlashSettings,
             *, breaking: BreakingSettings | None = None) -> FlashVerdict:
    """تصنيف الخبر: هل هو «ضروري فوراً» أم خبر عادي؟

    القواعد:
    - كلمة من فئة `war`/`disaster` في العنوان => عاجل مهما كان.
    - كلمتان من `holiday`/`major` في العنوان، أو كلمة + إشارة «عاجل» => عاجل.
    - وجود كلمات «فيديو/شاهد/تحليل» في العنوان **تخفض** الاحتمال (محتوى غير عاجل).
    """
    if not settings.enabled:
        return FlashVerdict(False, reason="المسار السريع معطّل")

    title = normalize(article.title)
    summary = normalize(article.summary[:400])
    haystack = f"{title} {summary}"

    # ---- حارس الأهمية: لا نُفجّر قناة بمحتوى ترفيهي ----
    if any(normalize(w) in title for w in _SPAM_GUARD):
        return FlashVerdict(False, reason="محتوى غير عاجل (فيديو/تقرير/تحليل)")

    hits: list[str] = []
    category, score = "", 0

    for cat in settings.categories or list(PRIORITY_RULES):
        words = PRIORITY_RULES.get(cat, [])
        title_hits = [w for w in words if _has(title, w)]
        summary_hits = [w for w in words if _has(summary, w)]
        if not title_hits and not summary_hits:
            continue
        weight = {"war": 5, "disaster": 6, "major": 5, "holiday": 4}.get(cat, 2)
        # كلمة في العنوان أثقل من كلمة في الملخص
        decisive = _DECISIVE.get(cat, set())
        decisive_hits = [h for h in title_hits if normalize(h) in decisive]
        cat_score = weight * len(title_hits) + weight // 2 * min(len(summary_hits), 3)
        if decisive_hits:
            cat_score += weight        # كلمة حاسمة في العنوان تستحق دفعة إضافية
        if cat_score > score:
            score, category = cat_score, cat
        hits.extend(title_hits or summary_hits)

    if settings.keywords:
        kw_hits = [k for k in settings.keywords if _has(haystack, k)]
        if kw_hits:
            score += 6 * len(kw_hits)
            hits.extend(kw_hits)
            category = category or "custom"

    if breaking is not None:
        from .breaking import is_breaking

        is_brk, brk_hits = is_breaking(article, breaking)
        if is_brk:
            score += 3
            hits.extend(brk_hits[:2])
            category = category or "breaking"

    critical = any(_has(title, m) for m in CRITICAL_MARKERS)
    if critical:
        score += 2

    urgent = score >= settings.min_score
    if not urgent:
        return FlashVerdict(False, category=category, score=score, hits=list(dict.fromkeys(hits)),
                            reason=f"درجة {score} < الحدّ {settings.min_score}")

    return FlashVerdict(True, category=category or "breaking", score=score,
                        hits=list(dict.fromkeys(hits))[:6],
                        reason=f"درجة {score} · فئة {category}")


# سوابق ولواحق عربية شائعة: «العطلة»، «بالقصف»، «تمديدها»، «غارات»
_AR_PREFIXES = ("و", "ف", "ب", "ل", "ك", "ال", "وال", "بال", "فال", "كال", "لل", "ولل")
_AR_SUFFIXES = ("", "ه", "ها", "هم", "هما", "كم", "نا", "ات", "ين", "ون",
                "ان", "تين", "تان")


def _has(haystack: str, word: str) -> bool:
    """مطابقة كلمة مع حدود عربية واعية (تمنع «عيد» من مطابقة «تصعيد»).

    الكلمة تُطابَق ككلمة مستقلّة، مع السماح بالسوابق (ال/بال/و…) واللواحق
    (ها/ات/ين…) — لأن العربية تلتصق بها. بدون هذا نقع في إيجابيات كاذبة
    سخيفة مثل تصنيف خبر «تصعيد قضائي» ضمن فئة «عيد».
    """
    haystack = normalize(haystack)
    w = normalize(word).strip()
    if not w:
        return False
    if re.search(r"[a-z]", w):
        return re.search(rf"\b{re.escape(w)}\b", haystack) is not None

    boundary_before = r"(?:^|[\s\W])"
    boundary_after = r"(?:$|[\s\W])"
    # الكلمة كما هي (بأي سابقة/لاحقة) أو مع صيغة الجمع/التأنيث
    alternatives = [w]
    for prefix in _AR_PREFIXES:
        alternatives.append(prefix + w)
    pattern_parts = []
    for alt in alternatives:
        for suffix in _AR_SUFFIXES:
            pattern_parts.append(re.escape(alt + suffix))
    pattern = (f"{boundary_before}(?:{'|'.join(sorted(set(pattern_parts), key=len, reverse=True))})"
               f"{boundary_after}")
    return re.search(pattern, haystack) is not None


# --------------------------------------------------------------------------- #
#  حارس الحداثة
# --------------------------------------------------------------------------- #
def freshness(article: Article) -> tuple[bool, float, str]:
    """هل الخبر طازج بما يكفي للنشر الفوري؟ يعيد (مسموح، العمر بالدقائق، السبب)."""
    if not article.published:
        return True, -1.0, "بلا تاريخ نشر (نعتمد على كونه جديداً في الخلاصة)"
    minutes = (utcnow() - article.published).total_seconds() / 60
    if minutes < -10:
        return False, minutes, "تاريخ نشر مستقبلي غير منطقي"
    return True, minutes, ""


def is_fresh(article: Article, settings: FlashSettings) -> tuple[bool, float, str]:
    ok, minutes, reason = freshness(article)
    if not ok:
        return False, minutes, reason
    if minutes >= 0 and minutes > settings.max_age_minutes:
        return False, minutes, f"الخبر عمره {minutes:.0f} دقيقة (> {settings.max_age_minutes})"
    return True, minutes, ""


# --------------------------------------------------------------------------- #
#  الصياغة السريعة
# --------------------------------------------------------------------------- #
FLASH_SYSTEM = """أنت محرّر عاجل في غرفة أخبار. اكتب بأقصى سرعة ووضوح.
قواعد صارمة:
1) لا تختلق أي معلومة أو رقم أو اسم. انقل فقط ما ورد في النص.
2) الجملة الأولى = الخبر نفسه (من؟ ماذا؟ أين؟) في أقل من 18 كلمة.
3) لا مقدمات ولا خطابة ولا رأي. الحقيقة فقط، بصيغة الحاضر.
4) إن كانت المعلومات غير مكتملة، اكتب ما هو مؤكد فقط ولا تُكمل بالتفاصيل.
5) احذف كل عبارة إنشائية («في تطور خطير» لا تُضيف معلومة).

أخرج JSON فقط: {"title": "...", "body": "..."}
"""


def build_flash_prompt(article: Article, channel: Channel, verdict: FlashVerdict,
                       *, context_hint: str = "") -> str:
    labels = {"war": "حدث أمني/عسكري", "disaster": "كارثة/طقس",
              "holiday": "عطلة/مناسبة", "major": "قرار أو تغيير مهم",
              "breaking": "خبر عاجل", "custom": "خبر مهم"}
    kind = labels.get(verdict.category, "خبر عاجل")
    summary = re.sub(r"\s+", " ", article.summary or "")[:600]
    return f"""اكتب الآن منشوراً **عاجلاً** ({kind}) لمنصّة {channel.platform}.

المعلومات المتاحة:
- العنوان الأصلي: {article.title}
- النص: {summary}
- المصدر: {article.source_name}
{f"- سياق المناسبة: {context_hint}" if context_hint else ""}

{channel.style}

شروط:
- العنوان ≤ 12 كلمة، يبدأ بالمعلومة الأهم.
- الجسم: جملة أو جملتان فقط (≤ 260 حرفاً) — نحن في سباق دقائق لا ساعات.
- لا تُضف هاشتاغات (تُضاف آلياً)، ولا إيموجي في كل سطر.
- لا تذكر المصدر داخلياً (يُضاف آلياً).

أخرج JSON فقط: {{"title": "...", "body": "..."}}
"""


# --------------------------------------------------------------------------- #
#  المحرّك
# --------------------------------------------------------------------------- #
@dataclass
class FlashResult:
    published: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0       # مؤجّل حتى تظهر نتائجه
    waited: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def summary(self) -> str:
        return (f"نُشر فوراً={self.published} · مؤجّل(بلا نتائج)={self.deferred}"
                f" · تجاهل={self.skipped} · فشل={self.failed} · {self.elapsed_s:.1f}ث")


class FlashLane:
    """نشر الأخبار الضرورية فوراً — مسار مستقل عن الدورة العادية."""

    def __init__(self, settings, state, *, engine=None, writer=None,
                 calendar=None, notifier=None, publisher_factory=None) -> None:
        self.settings = settings
        self.state = state
        self.flash: FlashSettings = settings.flash
        self.engine = engine
        self.writer = writer
        self.calendar = calendar
        self.notifier = notifier
        self._factory = publisher_factory

    # ------------------------------------------------------------------ نشر
    def _publish(self, post, channel: Channel, image=None) -> bool:
        from .publishers import build_publisher

        factory = self._factory or build_publisher
        publisher = factory(channel, dry_run=self.settings.dry_run,
                            timeout=min(25, max(15, self.settings.request_timeout)))
        try:
            result = publisher.publish(post, image)
        except Exception as exc:  # noqa: BLE001 — المسار السريع لا يتوقّف
            log.error("فشل نشر فوري على %s: %s", channel.id, exc)
            return False
        finally:
            if image:
                image.cleanup()
        return result.ok

    def _write_fast(self, article: Article, channel: Channel, verdict: FlashVerdict,
                    analysis: OutcomeAnalysis | None = None,
                    followup_tag: str = "") -> object:
        """صياغة سريعة بنمط النتائج: ما نتج عن الحدث، لا وصفه.

        القاعدة: بطاقة «📋 النتائج» — حالة الأشخاص، المحافظات المشمولة/المستثناة،
        والقرارات. وإن لم تكن هناك نتائج واضحة، نُخرج ما هو مؤكد فقط.
        """
        context_hint = self.calendar.context_hint(
            tz_offset=self.settings.occasions.tz_offset) if self.calendar else ""
        outcome_mode = self.settings.outcomes.enabled
        if analysis is None:
            analysis = analyze_outcome(article)

        oc = self.settings.outcomes
        # 🎬 وضع الأنمي: الإعلان خبر بذاته — لا «نتائج» ولا «أثر عليك»
        anime_mode = False
        if self.settings.verticals.anime.enabled and self._looks_anime(article):
            from .verticals import classify_anime

            anime_mode = classify_anime(article).is_anime
        if anime_mode:
            outcome_mode = False

        # ٦) سطر الشفافية + ١) سطر الأثر — يُحسبان مرة واحدة للقناة
        line = ""
        if oc.source_line:
            src = self._source_meta(article)
            line = source_line(article, official=src[0], trust=src[1],
                               age_minutes=max(0, int(_age_minutes(article))))
        impact = impact_line(article, analysis) if (oc.impact_line and outcome_mode) else ""
        extra = {"impact": impact, "source_line": line}

        # 🎬 قطاع الأنمي: سطر الأسماء الأصلية (يمنع ضياع أسماء الأعمال بالترجمة)
        acfg = self.settings.verticals.anime
        if acfg.enabled and acfg.append_glossary and self._looks_anime(article):
            from .verticals import classify_anime, name_glossary

            av = classify_anime(article)
            if av.is_anime and av.titles:
                extra["glossary"] = name_glossary(av.titles)

        # ⚡ توافق مفاتيح المسارين: ملف صحة المفاتيح مشترك، لكن كاتب الدورة العادية
        # قد يكون جمّد كل الأزواج قبل قليل. إن كان كل شيء مجمّداً لا نضيّع ثوانٍ:
        # ننتقل مباشرة إلى قالب النتائج (السرعة أولاً، والصحة محفوظة على القرص).
        if self.engine is not None and hasattr(self.engine, "available_pairs"):
            try:
                if not self.engine.available_pairs():
                    log.info("🔋 كل أزواج المفاتيح مجمّدة حالياً — قالب فوري (بلا انتظار)")
                    self.engine = None
            except Exception:  # noqa: BLE001
                pass

        if self.engine is not None and getattr(self.engine, "keys", []):
            from .ai.writer import extract_json
            from .outcomes import OUTCOME_SYSTEM

            if anime_mode:
                from .verticals import ANIME_SYSTEM, build_anime_prompt, classify_anime

                prompt = build_anime_prompt(article, classify_anime(article))
                system = ANIME_SYSTEM
            elif outcome_mode:
                prompt = build_outcome_prompt(article, analysis)
                system = OUTCOME_SYSTEM
            else:
                prompt = build_flash_prompt(article, channel, verdict, context_hint=context_hint)
                system = FLASH_SYSTEM
            try:
                result = self.engine.generate(
                    prompt, system=system, temperature=0.3,
                    max_output_tokens=self.flash.max_output_tokens, json_response=True,
                )
                data = extract_json(result.get("text", "")) or {}
                if data.get("skip"):
                    log.info("⏭ النموذج لم يجد نتائج — لن يُنشر: %s", article.title[:60])
                    return None
                title = " ".join(str(data.get("title") or article.title).split())[:140]
                if anime_mode:
                    raw_body = " ".join(str(data.get("body") or "").split())
                    body = raw_body + (f"\n\n{extra['glossary']}" if extra.get("glossary") else "") \
                        + (f"\n\n<i>{line}</i>" if line else "")
                elif outcome_mode:
                    body = _compose_outcome_body(data, article, analysis, extra=extra)
                else:
                    body = (" ".join(str(data.get("body") or "").split())
                            + (f"\n\n<i>{line}</i>" if line else ""))
                if body:
                    if followup_tag:
                        title = f"{followup_tag} · {title}"[:140]
                    return _make_post(article, channel, title, body,
                                      model=f"flash:{result.get('model', '')}",
                                      verdict=verdict, analysis=analysis)
            except Exception as exc:  # noqa: BLE001 — السرعة أهم من الجمال
                log.warning("تعذّرت الصياغة السريعة (%s) — قالب النتائج", exc)

        if anime_mode:
            fallback = _anime_body(article, extra=extra)
        elif outcome_mode:
            fallback = _outcome_body_fallback(article, analysis, extra=extra)
        else:
            fallback = _fast_body(article) + (f"\n\n<i>{line}</i>" if line else "")
        if followup_tag:
            fallback = f"{followup_tag}\n\n{fallback}"
        title = _anime_title(article) if anime_mode else _outcome_title(article, analysis)
        return _make_post(article, channel, title, fallback,
                          model="flash:template", verdict=verdict, analysis=analysis)

    def _source_meta(self, article: Article) -> tuple[bool, float]:
        """رسمي؟ ووزن الثقة؟ — من إعدادات المصادر."""
        for src in getattr(self.settings, "sources", []) or []:
            if src.id == article.source_id:
                return bool(getattr(src, "official", False)), float(getattr(src, "trust", 1.0))
        return False, 1.0

    # ------------------------------------------------------------------ الدورة
    def run(self, articles: list[Article], *, channels: list[Channel] | None = None) -> FlashResult:
        """نشر كل خبر يستحق فوراً (من قائمة مقالات مسحوبة)."""
        t0 = time.time()
        result = FlashResult()
        channels = channels or [c for c in self.settings.channels if c.flash_eligible]

        if not channels:
            log.warning("المسار السريع: لا توجد قنوات مؤهّلة (flash_eligible)")
            return result

        # 🌐 تعريب المصادر الأجنبية أولاً (حتى تُصنَّف عربياً)
        articles = self._translate_candidates(articles)

        # الأحدث أولاً: الحرب تُنشر قبل التحليل
        candidates = sorted(articles, key=lambda a: a.published or utcnow(), reverse=True)
        limit = self.flash.max_items_per_run

        for art in candidates:
            if result.published >= limit:
                log.info("بلغ حدّ النشر الفوري (%d) لهذه الدورة", limit)
                break

            if self.state.has_article(art.fp) or self.state.was_flashed(art.fp):
                continue
            if self.state.was_recently_published(art.url, art.title):
                log.info("🔗 عاجل مكرر — سبق نشر الرابط/العنوان: %s", art.title[:65])
                result.skipped += 1
                continue

            verdict = classify(art, self.flash, breaking=self.settings.breaking)
            if not verdict.urgent:
                continue

            # 🎬 قطاعات: خبر الأنمي يخرج من بوّابة «الحدث الخام» — الإعلان هو الخبر
            anime_verdict = None
            if self.settings.verticals.anime.enabled and (
                    "أنمي" in (art.tags or []) or self._looks_anime(art)):
                from .verticals import classify_anime

                anime_verdict = classify_anime(art)
                if anime_verdict.is_anime:
                    ok_anime, why_anime = self._anime_policy(anime_verdict)
                    if not ok_anime:
                        log.info("🎬 رُفض أنمي — %s | %s", why_anime, art.title[:60])
                        result.skipped += 1
                        continue
                    log.info("🎬 خبر أنمي (%s) | %s", anime_verdict.summary(), art.title[:55])
                    analysis = OutcomeAnalysis(has_outcome=True, kinds=["anime"])
                    publish_ok, why = True, why_anime
                else:
                    publish_ok, why, analysis = should_publish(art, self.settings.outcomes)
            else:
                # 🔎 نمط النتائج: هل الخبر نتيجة (ما صار) أم حدث خام (ما وقع)؟
                publish_ok, why, analysis = should_publish(art, self.settings.outcomes)
            if not publish_ok:
                log.info("⏭ ينشر لاحقاً — %s | %s", why, art.title[:70])
                result.deferred += 1
                result.details.append(f"⏭ مؤجّل (بلا نتائج): {art.title[:70]}")
                # ٢) لا نُنسى الحادثة: تُسجَّل لتُتابع نتائجها لاحقاً
                self._register_deferred(art, analysis)
                continue
            if analysis.has_outcome:
                log.info("📋 نتائج مرصودة | %s", analysis.summary())
                # ٥) التحقّق المزدوج: مصدر رسمي أو مصدران مستقلّان
                ok_v, why_v = self._verify(art, analysis)
                if not ok_v:
                    log.info("🕵️ لم تُثبت النتيجة بعد (%s) — تُؤجّل: %s", why_v, art.title[:60])
                    result.deferred += 1
                    result.details.append(f"🕵️ بانتظار تأكيد: {art.title[:60]}")
                    self._register_deferred(art, analysis)
                    continue

            fresh, minutes, why = is_fresh(art, self.flash)
            if not fresh:
                log.info("⏳ عاجل لكن غير طازج (%s) — سيُعالج في الدورة العادية: %s",
                         why, art.title[:60])
                result.skipped += 1
                result.details.append(f"⏳ متأخّر: {art.title[:70]} ({why})")
                continue

            targets = [c for c in channels if self._targets(c, art)]
            if not targets:
                continue

            log.info("%s عاجل (%.0f دقيقة) | %s | %s", verdict.label(),
                     minutes if minutes >= 0 else 0, art.source_id, art.title[:70])

            # ٢) هل هذه النتيجة تُغلق حادثة كنّا نتابعها؟ نعرف قبل الصياغة لنضع الوسم.
            followup_tag = self._followup_badge_for(art) if self.settings.outcomes.followup else ""

            sent_any = False
            # 🎛 إيقاف قناة من لوحة التحكّم يوقف المسار السريع أيضاً
            try:
                from .control import ControlStore

                _ctl = ControlStore.load()
                targets = [c for c in targets if not _ctl.is_paused(c.id) and
                           _ctl.get(c.id).allow_now(tz_offset=self.settings.timezone_offset)[0]]
            except Exception:  # noqa: BLE001 — التحكّم لا يُسقط المسار السريع
                pass
            for channel in targets:
                allowed, why_not = self.state.flash_allowed(
                    channel.id,
                    max_per_hour=self.flash.max_per_hour,
                    min_gap_seconds=self.flash.min_gap_seconds,
                )
                if not allowed:
                    log.warning("⛔ %s: %s — تأجيل الخبر", channel.id, why_not)
                    result.waited.append(f"{channel.id}: {why_not}")
                    continue

                post = self._write_fast(art, channel, verdict, analysis,
                                        followup_tag=followup_tag)
                if post is None:
                    result.deferred += 1
                    result.details.append(f"⏭ لا نتائج كافية: {art.title[:70]}")
                    break
                post.breaking = True
                try:
                    from .control import ControlStore as _CS

                    _CS.load().record_post(channel.id, tz_offset=self.settings.timezone_offset)
                except Exception:  # noqa: BLE001
                    pass
                # جودة خفيفة: نمنع الأرقام المختلقة فقط (سريع جداً)
                if self.settings.quality.require_numbers_match:
                    from .quality import check_numbers

                    invented = check_numbers(post, art,
                                             strict=self.settings.quality.require_numbers_match)
                    if invented:
                        log.warning("🚧 رُفض فوري (أرقام مختلقة: %s)", ", ".join(invented[:3]))
                        result.details.append(
                            f"🚧 رفض جودة: {art.title[:60]} (أرقام: {', '.join(invented[:3])})")
                        continue

                # 🔗 وسوم UTM قبل النشر (لقياس أي منشور يجلب القارئ)
                if self.settings.links.enabled:
                    from .links import UTMSettings, campaign_for_post, tag_post_url

                    tag_post_url(post, platform=channel.platform, settings=UTMSettings(
                        enabled=self.settings.links.enabled,
                        source_override=self.settings.links.source_override,
                        medium=self.settings.links.medium,
                        campaign_prefix=self.settings.links.campaign_prefix,
                        keep_existing=self.settings.links.keep_existing,
                    ))

                image = None
                if channel.with_image and self.settings.images.enabled:
                    from .images import fetch_article_image

                    img_settings = self.settings.images
                    img_settings.timeout = min(6, img_settings.timeout)   # لا ننتظر صورة طويلاً
                    image = fetch_article_image(art.url, settings=img_settings)

                ok = self._publish(post, channel, image)
                if ok:
                    sent_any = True
                    result.published += 1
                    self.state.bump_flash(channel.id)
                    self.state.record_flash(art.fp, channel.id, post.title,
                                            source=art.source_id,
                                            gov_on=analysis.governorates_on,
                                            gov_off=analysis.governorates_off)
                    result.details.append(
                        f"{verdict.label()} {post.title[:80]} → {channel.name}"
                        + (" 🖼" if image and image.ok else ""))
                else:
                    result.failed += 1

            if sent_any:
                if followup_tag:
                    log.info("🔄 نُشرت نتيجة لحادثة متابَعة (%s): %s", followup_tag, art.title[:60])
                    result.details.append(f"{followup_tag} {art.title[:70]}")
                self.state.mark(art.fp, "posted", title=art.title[:120], via="flash")
                if self.notifier is not None:
                    try:
                        self.notifier._send(
                            f"⚡ <b>عاجل نُشر فوراً</b>\n{_esc(art.title[:150])}")
                    except Exception:  # noqa: BLE001
                        pass

        # ٢) مطابقة المقالات الجديدة بالحوادث المتابَعة (حتى لو لم تُصنَّف عاجلة)
        self._resolve_followups(articles, result)

        # ٧) الحوادث الصامتة: تنبيه إداري فقط — لا نشر
        self._alert_silent(result)

        # ٣+٨) خريطة المحافظات اليومية + كشف التفاوت
        self._publish_daily_matrix(result, channels)

        result.elapsed_s = time.time() - t0
        self.state.flash_stats["last_run"] = result.published
        log.info("⚡ المسار السريع انتهى | %s", result.summary())
        return result

    # ------------------------------------------------- 🎬 قطاع الأنمي
    def _looks_anime(self, article: Article) -> bool:
        """فحص رخيص قبل التحليل الكامل (يتفادى حساب كل خبر)."""
        hay = f"{article.title} {article.summary}".lower()
        return any(k in hay for k in ("anime", "أنمي", "انمي", "manga", "مانغا",
                                      "light novel", "لايت نوفل"))

    def _anime_policy(self, verdict) -> tuple[bool, str]:
        """سياسة نشر الأنمي (مختلفة عن الأخبار: الإعلان خبر بذاته)."""
        from .verticals import should_publish_anime

        cfg = self.settings.verticals.anime
        if cfg.publish_policy == "raw":
            return True, "أنمي — سياسة raw"
        return should_publish_anime(verdict, min_score=cfg.min_score)

    # ------------------------------------------------- 🌐 التعريب
    def _translate_candidates(self, articles: list[Article]) -> list[Article]:
        """تعريب العناصر الأجنبية قبل التصنيف (بسقف حفظاً للحصة).

        الخبر المعرَّب يأخذ عنواناً عربياً، فيدخل المصنّف العربي طبيعياً
        بدل أن يسقط لأنه «غير عربي».
        """
        tr = self.settings.translation
        if not (tr.enabled and self.engine is not None):
            return articles

        from .i18n import (
            apply_translation, is_already_covered, needs_translation, translate_article,
        )

        # عناوين شوهدت (عربية أصلاً + أصول عُرِّبت) لمنع ازدواج الخبر بلغتين
        seen_originals: list[str] = [p.get("title", "") for p in self.state.posted[-80:]]
        out: list[Article] = []
        done = 0
        for art in articles:
            if done >= tr.max_per_run or not needs_translation(art, target=tr.target):
                out.append(art)
                continue
            if (art.lang or "").lower() not in tr.langs:
                out.append(art)
                continue
            if is_already_covered(art.title, seen_originals):
                log.info("↩️ خبر أجنبي مكرّر عربياً — لا تعريب: %s", art.title[:55])
                continue
            res = translate_article(art, self.engine, target=tr.target)
            if res.ok and res.changed:
                done += 1
                log.info("🌐 عُرّب: %s → %s", art.title[:45], res.title[:45])
                out.append(apply_translation(art, res))
            else:
                log.info("↩️ لم يُعرَّب (%s): %s", res.reason, art.title[:50])
                out.append(art)
        if done:
            log.info("🌐 اكتمل تعريب %d خبراً في هذه الدورة", done)
        return out

    # ------------------------------------------------- v6: النتائج والمتابعة
    def _register_deferred(self, article: Article, analysis: OutcomeAnalysis) -> None:
        """تسجيل حادثة خام لمتابعة نتائجها."""
        if not self.settings.outcomes.followup:
            return
        payload = {
            "fp": article.fp, "title": article.title[:160], "url": article.url,
            "source_id": article.source_id, "source_name": article.source_name,
            "summary": (article.summary or "")[:400],
            "governorates": list(dict.fromkeys(
                analysis.governorates_on + analysis.governorates_off)),
            "score": 2.0 + (2.0 if analysis.escalate else 0.0)
                     + min(len(analysis.signals), 3) * 0.5,
        }
        # نستخدم منطق register_deferred للحقول الزمنية الموحّدة
        tmp: list[dict] = []
        if register_deferred(analysis, article, store=tmp):
            payload.update({k: v for k, v in tmp[0].items() if k not in payload})
            self.state.defer_incident(
                payload, max_items=self.settings.outcomes.followup_max_items)

    def _verify(self, article: Article, analysis: OutcomeAnalysis) -> tuple[bool, str]:
        """التحقّق المزدوج: هل النتيجة مدعومة بمصدر رسمي أو مصدرين مستقلّين؟"""
        oc = self.settings.outcomes
        if not oc.dual_verify:
            return True, "التحقّق المزدوج معطّل"
        official, trust = self._source_meta(article)
        evidence = [SourceEvidence(article.source_id, article.source_name,
                                   official=official, trust=trust)]
        # شواهد من المنشورات الفورية السابقة على نفس الموضوع (مصادر مستقلة)
        for item in self.state.flash_log[-40:]:
            if item.get("source") and item["source"] != article.source_id:
                if topic_overlap(item.get("title", ""), article.title) >= 0.45:
                    evidence.append(SourceEvidence(item["source"], item["source"], trust=1.0))
        verdict = verify_result(evidence, oc)
        return verdict.ok, verdict.reason

    def _resolve_followups(self, articles: list[Article], result: FlashResult) -> None:
        """٢) مطابقة الحوادث المتابَعة بنتائجها الجديدة."""
        oc = self.settings.outcomes
        if not oc.followup or not self.state.deferred_incidents:
            return
        resolved = resolve_followups(articles, self.state.deferred_incidents,
                                     min_hours=oc.followup_min_hours)
        for item, art, _a in resolved:
            badge = followup_badge(item)
            log.info("🔄 صدرت نتائج حادثة متابَعة %s: %s", badge, art.title[:70])
            result.details.append(f"{badge} {art.title[:70]}")
            self.state.defer_incident  # لا شيء: الحادثة أُزيلت في resolve_followups
            art_fp = art.fp
            if self.state.has_article(art_fp):
                continue
            # نُعيد الخبر إلى دورة النشر العادية في التشغيل القادم
            self.state.mark(art_fp, "skipped", title=art.title[:120],
                            reason="بانتظار نشر بطاقة التطوّر")

    def _followup_badge_for(self, article: Article) -> str:
        """وسم التطوّر إن كان الخبر يُغلق حادثة متابَعة."""
        for item in list(self.state.deferred_incidents):
            if item.get("url") == article.url:
                continue
            if topic_overlap(item.get("title", ""), article.title) >= 0.5:
                self.state.resolve_incident(item.get("fp", ""))
                return followup_badge(item)
        return ""

    def _publish_daily_matrix(self, result: FlashResult, channels: list[Channel]) -> None:
        """٣) منشور واحد يومياً: أي محافظة عطّلت وأي لا. + ٨) التفاوت الصارخ."""
        oc = self.settings.outcomes
        if not (oc.daily_matrix or oc.contrast):
            return
        today = utcnow().strftime("%Y-%m-%d")
        # نبني من نتائج اليوم المسجّلة في الحالة
        pairs: list[tuple[Article, OutcomeAnalysis]] = []
        # سجلّان: منشورات الدورة العادية + سجلّ المسار السريع (الأغنى بالمحافظات)
        for entry in (self.state.posted[-60:] + self.state.flash_log[-60:]):
            govs = entry.get("governorates") or {}
            if not govs:
                continue
            a = _light_article(entry)
            an = OutcomeAnalysis(has_outcome=True, kinds=["governorate"],
                                 governorates_on=list(govs.get("on") or []),
                                 governorates_off=list(govs.get("off") or []))
            pairs.append((a, an))
        if not pairs:
            return

        # ٨) التفاوت: خبر مستقل، مرة واحدة كل يوم
        if oc.contrast and self.state.contrast_sent.get("date") != today:
            line = governorate_contrast(pairs)
            if line:
                self._send_simple(line, channels, result, kind="contrast")
                self.state.contrast_sent = {"date": today, "text": line[:200]}
                result.details.append("⚖️ نُشر منشور تفاوت المحافظات")

        # ٣) الخريطة اليومية في الساعة المحدّدة
        if oc.daily_matrix and self.state.daily_matrix_date != today:
            local_hour = (utcnow().hour + self.settings.occasions.tz_offset) % 24
            if local_hour >= oc.daily_matrix_hour:
                card = build_governorate_daily(pairs, date_label=today)
                if card:
                    self._send_simple(card, channels, result, kind="matrix")
                    self.state.daily_matrix_date = today
                    result.details.append("🗺️ نُشرت خريطة المحافظات اليومية")

    def _send_simple(self, body: str, channels: list[Channel],
                     result: FlashResult, *, kind: str) -> bool:
        """نشر منشور نصي بسيط (بلا AI) على أول قناة تيليجرام مؤهّلة."""
        from .ai.writer import Post

        for channel in channels:
            if channel.platform != "telegram":
                continue
            allowed, why = self.state.flash_allowed(
                channel.id, max_per_hour=self.flash.max_per_hour,
                min_gap_seconds=self.flash.min_gap_seconds)
            if not allowed:
                log.info("⛔ %s: %s — تأجيل منشور %s", channel.id, why, kind)
                continue
            post = Post(channel_id=channel.id, platform=channel.platform,
                        title="خريطة المحافظات" if kind == "matrix" else "تفاوت المحافظات",
                        body=body, is_outcome=True, hashtags=["المحافظات", "عراق"],
                        category="مجتمع", source_url="", source_name="AutoNews",
                        model="template:matrix")
            if self._publish(post, channel):
                self.state.bump_flash(channel.id)
                result.published += 1
                return True
        return False

    def _alert_silent(self, result: FlashResult) -> None:
        """٧) تنبيه إداري واحد يجمع الحوادث الصامتة + حالات بانتظار تأكيد.

        قاعدة: **رسالة واحدة** لا ثلاث — لأن التنبيهات المتكرّرة تُهمَل.
        """
        oc = self.settings.outcomes
        # التنظيف أولاً: حادثة بلا نتيجة بعد ٣ أيام لا تُراقَب للأبد
        self.state.prune_incidents(max_age_hours=oc.followup_max_age_hours)
        if not (oc.silent_enabled and self.notifier is not None):
            return
        silent = silent_incidents(self.state.deferred_incidents, hours=oc.silent_hours)
        pending = [i for i in self.state.deferred_incidents
                   if not i.get("alerted") and i not in silent][:4]
        if not silent and not pending:
            return

        sections: list[tuple[str, list[str]]] = []
        if silent:
            sections.append(("🕵️ بلا نتائج منشورة (لا نشر — تحتاج تدخّلك)",
                             [f"{i.get('title', '')[:90]} — منذ {i.get('age_hours')}س"
                              for i in silent]))
        if pending:
            sections.append(("🔄 قيد المتابعة (تنتظر صدور النتيجة)",
                             [i.get("title", "")[:90] for i in pending]))
        try:
            if self.notifier.digest(title="📋 متابعة النتائج — المسار السريع",
                                    sections=sections):
                for it in silent:
                    for d in self.state.deferred_incidents:
                        if d.get("fp") == it.get("fp"):
                            d["alerted"] = True
                result.details.append(
                    f"🕵️ تنبيه واحد: {len(silent)} بلا نتائج · {len(pending)} قيد المتابعة")
        except Exception as exc:  # noqa: BLE001
            log.warning("تعذّر تنبيه الحوادث الصامتة: %s", exc)

    # ------------------------------------------------------------------ مساعدات
    def _targets(self, channel: Channel, article: Article) -> bool:
        """هل هذه القناة مهتمة بهذا الخبر؟ (مصدر/وسم/كلمة مفتاحية)."""
        if channel.sources and "*" not in channel.sources and article.source_id not in channel.sources:
            return False
        # 🚫 استثناء صريح: «القناة العامة لا تستقبل الأنمي» (فصل القطاعات)
        if channel.exclude_tags:
            if {t.lower() for t in article.tags} & {t.lower() for t in channel.exclude_tags}:
                return False
        if channel.tags:
            if not ({t.lower() for t in article.tags} & {t.lower() for t in channel.tags}):
                return False
        if channel.keywords:
            hay = normalize(article.title + " " + article.summary)
            if not any(normalize(k) in hay for k in channel.keywords):
                return False
        return True


def _compose_outcome_body(data: dict, article: Article, analysis: OutcomeAnalysis,
                          *, extra: dict | None = None) -> str:
    """تركيب بطاقة النتائج من مخرجات النموذج (منظمة، قابلة للقراءة)."""
    lines: list[str] = []

    gov_on = [str(g).strip() for g in (data.get("governorates_on") or []) if str(g).strip()]
    gov_off = [str(g).strip() for g in (data.get("governorates_off") or []) if str(g).strip()]
    if not gov_on and analysis.governorates_on:
        gov_on = analysis.governorates_on
    if not gov_off and analysis.governorates_off:
        gov_off = analysis.governorates_off

    people = " ".join(str(data.get("people") or "").split())
    decisions = " ".join(str(data.get("decisions") or "").split())
    body = str(data.get("body") or "").strip()

    if people:
        lines.append(f"👤 {people}")
    if gov_on:
        lines.append("✅ " + "، ".join(gov_on[:8]))
    if gov_off:
        lines.append("◻️ مستثناة: " + "، ".join(gov_off[:8]))
    if decisions:
        lines.append(f"📌 {decisions}")
    if not lines and body:
        return body
    if body and body not in "\n".join(lines):
        lines.append("")
        lines.append(body[:400])

    # v6: سطر «الأثر عليك» + سطر الشفافية
    extra = extra or {}
    if extra.get("glossary"):
        lines.append("")
        lines.append(extra["glossary"])
    if extra.get("impact"):
        lines.append("")
        lines.append(f"👉 {extra['impact']}")
    if extra.get("source_line"):
        lines.append(f"<i>{extra['source_line']}</i>")
    return "\n".join(lines).strip()


def _outcome_title(article: Article, analysis: OutcomeAnalysis) -> str:
    """عنوان يبدأ بالنتيجة (قالب احتياطي بلا AI)."""
    if analysis.governorates_on or analysis.governorates_off:
        parts = []
        if analysis.governorates_on:
            parts.append("عطلة/قرار في " + "، ".join(analysis.governorates_on[:3]))
        if analysis.governorates_off:
            parts.append("استثناء " + "، ".join(analysis.governorates_off[:2]))
        return " | ".join(parts)[:140]
    return article.title[:140]


def _outcome_body_fallback(article: Article, analysis: OutcomeAnalysis,
                           *, extra: dict | None = None) -> str:
    """بطاقة نتائج من النص نفسه (بلا AI) — مع رأس واضح + الأثر + المصدر."""
    card = build_outcome_body(article, analysis)
    if not card:
        return ""
    extra = extra or {}
    tail = ""
    if extra.get("glossary"):
        tail += f"\n\n{extra['glossary']}"
    if extra.get("impact"):
        tail += f"\n\n👉 {extra['impact']}"
    if extra.get("source_line"):
        tail += f"\n<i>{extra['source_line']}</i>"
    return f"📋 النتائج:\n{card}{tail}"


def _fast_body(article: Article) -> str:
    """قالب فوري: يُنشر حتى لو فشل كل شيء آخر."""
    text = " ".join((article.summary or article.content or article.title).split())
    if len(text) > 400:
        cut = text[:400]
        text = cut[: cut.rfind(".") + 1] if "." in cut[200:] else cut + "…"
    stamp = ""
    if article.published:
        minutes = max(0, int((utcnow() - article.published).total_seconds() // 60))
        stamp = f"🕒 قبل {minutes} دقيقة" if minutes < 120 else ""
    return f"{text}\n\n{stamp}".strip()


def _make_post(article: Article, channel: Channel, title: str, body: str, *,
               model: str, verdict: FlashVerdict, analysis: OutcomeAnalysis | None = None):
    from .ai.writer import Post

    return Post(
        channel_id=channel.id, platform=channel.platform, title=title, body=body,
        is_outcome=bool(analysis and analysis.has_outcome),
        hashtags=_flash_hashtags(article, channel, verdict),
        category={"war": "دولي", "disaster": "مجتمع", "holiday": "مجتمع",
                  "major": "سياسة"}.get(verdict.category, "متنوّع"),
        key_points=[], source_url=article.url, source_name=article.source_name,
        model=model, breaking=True, article_fp=article.fp,
    )


def _flash_hashtags(article: Article, channel: Channel, verdict: FlashVerdict) -> list[str]:
    base = {"war": ["عاجل", "أخبار"], "disaster": ["عاجل", "تحذير"],
            "holiday": ["عطلة", "أخبار"], "major": ["عاجل", "أخبار"]}.get(
        verdict.category, ["عاجل"])
    tags = list(dict.fromkeys(base + [t for t in (channel.tags or article.tags)][:2]))
    return [t.replace(" ", "_") for t in tags if t][: max(2, channel.add_hashtags)]


def _light_article(entry: dict) -> Article:
    """مقال خفيف من سجلّ المنشورات (لإعادة استخدامه في التجميع اليومي)."""
    return Article(source_id="posted", source_name=entry.get("source", "سجلّ"),
                   title=entry.get("title", ""), summary="", url=entry.get("url", ""))


def _anime_title(article: Article) -> str:
    """عنوان أنمي: يبدأ بالحدث ثم اسم العمل (والاسم الأصلي محفوظ)."""
    from .verticals import classify_anime

    v = classify_anime(article)
    work = v.titles[0] if v.titles else ""
    icons = {"إعلان": "🎬 إعلان", "عرض": "📺 موعد عرض", "انتهاء": "🏁 انتهاء",
             "موسم": "🔁 موسم جديد", "تريلر": "🎥 تريلر", "طاقم": "🎙 طاقم العمل"}
    head = icons.get(v.event, "🎬 أنمي")
    bits = [head]
    if work:
        bits.append(work)
    if v.studios:
        bits.append("· " + v.studios[0])
    if v.season and v.season not in " ".join(bits):
        bits.append("· " + v.season)
    title = " ".join(bits)
    # إن لم نستخرج اسماً لاتينياً، نستخدم عنوان الخبر كما هو
    if len(title) < 12:
        return article.title[:140]
    return title[:140]


def _anime_body(article: Article, *, extra: dict | None = None) -> str:
    """بطاقة أنمي: ماذا صار (إعلان/عرض/انتهاء) + الاستوديو + الموسم + الأسماء."""
    from .verticals import classify_anime

    v = classify_anime(article)
    extra = extra or {}
    lines: list[str] = []

    # الحدث في سطر واحد
    if v.event:
        ev = {"إعلان": "🆕 إعلان جديد", "عرض": "📺 موعد العرض",
              "انتهاء": "🏁 انتهاء/إيقاف", "موسم": "🔁 موسم جديد",
              "تريلر": "🎥 تريلر/مقطع دعائي", "طاقم": "🎙 طاقم العمل"}
        lines.append(ev.get(v.event, "🎬 خبر"))

    meta: list[str] = []
    if v.studios:
        meta.append("الاستوديو: " + "، ".join(v.studios[:2]))
    if v.format:
        meta.append("الصيغة: " + v.format)
    if v.season:
        meta.append("الموسم: " + v.season)
    if v.genres:
        meta.append("النوع: " + "، ".join(v.genres[:2]))
    if meta:
        lines.append(" · ".join(meta))

    # مقتطف من الخبر
    text = " ".join((article.summary or article.content or "").split())
    if text:
        lines.append("")
        lines.append(text[:300] + ("…" if len(text) > 300 else ""))
    elif not lines:
        lines.append(article.title[:200])

    if extra.get("glossary"):
        lines.append("")
        lines.append(extra["glossary"])
    if extra.get("source_line"):
        lines.append("")
        lines.append(f"<i>{extra['source_line']}</i>")
    return "\n".join(lines).strip()


def _age_minutes(article: Article) -> float:
    """عمر الخبر بالدقائق (سالب إن كان تاريخه في المستقبل/مجهولاً)."""
    if not article.published:
        return -1.0
    return (utcnow() - article.published).total_seconds() / 60.0


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
#  النشر الفوري اليدوي (Push-to-Channel)
# --------------------------------------------------------------------------- #
class ScopedPublisher:
    """نشر فوري من الجوال: قناة تيليجرام تعمل بنموذج «وكالة» (Push-to-Channel).

    أنشئ القناة من تطبيق تيليجرام → Manage Channel → Administrators → Post Messages،
    واجعلها **غير عامة** باسم مثل `AutoNews Urgent Scoped`. أي رسالة تنشرها هناك
    يلتقطها النظام فوراً ويُعيد صياغتها وينشرها على قنواتك الرسمية.

    لماذا تيليجرام فقط؟ لأن Facebook/X لا يوفّران قناة استقبال بهذه البساطة
    بلا موافقة تطبيق — وتيليجرام مصمَّم لهذا الغرض أصلاً.
    """

    TG_API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, bot_token: str, scoped_chat_id: str, target_channels: list[Channel],
                 settings, state, writer, dry_run: bool = False) -> None:
        self.bot_token = bot_token
        self.scoped_chat_id = scoped_chat_id
        self.targets = [c for c in target_channels if c.platform in ("telegram", "facebook", "x")]
        self.settings = settings
        self.state = state
        self.writer = writer
        self.dry_run = dry_run

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.scoped_chat_id)

    def _call(self, method: str, params: dict) -> dict:
        import json
        import urllib.parse
        import urllib.request

        url = (self.TG_API.format(token=self.bot_token, method=method)
               + "?" + urllib.parse.urlencode(params))
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            log.warning("فشل نداء تيليجرام (%s): %s", method, exc)
            return {}

    def flush(self, *, max_messages: int = 3, max_age_minutes: int = 60) -> list[str]:
        """سحب ما نشرتَه في القناة الخاصة ونشره فوراً على القنوات المستهدفة."""
        if not self.enabled:
            log.info("Push-to-Channel غير مهيّأ (TELEGRAM_SCOPED_CHAT_ID)")
            return []

        offset = int(self.state.usage.get("scoped_offset", 0))
        data = self._call("getUpdates", {"offset": offset + 1, "timeout": 0,
                                         "allowed_updates": '["channel_post"]'})
        published: list[str] = []
        for update in data.get("result", []) or []:
            offset = max(offset, int(update.get("update_id", 0)))
            msg = update.get("channel_post") or {}
            chat = (msg.get("chat") or {})
            if str(chat.get("id")) != str(self.scoped_chat_id):
                continue
            text = (msg.get("text") or msg.get("caption") or "").strip()
            if len(text) < 15:
                continue
            ts = int(msg.get("date", 0))
            age_minutes = (time.time() - ts) / 60 if ts else 0
            if max_age_minutes and age_minutes > max_age_minutes:
                log.info("تخطّي رسالة قديمة (%.0f دقيقة) في قناة الوكالة", age_minutes)
                continue
            if len(published) >= max_messages:
                break

            from .ingest import Article

            art = Article(source_id="scoped_editor", source_name="غرفة الأخبار",
                          title=text.splitlines()[0][:160], summary=text,
                          url="", published=utcnow())
            verdict = FlashVerdict(True, category="custom", score=99,
                                   reason="نشر يدوي فوري")
            for channel in self.targets:
                post = self.writer.write(art, channel, breaking=True)
                post.breaking = True
                from .publishers import build_publisher

                pub = build_publisher(channel, dry_run=self.dry_run,
                                      timeout=min(25, max(15, self.settings.request_timeout)))
                result = pub.publish(post, None)
                if result.ok:
                    published.append(f"{channel.name}: {post.title[:60]}")
                    self.state.record_flash(f"scoped:{ts}", channel.id, post.title,
                                            source="scoped_editor")
            # علامة «عالجنا» لتجنّب النشر المزدوج
            self.state.usage["scoped_offset"] = offset

        self.state.usage["scoped_offset"] = offset
        if published:
            log.info("📲 نُشر فوراً من قناة الوكالة: %d", len(published))
        return published
