"""اختبارات بوت إعداد القنوات (بمحاكاة تيليجرام — بلا شبكة)."""
from __future__ import annotations

import json

import pytest

from autonews import chatid, setup_bot
from autonews.setup_bot import BotConfig, SetupBot, parse_target
from autonews.setup_store import SetupStore

ADMIN = "555"

CHATS = {
    "@my_anime": {"id": -1001111111111, "title": "قناة الأنمي",
                  "type": "channel", "username": "my_anime"},
    "@my_main": {"id": -1002222222222, "title": "القناة الرئيسية",
                 "type": "channel", "username": "my_main"},
}
# تيليجرام يقبل المعرّف الرقمي أيضاً — المحاكاة يجب أن تعرف الاثنين
CHATS["-1001111111111"] = CHATS["@my_anime"]
CHATS["-1002222222222"] = CHATS["@my_main"]


@pytest.fixture
def bot(tmp_path, monkeypatch):
    sent: list[dict] = []

    def fake_call(token, method, params=None, timeout=20):
        p = params or {}
        if method == "getMe":
            return True, {"id": 999, "username": "auto_bot", "first_name": "AutoNews"}
        if method == "sendMessage":
            sent.append({"chat": p.get("chat_id"), "text": p.get("text", ""),
                         "kb": p.get("reply_markup")})
            return True, {"message_id": len(sent)}
        if method == "answerCallbackQuery":
            return True, True
        if method == "getChat":
            chat = CHATS.get(str(p.get("chat_id")))
            return (True, chat) if chat else (False, "Bad Request: chat not found")
        if method == "getChatMember":
            return True, {"status": "administrator"}
        return False, "unknown"

    monkeypatch.setattr(chatid, "_call", fake_call)
    monkeypatch.setattr(setup_bot, "_call", fake_call)
    store = SetupStore(path=tmp_path / "setup.json")
    cfg = BotConfig(token="t", admin_chat_id=ADMIN, setup_file=str(tmp_path / "setup.json"))
    b = SetupBot(cfg, known_channels=["tg_main", "tg_anime", "tg_sports"], store=store)
    b.sent = sent          # type: ignore[attr-defined]
    return b


def msg(text: str, chat: str = ADMIN, user: int = 555) -> dict:
    return {"message": {"chat": {"id": int(chat) if chat.lstrip("-").isdigit() else chat},
                        "from": {"id": user}, "text": text}}


def click(data: str, chat: str = ADMIN, user: int = 555) -> dict:
    return {"callback_query": {"id": "cb", "from": {"id": user},
                               "message": {"chat": {"id": int(chat) if chat.lstrip("-").isdigit() else chat}},
                               "data": data}}


# --------------------------------------------------------------------------- #
#  ١) تفسير المعرّفات
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,kind,value", [
    ("-1001234567890", "id", "-1001234567890"),
    ("@my_channel", "username", "@my_channel"),
    ("my_channel", "username", "@my_channel"),
    ("https://t.me/my_channel", "username", "@my_channel"),
    ("https://t.me/+AbCdEf123", "invite", "https://t.me/+AbCdEf123"),
    ("مرحبا", "unknown", "مرحبا"),
])
def test_parse_target(text, kind, value):
    assert parse_target(text) == (kind, value)


# --------------------------------------------------------------------------- #
#  ٢) اللوحة والأزرار
# --------------------------------------------------------------------------- #
def test_start_shows_keyboard_with_all_channels(bot):
    bot.handle_update(msg("/start"))
    last = bot.sent[-1]
    kb = json.loads(last["kb"])["inline_keyboard"]
    labels = [b["text"] for row in kb for b in row]
    assert any("الأنمي" in l for l in labels)
    assert any("الرياضة" in l for l in labels)
    assert any("فحص الجميع" in l for l in labels)


def test_keyboard_shows_status_icons(bot):
    bot.store.bind("tg_anime", "-1001111111111", title="قناة الأنمي")
    bot.handle_update(msg("/start"))
    kb = json.loads(bot.sent[-1]["kb"])["inline_keyboard"]
    labels = [b["text"] for row in kb for b in row]
    anime = next(l for l in labels if "الأنمي" in l)
    assert anime.startswith("✅"), "القناة المضبوطة تظهر بعلامة صح"


# --------------------------------------------------------------------------- #
#  ٣) المسار الكامل: زر → معرّف → تحقّق → حفظ
# --------------------------------------------------------------------------- #
def test_full_flow_binds_channel(bot):
    bot.handle_update(click("pick:tg_anime"))
    assert "أرسل الآن" in bot.sent[-1]["text"]
    assert bot.store.awaiting() == "tg_anime"

    bot.handle_update(msg("@my_anime"))
    assert "تمّ الربط بنجاح" in bot.sent[-1]["text"]
    b = bot.store.get("tg_anime")
    assert b.verified and b.chat_id == "-1001111111111"
    assert b.title == "قناة الأنمي" and b.is_admin
    assert bot.store.awaiting() == "", "تُلغى حالة الانتظار بعد النجاح"


def test_binding_is_persisted(bot, tmp_path):
    bot.handle_update(click("pick:tg_main"))
    bot.handle_update(msg("@my_main"))
    saved = SetupStore.load(tmp_path / "setup.json")
    assert saved.get("tg_main").verified
    assert saved.get("tg_main").chat_id == "-1002222222222"


def test_invalid_channel_gives_guidance(bot):
    bot.handle_update(click("pick:tg_sports"))
    bot.handle_update(msg("@does_not_exist"))
    text = bot.sent[-1]["text"]
    assert "❌" in text and "الحلّ" in text or "السبب الغالب" in text
    assert not bot.store.get("tg_sports").verified
    assert bot.store.get("tg_sports").error


def test_unknown_input_asks_again(bot):
    bot.handle_update(click("pick:tg_anime"))
    bot.handle_update(msg("هلا"))
    assert "لم أفهم" in bot.sent[-1]["text"]


def test_cancel_clears_pending(bot):
    bot.handle_update(click("pick:tg_anime"))
    bot.handle_update(msg("/cancel"))
    assert bot.store.awaiting() == ""
    assert "أُلغيت" in bot.sent[-1]["text"]


# --------------------------------------------------------------------------- #
#  ٤) الصلاحية (🔒 لا يضبط قنواتك غيرك)
# --------------------------------------------------------------------------- #
def test_outsider_is_rejected(bot):
    bot.handle_update(msg("@my_anime", chat="999", user=999))
    assert "غير مسموح" in bot.sent[-1]["text"]
    assert not bot.store.get("tg_main").verified


def test_allowed_users_list_works(tmp_path, monkeypatch):
    def fake_call(token, method, params=None, timeout=20):
        if method == "getMe":
            return True, {"id": 1, "username": "b", "first_name": "B"}
        if method == "sendMessage":
            return True, {"message_id": 1}
        return True, {}
    monkeypatch.setattr(setup_bot, "_call", fake_call)
    monkeypatch.setattr(chatid, "_call", fake_call)
    b = SetupBot(BotConfig(token="t", allowed_users=(42,), admin_chat_id=""),
                 known_channels=["tg_main"],
                 store=SetupStore(path=tmp_path / "s.json"))
    assert b._allowed(42, "1") is True
    assert b._allowed(7, "1") is False


def test_fail_closed_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_bot, "_call", lambda *a, **k: (True, {}))
    b = SetupBot(BotConfig(token="t"), known_channels=["tg_main"],
                 store=SetupStore(path=tmp_path / "s.json"))
    assert b._allowed(1, "1") is False, "بلا إعداد صلاحية ⇒ نرفض (fail-closed)"


# --------------------------------------------------------------------------- #
#  ٥) الإجراءات الجماعية
# --------------------------------------------------------------------------- #
def test_verify_all_updates_status(bot):
    bot.store.bind("tg_anime", "-1001111111111")
    bot.handle_update(click("verify_all"))
    assert "فحص كل القنوات" in bot.sent[-1]["text"]
    assert bot.store.get("tg_anime").verified


def test_test_all_sends_real_test(bot):
    bot.store.bind("tg_anime", "-1001111111111", title="قناة الأنمي")
    bot.handle_update(click("test_all"))
    assert "اختبار النشر" in bot.sent[-1]["text"]


def test_status_lists_missing(bot):
    bot.handle_update(msg("/status"))
    assert "لم تُضبط" in bot.sent[-1]["text"]


# --------------------------------------------------------------------------- #
#  ٦) الربط بالتوجيه الحقيقي
# --------------------------------------------------------------------------- #
def test_stored_id_is_used_by_config(tmp_path, monkeypatch):
    """المعرّف المحفوظ من البوت يجب أن يقرأه النظام."""
    import shutil

    from autonews.config_loader import load_config

    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    for f in ("channels.yml", "settings.yml", "occasions.yml"):
        shutil.copy(f"config/{f}", cfgdir / f)

    store = SetupStore(path=tmp_path / "setup.json")
    store.bind("tg_anime", "-1009999999999", title="قناة الأنمي")

    monkeypatch.setattr(
        "autonews.config_loader._setup_bindings",
        lambda path="": {"tg_anime": "-1009999999999"})
    monkeypatch.delenv("TELEGRAM_ANIME_CHAT_ID", raising=False)
    settings = load_config(str(cfgdir))
    anime = next(c for c in settings.channels if c.id == "tg_anime")
    assert anime.env("chat_id") == "-1009999999999"


# --------------------------------------------------------------------------- #
#  ق٧: إدارة القنوات من البوت بلا معرّفات ولا أسرار
# --------------------------------------------------------------------------- #
def _bot_with_config(tmp_path, monkeypatch):
    import shutil
    from unittest import mock

    import autonews.setup_bot as sb

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        shutil.copy(f"config/{name}", cfg / name)
    monkeypatch.setattr(sb, "send_message", lambda *a, **k: True)
    monkeypatch.setattr(sb, "bot_info", lambda t: {})
    monkeypatch.setattr(sb.SetupStore, "save", lambda self: None)
    bot = sb.SetupBot(sb.BotConfig(token="T"), known_channels=[])
    bot.config_dir = str(cfg)
    bot._allowed = lambda *a, **k: True
    sent: list[str] = []
    monkeypatch.setattr(sb.SetupBot, "send",
                        lambda self, c, t, **k: sent.append(t) or True)
    with mock.patch.object(sb, "send_message", lambda *a, **k: True):
        pass
    return bot, cfg, sent


def test_add_channel_by_name_only_auto_classifies(tmp_path, monkeypatch):
    """🎯 المتطلب ٢: إضافة قناة بالاسم/الوصف فقط → الاختصاص والمصادر تلقائياً."""
    from autonews.config_loader import load_config
    from autonews.sectors import sector_of

    bot, cfg, sent = _bot_with_config(tmp_path, monkeypatch)
    bot.store.start_awaiting("", telegram_user=0, kind="new_channel")
    bot._create_channel(111, "قناة الصحة والطب — كل ما يخص الطب والصحة والأدوية")

    ids = [c.id for c in load_config(str(cfg)).channels]
    cid = next(i for i in ids if i.startswith("tg_health"))
    assert sector_of(cid, config_dir=str(cfg)).key == "health"
    ch = next(c for c in load_config(str(cfg)).channels if c.id == cid)
    assert ch.sources != ["*"], "قناة جديدة بلا مصادر = صمت"
    assert "🎯 الاختصاص" in sent[-1]


def test_add_channel_old_format_still_works(tmp_path, monkeypatch):
    """لا حذف لميزة: الصيغة القديمة (معرّف | اسم | وسوم) ما زالت مقبولة."""
    from autonews.config_loader import load_config

    bot, cfg, _ = _bot_with_config(tmp_path, monkeypatch)
    bot.store.start_awaiting("", telegram_user=0, kind="new_channel")
    bot._create_channel(111, "tg_crypto | قناة العملات | عملات, بيتكوين")
    ch = next((c for c in load_config(str(cfg)).channels if c.id == "tg_crypto"), None)
    assert ch is not None and ch.tags == ["عملات", "بيتكوين"]


def test_warn_when_two_channels_share_sector(tmp_path, monkeypatch):
    """الخلط لا يكون صامتاً: قناة ثانية بنفس الاختصاص تُنبّه."""
    bot, cfg, sent = _bot_with_config(tmp_path, monkeypatch)
    bot.store.start_awaiting("", telegram_user=0, kind="new_channel")
    bot._create_channel(111, "قناة الأنمي والمانغا — أخبار الأنمي والمانغا")
    assert "تنبيه" in sent[-1] and "tg_anime" in sent[-1]


def test_unknown_name_stays_unrouted_until_topic_is_known(tmp_path, monkeypatch):
    """الاسم المبهم لا يُربط بالمصادر العامة ولا يستقبل أخباراً عشوائية."""
    from autonews.config_loader import load_config

    bot, cfg, sent = _bot_with_config(tmp_path, monkeypatch)
    before = {c.id for c in load_config(str(cfg)).channels}
    bot.store.start_awaiting("", telegram_user=0, kind="new_channel")
    bot._create_channel(111, "قناة رقم ٧")
    added = [c for c in load_config(str(cfg)).channels if c.id not in before]
    assert len(added) == 1
    assert added[0].id.startswith("tg_new")
    assert "لم أستطع تحديد اختصاص" in sent[-1]
    assert added[0].sources == []


def test_set_max_posts_from_bot(tmp_path, monkeypatch):
    """🔢 المتطلب ٤: تعديل عدد المنشورات من البوت (يقبل الأرقام العربية)."""
    from autonews.config_loader import load_config

    bot, cfg, sent = _bot_with_config(tmp_path, monkeypatch)
    bot._set_max_posts(111, "tg_tech", "٦")
    ch = next(c for c in load_config(str(cfg)).channels if c.id == "tg_tech")
    assert ch.max_posts_per_run == 6
    assert "✅" in sent[-1]


def test_set_max_posts_rejects_text(tmp_path, monkeypatch):
    bot, cfg, sent = _bot_with_config(tmp_path, monkeypatch)
    bot._set_max_posts(111, "tg_tech", "كثير")
    assert "❌" in sent[-1]
