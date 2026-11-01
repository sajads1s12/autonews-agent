"""فيسبوك: فحص الصفحة والتوكن قبل النشر — بدل انتظار فشل حقيقي.

    python -m autonews.fb_main --check          # هل الصفحة والتوكن سليمان؟
    python -m autonews.fb_main --test           # منشور تجريبي (غير مثبّت)
    python -m autonews.fb_main --token          # اطبع توكن الصفحة الطويل (للسرّ)

لماذا؟ أشهر فشل عملي: توكن مستخدم قصير الأمد، أو توكن بلا صلاحية
`pages_manage_posts`، أو `page_id` رقميّ مختلف عن الذي يظهر في الرابط.
هذه الأداة تكشف الثلاثة في ثانية واحدة.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"


def _get(path: str, params: dict, token: str, timeout: int = 20) -> dict:
    params = {**params, "access_token": token}
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(detail)
        except json.JSONDecodeError:
            return {"error": {"message": f"HTTP {exc.code}: {detail[:200]}"}}
    except Exception as exc:  # noqa: BLE001
        return {"error": {"message": f"شبكة: {exc}"}}


def _get_raw(path: str, params: dict, timeout: int = 20) -> dict:
    """طلب Graph بلا `access_token` (يستخدمه تبديل التوكن بـ app secret)."""
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(detail)
        except json.JSONDecodeError:
            return {"error": {"message": f"HTTP {exc.code}: {detail[:200]}"}}
    except Exception as exc:  # noqa: BLE001
        return {"error": {"message": f"شبكة: {exc}"}}


def extend_user_token(user_token: str, app_id: str = "", app_secret: str = "") -> tuple[str, str]:
    """تحويل توكن مستخدم قصير (ساعة) إلى **طويل الأمد** (٦٠ يوماً).

    لماذا هذه الخطوة حرجة؟
    ----------------------
    توكن الصفحة المأخوذ من توكن **قصير** ينتهي معه (ساعة!). أما المأخوذ من توكن
    **طويل** فلا ينتهي إطلاقاً. هذه هي الفروق التي تجعل الناس يظنّون أن
    «توكن الصفحة ينتهي» — وهو لا ينتهي إن بدأت صح.
    """
    app_id = app_id or os.environ.get("FACEBOOK_APP_ID", "")
    app_secret = app_secret or os.environ.get("FACEBOOK_APP_SECRET", "")
    if not (app_id and app_secret):
        return "", "لا APP_ID ولا APP_SECRET — التبديل يحتاجهما"
    data = _get_raw("oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": user_token,
    })
    if _err(data):
        return "", _err(data)
    token = str(data.get("access_token") or "")
    if not token:
        return "", "لم يُرجِع فيسبوك توكنًا"
    days = int(data.get("expires_in", 0)) // 86400
    return token, f"مُدَّ إلى {days} يوماً" if days else "مُدّ (بلا تاريخ انتهاء)"


def _err(data: dict) -> str:
    err = data.get("error") or {}
    return str(err.get("message") or "")


def resolve_page_token(user_token: str, page_id: str = "") -> tuple[str, str, dict]:
    """من توكن المستخدم → توكن الصفحة الطويل (+ اسمها ومعرّفها)."""
    data = _get("me/accounts", {"fields": "id,name,access_token", "limit": 100}, user_token)
    if _err(data):
        return "", _err(data), {}
    pages = data.get("data") or []
    if not pages:
        return "", ("لا توجد صفحات لهذا التوكن — تأكّد أنك تختار الصفحة "
                    "(Allow access to pages) عند تسجيل الدخول"), {}
    if page_id:
        for page in pages:
            if str(page.get("id")) == str(page_id):
                return str(page.get("access_token") or ""), "", page
        names = "، ".join(f"{p.get('name')} ({p.get('id')})" for p in pages[:5])
        return "", f"لم أجد الصفحة {page_id}. صفحاتك: {names}", {}
    page = pages[0]
    return str(page.get("access_token") or ""), "", page


def diagnose(token: str) -> int:
    """🔬 تشخيص خطوة بخطوة: هل التوكن يرى صفحات؟ أي صلاحية ناقصة؟

    الغرض: حين يظهر «النوع: User» و«انتهاء الصلاحية: خلال ساعة»، تحتاج أن تعرف
    بالضبط أين المشكلة — لا أن تخمّن. هذه الدالة تطبع كل خطوة ونتيجتها.
    """
    print("\n🔬 تشخيص التوكن\n" + "─" * 58)

    info = _get("debug_token", {"input_token": token}, token)
    data = info.get("data") or {}
    if _err(info):
        print(f"❌ التوكن غير صالح: {_err(info)}")
        return 2
    kind = "مستخدم" if data.get("type") == "USER" else ("صفحة ✅" if data.get("type") == "PAGE" else str(data.get("type")))
    print(f"١) النوع: {kind}")
    if data.get("type") == "USER":
        print("   ⚠️ توكن مستخدم = ينتهي قريباً. المطلوب توكن صفحة (لا ينتهي).")
    if data.get("expires_at"):
        import datetime

        when = datetime.datetime.fromtimestamp(int(data["expires_at"]),
                                              datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"   ⏳ ينتهي: {when}")
    elif data.get("is_valid"):
        print("   ♾ لا ينتهي (الأفضل)")

    scopes = data.get("scopes") or []
    need = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}
    missing = need - set(scopes)
    print(f"٢) الصلاحيات: {'، '.join(scopes) or 'لا شيء'}")
    if missing:
        print(f"   ❌ ناقصة: {'، '.join(sorted(missing))}")
        print("   الحلّ: حالات الاستخدام ← «إدارة كل شيء في صفحتك» ← أضفها")
    else:
        print("   ✅ الثلاث المطلوبة موجودة")

    pages = _get("me/accounts", {"fields": "id,name,access_token", "limit": 100}, token)
    if _err(pages):
        print(f"٣) الصفحات: ❌ {_err(pages)}")
        print("   ⚠️ الصلاحية موجودة لكن هذه النتيجة تعني: **لم تختر أي صفحة**")
        print("   في نافذة الموافقة. أعد توليد التوكن من Graph API Explorer")
        print("   واضغط «تعديل»/Edit على الصلاحيات ← اختر الصفحة ← Allow.")
        return 2
    listing = pages.get("data") or []
    if not listing:
        print("٣) الصفحات: ⚠️ القائمة فارغة")
        print("   السبب الأشهر: لم تختر صفحتك عند الموافقة على الصلاحيات.")
        print("   الحلّ: Graph API Explorer ← Generate Access Token ←")
        print("   في النافذة اضغط على اسم الصفحة ← Allow لكل صلاحية.")
        return 2

    print(f"٣) الصفحات: ✅ {len(listing)}")
    for page in listing:
        has_token = "✅" if page.get("access_token") else "⚠️"
        print(f"   {has_token} {page.get('name')} — {page.get('id')}")
    print("\n▶ الخطوة التالية: python -m autonews.fb_main --token")
    return 0


def check(token: str = "", page_id: str = "") -> int:
    page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
    user_token = os.environ.get("FACEBOOK_USER_TOKEN", "")
    page_id = page_id or os.environ.get("FACEBOOK_PAGE_ID", "")
    token = token or page_token or user_token

    print("\n📘 فحص فيسبوك\n" + "─" * 58)
    if not token:
        print("❌ لا توكن. أضف FACEBOOK_USER_TOKEN (مؤقّت) أو FACEBOOK_PAGE_TOKEN (دائم)")
        return 2
    if not page_id:
        print("❌ لا FACEBOOK_PAGE_ID — رقّم الصفحة يظهر في «About» أو من links")
        return 2

    if not page_token:
        print("ℹ️ وجدت توكن مستخدم فقط — أستخرج توكن الصفحة…")
        resolved, error, page = resolve_page_token(user_token, page_id)
        if not resolved:
            print(f"❌ {error}")
            return 2
        page_token = resolved
        print(f"✅ توكن الصفحة: {page.get('name')} ({page.get('id')})")

    # ٠) هل التوكن يرى الصفحة؟ (السبب الأول للفشل: تطبيق من حساب والصفحة في حساب آخر)
    info = _get(str(page_id), {"fields": "id,name,fan_count,link"}, page_token)
    if _err(info):
        print(f"❌ التوكن لا يصل للصفحة: {_err(info)}")
        print("   السببان الأشهر:")
        print("   ١) الصلاحيات: pages_show_list + pages_manage_posts + pages_read_engagement")
        print("      (تُفتح من: حالات الاستخدام ← «إدارة كل شيء في صفحتك» ← الصلاحيات)")
        print("   ٢) التطبيق من حساب، والصفحة في حساب آخر — أضف حسابك كـ Tester في التطبيق")
        print("      (App Roles → Roles → Add People) أو أنشئ التطبيق من حساب الصفحة نفسه.")
        return 2

    print(f"✅ الصفحة: {info.get('name')} ({info.get('id')})")
    if info.get("fan_count") is not None:
        print(f"👥 المتابعون: {info.get('fan_count')}")

    # صلاحية النشر: نطلب الحدود (لا ننشر شيئاً)
    perms = _get("me/permissions", {}, page_token)
    granted = {p.get("permission") for p in (perms.get("data") or [])
               if p.get("status") == "granted"}
    need = {"pages_manage_posts", "pages_read_engagement"}
    if granted:
        missing = need - granted
        if missing:
            print(f"⚠️ صلاحيات ناقصة: {'، '.join(sorted(missing))}")
            print("   أعد توليد التوكن واختر هاتين الصلاحيتين.")
        else:
            print("✅ صلاحيات النشر موجودة")
    else:
        # توكن الصفحة لا يكشف صلاحيات المستخدم — نكتفي بأن الوصول للصفحة نجح.
        print("ℹ️ الصلاحيات تُفحص نهائياً عند أول منشور تجريبي: --test --live")

    expiry = _get("debug_token", {"input_token": page_token}, page_token)
    if not _err(expiry):
        d = expiry.get("data") or {}
        if d.get("expires_at"):
            print("⚠️ التوكن ينتهي قريباً — استخدم توكن صفحة طويل الأمد")
        elif d.get("is_valid"):
            print("♾ توكن الصفحة دائم (لا ينتهي) — هذا المطلوب")
    print("\n✅ جاهز للنشر.")
    return 0


def show_token(app_id: str = "", app_secret: str = "") -> int:
    user_token = os.environ.get("FACEBOOK_USER_TOKEN", "")
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
    if not user_token:
        print("❌ ضع FACEBOOK_USER_TOKEN أولاً:\n   export FACEBOOK_USER_TOKEN=\"EAA...\"")
        return 2

    print("\n🔑 استخراج رموز الصفحة\n" + "─" * 58)

    # ١) تمديد توكن المستخدم (وإلّا صار توكن الصفحة قصير الأمد مثله)
    extended, note = extend_user_token(user_token, app_id, app_secret)
    if extended:
        print(f"١) توكن المستخدم: ✅ {note}")
        user_token = extended
    else:
        print(f"١) توكن المستخدم: ⚠️ لم أُمِدّه ({note})")
        print("   توكن الصفحة الناتج سينتهي معه (ساعة).")
        print("   للحصول على توكن لا ينتهي: أضف FACEBOOK_APP_ID + FACEBOOK_APP_SECRET")
        print("   (App Dashboard ← Settings → Basic) ثم أعد الأمر.")

    # ٢) توكن الصفحة
    token, error, page = resolve_page_token(user_token, page_id)
    if not token:
        print(f"\n❌ {error}")
        return 2
    print(f"٢) الصفحة: ✅ {page.get('name')} ({page.get('id')})")

    print("\n" + "═" * 58)
    print(f"FACEBOOK_PAGE_ID={page.get('id')}")
    print(f"FACEBOOK_PAGE_TOKEN={token}")
    print("═" * 58)
    print("\n⚠️ هذا سرّ. ضعه في GitHub Secrets ثم امسحه من أي ملف أو طرفية.")
    print("   Settings → Secrets and variables → Actions → New repository secret")
    return 0


def page_from_token(page_token: str) -> tuple[str, str, str]:
    """يقرأ الصفحة **من التوكن نفسه** — لا حاجة لمعرّف منفصل.

    توكن الصفحة يعرف هويته: `GET /me` يرجع الصفحة التي يخصّها.
    لذلك يكفي أن تملك توكن صالحاً واحداً ليعرف النظام على أي صفحة ينشر.
    """
    data = _get("me", {"fields": "id,name"}, page_token)
    if _err(data):
        return "", "", _err(data)
    pid, name = str(data.get("id") or ""), str(data.get("name") or "")
    if not pid:
        return "", "", "التوكن لا يعيد أي صفحة (هل هو توكن صفحة فعلاً؟)"
    return pid, name, ""


def autofill(config_dir: str = "config") -> int:
    """يكتفي بـ FACEBOOK_PAGE_TOKEN: يتحقّق، يستخرج المعرّف، ويحفظه.

    الهدف: **سرّ واحد فقط** يضعه المستخدم، وكل الباقي يُستنتج.
    """
    token = os.environ.get("FACEBOOK_PAGE_TOKEN", "").strip()
    print("\n📘 ضبط فيسبوك من التوكن\n" + "─" * 58)
    if not token:
        print("❌ ضع التوكن أولاً:\n   export FACEBOOK_PAGE_TOKEN=\"EAA...\"")
        return 2

    pid, name, error = page_from_token(token)
    if not pid:
        print(f"❌ التوكن لا يصل إلى صفحة: {error}")
        print("   تأكّد أنه **توكن صفحة** (Page token) لا توكن مستخدم.")
        return 2
    print(f"✅ الصفحة: {name} ({pid})")

    scope = _get("debug_token", {"input_token": token}, token).get("data") or {}
    if scope.get("expires_at"):
        import datetime

        when = datetime.datetime.fromtimestamp(int(scope["expires_at"]),
                                              datetime.timezone.utc).strftime("%Y-%m-%d")
        print(f"⚠️ ينتهي في {when} — توكن مستخدم لا صفحة. الأفضل إعادة توليده.")
    elif scope:
        print("♾ لا ينتهي — توكن دائم ✅")

    ok, msg = write_channel_extra_safe("fb_page", "page_id", pid, config_dir)
    if not ok:
        print(f"❌ تعذّر الحفظ: {msg}")
        return 2
    print(f"✅ حُفظ المعرّف تلقائياً في channels.yml")
    print("\n▶ جاهز. الباقي: الأعضاء يضيفون FACEBOOK_PAGE_TOKEN في GitHub Secrets فقط.")
    return 0


def write_channel_extra_safe(channel_id: str, key: str, value: str, config_dir: str) -> tuple[bool, str]:
    from .utils import write_channel_extra

    return write_channel_extra(channel_id, key, value, config_dir=config_dir)


def save_page(page_id: str, config_dir: str = "config") -> int:
    """حفظ معرّف الصفحة داخل channels.yml (ليس سرّاً) — بلا تعديل يدوي."""
    from .utils import write_channel_extra

    ok, msg = write_channel_extra("fb_page", "page_id", str(page_id), config_dir=config_dir)
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        return 2
    print("\nالخطوة الباقية (سرّ واحد فقط):")
    print("  Settings → Secrets and variables → Actions → New repository secret")
    print("  الاسم: FACEBOOK_PAGE_TOKEN")
    print("  القيمة: التوكن الدائم من --token")
    print("\nثم تحقّق: python -m autonews.fb_main --check")
    return 0


def test_post(dry_run: bool = True) -> int:
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
    token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
    if not (page_id and token):
        print("❌ تحتاج FACEBOOK_PAGE_ID و FACEBOOK_PAGE_TOKEN")
        return 2
    text = "🧪 اختبار AutoNews — إن وصلتك هذه فالربط سليم.\n(سيُحذف تلقائياً)"
    if dry_run:
        print(f"[TEST] كان سيُنشر على {page_id}:\n{text}")
        print("\n▶ للنشر الفعلي: --test --live")
        return 0
    data = json.dumps({"message": text}).encode()
    req = urllib.request.Request(
        f"{GRAPH}/{page_id}/feed?access_token={token}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            result = json.loads(resp.read().decode())
        print(f"✅ نُشر: {result.get('id')}")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"❌ {exc.read().decode('utf-8', 'replace')[:300]}")
        return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autonews-fb", description="فحص صفحة فيسبوك")
    p.add_argument("--check", action="store_true", help="فحص كامل")
    p.add_argument("--diagnose", action="store_true", help="تشخيص خطوة بخطوة")
    p.add_argument("--token", action="store_true", help="استخراج توكن الصفحة")
    p.add_argument("--test", action="store_true", help="منشور تجريبي")
    p.add_argument("--live", action="store_true", help="نشر حقيقي مع --test")
    p.add_argument("--page", default="", help="معرّف الصفحة")
    p.add_argument("--app-id", default="", help="App ID (لتمديد التوكن)")
    p.add_argument("--app-secret", default="", help="App Secret (لتمديد التوكن)")
    p.add_argument("--save-page", metavar="ID", default="",
                   help="حفظ معرّف الصفحة في channels.yml")
    p.add_argument("--autofill", action="store_true",
                   help="استخرج المعرّف من التوكن واحفظه (يكفي التوكن وحده)")
    args = p.parse_args(argv)
    if args.diagnose:
        tok = os.environ.get("FACEBOOK_USER_TOKEN") or os.environ.get("FACEBOOK_PAGE_TOKEN", "")
        if not tok:
            print("❌ ضع التوكن: export FACEBOOK_USER_TOKEN=\"EAA...\"")
            return 2
        return diagnose(tok)
    if args.autofill:
        return autofill()
    if args.save_page:
        return save_page(args.save_page)
    if args.token:
        return show_token(args.app_id, args.app_secret)
    if args.test:
        return test_post(dry_run=not args.live)
    return check(page_id=args.page)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
