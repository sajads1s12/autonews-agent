"""محرّك التوليد مع نظام تبديل تلقائي متعدد المفاتيح والنماذج.

الفكرة باختصار
--------------
نحن لا نتصل بـ"مفتاح واحد ونموذج واحد"، بل بمصفوفة أزواج (مفتاح × نموذج).
عند أي خطأ نُصنّفه (حصة / مفتاح / نموذج / مؤقّت) ثم نُطبّق سياسة التجميد
المناسبة وننتقل فوراً — في أجزاء من الثانية — إلى الزوج التالي، حتى ينجح الطلب.

    ┌──────── المصفوفة ────────┐
    │ key1 + flash   ✅        │ ← أول زوج يعمل يُستخدم
    │ key1 + pro     ⏳ cooldown│
    │ key2 + flash   🔒 auth    │ ← مفتاح معطوب يُعزل حتى نهاية التشغيل
    │ key2 + pro     ✅        │
    └──────────────────────────┘

المزايا التشغيلية:
- بلا اعتماد على SDK: اتصال REST مباشر عبر urllib (أقل نقاط فشل، لا تعارض إصدارات).
- صحة الأزواج تُحفظ بين التشغيلات في ملف JSON (لا نعيد اختبار المفتاح المستنفَد كل مرّة).
- إحصاءات كاملة عن المحاولات تُطبع في نهاية كل تشغيل (تظهر في سجلّ GitHub Actions).
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from ..utils import get_logger, iso_now, truncate
from .errors import (
    AIError, AIUnavailable, AuthError, ModelError, NonRetryableError, QuotaError,
    TransientError, classify_http_error,
)

log = get_logger("autonews.ai.engine")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]

# مدة تجميد المفتاح المعطوب (خطأ مصادقة) — حتى نهاية اليوم
AUTH_COOLDOWN_SECONDS = 24 * 3600
# مدة تجميد النموذج غير المتاح
MODEL_COOLDOWN_SECONDS = 12 * 3600


def mask_key(key: str) -> str:
    """إخفاء المفتاح في السجلّات (أمان إجباري)."""
    if not key:
        return "<empty>"
    if len(key) <= 10:
        return key[:2] + "***"
    return f"{key[:6]}…{key[-4:]}"


@dataclass
class Attempt:
    """سجلّ محاولة واحدة (للشفافية والتقارير)."""
    key_index: int
    model: str
    outcome: str          # success | quota | auth | model | transient | fatal
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class PairHealth:
    """صحة زوج (مفتاح، نموذج)."""
    key_index: int
    model: str
    cooldown_until: float = 0.0
    successes: int = 0
    failures: int = 0
    last_error: str = ""
    last_used: float = 0.0

    @property
    def label(self) -> str:
        return f"key#{self.key_index + 1}/{self.model}"


class KeyRotator:
    """مصفوفة المفاتيح × النماذج + سياسات التجميد والتبديل."""

    def __init__(
        self,
        keys: list[str],
        models: list[str] | None = None,
        *,
        cooldown_quota: int = 900,
        cooldown_error: int = 60,
        request_timeout: int = 90,
        max_retries_per_pair: int = 1,
        health_path: str | Path | None = "state/ai_health.json",
        api_endpoint: str = GEMINI_ENDPOINT,
        post_fn: Callable[[str, dict, dict, int], tuple[int, str]] | None = None,
        token_budget: int = 0,
    ) -> None:
        self.keys = [k for k in keys if k]
        self.models = list(models or DEFAULT_MODELS)
        self.cooldown_quota = cooldown_quota
        self.cooldown_error = cooldown_error
        self.request_timeout = request_timeout
        self.max_retries_per_pair = max_retries_per_pair
        self.api_endpoint = api_endpoint
        self.health_path = Path(health_path) if health_path else None
        self._post_fn = post_fn
        self._lock = threading.Lock()
        self._attempts: list[Attempt] = []
        self._health: dict[str, PairHealth] = {}
        self.token_budget = int(token_budget or 0)
        self.tokens_used = 0
        self._load_health()
        self._build_pairs()
        log.info(
            "محرّك الذكاء الاصطناعي جاهز | مفاتيح=%d | نماذج=%d | أزواج=%d | نماذج: %s",
            len(self.keys), len(self.models), len(self._pairs), ", ".join(self.models[:4]),
        )

    # ------------------------------------------------------------- الإعداد
    def _build_pairs(self) -> None:
        """
        ترتيب الأزواج: أولوية للنموذج الأفضل (كل المفاتيح معه) ثم النموذج التالي.
        هذا يضمن أن انتهاء حصة مفتاح واحد لا يهبط بجودة المخرجات إلى نموذج أضعف.
        """
        self._pairs: list[tuple[int, str]] = []
        for model in self.models:
            for key_index in range(len(self.keys)):
                pair = (key_index, model)
                if pair not in self._pairs:
                    self._pairs.append(pair)
        for key_index, model in self._pairs:
            self._health.setdefault(f"{key_index}:{model}", PairHealth(key_index, model))

    def _health_path_for(self) -> Path | None:
        return self.health_path

    def _load_health(self) -> None:
        path = self._health_path_for()
        if not path or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw.get("pairs", []):
                ph = PairHealth(
                    key_index=int(item["key_index"]),
                    model=str(item["model"]),
                    cooldown_until=float(item.get("cooldown_until", 0)),
                    successes=int(item.get("successes", 0)),
                    failures=int(item.get("failures", 0)),
                    last_error=str(item.get("last_error", "")),
                    last_used=float(item.get("last_used", 0)),
                )
                self._health[f"{ph.key_index}:{ph.model}"] = ph
            log.info("صحة المفاتيح محمّلة من %s", path)
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as exc:
            log.warning("تعذّر قراءة صحة المفاتيح (%s) — سيتم تجاهلها", exc)

    def _save_health(self) -> None:
        path = self._health_path_for()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": iso_now(),
                "pairs": [asdict(ph) for ph in self._health.values()],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:  # لا يجب أن يُسقط فشل الحفظ التشغيل
            log.warning("تعذّر حفظ صحة المفاتيح: %s", exc)

    # ------------------------------------------------------------- المساعدة
    def _ph(self, key_index: int, model: str) -> PairHealth:
        return self._health.setdefault(f"{key_index}:{model}", PairHealth(key_index, model))

    def _is_cooling(self, ph: PairHealth, now: float) -> bool:
        return ph.cooldown_until > now

    def available_pairs(self) -> list[tuple[int, str]]:
        now = time.time()
        with self._lock:
            return [p for p in self._pairs if not self._is_cooling(self._ph(*p), now)]

    def _freeze(self, key_index: int, model: str, seconds: float, reason: str, *, whole_key: bool = False,
                whole_model: bool = False) -> None:
        """تجميد زوج، أو كل أزواج مفتاح، أو كل أزواج نموذج."""
        now = time.time()
        with self._lock:
            targets: list[tuple[int, str]] = []
            if whole_key:
                targets = [p for p in self._pairs if p[0] == key_index]
            elif whole_model:
                targets = [p for p in self._pairs if p[1] == model]
            else:
                targets = [(key_index, model)]
            for pair in targets:
                ph = self._ph(*pair)
                ph.cooldown_until = max(ph.cooldown_until, now + seconds)
                ph.failures += 1
                ph.last_error = truncate(reason, 200)
                ph.last_used = now

    # ---------------------------------------------------------- استدعاء HTTP
    def _post(self, url: str, payload: dict, headers: dict, timeout: int) -> tuple[int, str]:
        """الطلب الفعلي (قابل للاستبدال في الاختبارات عبر post_fn)."""
        if self._post_fn is not None:
            return self._post_fn(url, payload, headers, timeout)

        import urllib.error
        import urllib.request

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientError(f"تعذّر الاتصال بخدمة Gemini: {exc}") from exc

    # --------------------------------------------------------- الواجهة العامة
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.75,
        max_output_tokens: int = 2048,
        json_response: bool = True,
    ) -> dict[str, Any]:
        """
        توليد نص مع تبديل تلقائي كامل.

        Returns:
            {"text": str, "model": str, "key_index": int, "attempts": int}

        Raises:
            AIUnavailable: جميع الأزواج مستنفدة/مجمّدة.
            NonRetryableError: الطلب نفسه مرفوض (لا فائدة من التبديل).
        """
        if not self.keys:
            raise AIUnavailable("لا توجد مفاتيح API مُهيّأة (GEMINI_API_KEYS فارغ)")
        if self.token_budget and self.tokens_used >= self.token_budget:
            raise AIUnavailable(
                f"استُهلكت ميزانية التوكنات لهذا التشغيل "
                f"({self.tokens_used}/{self.token_budget}) — يُستكمل الباقي في التشغيل القادم"
            )

        payload_base: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if json_response:
            payload_base["generationConfig"]["responseMimeType"] = "application/json"
        if system:
            payload_base["systemInstruction"] = {"parts": [{"text": system}]}

        tried: list[tuple[int, str]] = []
        start_all = time.time()

        while True:
            candidates = [p for p in self.available_pairs() if p not in tried]
            if not candidates:
                raise AIUnavailable(
                    "استُنفدت كل المفاتيح وكل النماذج المتاحة (حصة/تعطيل) — سيُعاد المحاولة تلقائياً في التشغيل القادم",
                    health=self.health_snapshot(),
                )

            key_index, model = candidates[0]
            tried.append((key_index, model))
            key = self.keys[key_index]
            url = self.api_endpoint.format(model=model)
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": key,
                "User-Agent": "AutoNewsAgent/1.0",
            }

            for retry in range(self.max_retries_per_pair + 1):
                t0 = time.time()
                try:
                    status, body = self._post(url, payload_base, headers, self.request_timeout)
                    elapsed = int((time.time() - t0) * 1000)

                    if status >= 400:
                        raise classify_http_error(status, body)

                    text, usage = self._extract_text(body)
                    if not text.strip():
                        raise TransientError("استجابة فارغة من النموذج")

                    ph = self._ph(key_index, model)
                    ph.successes += 1
                    ph.last_used = time.time()
                    ph.cooldown_until = 0.0
                    self._attempts.append(Attempt(key_index, model, "success", elapsed_ms=elapsed))
                    self._save_health()
                    log.info(
                        "✅ توليد ناجح | %s | محاولات=%d | زمن=%.1fs | tokens≈%s",
                        ph.label, len(self._attempts), time.time() - start_all, usage,
                    )
                    used_tokens = int((usage or {}).get("prompt") or 0) + int((usage or {}).get("output") or 0)
                    self.tokens_used += used_tokens
                    return {
                        "text": text,
                        "model": model,
                        "key_index": key_index,
                        "key_masked": mask_key(key),
                        "attempts": len(self._attempts),
                        "elapsed_s": round(time.time() - start_all, 2),
                        "usage": usage,
                        "tokens_used": used_tokens,
                    }

                except NonRetryableError:
                    self._attempts.append(Attempt(key_index, model, "fatal", "طلب مرفوض"))
                    self._save_health()
                    raise

                except QuotaError as exc:
                    # المفتاح/النموذج استنفد حصته → تجميد الزوج والانتقال فوراً
                    wait = self.cooldown_quota + random.randint(0, 30)
                    log.warning("⏳ حصة منتهية | %s/%s → تجميد %ds | %s",
                                mask_key(key), model, wait, truncate(str(exc.detail or exc), 120))
                    self._freeze(key_index, model, wait, "quota")
                    self._attempts.append(Attempt(key_index, model, "quota", str(exc)))
                    self._save_health()
                    break  # الزوج التالي مباشرة

                except AuthError as exc:
                    log.error("🔒 مفتاح غير صالح | %s → عزل المفتاح كلياً | %s",
                              mask_key(key), truncate(str(exc.detail or exc), 140))
                    self._freeze(key_index, model, AUTH_COOLDOWN_SECONDS, "auth", whole_key=True)
                    self._attempts.append(Attempt(key_index, model, "auth", str(exc)))
                    self._save_health()
                    break

                except ModelError as exc:
                    log.warning("🚫 نموذج غير متاح | %s → تجميد النموذج | %s",
                                model, truncate(str(exc.detail or exc), 140))
                    self._freeze(key_index, model, MODEL_COOLDOWN_SECONDS, "model", whole_model=True)
                    self._attempts.append(Attempt(key_index, model, "model", str(exc)))
                    self._save_health()
                    break

                except TransientError as exc:
                    elapsed = int((time.time() - t0) * 1000)
                    if retry < self.max_retries_per_pair:
                        backoff = (1.5 ** retry) + random.uniform(0, 0.6)
                        log.warning("⚠️ خطأ مؤقّت | %s/%s | إعادة محاولة بعد %.1fs | %s",
                                    mask_key(key), model, backoff, truncate(str(exc), 120))
                        time.sleep(backoff)
                        continue
                    log.warning("⚠️ خطأ مؤقّت متكرّر | %s/%s → تجميد مؤقّت | %s",
                                mask_key(key), model, truncate(str(exc), 120))
                    self._freeze(key_index, model, self.cooldown_error, "transient")
                    self._attempts.append(Attempt(key_index, model, "transient", str(exc), elapsed))
                    self._save_health()
                    break

                except AIError as exc:  # احتياط لأي نوع جديد
                    log.warning("خطأ AI غير متوقّع | %s/%s | %s", mask_key(key), model, exc)
                    self._freeze(key_index, model, self.cooldown_error, exc.kind)
                    self._attempts.append(Attempt(key_index, model, exc.kind, str(exc)))
                    self._save_health()
                    break

    # ------------------------------------------------------------- الاستخلاص
    @staticmethod
    def _extract_text(body: str) -> tuple[str, dict]:
        """استخلاص النص من استجابة Gemini مع معالجة حجب المحتوى."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TransientError(f"استجابة غير صالحة من الخدمة: {body[:200]}") from exc

        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise NonRetryableError(f"حُجب الطلب: {feedback['blockReason']}")

        candidates = data.get("candidates") or []
        if not candidates:
            raise TransientError(f"لا توجد مرشّحات في الاستجابة: {body[:200]}")

        cand = candidates[0]
        finish = cand.get("finishReason", "")
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise NonRetryableError(f"حُجب المحتوى: {finish}")

        parts = ((cand.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = data.get("usageMetadata") or {}
        return text, {
            "prompt": usage.get("promptTokenCount"),
            "output": usage.get("candidatesTokenCount"),
        }

    # ------------------------------------------------------------- التتبع
    @property
    def attempts(self) -> list[Attempt]:
        return list(self._attempts)

    def reset_stats(self) -> None:
        self._attempts.clear()

    def health_snapshot(self) -> list[dict]:
        now = time.time()
        snap = []
        for key_index, model in self._pairs:
            ph = self._ph(key_index, model)
            snap.append({
                "key": f"key#{key_index + 1}",
                "model": model,
                "status": "cooling" if ph.cooldown_until > now else "ready",
                "cooldown_left_s": int(max(0, ph.cooldown_until - now)),
                "successes": ph.successes,
                "failures": ph.failures,
                "last_error": ph.last_error,
            })
        return snap

    # ------------------------------------------------------- فحص المفاتيح
    def self_test(self, *, max_keys: int = 10) -> list[dict]:
        """فحص كل مفتاح بنموذج واحد سريع، بلا تجميد طويل.

        يُفيد في تشخيص "أي المفاتيح يعمل فعلاً؟" قبل الاعتماد على النظام،
        ويُستدعى من `--check --probe`.
        """
        model = self.models[0] if self.models else "gemini-2.5-flash"
        results: list[dict] = []
        for key_index, key in enumerate(self.keys[:max_keys]):
            url = self.api_endpoint.format(model=model)
            headers = {"Content-Type": "application/json", "x-goog-api-key": key,
                       "User-Agent": "AutoNewsAgent/1.0"}
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "اكتب كلمة واحدة: تم"}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8},
            }
            t0 = time.time()
            entry = {"key": f"key#{key_index + 1}", "masked": mask_key(key), "model": model}
            try:
                status, body = self._post(url, payload, headers, min(self.request_timeout, 30))
                elapsed = int((time.time() - t0) * 1000)
                if status >= 400:
                    raise classify_http_error(status, body)
                text, _ = self._extract_text(body)
                entry.update(status="✅ يعمل", detail=truncate(text.strip(), 40), ms=elapsed)
                ph = self._ph(key_index, model)
                ph.successes += 1
                ph.cooldown_until = 0.0
            except AIError as exc:
                elapsed = int((time.time() - t0) * 1000)
                entry.update(status=f"❌ {exc.kind}", detail=truncate(str(exc.detail or exc), 90), ms=elapsed)
                if exc.kind in ("quota", "auth", "model"):
                    seconds = {"quota": self.cooldown_quota, "auth": AUTH_COOLDOWN_SECONDS,
                               "model": MODEL_COOLDOWN_SECONDS}[exc.kind]
                    self._freeze(key_index, model, seconds, exc.kind,
                                 whole_key=exc.kind == "auth")
            except Exception as exc:  # noqa: BLE001
                entry.update(status="❌ خطأ", detail=truncate(str(exc), 90), ms=0)
            results.append(entry)
        self._save_health()
        return results

    def markdown_report(self) -> str:
        """تقرير ماركداون يظهر في ملخّص GitHub Actions."""
        lines = ["### 🔑 حالة المفاتيح والنماذج", "", "| المفتاح | النموذج | الحالة | نجاح | فشل | آخر خطأ |",
                 "|---|---|---|---|---|---|"]
        for row in self.health_snapshot():
            status = "✅ جاهز" if row["status"] == "ready" else f"⏳ {row['cooldown_left_s']}s"
            err = row["last_error"].replace("|", " ")[:48]
            lines.append(
                f"| {row['key']} | `{row['model']}` | {status} | {row['successes']} | {row['failures']} | {err} |"
            )
        used = {}
        for a in self._attempts:
            used[a.outcome] = used.get(a.outcome, 0) + 1
        if used:
            lines += ["", "**محاولات هذا التشغيل:** " + " · ".join(f"{k}={v}" for k, v in used.items())]
        total_pairs = len(self._pairs)
        ready = sum(1 for r in self.health_snapshot() if r["status"] == "ready")
        lines += ["", f"**الأزواج الجاهزة:** {ready}/{total_pairs}"]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  الأسماء المتوافقة مع التوصيف
# --------------------------------------------------------------------------- #
FallbackEngine = KeyRotator
