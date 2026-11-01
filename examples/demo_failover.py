"""عرض تجريبي لنظام التبديل التلقائي — بلا مفاتيح حقيقية وبلا شبكة.

يحاكي خادم Gemini كي يرى المستخدم ما يحدث فعلياً عند انتهاء الحصص:

    python examples/demo_failover.py

المشهد:
  key#1  → حصتها منتهية (429)  ⇒ تُجمّد وتُنتقل إلى key#2
  key#2  → مفتاح معطوب (400 API_KEY_INVALID) ⇒ يُعزل
  key#3  → نجاح ✅
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autonews.ai.engine import KeyRotator, mask_key  # noqa: E402
from autonews.utils import setup_logging  # noqa: E402

KEYS = [
    "AIzaSyDEMO_key_number_one_0000000001",
    "AIzaSyDEMO_key_number_two_0000000002",
    "AIzaSyDEMO_key_number_three_000003",
]

OK = json.dumps({
    "candidates": [{
        "content": {"parts": [{"text": json.dumps({
            "title": "بغداد تطلق منصة وطنية للذكاء الاصطناعي",
            "body": "أعلنت وزارة الاتصالات إطلاق منصة وطنية للذكاء الاصطناعي تهدف إلى "
                    "أتمتة الخدمات الحكومية وتدريب الكوادر الشابة، على أن تبدأ المرحلة "
                    "الأولى مطلع العام المقبل.",
            "hashtags": ["العراق", "ذكاء_اصطناعي", "تقنية"],
            "category": "تقنية",
            "key_points": ["أتمتة الخدمات الحكومية", "تدريب الكوادر الشابة"],
            "sensitive": False,
        }, ensure_ascii=False)}]},
        "finishReason": "STOP",
    }],
    "usageMetadata": {"promptTokenCount": 820, "candidatesTokenCount": 210},
})

QUOTA = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                              "message": "Quota exceeded for quota metric 'generate_content_free_tier_requests'"}})
INVALID_KEY = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                    "message": "API key not valid. Please pass a valid API key."}})

# سكربت الاستجابات: مفتاح → ما يحدث
BEHAVIOUR = {
    KEYS[0]: (429, QUOTA),
    KEYS[1]: (400, INVALID_KEY),
    KEYS[2]: (200, OK),
}


def fake_gemini(url: str, payload: dict, headers: dict, timeout: int) -> tuple[int, str]:
    """خادم Gemini وهمي: يردّ حسب المفتاح المستخدم."""
    key = headers.get("x-goog-api-key", "")
    model = url.split("/models/")[1].split(":")[0]
    status, body = BEHAVIOUR.get(key, (200, OK))
    label = {"429": "⏳ حصة منتهية", "400": "🔒 مفتاح معطوب", "200": "✅ نجاح"}[str(status)]
    print(f"   → طلب إلى {model} بالمفتاح {mask_key(key):<22} ← {label}")
    return status, body


def main() -> int:
    setup_logging("INFO")
    print("\n🎬 محاكاة انتهاء الحصص والتبديل التلقائي")
    print("=" * 70)

    engine = KeyRotator(
        keys=KEYS,
        models=["gemini-2.5-flash", "gemini-1.5-pro"],
        cooldown_quota=900,
        cooldown_error=60,
        max_retries_per_pair=0,
        health_path=None,           # لا نحفظ حالة تجربة وهمية
        post_fn=fake_gemini,
    )

    result = engine.generate("أعد صياغة الخبر التالي بأسلوب صحفي…")

    print("=" * 70)
    print(f"✅ نجح التوليد بالنموذج: {result['model']} (المفتاح key#{result['key_index'] + 1})")
    print(f"   الوقت المستغرق: {result['elapsed_s']}s | عدد المحاولات: {result['attempts']}")
    print("\n📄 الناتج:")
    print(json.dumps(json.loads(result["text"]), ensure_ascii=False, indent=2))
    print("\n" + engine.markdown_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
