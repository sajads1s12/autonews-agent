"""إدارة الحالة: منع التكرار، طابور إعادة المحاولة، المراجعة البشرية، صحة المصادر.

تُحفظ الحالة في ملف JSON (افتراضياً state/state.json) — في GitHub Actions
نرفعها كـ Artifact/Cache ونُعيدها في التشغيل التالي لضمان عدم تكرار النشر.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import get_logger, iso_now, parse_dt, truncate, utcnow

log = get_logger("autonews.state")

MAX_POSTED_HISTORY = 800
MAX_ARTICLE_HISTORY = 4000
MAX_RETRY_QUEUE = 120
MAX_PENDING_REVIEWS = 60


@dataclass(slots=True)
class State:
    version: int = 2
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    runs: int = 0
    # بصمات المقالات المعالَجة -> {"status": posted/skipped/failed, ...}
    articles: dict[str, dict[str, Any]] = field(default_factory=dict)
    # سجلّ آخر المنشورات
    posted: list[dict[str, Any]] = field(default_factory=list)
    # إحصاءات عامة
    stats: dict[str, int] = field(default_factory=dict)
    # ---- إضافات v2 ----
    retry_queue: list[dict[str, Any]] = field(default_factory=list)      # منشورات تحتاج إعادة
    pending_reviews: dict[str, dict[str, Any]] = field(default_factory=dict)  # مسوّدات بانتظار موافقة
    source_health: dict[str, dict[str, Any]] = field(default_factory=dict)    # صحة كل مصدر
    usage: dict[str, Any] = field(default_factory=dict)                  # ميزانية التوكنات
    ab_stats: dict[str, dict[str, int]] = field(default_factory=dict)    # اختبار الأساليب A/B
    consecutive_zero_runs: int = 0                                        # تشغيلات بلا نشر (للتنبيه)
    # ---- المسار السريع (Fast Lane) ----
    flash_log: list[dict[str, Any]] = field(default_factory=list)        # ما نُشر فوراً
    flash_windows: dict[str, dict[str, Any]] = field(default_factory=dict)  # عدّاد الساعة لكل قناة
    flash_stats: dict[str, int] = field(default_factory=dict)
    # ---- v6: النتائج ----
    deferred_incidents: list[dict[str, Any]] = field(default_factory=list)  # حوادث بانتظار نتائجها
    daily_matrix_date: str = ""            # آخر يوم صدرت فيه خريطة المحافظات
    contrast_sent: dict[str, str] = field(default_factory=dict)  # منع تكرار منشور التفاوت

    # ---------------------------------------------------------------- المقالات
    def has_article(self, fp: str, *, retry_failed_after_runs: int = 3) -> bool:
        entry = self.articles.get(fp)
        if not entry:
            return False
        status = entry.get("status")
        # نُشر عبر المسار السريع: نسمح للدورة العادية بإضافة تفاصيله (إن كان مهماً)
        if status == "posted" and entry.get("via") == "flash":
            passes = int(entry.get("flash_passes", 0))
            if passes >= 1:
                return True
            entry["flash_passes"] = passes + 1
            log.info("خبر نُشر فوراً — الدورة العادية ستضيف التفاصيل (بصمة %s)", fp)
            return False
        if status in ("posted", "skipped"):
            return True
        if status == "failed":
            if self.runs - int(entry.get("run", 0)) < retry_failed_after_runs:
                return True
            log.info("إعادة محاولة مقال فاشل سابقاً (بصمة %s)", fp)
            return False
        return True

    def mark(self, fp: str, status: str, **extra: Any) -> None:
        self.articles[fp] = {
            "status": status, "run": self.runs, "at": iso_now(),
            **{k: v for k, v in extra.items() if v is not None},
        }
        self.stats[status] = self.stats.get(status, 0) + 1
        self._trim()

    # حالات تحمل عنواناً كاملاً (تضخّم الملف) — نُبقي الأنواع الضرورية فقط
    _STATUSES_KEEPING_TITLE = {"posted", "review", "deferred"}

    def _trim(self) -> None:
        # ١) ضغط السجلّات القديمة: «failed/skipped» لا تحتاج عنواناً كاملاً
        #    (كل عنوان ≈ 150 بايت × آلاف = ميغابايت في المستودع بلا فائدة)
        for fp, entry in self.articles.items():
            if "title" in entry and entry.get("status") not in self._STATUSES_KEEPING_TITLE:
                entry.pop("title", None)
                entry["t"] = 1   # علامة: عُومل مسبقاً (لا نكرّر المرور)
        # ٢) الحدّ الأقصى
        if len(self.articles) <= MAX_ARTICLE_HISTORY:
            return
        ordered = sorted(self.articles.items(), key=lambda kv: kv[1].get("at", ""))
        self.articles = dict(ordered[-MAX_ARTICLE_HISTORY:])

    # ---------------------------------------------------------------- النشر
    def record_post(self, *, channel: str, article_fp: str, title: str, url: str,
                    platform: str, post_ref: str = "", variant: str = "",
                    gov_on: list[str] | None = None,
                    gov_off: list[str] | None = None, source: str = "") -> None:
        entry = {
            "at": iso_now(), "channel": channel, "platform": platform,
            "article_fp": article_fp, "title": truncate(title, 160),
            "url": url, "ref": post_ref, "variant": variant,
        }
        if source:
            entry["source"] = source
        if gov_on or gov_off:
            entry["governorates"] = {"on": list(gov_on or []), "off": list(gov_off or [])}
        self.posted.append(entry)
        if len(self.posted) > MAX_POSTED_HISTORY:
            self.posted = self.posted[-MAX_POSTED_HISTORY:]

    @staticmethod
    def _canonical_news_url(value: str) -> str:
        """توحيد رابط الخبر وحذف معاملات التتبع حتى لا يتكرر بسبب UTM."""
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        try:
            parts = urlsplit((value or "").strip())
            if not parts.scheme or not parts.netloc:
                return (value or "").strip().rstrip("/")
            tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_content",
                        "utm_term", "fbclid", "gclid", "mc_cid", "mc_eid"}
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                     if k.lower() not in tracking]
            return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                               parts.path.rstrip("/"), urlencode(query), ""))
        except Exception:
            return (value or "").strip().rstrip("/")

    def was_recently_published(self, url: str, title: str, *, lookback: int = 500) -> bool:
        """منع إعادة نشر رابط الخبر نفسه حتى لو اختلفت معاملات التتبع."""
        canonical = self._canonical_news_url(url)
        if not canonical:
            return False
        from difflib import SequenceMatcher
        import re
        def norm_title(value: str) -> str:
            value = (value or "").lower()
            value = re.sub(r"https?://\S+, " ", value)
            value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
            return " ".join(value.split())
        current_title = norm_title(title)
        for item in self.posted[-lookback:]:
            old_url = self._canonical_news_url(str(item.get("url", "")))
            if old_url and old_url == canonical:
                return True
            old_title = norm_title(str(item.get("title", "")))
            if current_title and old_title:
                if current_title == old_title:
                    return True
                # تشابه عالٍ جداً فقط لتجنب دمج أخبار مختلفة عن الموضوع نفسه.
                if min(len(current_title), len(old_title)) >= 32 and SequenceMatcher(
                        None, current_title, old_title).ratio() >= 0.96:
                    return True
        return False

    def was_posted_to(self, channel_id: str, article_fp: str) -> bool:
        return any(
            p["channel"] == channel_id and p["article_fp"] == article_fp
            for p in self.posted[-200:]
        )

    def recent_titles(self, limit: int = 120) -> list[str]:
        """أحدث العناوين المنشورة — لمنع التكرار عبر التشغيلات."""
        return [p.get("title", "") for p in self.posted[-limit:] if p.get("title")]

    # ---------------------------------------------------------- طابور الإعادة
    def queue_retry(self, *, channel_id: str, post: dict, reason: str) -> None:
        """إضافة منشور فاشل إلى طابور الإعادة (مع منع التكرار)."""
        key = f"{channel_id}:{post.get('source_url', '')}"
        for item in self.retry_queue:
            if item.get("key") == key:
                item["attempts"] = int(item.get("attempts", 1)) + 1
                item["reason"] = reason[:200]
                item["at"] = iso_now()
                return
        self.retry_queue.append({
            "key": key, "channel": channel_id, "post": post,
            "reason": reason[:200], "attempts": 1, "at": iso_now(),
        })
        if len(self.retry_queue) > MAX_RETRY_QUEUE:
            self.retry_queue = self.retry_queue[-MAX_RETRY_QUEUE:]
        log.info("📥 أُضيف إلى طابور الإعادة: %s (%s)", channel_id, reason[:80])

    def take_retries(self, limit: int = 5, max_attempts: int = 3) -> list[dict[str, Any]]:
        """سحب عناصر جاهزة للإعادة وإزالتها من الطابور."""
        ready = [r for r in self.retry_queue if int(r.get("attempts", 1)) < max_attempts][:limit]
        for item in ready:
            self.retry_queue.remove(item)
        dead = [r for r in self.retry_queue if int(r.get("attempts", 1)) >= max_attempts]
        for item in dead:
            log.warning("⛔ منشور تجاوز محاولات الإعادة: %s", item.get("key"))
            self.retry_queue.remove(item)
        return ready

    # -------------------------------------------------------- المراجعة البشرية
    def add_pending_review(self, review_id: str, payload: dict[str, Any]) -> None:
        self.pending_reviews[review_id] = {**payload, "at": iso_now()}
        if len(self.pending_reviews) > MAX_PENDING_REVIEWS:
            oldest = sorted(self.pending_reviews.items(), key=lambda kv: kv[1].get("at", ""))
            self.pending_reviews = dict(oldest[-MAX_PENDING_REVIEWS:])

    def pop_pending_review(self, review_id: str) -> dict[str, Any] | None:
        return self.pending_reviews.pop(review_id, None)

    def expired_reviews(self, timeout_hours: int) -> list[str]:
        now = utcnow()
        expired = []
        for rid, payload in self.pending_reviews.items():
            dt = parse_dt(payload.get("at"))
            if dt and (now - dt).total_seconds() > timeout_hours * 3600:
                expired.append(rid)
        return expired

    # --------------------------------------------------------- صحة المصادر
    def record_source_result(self, source_id: str, *, ok: bool, items: int = 0,
                             fail_threshold: int = 5) -> bool:
        """تسجيل نتيجة مصدر. يُعيد True إن وجب تعطيله تلقائياً."""
        health = self.source_health.setdefault(
            source_id, {"failures": 0, "successes": 0, "last_ok": "", "disabled": False})
        if ok:
            health["successes"] = int(health.get("successes", 0)) + 1
            health["failures"] = 0
            health["last_ok"] = iso_now()
            health["last_items"] = items
            return False
        health["failures"] = int(health.get("failures", 0)) + 1
        health["last_fail"] = iso_now()
        if health["failures"] >= fail_threshold and not health.get("disabled"):
            health["disabled"] = True
            log.error("🚫 تعطيل المصدر '%s' تلقائياً بعد %d فشل متتالٍ", source_id, health["failures"])
            return True
        return False

    def source_disabled(self, source_id: str) -> bool:
        return bool(self.source_health.get(source_id, {}).get("disabled"))

    # --------------------------------------------------------- ميزانية/إحصاء
    def add_usage(self, *, calls: int = 0, tokens: int = 0) -> None:
        usage = self.usage.setdefault("current_run", {"calls": 0, "tokens": 0})
        usage["calls"] = int(usage.get("calls", 0)) + calls
        usage["tokens"] = int(usage.get("tokens", 0)) + tokens
        self.usage["total_tokens"] = int(self.usage.get("total_tokens", 0)) + tokens
        self.usage["total_calls"] = int(self.usage.get("total_calls", 0)) + calls

    def reset_run_usage(self) -> None:
        self.usage["current_run"] = {"calls": 0, "tokens": 0}

    def record_variant(self, channel_id: str, variant: str) -> None:
        stats = self.ab_stats.setdefault(channel_id, {})
        stats[variant] = int(stats.get(variant, 0)) + 1

    def next_variant(self, channel_id: str) -> str:
        """اختيار الأسلوب التالي في اختبار A/B (A/B/A/B...) وتسجيله فوراً."""
        stats = self.ab_stats.setdefault(channel_id, {"A": 0, "B": 0})
        pick = "A" if int(stats.get("A", 0)) <= int(stats.get("B", 0)) else "B"
        stats[pick] = int(stats.get(pick, 0)) + 1
        return pick

    # ------------------------------------------------------------ المسار السريع
    def was_flashed(self, fp: str, *, hours: float = 6.0) -> bool:
        """هل نُشر هذا الخبر فوراً خلال آخر (hours) ساعة؟"""
        cutoff = utcnow().timestamp() - hours * 3600
        for item in self.flash_log[-300:]:
            if item.get("fp") != fp:
                continue
            dt = parse_dt(item.get("at"))
            if dt and dt.timestamp() >= cutoff:
                return True
        return False

    def record_flash(self, fp: str, channel: str, title: str, *, source: str = "",
                     gov_on: list[str] | None = None,
                     gov_off: list[str] | None = None) -> None:
        self.flash_log.append({
            "fp": fp, "channel": channel, "title": truncate(title, 150),
            "source": source, "at": iso_now(),
            "governorates": {"on": list(gov_on or []), "off": list(gov_off or [])},
        })
        if len(self.flash_log) > 600:
            self.flash_log = self.flash_log[-600:]
        self.flash_stats["posted"] = self.flash_stats.get("posted", 0) + 1

    def flash_allowed(self, channel_id: str, *, max_per_hour: int,
                      min_gap_seconds: int = 0, now: float | None = None) -> tuple[bool, str]:
        """سقف الساعة + أقل فاصل زمني بين خبرين (حماية من الفيضان)."""
        now = now or utcnow().timestamp()
        window = self.flash_windows.setdefault(
            channel_id, {"start": now, "count": 0, "last": 0.0})
        if now - float(window.get("start", now)) >= 3600:
            window["start"] = now
            window["count"] = 0
        if int(window.get("count", 0)) >= max_per_hour:
            return False, f"تجاوز سقف {max_per_hour} خبر/ساعة"
        if min_gap_seconds and now - float(window.get("last", 0)) < min_gap_seconds:
            remaining = int(min_gap_seconds - (now - float(window.get("last", 0))))
            return False, f"فاصل أدنى بين الأخبار ({remaining}ث متبقية)"
        return True, ""

    def bump_flash(self, channel_id: str, *, now: float | None = None) -> None:
        now = now or utcnow().timestamp()
        window = self.flash_windows.setdefault(
            channel_id, {"start": now, "count": 0, "last": 0.0})
        window["count"] = int(window.get("count", 0)) + 1
        window["last"] = now

    # ------------------------------------------------- v6: حوادث بانتظار نتائجها
    def defer_incident(self, payload: dict[str, Any], *, max_items: int = 60) -> bool:
        """تسجيل حادثة مؤجّلة لمتابعة نتائجها (مع منع التكرار)."""
        fp = payload.get("fp", "")
        for item in self.deferred_incidents:
            if item.get("fp") == fp:
                item["checks"] = int(item.get("checks", 0)) + 1
                item["last_check"] = iso_now()
                return False
        self.deferred_incidents.append(payload)
        if len(self.deferred_incidents) > max_items:
            # نحتفظ بالأعلى أهمية والأحدث
            self.deferred_incidents.sort(key=lambda i: (-float(i.get("score", 0)), i.get("first_seen", "")))
            self.deferred_incidents = self.deferred_incidents[:max_items]
        return True

    def resolve_incident(self, fp: str) -> dict[str, Any] | None:
        for item in list(self.deferred_incidents):
            if item.get("fp") == fp:
                self.deferred_incidents.remove(item)
                return item
        return None

    def prune_incidents(self, *, max_age_hours: float = 72.0) -> int:
        """حذف الحوادث المؤجّلة التي انتهت صلاحيتها (بلا نتائج بعد 3 أيام).

        بلا هذا التنظيف ينمو ملف الحالة بلا حدّ، وتصير «الحوادث الصامتة» ضجيجاً.
        """
        now = utcnow()
        kept, removed = [], 0
        for item in self.deferred_incidents:
            dt = parse_dt(item.get("first_seen"))
            if dt and (now - dt).total_seconds() > max_age_hours * 3600:
                removed += 1
                continue
            kept.append(item)
        if removed:
            log.info("🧹 حُذفت %d حادثة مؤجّلة تجاوزت %.0f ساعة", removed, max_age_hours)
            self.deferred_incidents = kept
        return removed

    def aged_incidents(self, hours: float) -> list[dict[str, Any]]:
        """حوادث تجاوزت المهلة وما زالت بلا نتيجة (للتنبيه الإداري)."""
        out = []
        for item in self.deferred_incidents:
            dt = parse_dt(item.get("first_seen"))
            if dt and (utcnow() - dt).total_seconds() >= hours * 3600:
                if not item.get("alerted"):
                    out.append(item)
        return out

    # ---------------------------------------------------------------- التشغيل
    def begin_run(self) -> None:
        self.runs += 1
        self.updated_at = iso_now()
        self.reset_run_usage()

    def finish_run(self, published: int) -> None:
        self.consecutive_zero_runs = 0 if published > 0 else self.consecutive_zero_runs + 1

    # ------------------------------------------------------------ (De)Serialize
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "created_at": self.created_at,
            "updated_at": iso_now(), "runs": self.runs,
            "articles": self.articles, "posted": self.posted, "stats": self.stats,
            "retry_queue": self.retry_queue, "pending_reviews": self.pending_reviews,
            "source_health": self.source_health, "usage": self.usage,
            "ab_stats": self.ab_stats, "consecutive_zero_runs": self.consecutive_zero_runs,
            "flash_log": self.flash_log, "flash_windows": self.flash_windows,
            "flash_stats": self.flash_stats,
            "deferred_incidents": self.deferred_incidents,
            "daily_matrix_date": self.daily_matrix_date,
            "contrast_sent": self.contrast_sent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "State":
        return cls(
            version=int(data.get("version", 2)),
            created_at=str(data.get("created_at") or iso_now()),
            updated_at=str(data.get("updated_at") or iso_now()),
            runs=int(data.get("runs", 0)),
            articles=dict(data.get("articles") or {}),
            posted=list(data.get("posted") or []),
            stats=dict(data.get("stats") or {}),
            retry_queue=list(data.get("retry_queue") or []),
            pending_reviews=dict(data.get("pending_reviews") or {}),
            source_health=dict(data.get("source_health") or {}),
            usage=dict(data.get("usage") or {}),
            ab_stats=dict(data.get("ab_stats") or {}),
            consecutive_zero_runs=int(data.get("consecutive_zero_runs", 0)),
            flash_log=list(data.get("flash_log") or []),
            flash_windows=dict(data.get("flash_windows") or {}),
            flash_stats=dict(data.get("flash_stats") or {}),
            deferred_incidents=list(data.get("deferred_incidents") or []),
            daily_matrix_date=str(data.get("daily_matrix_date") or ""),
            contrast_sent=dict(data.get("contrast_sent") or {}),
        )


class StateStore:
    """قراءة/كتابة الحالة بشكل آمن (كتابة ذرّية عبر ملف مؤقّت)."""

    def __init__(self, path: str | Path = "state/state.json") -> None:
        self.path = Path(path)

    def load(self) -> State:
        if not self.path.exists():
            log.info("لا توجد حالة سابقة — بدء سجل جديد (%s)", self.path)
            return State()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            state = State.from_dict(data)
            log.info(
                "الحالة محمّلة | تشغيلات=%d | مقالات=%d | منشورات=%d | إعادة=%d | مراجعة=%d",
                state.runs, len(state.articles), len(state.posted),
                len(state.retry_queue), len(state.pending_reviews),
            )
            return state
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.warning("تعذّر قراءة ملف الحالة (%s) — بدء سجل جديد", exc)
            return State()

    def save(self, state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)
        log.info("تم حفظ الحالة: %s (%.1f KB)", self.path, len(payload) / 1024)

    def last_run_age_hours(self) -> float | None:
        state = self.load()
        dt = parse_dt(state.updated_at)
        if not dt:
            return None
        return (utcnow() - dt).total_seconds() / 3600
