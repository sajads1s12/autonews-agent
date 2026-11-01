"""عزل البيئة عن الاختبارات — النتيجة نفسها في أي بيئة تشغيل.

لماذا؟ كانت الاختبارات تفترض «بيئة نظيفة» بلا متغيّرات. لكن CI يضبط
`AI_DISABLED=true`، وواجهة GitHub تسمح بإضافة متغيّرات على مستوى المستودع،
والمطوّر قد يكون عنده `.env` محمّل. فأي متغيّر منها كان يقلب النتيجة:
سقوط اختبارات سليمة أو — الأسوأ — نجاح اختبارات معطوبة.

الاختبار الذي يريد متغيّراً يضبطه بنفسه بعد هذا العزل (`monkeypatch.setenv`).
"""
from __future__ import annotations

import pytest

#  متغيّرات تُغيّر سلوك النظام فعلياً. نُفرّغها قبل كل اختبار.
OPERATIONAL_ENV = (
    # تحميل الإعدادات
    "AI_DISABLED", "REQUIRE_REVIEW", "ENABLED_CHANNELS", "ENABLED_SOURCES",
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GEMINI_MODELS",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_SCOPED_CHAT_ID",
    "STATE_FILE", "MAX_ITEMS_PER_RUN", "TZ_OFFSET", "LOG_LEVEL",
    # المستودع الخاص (مزامنة الحالة)
    "STATE_REPO", "STATE_REPO_URL", "STATE_REPO_FULL", "STATE_REPO_TOKEN",
    "STATE_REPO_BRANCH", "STATE_WORKDIR",
    # البوت ولوحة الإعداد
    "TELEGRAM_ADMIN_CHAT_ID", "SETUP_ALLOWED_USERS", "SETUP_FILE",
    "SETUP_TIMEOUT", "ALERT_BOT_TOKEN", "ALERT_CHAT_ID",
    # فيسبوك
    "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN", "FACEBOOK_APP_ID",
    "FACEBOOK_APP_SECRET", "FACEBOOK_USER_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """يمسح المتغيّرات التشغيلية قبل كل اختبار (ثم يعيدها pytest تلقائياً)."""
    for name in OPERATIONAL_ENV:
        monkeypatch.delenv(name, raising=False)
    for i in range(1, 11):
        monkeypatch.delenv(f"GEMINI_API_KEY_{i}", raising=False)
    yield
