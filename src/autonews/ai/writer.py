"""طبقة الصياغة: تحويل المقال الخام إلى منشور جاهز لكل منصّة.

تتضمّن:
- بناء الطلب حسب أسلوب القناة (مع دعم A/B) وحالة «عاجل».
- استخلاص JSON مع محاولة إصلاح تلقائية.
- قالب احتياطي (بدون AI) يضمن استمرار النشر.
- سلاسل (Threads) للمنصّات التي تدعمها مثل X.
- تطبيق التوقيع/المصدر/الهاشتاغات واحترام حدّ الأحرف لكل منصّة.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .prompts import BANNED_PHRASES

from ..config_loader import Channel
from ..ingest import Article
from ..utils import get_logger, human_age, strip_html, truncate
from .engine import KeyRotator
from .errors import AIUnavailable, NonRetryableError
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt

log = get_logger("autonews.ai.writer")

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class Post:
    """منشور نهائي جاهز للنشر على منصّة واحدة."""
    channel_id: str
    platform: str
    title: str
    body: str
    hashtags: list[str] = field(default_factory=list)
    category: str = "متنوّع"
    key_points: list[str] = field(default_factory=list)
    source_url: str = ""
    source_name: str = ""
    model: str = ""
    used_fallback: bool = False
    sensitive: bool = False
    variant: str = "A"
    breaking: bool = False
    article_fp: str = ""
    hook: str = ""              # أول جملة تخطف الانتباه
    reader_value: str = ""      # «لماذا يهمّك؟»
    is_update: bool = False     # تحديث على خبر سابق
    is_outcome: bool = False    # بطاقة نتائج (ما صار) لا حدث خام (ما وقع)

    @property
    def hashtags_line(self) -> str:
        return " ".join(h if h.startswith("#") else f"#{h}" for h in self.hashtags if h)

    # ---------------------------------------------------------------- التركيب
    def render(self, channel: Channel) -> str:
        """تركيب النص النهائي وفق قواعد القناة وحدودها."""
        parts: list[str] = []
        title = self.title
        if self.breaking and channel.platform != "x":
            if not title.startswith("🔴"):
                title = "🔴 عاجل | " + title
        elif self.is_update and channel.platform != "x":
            if not title.startswith(("🔄", "🔴")):
                title = "🔄 تحديث | " + title
        elif self.is_outcome and channel.platform != "x" and not title.startswith(("🔴", "🔄", "📋")):
            title = "📋 " + title

        if channel.platform == "x":
            head = title
            if self.body:
                head += "\n\n" + truncate(self.body, max(80, channel.max_chars - len(title) - 60))
            parts.append(head)
        else:
            if title:
                parts.append(f"🗞 {title}" if channel.add_emoji and not title.startswith("🔴") else title)
            if self.body:
                parts.append(self.body)
        if self.hashtags_line and channel.add_hashtags > 0:
            parts.append(self.hashtags_line)
        if channel.include_source_link and self.source_url:
            label = self.source_name or "المصدر"
            parts.append(f"🔗 المصدر: اقرأ الخبر كاملاً لدى {label}:\n{self.source_url}")
        sig = _signature(channel)
        if sig:
            parts.append(sig)

        text = "\n\n".join(p for p in parts if p and p.strip())
        return _finish(text, channel)

    def render_thread(self, channel: Channel) -> list[str]:
        """تقسيم المنشور إلى سلسلة تغريدات مترابطة (X)."""
        from ..utils import chunk_text

        budget = max(60, channel.max_chars - 12)   # مساحة للترقيم "1/3"
        headline = self.title
        if self.breaking:
            headline = headline if headline.startswith("🔴") else f"🔴 عاجل | {headline}"
        body_chunks = chunk_text(self.body, budget) if self.body else []
        points = [f"{i}. {p}" for i, p in enumerate(self.key_points[:4], 1)]

        if not body_chunks and not points:
            return [truncate(self.render(channel), channel.max_chars)]

        parts: list[str] = []
        parts.append(f"{headline}\n🧵 1/…")
        parts.extend(body_chunks[:3] or [])
        if points:
            parts.append("📌 أبرز النقاط:\n" + "\n".join(points))
        if self.hashtags_line:
            parts.append(self.hashtags_line)
        if channel.include_source_link and self.source_url:
            parts.append(f"🔗 {self.source_name or 'المصدر'}\n{self.source_url}")
        sig = _signature(channel)
        if sig:
            parts.append(sig)

        # إن كان كل المحتوى يُحتمل في تغريدة واحدة، لا داعي لسلسلة
        combined = "\n\n".join(p for p in parts if p)
        if len(combined) <= channel.max_chars:
            return [combined]

        # دمج الأجزاء القصيرة مع ما قبلها (هاشتاغات/مصدر منفردان = تغريدة ضعيفة)
        merged: list[str] = []
        for part in parts:
            if merged and len(part) < 90 and len(merged[-1]) + len(part) + 2 <= channel.max_chars:
                merged[-1] = f"{merged[-1]}\n\n{part}"
            else:
                merged.append(part)
        parts = merged

        total = len(parts)
        parts = [truncate(p, channel.max_chars) for p in parts]
        # ترقيم واضح لكل جزء
        numbered = []
        for i, part in enumerate(parts, 1):
            if i == 1:
                part = part.replace("🧵 1/…", f"🧵 1/{total}")
            else:
                part = f"({i}/{total}) {part}"
            numbered.append(truncate(part, channel.max_chars))
        # الختم يُضاف للتغريدة الأخيرة فقط (كي لا يتكرّر في كل جزء)
        if numbered:
            numbered[-1] = _finish(numbered[-1], channel, platform="x")
        return numbered

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel_id, "platform": self.platform, "title": self.title,
            "body": self.body, "hashtags": self.hashtags, "category": self.category,
            "key_points": self.key_points, "source_url": self.source_url,
            "model": self.model, "fallback": self.used_fallback,
            "variant": self.variant, "breaking": self.breaking,
            "article_fp": self.article_fp, "hook": self.hook,
            "reader_value": self.reader_value, "is_update": self.is_update,
            "source_name": self.source_name, "is_outcome": self.is_outcome,
            "sensitive": self.sensitive,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Post":
        """استعادة منشور من الحالة (لطابور الإعادة/المراجعة)."""
        return cls(
            channel_id=str(data.get("channel", "")), platform=str(data.get("platform", "")),
            title=str(data.get("title", "")), body=str(data.get("body", "")),
            hashtags=list(data.get("hashtags") or []), category=str(data.get("category", "متنوّع")),
            key_points=list(data.get("key_points") or []), source_url=str(data.get("source_url", "")),
            source_name=str(data.get("source_name", "")), model=str(data.get("model", "")),
            used_fallback=bool(data.get("fallback")), sensitive=bool(data.get("sensitive")),
            variant=str(data.get("variant", "A")), breaking=bool(data.get("breaking")),
            article_fp=str(data.get("article_fp", "")),
            hook=str(data.get("hook", "")), reader_value=str(data.get("reader_value", "")),
            is_update=bool(data.get("is_update")), is_outcome=bool(data.get("is_outcome")),
        )


# --------------------------------------------------------------------------- #
#  الختم (بصمة الملكية) — نقطة واحدة لكل منصّة
# --------------------------------------------------------------------------- #
def _signature(channel: Channel) -> str:
    """التوقيع القديم (`signature`) — يُلغى تلقائياً إن كانت بصمة الملكية مفعّلة.

    السبب: اجتماعهما يُنتج توقيعين متتاليين («تابعونا…» ثم «📡 @قناتك»)
    وهو تكرار يبدو خطأً مطبعياً. البصمة الأقوى (وفيها المعرّف) تفوز.
    """
    if not (channel.include_signature and channel.signature):
        return ""
    from ..branding import Branding   # استيراد مؤجّل

    return "" if Branding.from_channel(channel).ready else channel.signature


def _finish(text: str, channel: Channel, *, platform: str = "") -> str:
    """تركيب التوقيع المرئي + العلامة الخفيّة ثم قصّ النصّ لحدّ المنصّة."""
    from ..branding import Branding, apply_branding   # استيراد مؤجّل

    brand = Branding.from_channel(channel)
    if not brand.enabled:
        return truncate(text, channel.max_chars, ellipsis="…")
    return apply_branding(text, brand, platform=platform or channel.platform,
                          max_chars=channel.max_chars)


# --------------------------------------------------------------------------- #
#  تنظيف الأسلوب: إزالة الكلمات الميتة وتكرار العنوان
# --------------------------------------------------------------------------- #
def polish_body(text: str, *, title: str = "", max_words: int = 26) -> str:
    """تنظيف لغوي وأسلوبي يجعل المنشور مقروءاً على شاشة الهاتف.

    1) يحذف العبارات الميتة («جدير بالذكر»، «في سياق متصل»…).
    2) يمنع تكرار العنوان في أول الجسم.
    3) يقصّ التمهيد الممل («في تطور جديد، …»).
    4) **يقسّم الجمل الطويلة إلى أسطر قصيرة** — أهم أداة ضد الإحساس بالملل:
       الجملة التي تتجاوز 26 كلمة تُقسَّم عند الفواصل إلى أسطر منفصلة.
    """
    if not text:
        return ""
    body = " ".join(text.split())

    removed_at_start = False
    for phrase in BANNED_PHRASES:
        pattern = rf"[،؛]?\s*{re.escape(phrase)}\s*[،:؛]?\s*"
        if re.match(pattern, body, flags=re.I):
            removed_at_start = True
            body = re.sub(pattern, "", body, flags=re.I, count=1)
        else:
            body = re.sub(pattern, "، ", body, flags=re.I)

    # إن حُذفت عبارة من بداية النص، أزل أدوات الربط المعلّقة («أن»، «إن»، «كما»، «و»)
    if removed_at_start:
        body = re.sub(r"^(?:أن|إن|كما|و|ثم|ف|لقد|قد)\s+", "", body.strip())
        body = body[:1].upper() + body[1:] if body else body

    # إزالة تكرار العنوان في أول الجسم
    if title:
        head = body[: len(title) + 30]
        if head.startswith(title) or (title in head and head.index(title) < 10):
            body = body.replace(title, "", 1).lstrip(" :–-—،.")

    # التمهيد الممل
    body = re.sub(r"^(?:في (?:تطور|خطوة|سياق|إطار)[^،.]{0,40}[،.]\s*)", "", body)
    body = re.sub(r"[!]{2,}", "!", body)
    body = re.sub(r"\s+([،.؛:])", r"\1", body)
    body = re.sub(r"\s{2,}", " ", body).strip()

    # ---- تقسيم الجمل الطويلة إلى أسطر قصيرة ----
    sentences = [x.strip() for x in re.split(r"(?<=[.!؟?])\s+", body) if x.strip()]
    lines: list[str] = []
    for sent in sentences:
        if len(sent.split()) <= max_words:
            lines.append(sent)
            continue
        chunk: list[str] = []
        for part in re.split(r"\s*[،؛]\s*", sent):
            part = part.strip()
            if not part:
                continue
            if len(" ".join(chunk + [part]).split()) > max_words and chunk:
                lines.append(" ".join(chunk).rstrip("،؛") + "،")
                chunk = [part]
            else:
                chunk.append(part)
        if chunk:
            lines.append(" ".join(chunk))
    return "\n".join(x for x in lines if x).strip()


def ensure_engaging(text: str, *, platform: str) -> str:
    """إضافة سؤال تفاعل واحد للمنصّات الاجتماعية إن غاب (بلا افتعال)."""
    if platform not in ("facebook", "x"):
        return text
    tail = text.strip()[-80:].lower()
    if "؟" in tail or "?" in tail:
        return text
    question = ("ما رأيك بهذا التطور؟" if platform == "facebook"
                else "رأيك؟")
    return f"{text.rstrip()}\n\n{question}"


# --------------------------------------------------------------------------- #
#  تنظيف/تحليل مخرجات النموذج
# --------------------------------------------------------------------------- #
def extract_json(raw: str) -> dict[str, Any] | None:
    """محاولة استخلاص JSON من نص قد يحتوي شرحاً أو أسوار كود."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJ_RE.search(text)
    if not match:
        return None
    candidate = re.sub(r",\s*([}\]])", r"\1", match.group(0))
    candidate = candidate.replace("“", '"').replace("”", '"')
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_hashtags(values: Any, limit: int) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        tag = value.strip().lstrip("#").replace(" ", "_")
        tag = re.sub(r"[^\w\u0600-\u06FF_]", "", tag)
        if not tag or len(tag) > 30:
            continue
        if tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
        if len(out) >= limit:
            break
    return out


def _normalize_points(values: Any, limit: int = 4) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    points = []
    for value in values:
        if not isinstance(value, str):
            continue
        clean = " ".join(strip_html(value).split())
        if clean:
            points.append(truncate(clean, 110))
        if len(points) >= limit:
            break
    return points


# --------------------------------------------------------------------------- #
#  الصياغة
# --------------------------------------------------------------------------- #
class ContentWriter:
    """يحوّل المقالات إلى منشورات."""

    def __init__(self, engine: KeyRotator | None, settings, state=None) -> None:
        self.engine = engine
        self.settings = settings
        self.state = state          # اختياري: لتسجيل ميزانية التوكنات و A/B

    def _next_variant(self, channel: Channel) -> str:
        if not channel.style_b or self.state is None:
            return "A"
        try:
            return self.state.next_variant(channel.id)
        except Exception:  # noqa: BLE001
            return "A"

    def write(self, article: Article, channel: Channel, *, breaking: bool = False,
              context_hint: str = "", is_update: bool = False,
              previous_title: str = "") -> Post:
        """صياغة منشور واحد لقناة واحدة."""
        variant = self._next_variant(channel)

        if self.engine is None or not getattr(self.engine, "keys", []):
            log.warning("لا يوجد محرّك AI متاح — صياغة احتياطية للقناة %s", channel.id)
            return self._fallback_post(article, channel, reason="no_keys", breaking=breaking,
                                       is_update=is_update)

        prompt = build_user_prompt(article, channel, variant=variant, breaking=breaking,
                                   context_hint=context_hint, is_update=is_update,
                                   previous_title=previous_title)
        ai = self.settings.ai if hasattr(self.settings, "ai") else self.settings
        try:
            result = self.engine.generate(
                prompt, system=SYSTEM_PROMPT, temperature=ai.temperature,
                max_output_tokens=ai.max_output_tokens, json_response=True,
            )
        except AIUnavailable as exc:
            log.error("لا مفاتيح/نماذج متاحة الآن: %s", exc)
            return self._fallback_post(article, channel, reason="ai_unavailable", breaking=breaking)
        except NonRetryableError as exc:
            log.warning("رفض المحتوى من النموذج (%s) — استخدام القالب الاحتياطي", exc)
            return self._fallback_post(article, channel, reason="blocked", breaking=breaking)

        usage = result.get("usage") or {}
        if self.state is not None:
            self.state.add_usage(
                calls=1,
                tokens=int(usage.get("prompt") or 0) + int(usage.get("output") or 0),
            )

        data = extract_json(result["text"])
        if data is None:
            log.warning("استجابة غير JSON — محاولة إصلاح تلقائية")
            try:
                repair = self.engine.generate(
                    build_repair_prompt(result["text"]),
                    system="أنت مصحّح JSON. أخرج JSON صالحاً فقط.",
                    temperature=0.1, max_output_tokens=1200, json_response=True,
                )
                if self.state is not None:
                    self.state.add_usage(calls=1)
                data = extract_json(repair["text"])
            except (AIUnavailable, NonRetryableError) as exc:
                log.warning("فشل الإصلاح التلقائي: %s", exc)
        if data is None:
            log.warning("تعذّر الحصول على JSON صالح — قالب احتياطي")
            return self._fallback_post(article, channel, reason="bad_json", breaking=breaking)

        title = " ".join(strip_html(str(data.get("title") or article.title)).split())
        body = strip_html(str(data.get("body") or data.get("content") or ""))
        if len(body) < 40:      # مخرَج ضعيف جداً
            log.warning("جسم المنشور قصير جداً — استخدام القالب الاحتياطي")
            return self._fallback_post(article, channel, reason="short_body",
                                       breaking=breaking, is_update=is_update)

        # تنظيف الأسلوب + ضمان وجود خطّاف + سؤال تفاعل عند الحاجة
        body = polish_body(body, title=title)
        hook = " ".join(str(data.get("hook") or "").split())[:120]
        reader_value = " ".join(str(data.get("reader_value") or "").split())[:200]
        if not hook and body:
            hook = " ".join(body.split()[:10])
        if channel.platform in ("facebook", "x"):
            body = ensure_engaging(body, platform=channel.platform)

        post = Post(
            channel_id=channel.id, platform=channel.platform,
            title=truncate(title, 160), body=body.strip(),
            hashtags=_normalize_hashtags(data.get("hashtags"), channel.add_hashtags),
            category=str(data.get("category") or "متنوّع").strip(),
            key_points=_normalize_points(data.get("key_points")),
            source_url=article.url, source_name=article.source_name,
            model=result.get("model", ""), sensitive=bool(data.get("sensitive")),
            variant=variant, breaking=breaking, article_fp=article.fp,
            hook=hook, reader_value=reader_value, is_update=is_update,
        )
        if not post.hashtags and channel.add_hashtags > 0:
            post.hashtags = self._fallback_hashtags(article, channel)
        return post

    # ------------------------------------------------------------ الاحتياطي
    def _fallback_hashtags(self, article: Article, channel: Channel) -> list[str]:
        tags = [t for t in (channel.tags or article.tags) if t and len(t) < 25]
        defaults = ["أخبار", "عاجل", "متابعة"]
        result: list[str] = []
        for tag in tags + defaults:
            clean = re.sub(r"[^\w\u0600-\u06FF]", "_", str(tag)).strip("_")
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= channel.add_hashtags:
                break
        return result

    def _fallback_post(self, article: Article, channel: Channel, *, reason: str,
                       breaking: bool = False, is_update: bool = False) -> Post:
        summary = article.summary or article.content or article.title
        summary = " ".join(strip_html(summary).split())
        # القالب الاحتياطي يلتزم بنفس اختصار الصياغة الذكية:
        # ثلاث جمل كحد أقصى و420 حرفاً، بلا حشو أو توقيت يستهلك مساحة الخبر.
        sentences = re.split(r"(?<=[.!؟。])\s+", summary)
        summary = " ".join(part.strip() for part in sentences[:3] if part.strip())
        body = truncate(summary, min(420, max(180, int(channel.max_chars * 0.55))))
        return Post(
            channel_id=channel.id, platform=channel.platform,
            title=truncate(article.title, 150), body=body,
            hashtags=self._fallback_hashtags(article, channel), category="متنوّع",
            source_url=article.url, source_name=article.source_name,
            model=f"fallback:{reason}", used_fallback=True,
            breaking=breaking, article_fp=article.fp,
            hook=" ".join((article.title or "").split()[:10]),
            is_update=is_update,
        )

    def write_batch(self, article: Article, channels: list[Channel], *,
                    breaking: bool = False, context_hint: str = "",
                    is_update: bool = False, previous_title: str = "") -> dict[str, Post]:
        return {
            ch.id: self.write(article, ch, breaking=breaking, context_hint=context_hint,
                              is_update=is_update, previous_title=previous_title)
            for ch in channels
        }

    # --------------------------------------------------------- بطاقة تهنئة
    def greeting(self, occasion, channel: Channel) -> Post | None:
        """صياغة بطاقة تهنئة لمناسبة رسمية (تُنشر مرّة واحدة)."""
        from .prompts import build_greeting_prompt

        if not getattr(occasion, "greeting", ""):
            return None
        if self.engine is None or not getattr(self.engine, "keys", []):
            return Post(channel_id=channel.id, platform=channel.platform,
                        title=occasion.name, body=occasion.greeting,
                        hashtags=[occasion.name.replace(" ", "_")],
                        model="fallback:greeting")
        try:
            result = self.engine.generate(
                build_greeting_prompt(occasion.name, occasion.tone,
                                      occasion.style_hint, channel),
                system="أنت كاتب بطاقات تهنئة عربي موجز ودافئ، بلا خطابة.",
                temperature=0.8, max_output_tokens=400, json_response=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("تعذّر توليد بطاقة التهنئة: %s", exc)
            return Post(channel_id=channel.id, platform=channel.platform,
                        title=occasion.name, body=occasion.greeting,
                        model="fallback:greeting")
        data = extract_json(result.get("text", "")) or {}
        body = " ".join(str(data.get("body") or occasion.greeting).split())
        return Post(channel_id=channel.id, platform=channel.platform,
                    title=str(data.get("title") or occasion.name), body=body,
                    hashtags=[occasion.name.replace(" ", "_")],
                    model=result.get("model", ""), category="مجتمع")
