"""تصنيف أخطاء مزوّد الذكاء الاصطناعي — أساس نظام التبديل التلقائي.

كل خطأ يُترجم إلى نوع واحد من أربعة، ولكل نوع سياسة مختلفة:

QuotaError     → الحصة انتهت (429)  ⇒ تجميد الزوج (مفتاح+نموذج) ثم الانتقال.
AuthError      → المفتاح غير صالح (400/401/403) ⇒ تجميد المفتاح كلياً.
ModelError     → النموذج غير متاح/غير مدعوم (404/400) ⇒ تجميد النموذج كلياً.
TransientError → خطأ مؤقت (5xx / شبكة / مهلة) ⇒ إعادة محاولة محدودة ثم الانتقال.
NonRetryable   → طلب مرفوض (سياسة/محتوى) ⇒ لا فائدة من التبديل، ارفع الخطأ فوراً.
"""
from __future__ import annotations


class AIError(RuntimeError):
    """الأصل لكل أخطاء المحرّك الذكي."""

    kind = "ai_error"
    retryable = False

    def __init__(self, message: str, *, status: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - تنسيق فقط
        base = super().__str__()
        return f"{base} [{self.kind}" + (f" {self.status}" if self.status else "") + "]"


class QuotaError(AIError):
    """انتهاء الحصة أو تجاوز حدود الطلبات (429 / RESOURCE_EXHAUSTED)."""
    kind = "quota"
    retryable = True


class AuthError(AIError):
    """مفتاح غير صالح أو صلاحيات ناقصة (401/403 / API_KEY_INVALID)."""
    kind = "auth"
    retryable = False


class ModelError(AIError):
    """النموذج غير موجود أو غير مدعوم لهذا المفتاح (404 / 400 model)."""
    kind = "model"
    retryable = False


class TransientError(AIError):
    """خلل مؤقت في الخدمة أو الشبكة (5xx / timeout / connection)."""
    kind = "transient"
    retryable = True


class NonRetryableError(AIError):
    """الطلب نفسه مرفوض (سلامة محتوى/صيغة) — التبديل لن يفيد."""
    kind = "fatal"
    retryable = False


class AIUnavailable(AIError):
    """استُنفدت كل المفاتيح وكل النماذج — لا يمكن التوليد الآن."""
    kind = "unavailable"
    retryable = True

    def __init__(self, message: str, *, health: list[dict] | None = None) -> None:
        super().__init__(message)
        self.health = health or []


# كلمات مفتاحية موجودة في رسائل Gemini/REST لتصنيف أدق
_QUOTA_HINTS = ("quota", "rate limit", "resource_exhausted", "resource exhausted",
                "too many requests", "exceeded", "billing")
_AUTH_HINTS = ("api key not valid", "api_key_invalid", "invalid api key", "unauthenticated",
               "permission_denied", "permission denied", "forbidden", "api key expired",
               "leaked", "unauthorized")
_MODEL_HINTS = ("not found", "is not supported", "unsupported model", "does not exist",
                "unknown model", "not available")
_SAFETY_HINTS = ("safety", "blocked", "prohibited", "policy", "recitation", "harmful")


def classify_http_error(status: int | None, body: str) -> AIError:
    """تحويل رمز HTTP ورسالة الخدمة إلى نوع خطأ معروف."""
    text = (body or "").lower()
    if status == 429 or any(h in text for h in _QUOTA_HINTS):
        return QuotaError("انتهت حصة المفتاح/النموذج (quota)", status=status, detail=body[:400])
    if status in (401, 403) or any(h in text for h in _AUTH_HINTS):
        return AuthError("مفتاح API غير صالح أو منتهي الصلاحية", status=status, detail=body[:400])
    if status == 404 or any(h in text for h in _MODEL_HINTS):
        return ModelError("النموذج غير متاح لهذا المفتاح", status=status, detail=body[:400])
    if status is not None and status >= 500:
        return TransientError("خلل مؤقت في خدمة Gemini", status=status, detail=body[:400])
    if any(h in text for h in _SAFETY_HINTS):
        return NonRetryableError("رُفض المحتوى بسبب سياسات السلامة", status=status, detail=body[:400])
    if status == 400:
        return NonRetryableError("طلب غير صالح (400)", status=status, detail=body[:400])
    return TransientError(f"خطأ غير مصنّف (status={status})", status=status, detail=body[:400])
