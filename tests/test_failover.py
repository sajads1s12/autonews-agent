"""اختبارات نظام التبديل التلقائي (Multi-Key / Multi-Model Fallback).

كل الاختبارات بلا شبكة: نستبدل دالة الطلب بـ post_fn مُحاكى.
تشغيل:  pytest -q
"""
from __future__ import annotations

import json

import pytest

from autonews.ai.engine import KeyRotator, mask_key
from autonews.ai.errors import AIUnavailable, NonRetryableError, classify_http_error


def ok_body(text: str = '{"title":"t","body":"b" * 60}') -> str:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 80},
    })


QUOTA_BODY = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                   "message": "Quota exceeded for quota metric"}})
AUTH_BODY = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                  "message": "API key not valid. Please pass a valid API key."}})
MODEL_BODY = json.dumps({"error": {"code": 404, "status": "NOT_FOUND",
                                   "message": "models/gemini-1.5-pro is not found"}})


def make_engine(keys, models, script, tmp_path):
    """محرّك يُعيد استجابات وفق سكربت: كل نداء يستهلك العنصر التالي."""
    calls = []

    def fake_post(url, payload, headers, timeout):
        model = url.split("/models/")[1].split(":")[0]
        key = headers.get("x-goog-api-key")
        calls.append({"model": model, "key": key})
        action = script.pop(0) if script else ("ok", "")
        kind, body = action if isinstance(action, tuple) else ("ok", "")
        if kind == "ok":
            return 200, ok_body()
        if kind == "http":
            return body[0], body[1]
        raise body

    eng = KeyRotator(
        keys=keys,
        models=models,
        cooldown_quota=60,
        cooldown_error=1,
        max_retries_per_pair=0,
        health_path=tmp_path / "health.json",
        post_fn=fake_post,
    )
    eng._calls = calls
    return eng


# --------------------------------------------------------------------------- #
#  1) المفتاح الأول مستنفَد → الانتقال للمفتاح الثاني فوراً
# --------------------------------------------------------------------------- #
def test_quota_failover_to_next_key(tmp_path):
    eng = make_engine(
        keys=["KEY_A", "KEY_B"],
        models=["gemini-2.5-flash"],
        script=[("http", (429, QUOTA_BODY)), ("ok", "")],
        tmp_path=tmp_path,
    )
    result = eng.generate("اكتب خبراً")
    assert result["key_index"] == 1          # انتقل للمفتاح الثاني
    assert "t" in result["text"]
    assert eng.attempts[0].outcome == "quota"


# --------------------------------------------------------------------------- #
#  2) كل المفاتيح مستنفدة على النموذج الأول → الانتقال لنموذج بديل
# --------------------------------------------------------------------------- #
def test_model_fallback_after_all_keys_quota(tmp_path):
    eng = make_engine(
        keys=["K1", "K2", "K3"],
        models=["gemini-2.5-flash", "gemini-1.5-pro"],
        script=[("http", (429, QUOTA_BODY))] * 3 + [("ok", "")],
        tmp_path=tmp_path,
    )
    result = eng.generate("خبر")
    assert result["model"] == "gemini-1.5-pro"
    assert sum(1 for a in eng.attempts if a.outcome == "quota") == 3


# --------------------------------------------------------------------------- #
#  3) مفتاح غير صالح → عزل المفتاح كلياً (كل النماذج)
# --------------------------------------------------------------------------- #
def test_invalid_key_isolated_entirely(tmp_path):
    eng = make_engine(
        keys=["BAD_KEY", "GOOD_KEY"],
        models=["gemini-2.5-flash", "gemini-1.5-pro"],
        script=[("http", (400, AUTH_BODY)), ("ok", "")],
        tmp_path=tmp_path,
    )
    result = eng.generate("خبر")
    assert result["key_index"] == 1
    snapshot = eng.health_snapshot()
    bad = [r for r in snapshot if r["key"] == "key#1"]
    assert bad and all(r["status"] == "cooling" for r in bad), "المفتاح المعطوب يجب أن يُجمّد كلياً"


# --------------------------------------------------------------------------- #
#  4) نموذج غير متاح → تجميد النموذج لكل المفاتيح
# --------------------------------------------------------------------------- #
def test_unknown_model_frozen_for_all_keys(tmp_path):
    eng = make_engine(
        keys=["K1", "K2"],
        models=["gemini-9.9-ghost", "gemini-1.5-flash"],
        script=[("http", (404, MODEL_BODY)), ("ok", "")],
        tmp_path=tmp_path,
    )
    result = eng.generate("خبر")
    assert result["model"] == "gemini-1.5-flash"
    ghosts = [r for r in eng.health_snapshot() if r["model"] == "gemini-9.9-ghost"]
    assert all(r["status"] == "cooling" for r in ghosts)


# --------------------------------------------------------------------------- #
#  5) استنفاد كل الأزواج → AIUnavailable (بلا انهيار)
# --------------------------------------------------------------------------- #
def test_all_pairs_exhausted_raises_unavailable(tmp_path):
    eng = make_engine(
        keys=["K1", "K2"],
        models=["m1", "m2"],
        script=[("http", (429, QUOTA_BODY))] * 4,
        tmp_path=tmp_path,
    )
    with pytest.raises(AIUnavailable):
        eng.generate("خبر")
    assert len(eng.attempts) == 4


# --------------------------------------------------------------------------- #
#  6) رفض المحتوى (سلامة) لا يجب أن يُبدّل المفاتيح — يرفع فوراً
# --------------------------------------------------------------------------- #
def test_safety_block_is_not_retried(tmp_path):
    eng = make_engine(
        keys=["K1", "K2"],
        models=["m1"],
        script=[("http", (400, json.dumps({"error": {"message": "blocked by safety policy"}})))],
        tmp_path=tmp_path,
    )
    with pytest.raises(NonRetryableError):
        eng.generate("خبر")
    assert len(eng.attempts) == 1


# --------------------------------------------------------------------------- #
#  7) صحة الأزواج تُحفظ وتُستعاد بين التشغيلات (لا نعيد اختبار مفتاح معطوب)
# --------------------------------------------------------------------------- #
def test_health_persisted_between_runs(tmp_path):
    health = tmp_path / "health.json"
    # المحاولة الأولى تُصطدم بالمفتاح المعطوب ثم تنجح بالثاني
    script = [("http", (400, AUTH_BODY))]
    eng1 = make_engine(keys=["BAD", "OK"], models=["m1"], script=script, tmp_path=tmp_path)
    eng1.health_path = health
    eng1.generate("خبر")
    assert health.exists()

    # التشغيل التالي: المفتاح المعطوب يجب أن يكون مجمّداً مسبقاً
    eng2 = KeyRotator(keys=["BAD", "OK"], models=["m1"], cooldown_quota=600,
                      health_path=health, post_fn=lambda *a: (200, ok_body()))
    ready = eng2.available_pairs()
    assert (0, "m1") not in ready, "المفتاح المعطوب يجب ألا يُجرَّب مجدداً"
    assert (1, "m1") in ready


# --------------------------------------------------------------------------- #
#  8) تصنيف الأخطاء
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status,body,expected", [
    (429, "{}", "quota"),
    (403, "{}", "auth"),
    (500, "{}", "transient"),
    (404, "{}", "model"),
    (400, '{"error":{"message":"quota exceeded"}}', "quota"),
    (400, '{"error":{"message":"random"}}', "fatal"),
])
def test_error_classification(status, body, expected):
    assert classify_http_error(status, body).kind == expected


# --------------------------------------------------------------------------- #
#  9) الأمان: إخفاء المفاتيح في السجلّات
# --------------------------------------------------------------------------- #
def test_mask_key_never_leaks_full_key():
    key = "test-key-0000000000000000000000000000"   # وهمي: لا يشبه نمط Google
    masked = mask_key(key)
    assert masked != key
    assert key[-8:] not in masked


def test_empty_keys_raise_clear_error(tmp_path):
    eng = KeyRotator(keys=[], models=["m1"], health_path=tmp_path / "h.json")
    with pytest.raises(AIUnavailable):
        eng.generate("x")
