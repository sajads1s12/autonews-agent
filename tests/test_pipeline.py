"""اختبارات المسار الكامل بلا شبكة: RSS ← تصفية ← توجيه ← صياغة ← نشر ← حالة."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonews.ai import ContentWriter, Post, extract_json
from autonews.ai.engine import KeyRotator
from autonews.config_loader import Channel, Settings, Source, load_config
from autonews.ingest.rss import Article, _parse_with_etree
from autonews.publishers import ConsolePublisher, PublishResult, build_publisher
from autonews.router import ContentRouter
from autonews.state import State, StateStore
from autonews.utils import chunk_text, fingerprint, normalize_arabic, strip_html, truncate

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
#  أدوات
# --------------------------------------------------------------------------- #
def sample_article(**kw) -> Article:
    defaults = dict(
        source_id="test_src",
        source_name="مصدر تجريبي",
        title="افتتاح مشروع تقني كبير في بغداد لدعم الشركات الناشئة",
        summary="أعلنت الحكومة اليوم عن إطلاق مشروع تقني كبير في العاصمة بغداد "
                "يهدف إلى دعم الشركات الناشئة وتوفير آلاف فرص العمل للشباب.",
        url="https://example.com/news/123",
        lang="ar",
        tags=["تقنية", "اقتصاد"],
    )
    defaults.update(kw)
    return Article(**defaults)


def sample_channel(**kw) -> Channel:
    defaults = dict(id="tg_test", name="قناة اختبار", platform="telegram",
                    sources=["*"], max_chars=4096, add_hashtags=3)
    defaults.update(kw)
    return Channel(**defaults)


# --------------------------------------------------------------------------- #
#  1) أدوات النصوص والحماية من التكرار
# --------------------------------------------------------------------------- #
def test_strip_html_and_entities():
    raw = "<p>خبر <b>مهم</b> &amp; عاجل</p><script>bad()</script>"
    clean = strip_html(raw)
    assert "خبر" in clean and "&amp;" not in clean and "bad()" not in clean


def test_fingerprint_is_stable_and_arabic_normalized():
    a = fingerprint("أعلنت الحكومة عن مشروع جديد")
    b = fingerprint("اعلنت الحكومه عن مشروع جديد")
    assert a == b, "يجب أن يتساوى النصّان بعد توحيد الحروف العربية"


def test_normalize_arabic():
    assert normalize_arabic("إسلاميّة") == normalize_arabic("اسلاميه")


def test_chunk_text_respects_limit():
    text = ("جملة تجريبية طويلة " * 200).strip()
    chunks = chunk_text(text, 500)
    assert chunks and all(len(c) <= 500 for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks) [:20] in text.replace(" ", "")[:20]


def test_truncate_never_exceeds_limit():
    assert len(truncate("كلمة " * 100, 50)) <= 50


# --------------------------------------------------------------------------- #
#  2) تحليل RSS
# --------------------------------------------------------------------------- #
def test_parse_rss_fixture():
    raw = (FIXTURES / "sample_rss.xml").read_bytes()
    entries = _parse_with_etree(raw)
    assert len(entries) == 2
    assert entries[0]["title"].startswith("عنوان الخبر الأول")
    assert entries[0]["link"].startswith("https://")


# --------------------------------------------------------------------------- #
#  3) التوجيه
# --------------------------------------------------------------------------- #
def test_router_matches_source_and_tags():
    tg = sample_channel(id="tg_all", sources=["*"])
    tech = sample_channel(id="tg_tech", sources=["test_src"], tags=["تقنية"])
    sport = sample_channel(id="tg_sport", sources=["*"], tags=["رياضة"])
    router = ContentRouter([tg, tech, sport])
    targets = {c.id for c in router.targets(sample_article())}
    assert "tg_all" in targets and "tg_tech" in targets
    assert "tg_sport" not in targets


def test_router_keyword_matching():
    eco = sample_channel(id="tg_eco", sources=["*"], keywords=["بغداد"])
    router = ContentRouter([eco])
    assert router.targets(sample_article())
    assert not router.targets(sample_article(title="طقس حار في البصرة", summary="درجات الحرارة"))


def test_router_respects_source_whitelist():
    router = ContentRouter([sample_channel(id="only_bbc", sources=["bbc_arabic"])])
    assert router.targets(sample_article()) == []


# --------------------------------------------------------------------------- #
#  4) استخلاص JSON من مخرجات النموذج
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    '{"title":"t","body":"b"}',
    '```json\n{"title":"t","body":"b"}\n```',
    'إليك النتيجة:\n{"title":"t","body":"b"}\nشكراً',
    '{"title":"t","body":"b",}',
])
def test_extract_json_variants(raw):
    data = extract_json(raw)
    assert data and data["title"] == "t"


def test_extract_json_rejects_garbage():
    assert extract_json("لا يوجد JSON هنا") is None


# --------------------------------------------------------------------------- #
#  5) الصياغة + القالب الاحتياطي
# --------------------------------------------------------------------------- #
def test_writer_uses_ai_when_available(tmp_path):
    payload = json.dumps({
        "title": "بغداد تطلق مشروعاً تقنياً لدعم الشركات الناشئة",
        "body": "أعلنت الحكومة عن مشروع تقني واسع في بغداد يستهدف دعم الشركات الناشئة "
                "وتوفير فرص عمل للشباب، في خطوة تهدف لتعزيز الاقتصاد الرقمي." ,
        "hashtags": ["بغداد", "تقنية", "ريادة_الأعمال"],
        "category": "اقتصاد",
        "key_points": ["دعم الشركات الناشئة", "آلاف فرص العمل"],
        "sensitive": False,
    })
    body = json.dumps({"candidates": [{"content": {"parts": [{"text": payload}]}}]})
    engine = KeyRotator(keys=["K"], models=["m1"], health_path=tmp_path / "h.json",
                        post_fn=lambda *a: (200, body))
    settings = Settings()
    writer = ContentWriter(engine, settings)
    post = writer.write(sample_article(), sample_channel())

    assert not post.used_fallback
    assert "بغداد" in post.title
    assert post.hashtags and post.category == "اقتصاد"
    rendered = post.render(sample_channel())
    assert "#" in rendered and "المصدر" in rendered


def test_writer_falls_back_without_engine():
    writer = ContentWriter(None, Settings())
    post = writer.write(sample_article(), sample_channel())
    assert post.used_fallback
    assert post.body
    assert post.source_url == "https://example.com/news/123"


def test_post_render_respects_x_limit():
    post = Post(channel_id="x", platform="x", title="عنوان", body="ن" * 500,
                hashtags=["خبر"], source_url="https://e.co/1")
    text = post.render(sample_channel(id="x1", platform="x", max_chars=280))
    assert len(text) <= 280


# --------------------------------------------------------------------------- #
#  6) الحالة: منع النشر المزدوج
# --------------------------------------------------------------------------- #
def test_state_prevents_double_processing(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.begin_run()
    fp = sample_article().fp

    assert not state.has_article(fp)
    state.mark(fp, "posted")
    store.save(state)

    reloaded = store.load()
    assert reloaded.has_article(fp), "المقال المنشور يجب ألا يُعالج مجدداً"
    assert reloaded.runs == 1


def test_state_retries_failed_after_n_runs(tmp_path):
    state = State()
    fp = "abc"
    state.begin_run()
    state.mark(fp, "failed")
    assert state.has_article(fp, retry_failed_after_runs=3)
    for _ in range(3):
        state.begin_run()
    assert not state.has_article(fp, retry_failed_after_runs=3)


def test_state_survives_corruption(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ هذا ليس JSON", encoding="utf-8")
    state = StateStore(path).load()          # يجب ألا يرفع استثناء
    assert state.runs == 0


# --------------------------------------------------------------------------- #
#  7) النشر (dry-run والمصنع)
# --------------------------------------------------------------------------- #
def test_console_publisher_prints(capsys):
    pub = ConsolePublisher(sample_channel(platform="console"), dry_run=False)
    result = pub.publish(Post(channel_id="c", platform="console", title="عنوان", body="متن"))
    assert result.ok
    assert "عنوان" in capsys.readouterr().out


def test_dry_run_never_calls_api(monkeypatch):
    ch = sample_channel(platform="telegram", credentials={"bot_token": "TELEGRAM_BOT_TOKEN",
                                                          "chat_id": "TELEGRAM_CHAT_ID"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@test")
    pub = build_publisher(ch, dry_run=True)
    called = {"n": 0}
    monkeypatch.setattr(pub, "_request", lambda *a, **k: called.__setitem__("n", 1))
    result = pub.publish(Post(channel_id="tg", platform="telegram", title="t", body="b"))
    assert result.ok and result.dry_run and called["n"] == 0


def test_missing_credentials_skips_gracefully(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    ch = sample_channel(platform="telegram", credentials={"bot_token": "TELEGRAM_BOT_TOKEN",
                                                          "chat_id": "TELEGRAM_CHAT_ID"})
    result = build_publisher(ch, dry_run=False).publish(
        Post(channel_id="tg", platform="telegram", title="t", body="b"))
    assert not result.ok and result.skipped


def test_telegram_publish_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@test")
    ch = sample_channel(platform="telegram", credentials={"bot_token": "TELEGRAM_BOT_TOKEN",
                                                          "chat_id": "TELEGRAM_CHAT_ID"})
    pub = build_publisher(ch, dry_run=False)
    monkeypatch.setattr(pub, "_request", lambda *a, **k: (200, json.dumps({"ok": True, "result": {"message_id": 42}})))
    result = pub.publish(Post(channel_id="tg", platform="telegram", title="t", body="b"))
    assert result.ok and result.ref == "42"


# --------------------------------------------------------------------------- #
#  8) تحميل الإعدادات
# --------------------------------------------------------------------------- #
#  متغيّرات تشغيلية تُغيّر سلوك load_config. الاختبارات التي تفحص **الملفات**
#  يجب أن تعزلها، وإلا فالنتيجة تتوقّف على بيئة من يشغّلها:
#    · AI_DISABLED=true في CI  → keys تُفرَّغ فيفشل فحص المفاتيح
#    · REQUIRE_REVIEW=true      → review يُفرض
#    · ENABLED_CHANNELS/SOURCES → قوائم تُقلَّم
ENV_NOISE = ("AI_DISABLED", "REQUIRE_REVIEW", "ENABLED_CHANNELS", "ENABLED_SOURCES",
             "GEMINI_API_KEYS", "GEMINI_API_KEY", "GEMINI_MODELS", "TELEGRAM_BOT_TOKEN",
             "TELEGRAM_CHAT_ID", "STATE_FILE", "MAX_ITEMS_PER_RUN", "TZ_OFFSET")


def isolate_env(monkeypatch):
    """يعزل الاختبار عن أي متغيّر بيئة يغيّر نتيجة تحميل الإعدادات."""
    for name in ENV_NOISE:
        monkeypatch.delenv(name, raising=False)
    for i in range(1, 11):
        monkeypatch.delenv(f"GEMINI_API_KEY_{i}", raising=False)


def test_load_config_from_repo(monkeypatch):
    isolate_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEYS", "k1,k2,k3")
    settings = load_config("config")
    assert len(settings.ai.keys) == 3
    assert settings.sources and settings.channels
    assert all(c.platform in ("telegram", "facebook", "x", "console") for c in settings.channels)


def test_load_config_reads_numbered_keys(monkeypatch):
    isolate_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY_1", "key-one-long-enough")
    monkeypatch.setenv("GEMINI_API_KEY_3", "key-three-long-enough")
    settings = load_config("config")
    assert settings.ai.keys == ["key-one-long-enough", "key-three-long-enough"]


def test_ai_disabled_env_empties_keys(monkeypatch):
    """AI_DISABLED سلوك **مقصود** لا خطأ: يُفرّغ المفاتيح ليُستخدم النصّ الاحتياطي."""
    isolate_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEYS", "k1,k2,k3")
    monkeypatch.setenv("AI_DISABLED", "true")
    assert load_config("config").ai.keys == []


def test_run_once_has_all_names_it_uses():
    """حرس ضد أخطاء NameError في المسار الكامل — كلها كانت تُسقِط النشر بصمت.

    حدث فعلاً: ControlStore وKIND_SCHEDULED كانا مستخدمَين وغير مستوردَين،
    فكان التشغيل يموت بعد سحب الأخبار وقبل النشر.
    """
    import autonews.main as M

    for name in ("ControlStore", "KIND_SCHEDULED", "KIND_APPROVAL", "decide",
                 "due_iso", "local_hour", "minutes_until_open", "preview_text"):
        assert hasattr(M, name), f"main.py يستخدم {name} بلا استيراد"
