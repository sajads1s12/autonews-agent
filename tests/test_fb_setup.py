"""اختبارات مساعد إعداد فيسبوك — بالردود المحاكاة (بلا شبكة)."""
from __future__ import annotations

import autonews.fb_main as fb


def test_resolve_page_token_picks_requested_page(monkeypatch):
    pages = {"data": [
        {"id": "111", "name": "صفحة أ", "access_token": "tokA"},
        {"id": "222", "name": "صفحة ب", "access_token": "tokB"},
    ]}
    monkeypatch.setattr(fb, "_get", lambda *a, **k: pages)
    token, error, page = fb.resolve_page_token("user", "222")
    assert token == "tokB" and not error and page["name"] == "صفحة ب"


def test_resolve_page_token_lists_available_on_mismatch(monkeypatch):
    pages = {"data": [{"id": "111", "name": "صفحة أ", "access_token": "tokA"}]}
    monkeypatch.setattr(fb, "_get", lambda *a, **k: pages)
    token, error, _ = fb.resolve_page_token("user", "999")
    assert not token
    assert "لم أجد الصفحة 999" in error and "صفحة أ" in error


def test_resolve_page_token_without_pages_explains_fix(monkeypatch):
    monkeypatch.setattr(fb, "_get", lambda *a, **k: {"data": []})
    token, error, _ = fb.resolve_page_token("user")
    assert not token and "السماح بالصفحات" in error or "Allow access" in error


def test_resolve_propagates_api_error(monkeypatch):
    monkeypatch.setattr(fb, "_get", lambda *a, **k: {"error": {"message": "Invalid token"}})
    token, error, _ = fb.resolve_page_token("bad")
    assert not token and "Invalid token" in error


def test_check_without_token(monkeypatch, capsys):
    for var in ("FACEBOOK_PAGE_TOKEN", "FACEBOOK_USER_TOKEN", "FACEBOOK_PAGE_ID"):
        monkeypatch.delenv(var, raising=False)
    assert fb.check() == 2
    out = capsys.readouterr().out
    assert "لا توكن" in out


def test_check_without_page_id(monkeypatch, capsys):
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "tok")
    monkeypatch.delenv("FACEBOOK_PAGE_ID", raising=False)
    assert fb.check() == 2
    assert "FACEBOOK_PAGE_ID" in capsys.readouterr().out


def test_check_success_reports_permanent_token(monkeypatch, capsys):
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "ptok")
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "123")

    def fake_get(path, params, token, timeout=20):
        if path == "123":
            return {"id": "123", "name": "أخبار العراق", "fan_count": 1520}
        if path == "me/permissions":
            return {"data": [{"permission": "pages_manage_posts", "status": "granted"},
                             {"permission": "pages_read_engagement", "status": "granted"}]}
        if path == "debug_token":
            return {"data": {"is_valid": True}}
        return {}

    monkeypatch.setattr(fb, "_get", fake_get)
    assert fb.check() == 0
    out = capsys.readouterr().out
    assert "أخبار العراق" in out and "1520" in out
    assert "دائم" in out


def test_check_degrades_token_needs_permissions(monkeypatch, capsys):
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "ptok")
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "123")
    monkeypatch.setattr(fb, "_get", lambda path, *a, **k: (
        {"id": "123", "name": "ص"} if path == "123" else {}
    ))
    fb.check()
    # توكن الصفحة لا يكشف الصلاحيات ⇒ نُرشد للاختبار الفعلي بدل ادّعاء الفحص
    assert "الصلاحيات تُفحص" in capsys.readouterr().out


def test_test_post_dry_run_does_not_publish(monkeypatch, capsys):
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "123")
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "tok")
    assert fb.test_post(dry_run=True) == 0
    out = capsys.readouterr().out
    assert "[TEST]" in out and "--live" in out


def test_token_output_marks_as_secret(monkeypatch, capsys):
    monkeypatch.setenv("FACEBOOK_USER_TOKEN", "user")
    monkeypatch.setattr(fb, "resolve_page_token", lambda u, p="": ("ptok", "", {"id": "9", "name": "ص"}))
    assert fb.show_token() == 0
    out = capsys.readouterr().out
    assert "FACEBOOK_PAGE_TOKEN=ptok" in out and "سرّ" in out


# --------------------------------------------------------------------------- #
#  🔬 التشخيص — الحالة الواقعية: صلاحيات موجودة + توكن مستخدم + صفحات فارغة
# --------------------------------------------------------------------------- #
def test_diagnose_flags_user_token(monkeypatch, capsys):
    monkeypatch.setattr(fb, "_get", lambda path, *a, **k: (
        {"data": {"type": "USER", "is_valid": True, "expires_at": 1793545200,
                  "scopes": ["pages_show_list", "pages_manage_posts",
                             "pages_read_engagement"]}}
        if path == "debug_token" else
        {"data": [{"id": "1", "name": "صفحتي", "access_token": "ptok"}]}
    ))
    assert fb.diagnose("tok") == 0
    out = capsys.readouterr().out
    assert "مستخدم" in out and "توكن صفحة" in out
    assert "✅ الثلاث المطلوبة" in out
    assert "صفحتي" in out


def test_diagnose_explains_empty_pages(monkeypatch, capsys):
    """أشهر فشل: الصلاحية موجودة لكن لم تُختَر الصفحة ⇒ القائمة فارغة."""
    monkeypatch.setattr(fb, "_get", lambda path, *a, **k: (
        {"data": {"type": "USER", "is_valid": True,
                  "scopes": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]}}
        if path == "debug_token" else {"data": []}
    ))
    assert fb.diagnose("tok") == 2
    out = capsys.readouterr().out
    assert "القائمة فارغة" in out and "لم تختر" in out


def test_diagnose_reports_missing_scope(monkeypatch, capsys):
    monkeypatch.setattr(fb, "_get", lambda path, *a, **k: (
        {"data": {"type": "USER", "is_valid": True, "scopes": ["pages_show_list"]}}
        if path == "debug_token" else {"data": [{"id": "1", "name": "ص", "access_token": "t"}]}
    ))
    fb.diagnose("tok")
    assert "ناقصة" in capsys.readouterr().out


def test_diagnose_invalid_token(monkeypatch, capsys):
    monkeypatch.setattr(fb, "_get", lambda *a, **k: {"error": {"message": "Invalid OAuth"}})
    assert fb.diagnose("bad") == 2
    assert "غير صالح" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  ربط الصفحة من البوت (بلا تعديل ملفات)
# --------------------------------------------------------------------------- #
def test_save_page_writes_env_override(tmp_path, capsys):
    import shutil
    from pathlib import Path

    from autonews.config_loader import load_config

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, cfg / name)

    assert fb.save_page("1234567890123456", config_dir=str(cfg)) == 0
    ch = next(c for c in load_config(str(cfg)).channels if c.id == "fb_page")
    assert ch.env("page_id") == "1234567890123456"
    # التوكن لا يُكتب في المستودع أبداً
    text = (cfg / "channels.yml").read_text(encoding="utf-8")
    assert "page_access_token" in text          # اسم المتغيّر فقط
    assert "EAA" not in text, "لا يُحفظ أي توكن في الملف"


def test_save_page_replaces_previous_value(tmp_path):
    import shutil
    from pathlib import Path

    from autonews.config_loader import load_config

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, cfg / name)
    fb.save_page("111111111111111", config_dir=str(cfg))
    fb.save_page("222222222222222", config_dir=str(cfg))
    ch = next(c for c in load_config(str(cfg)).channels if c.id == "fb_page")
    assert ch.env("page_id") == "222222222222222"


def test_unconfigured_page_id_does_not_inherit_main_channel(tmp_path):
    """حماية: قبل الضبط لا تُنشَر أخبار فيسبوك في قناة تيليجرام بالغلط."""
    import shutil
    from pathlib import Path

    from autonews.config_loader import load_config

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, cfg / name)
    # نُفرّغ المعرّف المحفوظ (المستودع الحقيقي يحمل معرّف الصفحة الآن) لنختبر
    # سلوك «قبل الضبط»: يجب ألّا تُورَّث خانة تيليجرام الرئيسية لصفحة فيسبوك.
    channels_yml = cfg / "channels.yml"
    text = channels_yml.read_text(encoding="utf-8")
    channels_yml.write_text(
        __import__("re").sub(r'(?m)^\s+ENV_PAGE_ID:.*\n', "", text), encoding="utf-8")

    loaded = load_config(str(cfg))
    page = next(c for c in loaded.channels if c.id == "fb_page")
    assert page.env("page_id") == "", "لا قيمة افتراضية تُسرَّب لقناة فيسبوك"


# --------------------------------------------------------------------------- #
#  تمديد التوكن — الفرق بين "ينتهي بعد ساعة" و"لا ينتهي أبداً"
# --------------------------------------------------------------------------- #
def test_extend_user_token_requires_app_credentials(monkeypatch):
    for var in ("FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"):
        monkeypatch.delenv(var, raising=False)
    token, note = fb.extend_user_token("short")
    assert not token and "APP_ID" in note


def test_extend_user_token_success(monkeypatch):
    monkeypatch.setattr(fb, "_get_raw", lambda *a, **k: {
        "access_token": "LONG_TOKEN", "expires_in": 5184000})
    token, note = fb.extend_user_token("short", "app", "secret")
    assert token == "LONG_TOKEN" and "60" in note


def test_extend_user_token_api_error(monkeypatch):
    monkeypatch.setattr(fb, "_get_raw", lambda *a, **k: {"error": {"message": "bad secret"}})
    token, note = fb.extend_user_token("short", "app", "wrong")
    assert not token and "bad secret" in note


def test_show_token_warns_when_not_extended(monkeypatch, capsys):
    """بلا APP_SECRET: يجب أن يحذّر أن التوكن سينتهي — لا أن يسكت."""
    monkeypatch.setenv("FACEBOOK_USER_TOKEN", "short")
    monkeypatch.delenv("FACEBOOK_APP_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
    monkeypatch.setattr(fb, "resolve_page_token", lambda u, p="": ("PT", "", {"id": "9", "name": "ص"}))
    assert fb.show_token() == 0
    out = capsys.readouterr().out
    assert "لم أُمِدّه" in out and "ساعة" in out
    assert "FACEBOOK_PAGE_TOKEN=PT" in out


def test_show_token_uses_extended_token(monkeypatch, capsys):
    monkeypatch.setenv("FACEBOOK_USER_TOKEN", "short")
    seen = {}

    def fake_resolve(user_token, page_id=""):
        seen["token"] = user_token
        return "PT", "", {"id": "9", "name": "ص"}

    monkeypatch.setattr(fb, "extend_user_token", lambda u, a="", s="": ("LONG", "مُدَّ إلى 60 يوماً"))
    monkeypatch.setattr(fb, "resolve_page_token", fake_resolve)
    fb.show_token()
    assert seen["token"] == "LONG", "يجب استخدام التوكن الممدَّد لا القصير"
    assert "مُدَّ" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  سرّ واحد يكفي: التوكن يعرف صفحته
# --------------------------------------------------------------------------- #
def test_page_from_token_reads_page_identity(monkeypatch):
    monkeypatch.setattr(fb, "_get", lambda path, *a, **k:
                        {"id": "123456", "name": "أخبار العراق"} if path == "me" else {})
    pid, name, error = fb.page_from_token("ptok")
    assert pid == "123456" and name == "أخبار العراق" and not error


def test_page_from_token_rejects_user_token(monkeypatch):
    monkeypatch.setattr(fb, "_get", lambda *a, **k: {"id": "99", "name": "سجاد"})
    # توكن مستخدم يرجّع مستخدماً لا صفحة — نكتشف ذلك بالاسم/النوع عبر debug
    monkeypatch.setattr(fb, "_get", lambda path, *a, **k: (
        {"error": {"message": "لا صفحة"}} if path == "me" else {}))
    pid, _, error = fb.page_from_token("utok")
    assert not pid and "لا صفحة" in error


def test_autofill_saves_id_from_token(monkeypatch, tmp_path, capsys):
    import shutil
    from pathlib import Path

    from autonews.config_loader import load_config

    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("channels.yml", "settings.yml", "occasions.yml"):
        src = Path("config") / name
        if src.exists():
            shutil.copy(src, cfg / name)

    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "ptok")
    monkeypatch.setattr(fb, "page_from_token", lambda t: ("987654321", "صفحتي", ""))
    monkeypatch.setattr(fb, "_get", lambda *a, **k: {"data": {"is_valid": True}})

    assert fb.autofill(config_dir=str(cfg)) == 0
    ch = next(c for c in load_config(str(cfg)).channels if c.id == "fb_page")
    assert ch.env("page_id") == "987654321"
    out = capsys.readouterr().out
    assert "دائم" in out


def test_autofill_without_token(capsys, monkeypatch):
    monkeypatch.delenv("FACEBOOK_PAGE_TOKEN", raising=False)
    assert fb.autofill() == 2
    assert "ضع التوكن" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  تحويل معرّف القناة القصير إلى الشكل الذي يفهمه Telegram
# --------------------------------------------------------------------------- #
def test_to_channel_handle_fixes_short_id():
    from autonews.chatid import to_channel_handle

    # ما يعرضه Telegram في التطبيق (10 أرقام) يجب أن يصبح -100…
    assert to_channel_handle("6444047765") == "-1006444047765"


def test_to_channel_handle_keeps_full_id():
    from autonews.chatid import to_channel_handle

    assert to_channel_handle("-1006444047765") == "-1006444047765"


def test_to_channel_handle_passes_through_username():
    from autonews.chatid import to_channel_handle

    assert to_channel_handle("@my_channel") == "@my_channel"
    assert to_channel_handle("") == ""
