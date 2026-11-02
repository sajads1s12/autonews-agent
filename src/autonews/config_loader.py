"""تحميل والتحقّق من إعدادات المشروع (config/settings.yml + config/channels.yml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .utils import env_str, get_logger, split_env_list

log = get_logger("autonews.config")


# --------------------------------------------------------------------------- #
#  النماذج (Dataclasses)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Source:
    """مصدر خبري (RSS / Atom)."""
    id: str
    name: str
    url: str
    type: str = "rss"
    lang: str = "ar"
    weight: float = 1.0
    trust: float = 1.0          # مصداقية المصدر (ترجيح عند تعارض الأنباء)
    official: bool = False      # مصدر رسمي/حكومي (أولوية أعلى وأسرع)
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    max_items: int = 10


@dataclass(slots=True)
class Channel:
    """قناة نشر (تيليجرام / فيسبوك / X / اختبار)."""
    id: str
    name: str
    platform: str
    description: str = ""
    enabled: bool = True
    # توجيه
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)   # وسوم تُستثنى (فصل القطاعات)
    keywords: list[str] = field(default_factory=list)
    # أسلوب الصياغة
    style: str = ""
    style_b: str = ""            # أسلوب بديل لاختبار A/B
    language: str = "ar"
    max_chars: int = 4096
    add_hashtags: int = 4
    add_emoji: bool = True
    include_source_link: bool = True
    include_signature: bool = True
    signature: str = ""
    # سلوك النشر
    max_posts_per_run: int = 3
    max_chars_per_run: int = 0   # 0 = بلا حد
    breaking_boost: bool = False # السماح بتجاوز السقف للأخبار العاجلة
    with_image: bool = True      # إرفاق صورة الخبر (إن وُجدت)
    review_required: bool = False  # إرسال مسوّدة للموافقة قبل النشر
    threads: bool = False        # تقسيم الخبر الطويل إلى سلسلة (X)
    flash_eligible: bool = True   # مؤهّلة للمسار السريع (النشر الفوري)
    flash_priority: int = 0       # أولوية داخل المسار السريع (الأعلى أولاً)
    # اعتمادات (أسماء متغيّرات البيئة)
    credentials: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def env(self, key: str, default: str = "") -> str:
        """قراءة اعتماد بثلاث أولويات:

            ١) `state/channels_setup.json` — ما ضبطته من البوت (الأعلى)
            ٢) `extra.ENV_<KEY>`           — ما تكتبه أداة chatid
            ٣) متغيّر البيئة               — GitHub Secrets

        لماذا؟ المعرّف يُضبط من تيليجرام بنقرة (بلا تعديل ملفات ولا دفع)،
        بينما يبقى التوكن سرّاً في متغيّرات البيئة.
        """
        if key == "chat_id":
            from .setup_store import SetupStore

            stored = _setup_bindings().get(self.id, "")
            if stored:
                return stored
        override = self.extra.get(f"ENV_{key.upper()}")
        if override:
            return str(override)
        env_name = self.credentials.get(key, "")
        return env_str(env_name, default) if env_name else default


# معرّفات مضبوطة من البوت (تُقرأ مرّة لكل تحميل إعدادات)
_SETUP_CACHE: dict[str, str] = {}


def _setup_bindings(path: str = "state/channels_setup.json") -> dict[str, str]:
    """معرّفات القنوات المحفوظة من البوت (مع تخزين مؤقّت خفيف)."""
    global _SETUP_CACHE
    try:
        from .setup_store import SetupStore

        store = SetupStore.load(path)
        _SETUP_CACHE = {cid: b.chat_id for cid, b in store.bindings.items()
                        if b.verified and b.chat_id}
    except Exception:  # noqa: BLE001 — لا تُفشل التحميل
        pass
    return _SETUP_CACHE


_BRAND_CACHE: dict[str, dict[str, Any]] = {}


def _brand_bindings(path: str = "state/channels_setup.json") -> dict[str, dict[str, Any]]:
    """بصمات ضبطها المستخدم من البوت (handle/العلامة المائية) — تتقدّم على الملف."""
    global _BRAND_CACHE
    try:
        from .setup_store import SetupStore

        store = SetupStore.load(path)
        _BRAND_CACHE = {
            cid: {"handle": b.handle, **({"title": b.title} if b.title else {})}
            for cid, b in store.bindings.items() if b.handle
        }
    except Exception:  # noqa: BLE001 — لا تُفشل التحميل
        pass
    return _BRAND_CACHE


@dataclass(slots=True)
class AISettings:
    provider: str = "gemini"
    keys: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    temperature: float = 0.75
    max_output_tokens: int = 2048
    request_timeout: int = 90
    max_retries_per_pair: int = 1
    cooldown_quota_seconds: int = 900
    cooldown_error_seconds: int = 60
    max_items_per_run: int = 12
    token_budget_per_run: int = 0   # 0 = بلا حد


@dataclass(slots=True)
class AlertSettings:
    enabled: bool = True
    bot_token: str = ""
    chat_id: str = ""
    on_zero_runs: int = 2          # نبّه إذا تكرّر تشغيل بلا نشر
    on_errors: bool = True
    daily_summary: bool = True


@dataclass(slots=True)
class QualitySettings:
    require_numbers_match: bool = True
    min_body_chars: int = 90
    max_title_chars: int = 160
    block_sensitive: bool = False
    max_hashtags: int = 6


@dataclass(slots=True)
class ImageSettings:
    enabled: bool = True
    timeout: int = 12
    max_bytes: int = 5_000_000
    download_captions: bool = True


@dataclass(slots=True)
class ReviewSettings:
    enabled: bool = False
    timeout_hours: int = 6          # بعدها يُلغى المسوّد تلقائياً
    max_per_run: int = 5
    auto_approve_if_no_operator: bool = False


@dataclass(slots=True)
class ControlSettings:
    """🎛 لوحة التحكّم: مراجعة ذكية · ساعات هدوء · فاصل بين المنشورات.

    `review_mode` هو الافتراضي لكل قناة (يمكن تجاوزه في القناة نفسها من البوت):
      smart  = لا تُراجع إلّا ما يشكّ فيه النظام (مستحسن)
      always = كل منشور يحتاج موافقة
      never  = نشر مباشر بلا أسئلة
    """
    enabled: bool = True
    review_mode: str = "smart"
    min_score: float = 0.85            # تحت هذه الدرجة → مراجعة (النمط الذكي)
    quiet_hours_start: int = -1        # -1 = معطّل · 22 = من 22:00
    quiet_hours_end: int = -1          # 6 = حتى 06:00
    breaking_bypass_quiet: bool = True # العاجل لا ينتظر ساعات الهدوء أبداً
    min_gap_minutes: int = 0           # فاصل أدنى بين منشورين في القناة نفسها
    max_per_hour: int = 0              # سقف الساعة لكل قناة (0 = بلا حد)
    queue_max_hours: float = 48.0      # حذف ما تجمّد في الطابور بعد كذا ساعة
    notify_approvals: bool = True      # إرسال ما يحتاج موافقة إلى الإدارة


@dataclass(slots=True)
class BreakingSettings:
    enabled: bool = True
    keywords: list[str] = field(default_factory=list)
    bypass_caps: bool = True
    priority: int = 10
    prefix: str = "🔴 عاجل"
    max_per_run: int = 2


@dataclass(slots=True)
class RetrySettings:
    enabled: bool = True
    max_attempts: int = 3
    per_run: int = 3


@dataclass(slots=True)
class FlashSettings:
    """المسار السريع: نشر الأخبار الضرورية في دقيقتها."""
    enabled: bool = True
    max_age_minutes: int = 45        # لا نشر فوري لخبر أقدم من كذا دقيقة
    min_score: int = 5               # عتبة التصنيف

    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    max_per_hour: int = 6            # سقف لكل قناة/ساعة
    min_gap_seconds: int = 25        # فاصل أدنى بين خبرين
    max_items_per_run: int = 3
    max_output_tokens: int = 700     # توكنات أقل = استجابة أسرع

    notify_telegram: bool = True     # تنبيه إداري عند كل نشر فوري
    scoped_chat_id: str = ""         # قناة «وكالة» للنشر اليدوي الفوري


@dataclass(slots=True)
class OutcomeSettings:
    """نمط النتائج: نشر ما نتج عن الحدث لا الحدث نفسه."""
    enabled: bool = True
    policy: str = "outcomes_only"        # outcomes_only | hybrid | raw
    allow_incident_headlines: bool = False   # نشر عنوان الحدث الخام (فقط في hybrid)
    governorate_matrix: bool = True      # بناء جدول «أي محافظة مشمولة/مستثناة»
    min_outcome_chars: int = 40
    # ---- مميزات v6 ----
    impact_line: bool = True             # سطر «الأثر عليك» في كل بطاقة نتيجة
    followup: bool = True                # متابعة الحادثة المؤجّلة حتى تصدر نتيجتها
    followup_min_hours: float = 1.0      # أقل عمر قبل اعتبار الخبر «تطوّراً»
    followup_max_items: int = 60         # سقف الحوادث المتابَعة (يحفظ حجم الحالة)
    followup_max_age_hours: float = 72.0  # بعدها تُحذف الحادثة (لا متابعة أبدية)
    daily_matrix: bool = True            # منشور «خريطة المحافظات» اليومي
    daily_matrix_hour: int = 20          # الساعة المحلية (0-23) التي يصدر فيها
    contrast: bool = True                # كشف تفاوت المحافظات كخبر مستقل
    dual_verify: bool = False            # لا تُنشر النتيجة إلا بمصدر رسمي أو مصدرين
    source_line: bool = True             # سطر الشفافية (المصدر + الثقة + العمر)
    silent_hours: float = 6.0            # حادثة بلا نتائج بعد X ساعة ⇒ تنبيه إداري
    silent_enabled: bool = True


@dataclass(slots=True)
class TranslationSettings:
    """تعريب المصادر الأجنبية."""
    enabled: bool = True
    target: str = "ar"
    langs: list[str] = field(default_factory=lambda: ["en", "fr", "tr"])
    max_per_run: int = 5             # سقف التعريب لكل دورة (حفظاً للحصة)
    keep_original_name: bool = True  # إبقاء الاسم الأصلي بين قوسين
    require_numbers_match: bool = True


@dataclass(slots=True)
class AnimeSettings:
    """عمود الأنمي: نفس المحرّك، سياسة نشر مختلفة."""
    enabled: bool = True
    min_score: float = 3.0
    publish_policy: str = "announcement"   # announcement | outcomes | raw
    keep_original_names: bool = True
    append_glossary: bool = True
    translate: bool = True
    skip_incident_gate: bool = True        # لا يمرّ على بوّابة «حدث بلا نتائج»
    genre_tag_limit: int = 2


@dataclass(slots=True)
class VerticalSettings:
    """كل القطاعات المتخصّصة."""
    anime: AnimeSettings = field(default_factory=AnimeSettings)

    def enabled_tags(self) -> set[str]:
        """الوسوم التي تُشغّل قطاعاً مفعّلاً (للتوجيه)."""
        tags: set[str] = set()
        if self.anime.enabled:
            tags.add("أنمي")
        return tags


@dataclass(slots=True)
class LinksSettings:
    """روابط UTM القابلة للقياس."""
    enabled: bool = True
    source_override: str = ""
    medium: str = "social"
    campaign_prefix: str = "autonews"
    keep_existing: bool = True


@dataclass(slots=True)
class DashboardSettings:
    """لوحة الأداء HTML + أفضل أسبوع."""
    enabled: bool = True
    output_file: str = "logs/dashboard.html"
    best_of_file: str = "logs/best_of_week.md"
    days: int = 7
    top_n: int = 10


@dataclass(slots=True)
class OccasionSettings:
    enabled: bool = True
    config_file: str = "config/occasions.yml"
    greetings: bool = False
    tz_offset: int = 3


@dataclass(slots=True)
class CrawlSettings:
    """إعدادات التصفّح الآلي."""
    enrich_articles: bool = True      # زيارة صفحة الخبر لإكمال المتن الناقص
    autodiscover: bool = True         # البحث التلقائي عن الخلاصة عند فشلها
    max_retries: int = 2
    respect_robots: bool = True
    fetch_budget_seconds: int = 240     # سقف زمني لمرحلة السحب (حماية من تجاوز مهلة Actions)
    max_enrich_per_source: int = 2      # أقصى عدد صفحات تُزار لكل مصدر لإكمال المتن


@dataclass(slots=True)
class Settings:
    timezone_offset: int = 3
    log_level: str = "INFO"
    max_items_per_run: int = 12
    max_per_source: int = 5
    max_age_hours: int = 48
    min_title_chars: int = 18
    retry_failed_after_runs: int = 3
    state_file: str = "state/state.json"
    state_commit: bool = True
    dry_run: bool = False
    dedup_similarity: float = 0.80
    cross_run_dedup: bool = True
    cross_run_lookback: int = 150
    cross_run_similarity: float = 0.62
    request_timeout: int = 30
    user_agent: str = "AutoNewsAgent/1.0 (+https://github.com/)"
    ai: AISettings = field(default_factory=AISettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    quality: QualitySettings = field(default_factory=QualitySettings)
    images: ImageSettings = field(default_factory=ImageSettings)
    review: ReviewSettings = field(default_factory=ReviewSettings)
    control: ControlSettings = field(default_factory=ControlSettings)
    breaking: BreakingSettings = field(default_factory=BreakingSettings)
    retry: RetrySettings = field(default_factory=RetrySettings)
    occasions: OccasionSettings = field(default_factory=OccasionSettings)
    crawl: CrawlSettings = field(default_factory=CrawlSettings)
    flash: FlashSettings = field(default_factory=FlashSettings)
    outcomes: OutcomeSettings = field(default_factory=OutcomeSettings)
    translation: TranslationSettings = field(default_factory=TranslationSettings)
    links: LinksSettings = field(default_factory=LinksSettings)
    verticals: VerticalSettings = field(default_factory=VerticalSettings)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)
    sources: list[Source] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)

    @property
    def enrich_articles(self) -> bool:
        return self.crawl.enrich_articles

    @property
    def ai_ns(self) -> SimpleNamespace:
        """واجهة متوافقة مع المكوّنات التي تتوقّع settings.ai.*"""
        return SimpleNamespace(**{f: getattr(self.ai, f) for f in self.ai.__slots__})


class ConfigError(RuntimeError):
    """خطأ في الإعدادات يمنع التشغيل الآمن."""


# --------------------------------------------------------------------------- #
#  أدوات مساعدة
# --------------------------------------------------------------------------- #
def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"ملف الإعدادات غير موجود: {path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("مكتبة PyYAML غير مثبّتة. نفّذ: pip install -r requirements.txt") from exc
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"محتوى الملف غير صالح (يجب أن يكون قاموساً): {path}")
    return data


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return split_env_list(value)
    return [value]


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "نعم")


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
#  التحميل
# --------------------------------------------------------------------------- #
def _merge_branding(item: dict) -> dict:
    """دمج بصمة الملف مع ما ضبطه المستخدم من البوت (البوت يتقدّم)."""
    raw = dict(item.get("branding") or {})
    from_bot = _brand_bindings().get(str(item.get("id", "")), {})
    if from_bot:
        raw.update({k: v for k, v in from_bot.items() if v})
    return raw


def load_config(config_dir: str | Path = "config", *, override_dry_run: bool | None = None) -> Settings:
    """تحميل الإعدادات من مجلد config مع دعم تجاوزات البيئة (ENV)."""
    base = Path(config_dir)
    raw = _load_yaml(base / "settings.yml")
    raw_channels = _load_yaml(base / "channels.yml")

    ai_raw = _section(raw, "ai")
    run_raw = _section(raw, "run")

    # ---- المفاتيح: GEMINI_API_KEYS أو GEMINI_API_KEY_1..10 ----
    keys = split_env_list(env_str("GEMINI_API_KEYS"))
    if not keys:
        for i in range(1, 11):
            key = env_str(f"GEMINI_API_KEY_{i}")
            if key:
                keys.append(key)
    if not keys:
        single = env_str("GEMINI_API_KEY")
        if single:
            keys.append(single)

    models = split_env_list(env_str("GEMINI_MODELS")) or [str(m) for m in _as_list(ai_raw.get("models"))]

    ai = AISettings(
        provider=str(ai_raw.get("provider", "gemini")),
        keys=keys,
        models=models,
        temperature=float(ai_raw.get("temperature", 0.75)),
        max_output_tokens=int(ai_raw.get("max_output_tokens", 2048)),
        request_timeout=int(ai_raw.get("request_timeout", 90)),
        max_retries_per_pair=int(ai_raw.get("max_retries_per_pair", 1)),
        cooldown_quota_seconds=int(ai_raw.get("cooldown_quota_seconds", 900)),
        cooldown_error_seconds=int(ai_raw.get("cooldown_error_seconds", 60)),
        max_items_per_run=int(ai_raw.get("max_items_per_run", 12)),
        token_budget_per_run=int(ai_raw.get("token_budget_per_run", 0)),
    )

    # ---- المصادر ----
    sources: list[Source] = []
    for item in _as_list(raw.get("sources")):
        if not isinstance(item, dict) or not item.get("url"):
            log.warning("تم تجاهل مصدر غير صالح: %s", item)
            continue
        sid = str(item.get("id") or item.get("name") or f"src{len(sources) + 1}").strip()
        sources.append(Source(
            id=sid,
            name=str(item.get("name") or sid),
            url=str(item["url"]).strip(),
            type=str(item.get("type", "rss")).lower(),
            lang=str(item.get("lang", "ar")),
            weight=float(item.get("weight", 1.0)),
            trust=float(item.get("trust", 1.0)),
            official=_as_bool(item.get("official"), False),
            tags=[str(t).lower() for t in _as_list(item.get("tags"))],
            enabled=_as_bool(item.get("enabled"), True),
            max_items=int(item.get("max_items", 10)),
        ))
    sources = [s for s in sources if s.enabled]
    if not sources:
        raise ConfigError("لا يوجد أي مصدر مفعّل في config/settings.yml")
    _check_unique([s.id for s in sources], "معرّفات المصادر")

    # ---- القنوات ----
    channels: list[Channel] = []
    for item in _as_list(raw_channels.get("channels")):
        if not isinstance(item, dict) or not item.get("id"):
            log.warning("تم تجاهل قناة غير صالحة: %s", item)
            continue
        platform = str(item.get("platform", "")).strip().lower()
        if platform not in ("telegram", "facebook", "x", "twitter", "console"):
            raise ConfigError(f"منصّة غير مدعومة في القناة '{item.get('id')}': {platform}")
        if platform == "twitter":
            platform = "x"
        channels.append(Channel(
            id=str(item["id"]).strip(),
            name=str(item.get("name") or item["id"]),
            platform=platform,
            enabled=_as_bool(item.get("enabled"), True),
            sources=[str(s).lower() for s in _as_list(item.get("sources"))] or ["*"],
            tags=[str(t).lower() for t in _as_list(item.get("tags"))],
            exclude_tags=[str(t).lower() for t in _as_list(item.get("exclude_tags"))],
            keywords=[str(k).lower() for k in _as_list(item.get("keywords"))],
            style=str(item.get("style") or "").strip(),
            style_b=str(item.get("style_b") or "").strip(),
            language=str(item.get("language", "ar")),
            max_chars=int(item.get("max_chars", 4096)),
            add_hashtags=int(item.get("add_hashtags", 4)),
            add_emoji=_as_bool(item.get("add_emoji"), True),
            include_source_link=_as_bool(item.get("include_source_link"), True),
            include_signature=_as_bool(item.get("include_signature"), True),
            signature=str(item.get("signature", "")),
            max_posts_per_run=int(item.get("max_posts_per_run", 3)),
            max_chars_per_run=int(item.get("max_chars_per_run", 0)),
            breaking_boost=_as_bool(item.get("breaking_boost"), False),
            with_image=_as_bool(item.get("with_image"), True),
            review_required=_as_bool(item.get("review_required"), False),
            threads=_as_bool(item.get("threads"), False),
            flash_eligible=_as_bool(item.get("flash_eligible"), True),
            flash_priority=int(item.get("flash_priority", 0)),
            credentials={str(k): str(v) for k, v in (item.get("credentials") or {}).items()},
            # العلامة التجارية تُحفظ داخل extra كي لا يلمسها أي كود آخر
            extra={**({k: v for k, v in item.items()
                       if k in ("disable_web_page_preview", "parse_mode")}),
                   **({"branding": _merge_branding(item)} if _merge_branding(item) else {}),
                   **dict(item.get("extra") or {})},
        ))
    channels = [c for c in channels if c.enabled]
    if not channels:
        raise ConfigError("لا توجد قناة نشر مفعّلة في config/channels.yml")
    _check_unique([c.id for c in channels], "معرّفات القنوات")

    known = {s.id for s in sources}
    for ch in channels:
        for sid in ch.sources:
            if sid != "*" and sid not in known:
                log.warning("القناة '%s' تشير إلى مصدر غير معروف: %s (سيُتجاهل)", ch.id, sid)
        ch.sources = [s for s in ch.sources if s == "*" or s in known] or ["*"]

    # ---- الأقسام الإضافية ----
    alerts_raw = _section(raw, "alerts")
    alerts = AlertSettings(
        enabled=_as_bool(alerts_raw.get("enabled"), True),
        bot_token=env_str("ALERT_BOT_TOKEN") or env_str("TELEGRAM_BOT_TOKEN"),
        chat_id=env_str("ALERT_CHAT_ID") or env_str("TELEGRAM_ADMIN_CHAT_ID") or env_str("TELEGRAM_CHAT_ID"),
        on_zero_runs=int(alerts_raw.get("on_zero_runs", 2)),
        on_errors=_as_bool(alerts_raw.get("on_errors"), True),
        daily_summary=_as_bool(alerts_raw.get("daily_summary"), True),
    )

    q_raw = _section(raw, "quality")
    quality = QualitySettings(
        require_numbers_match=_as_bool(q_raw.get("require_numbers_match"), True),
        min_body_chars=int(q_raw.get("min_body_chars", 90)),
        max_title_chars=int(q_raw.get("max_title_chars", 160)),
        block_sensitive=_as_bool(q_raw.get("block_sensitive"), False),
        max_hashtags=int(q_raw.get("max_hashtags", 6)),
    )

    i_raw = _section(raw, "images")
    images = ImageSettings(
        enabled=_as_bool(i_raw.get("enabled"), True),
        timeout=int(i_raw.get("timeout", 12)),
        max_bytes=int(i_raw.get("max_bytes", 5_000_000)),
        download_captions=_as_bool(i_raw.get("generate_caption"), True),
    )

    c_raw = _section(raw, "control")
    control = ControlSettings(
        enabled=_as_bool(c_raw.get("enabled"), True),
        review_mode=str(c_raw.get("review_mode", "smart")).lower(),
        min_score=float(c_raw.get("min_score", 0.85)),
        quiet_hours_start=int(c_raw.get("quiet_hours_start", -1)),
        quiet_hours_end=int(c_raw.get("quiet_hours_end", -1)),
        breaking_bypass_quiet=_as_bool(c_raw.get("breaking_bypass_quiet"), True),
        min_gap_minutes=int(c_raw.get("min_gap_minutes", 0)),
        max_per_hour=int(c_raw.get("max_per_hour", 0)),
        queue_max_hours=float(c_raw.get("queue_max_hours", 48.0)),
        notify_approvals=_as_bool(c_raw.get("notify_approvals"), True),
    )
    if control.review_mode not in ("smart", "always", "never"):
        log.warning("control.review_mode غير معروف (%s) — سيُستخدم smart", control.review_mode)
        control.review_mode = "smart"

    r_raw = _section(raw, "review")
    review = ReviewSettings(
        enabled=_as_bool(r_raw.get("enabled"), False),
        timeout_hours=int(r_raw.get("timeout_hours", 6)),
        max_per_run=int(r_raw.get("max_per_run", 5)),
        auto_approve_if_no_operator=_as_bool(r_raw.get("auto_approve_if_no_operator"), False),
    )

    b_raw = _section(raw, "breaking")
    breaking = BreakingSettings(
        enabled=_as_bool(b_raw.get("enabled"), True),
        keywords=[str(k).lower() for k in _as_list(b_raw.get("keywords"))],
        bypass_caps=_as_bool(b_raw.get("bypass_caps"), True),
        priority=int(b_raw.get("priority", 10)),
        prefix=str(b_raw.get("prefix", "🔴 عاجل")),
        max_per_run=int(b_raw.get("max_per_run", 2)),
    )

    t_raw = _section(raw, "retry")
    retry = RetrySettings(
        enabled=_as_bool(t_raw.get("enabled"), True),
        max_attempts=int(t_raw.get("max_attempts", 3)),
        per_run=int(t_raw.get("per_run", 3)),
    )

    fl_raw = _section(raw, "flash")
    flash = FlashSettings(
        enabled=_as_bool(fl_raw.get("enabled"), True),
        max_age_minutes=int(fl_raw.get("max_age_minutes", 45)),
        min_score=int(fl_raw.get("min_score", 5)),
        categories=[str(c).lower() for c in _as_list(fl_raw.get("categories"))],
        keywords=[str(k).lower() for k in _as_list(fl_raw.get("keywords"))],
        max_per_hour=int(fl_raw.get("max_per_hour", 6)),
        min_gap_seconds=int(fl_raw.get("min_gap_seconds", 25)),
        max_items_per_run=int(fl_raw.get("max_items_per_run", 3)),
        max_output_tokens=int(fl_raw.get("max_output_tokens", 700)),
        notify_telegram=_as_bool(fl_raw.get("notify_telegram"), True),
        scoped_chat_id=env_str("TELEGRAM_SCOPED_CHAT_ID", str(fl_raw.get("scoped_chat_id", ""))),
    )

    out_raw = _section(raw, "outcomes")
    outcomes = OutcomeSettings(
        enabled=_as_bool(out_raw.get("enabled"), True),
        policy=str(out_raw.get("policy", "outcomes_only")).lower(),
        allow_incident_headlines=_as_bool(out_raw.get("allow_incident_headlines"), False),
        governorate_matrix=_as_bool(out_raw.get("governorate_matrix"), True),
        min_outcome_chars=int(out_raw.get("min_outcome_chars", 40)),
        impact_line=_as_bool(out_raw.get("impact_line"), True),
        followup=_as_bool(out_raw.get("followup"), True),
        followup_min_hours=float(out_raw.get("followup_min_hours", 1.0)),
        followup_max_items=int(out_raw.get("followup_max_items", 60)),
        followup_max_age_hours=float(out_raw.get("followup_max_age_hours", 72.0)),
        daily_matrix=_as_bool(out_raw.get("daily_matrix"), True),
        daily_matrix_hour=int(out_raw.get("daily_matrix_hour", 20)),
        contrast=_as_bool(out_raw.get("contrast"), True),
        dual_verify=_as_bool(out_raw.get("dual_verify"), False),
        source_line=_as_bool(out_raw.get("source_line"), True),
        silent_hours=float(out_raw.get("silent_hours", 6.0)),
        silent_enabled=_as_bool(out_raw.get("silent_enabled"), True),
    )

    vt_raw = _section(raw, "verticals")
    an_raw = _section(vt_raw, "anime")
    verticals = VerticalSettings(anime=AnimeSettings(
        enabled=_as_bool(an_raw.get("enabled"), True),
        min_score=float(an_raw.get("min_score", 3.0)),
        publish_policy=str(an_raw.get("publish_policy", "announcement")),
        keep_original_names=_as_bool(an_raw.get("keep_original_names"), True),
        append_glossary=_as_bool(an_raw.get("append_glossary"), True),
        translate=_as_bool(an_raw.get("translate"), True),
        skip_incident_gate=_as_bool(an_raw.get("skip_incident_gate"), True),
        genre_tag_limit=int(an_raw.get("genre_tag_limit", 2)),
    ))

    ln_raw = _section(raw, "links")
    links = LinksSettings(
        enabled=_as_bool(ln_raw.get("enabled"), True),
        source_override=str(ln_raw.get("source_override", "")),
        medium=str(ln_raw.get("medium", "social")),
        campaign_prefix=str(ln_raw.get("campaign_prefix", "autonews")),
        keep_existing=_as_bool(ln_raw.get("keep_existing"), True),
    )

    db_raw = _section(raw, "dashboard")
    dashboard = DashboardSettings(
        enabled=_as_bool(db_raw.get("enabled"), True),
        output_file=str(db_raw.get("output_file", "logs/dashboard.html")),
        best_of_file=str(db_raw.get("best_of_file", "logs/best_of_week.md")),
        days=int(db_raw.get("days", 7)),
        top_n=int(db_raw.get("top_n", 10)),
    )

    tr_raw = _section(raw, "translation")
    translation = TranslationSettings(
        enabled=_as_bool(tr_raw.get("enabled"), True),
        target=str(tr_raw.get("target", "ar")),
        langs=[str(x).lower() for x in (tr_raw.get("langs") or ["en", "fr", "tr"])],
        max_per_run=int(tr_raw.get("max_per_run", 5)),
        keep_original_name=_as_bool(tr_raw.get("keep_original_name"), True),
        require_numbers_match=_as_bool(tr_raw.get("require_numbers_match"), True),
    )

    occ_raw = _section(raw, "occasions")
    occasions = OccasionSettings(
        enabled=_as_bool(occ_raw.get("enabled"), True),
        config_file=str(occ_raw.get("config_file", "config/occasions.yml")),
        greetings=_as_bool(occ_raw.get("greetings"), False),
        tz_offset=int(occ_raw.get("tz_offset", run_raw.get("timezone_offset", 3))),
    )

    crawl_raw = _section(raw, "crawl")
    crawl = CrawlSettings(
        enrich_articles=_as_bool(crawl_raw.get("enrich_articles"), True),
        autodiscover=_as_bool(crawl_raw.get("autodiscover"), True),
        max_retries=int(crawl_raw.get("max_retries", 2)),
        respect_robots=_as_bool(crawl_raw.get("respect_robots"), True),
        fetch_budget_seconds=int(crawl_raw.get("fetch_budget_seconds", 240)),
        max_enrich_per_source=int(crawl_raw.get("max_enrich_per_source", 2)),
    )

    dry_run = _as_bool(os.environ.get("DRY_RUN"), False)
    if override_dry_run is not None:
        dry_run = override_dry_run

    settings = Settings(
        timezone_offset=int(env_str("TZ_OFFSET", str(run_raw.get("timezone_offset", 3)))),
        log_level=env_str("LOG_LEVEL", str(run_raw.get("log_level", "INFO"))),
        max_items_per_run=int(env_str("MAX_ITEMS_PER_RUN", str(run_raw.get("max_items_per_run", 12)))),
        max_per_source=int(run_raw.get("max_per_source", 5)),
        max_age_hours=int(run_raw.get("max_age_hours", 48)),
        min_title_chars=int(run_raw.get("min_title_chars", 18)),
        retry_failed_after_runs=int(run_raw.get("retry_failed_after_runs", 3)),
        state_file=str(env_str("STATE_FILE", str(run_raw.get("state_file", "state/state.json")))),
        state_commit=_as_bool(run_raw.get("state_commit"), True),
        dry_run=dry_run,
        dedup_similarity=float(run_raw.get("dedup_similarity", 0.80)),
        cross_run_dedup=_as_bool(run_raw.get("cross_run_dedup"), True),
        cross_run_lookback=int(run_raw.get("cross_run_lookback", 150)),
        cross_run_similarity=float(run_raw.get("cross_run_similarity", 0.62)),
        request_timeout=int(run_raw.get("request_timeout", 30)),
        user_agent=str(run_raw.get("user_agent", "AutoNewsAgent/1.0 (+https://github.com/)")),
        ai=ai,
        alerts=alerts,
        quality=quality,
        images=images,
        review=review,
        control=control,
        breaking=breaking,
        retry=retry,
        occasions=occasions,
        crawl=crawl,
        flash=flash,
        outcomes=outcomes,
        translation=translation,
        links=links,
        verticals=verticals,
        dashboard=dashboard,
        sources=sources,
        channels=channels,
    )
    settings = _apply_env_overrides(settings)

    if not settings.ai.models:
        raise ConfigError("لا توجد نماذج Gemini محدّدة في ai.models")
    log.info(
        "الإعدادات محمّلة | مصادر=%d | قنوات=%d | مفاتيح=%d | نماذج=%d | "
        "صور=%s | مراجعة=%s | عاجل=%s | dry_run=%s",
        len(settings.sources), len(settings.channels), len(settings.ai.keys),
        len(settings.ai.models), settings.images.enabled, settings.review.enabled,
        settings.breaking.enabled, settings.dry_run,
    )
    return settings


def _check_unique(values: list[str], label: str) -> None:
    seen = set()
    for v in values:
        if v in seen:
            raise ConfigError(f"تكرار في {label}: '{v}'")
        seen.add(v)


def _apply_env_overrides(settings: Settings) -> Settings:
    """تجاوزات تشغيلية سريعة من البيئة."""
    only_channels = split_env_list(env_str("ENABLED_CHANNELS"))
    if only_channels:
        wanted = {c.lower() for c in only_channels}
        for ch in settings.channels:
            ch.enabled = ch.id.lower() in wanted
        settings.channels = [c for c in settings.channels if c.enabled]
        log.info("قنوات مفعّلة يدوياً: %s", [c.id for c in settings.channels])

    only_sources = split_env_list(env_str("ENABLED_SOURCES"))
    if only_sources:
        wanted = {s.lower() for s in only_sources}
        settings.sources = [s for s in settings.sources if s.id.lower() in wanted]
        log.info("مصادر مفعّلة يدوياً: %s", [s.id for s in settings.sources])

    if env_str("REQUIRE_REVIEW").lower() in ("1", "true", "yes"):
        settings.review.enabled = True
        for ch in settings.channels:
            ch.review_required = True
        log.warning("وضع المراجعة البشرية مفعّل عبر البيئة — لن يُنشر شيء بلا موافقة")

    if env_str("AI_DISABLED").strip().lower() in ("1", "true", "yes"):
        settings.ai.keys = []
        log.warning("AI_DISABLED مفعّل — سيتم استخدام صياغة احتياطية بدون ذكاء اصطناعي")
    return settings
