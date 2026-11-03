"""اختبارات أداة معرّف القناة (بمحاكاة تيليجرام — بلا شبكة)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from autonews import chatid
from autonews.chatid import ChatInfo, check_chat, discover_chats, env_block, render_report

BOT = {"id": 999, "username": "my_bot", "first_name": "AutoNews"}


@pytest.fixture
def fake_tg(monkeypatch):
    """محاكاة واجهة تيليجرام: قناة صحيحة + مجموعة معطّلة."""
    def _call(token, method, params=None, timeout=20):
        p = params or {}
        if method == "getMe":
            return True, BOT
        if method == "getUpdates":
            return True, [
                {"channel_post": {"chat": {"id": -1001234567890, "title": "قناتي الإخبارية",
                                           "type": "channel", "username": "my_channel"}}},
                {"message": {"chat": {"id": -1005555555555, "title": "قناة معطّلة",
                                      "type": "channel"}}},
            ]
        if method == "getChat":
            if str(p.get("chat_id")).endswith("1234567890"):
                return True, {"id": -1001234567890, "title": "قناتي الإخبارية",
                              "type": "channel", "username": "my_channel"}
            return False, "Bad Request: chat not found"
        if method == "getChatMember":
            if str(p.get("chat_id")).endswith("1234567890"):
                return True, {"status": "administrator"}
            return True, {"status": "left"}
        if method == "sendMessage":
            return True, {"message_id": 4242}
        return False, "unknown"
    monkeypatch.setattr(chatid, "_call", _call)
    return _call


# --------------------------------------------------------------------------- #
#  الاكتشاف والتحقّق
# --------------------------------------------------------------------------- #
def test_discover_finds_chats(fake_tg):
    chats = discover_chats("t")
    assert len(chats) == 2
    assert {c.id for c in chats} == {-1001234567890, -1005555555555}


def test_check_chat_admin_ok(fake_tg):
    info = check_chat("t", "-1001234567890")
    assert info.ok() and info.is_admin and info.can_post
    assert info.title == "قناتي الإخبارية" and info.mention == "@my_channel"


def test_check_chat_missing_returns_error(fake_tg):
    info = check_chat("t", "-1009999999999")
    assert not info.ok() and "chat not found" in info.error


def test_check_chat_non_admin_channel_fails(fake_tg, monkeypatch):
    def _call(token, method, params=None, timeout=20):
        if method == "getMe":
            return True, BOT
        if method == "getChat":
            return True, {"id": -100, "title": "قناة", "type": "channel"}
        if method == "getChatMember":
            return True, {"status": "member"}
        return False, "x"
    monkeypatch.setattr(chatid, "_call", _call)
    info = check_chat("t", "-100")
    assert not info.ok()
    assert "مشرف" in info.error


def test_check_chat_rejects_private_type(monkeypatch):
    monkeypatch.setattr(chatid, "_call", lambda *a, **k: (True, {"id": 1, "type": "private"}))
    info = check_chat("t", "1")
    assert not info.ok() and "لا يصلح" in info.error


def test_send_test_success(fake_tg):
    ok, mid = chatid.send_test("t", "-1001234567890")
    assert ok and mid == "4242"


# --------------------------------------------------------------------------- #
#  التقرير
# --------------------------------------------------------------------------- #
def test_report_lists_ids_and_secrets(fake_tg):
    infos = [check_chat("t", i.id) for i in discover_chats("t")]
    report = render_report(BOT, infos)
    assert "-1001234567890" in report
    assert "GitHub Secrets" in report
    assert "TELEGRAM_CHAT_ID=" in report


def test_report_no_chats_gives_guidance(monkeypatch):
    monkeypatch.setattr(chatid, "_call", lambda *a, **k: (True, []))
    report = render_report(BOT, discover_chats("t"))
    assert "لم أجد أي محادثة" in report and "مشرفاً" in report


def test_report_invalid_token():
    assert "غير صالح" in render_report(None, [])


def test_env_block_contains_id(fake_tg):
    info = check_chat("t", "-1001234567890")
    block = env_block(info)
    assert "TELEGRAM_CHAT_ID=-1001234567890" in block and "قناتي الإخبارية" in block


# --------------------------------------------------------------------------- #
#  الحفظ
# --------------------------------------------------------------------------- #
def test_save_to_channels_writes_extra(tmp_path):
    from autonews.chatid_main import save_to_channels

    f = tmp_path / "channels.yml"
    shutil.copy("config/channels.yml", f)
    msg = save_to_channels(f, "tg_main", "-1001234567890")
    assert msg.startswith("✅")

    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    ch = next(c for c in data["channels"] if c["id"] == "tg_main")
    assert ch["extra"]["ENV_CHAT_ID"] == "-1001234567890"
    # السرّ يبقى ربطاً بمتغيّر بيئة
    assert ch["credentials"]["chat_id"] == "TELEGRAM_CHAT_ID"


def test_save_to_channels_keeps_other_channels(tmp_path):
    from autonews.chatid_main import save_to_channels

    f = tmp_path / "channels.yml"
    shutil.copy("config/channels.yml", f)
    before = yaml.safe_load(f.read_text(encoding="utf-8"))
    save_to_channels(f, "tg_main", "-1001234567890")
    after = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert len(before["channels"]) == len(after["channels"])
    others_b = [c for c in before["channels"] if c["id"] != "tg_main"]
    others_a = [c for c in after["channels"] if c["id"] != "tg_main"]
    assert others_b == others_a, "يجب ألّا تتغيّر القنوات الأخرى"


def test_save_to_channels_unknown_channel(tmp_path):
    from autonews.chatid_main import save_to_channels

    f = tmp_path / "channels.yml"
    shutil.copy("config/channels.yml", f)
    assert "لم أجد القناة" in save_to_channels(f, "nope", "-1001")


def test_saved_id_overrides_env(tmp_path, monkeypatch):
    from autonews.chatid_main import main

    cfg = tmp_path / "config"
    cfg.mkdir()
    shutil.copy("config/channels.yml", cfg / "channels.yml")
    shutil.copy("config/settings.yml", cfg / "settings.yml")
    shutil.copy("config/occasions.yml", cfg / "occasions.yml")

    from autonews.chatid_main import save_to_channels
    save_to_channels(cfg / "channels.yml", "tg_main", "-1007777777777")

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@old_channel")
    # اعزل الاختبار عن ملف الحالة الحقيقي الموجود على جهاز المطوّر.
    from autonews import config_loader
    monkeypatch.setattr(config_loader, "_setup_bindings", lambda *a, **k: {})
    from autonews.config_loader import load_config
    settings = load_config(str(cfg))
    ch = next(c for c in settings.channels if c.id == "tg_main")
    assert ch.env("chat_id") == "-1007777777777", "المعرّف المحفوظ يتجاوز متغيّر البيئة"


def test_save_to_env_file(tmp_path):
    from autonews.chatid_main import save_to_env

    env = tmp_path / ".env"
    env.write_text("TELEGRAM_CHAT_ID=@old\nOTHER=1\n", encoding="utf-8")
    save_to_env(env, "123:ABC", "-100123")
    text = env.read_text(encoding="utf-8")
    assert "TELEGRAM_CHAT_ID=-100123" in text
    assert "OTHER=1" in text, "لا يمسح إعدادات أخرى"
    assert text.count("TELEGRAM_CHAT_ID") == 1, "لا يكرّر السطر"


def test_main_without_token(monkeypatch, capsys):
    from autonews.chatid_main import main

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_BOT_TOKEN", raising=False)
    assert main([]) == 2
    assert "لا توكن" in capsys.readouterr().out
