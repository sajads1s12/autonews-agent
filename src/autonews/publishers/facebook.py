"""ناشر فيسبوك — Graph API (صفحة) مع دعم الصور.

يدعم طريقتين للتوثيق:
1) page_access_token جاهز (طويل الأمد) — الأبسط.
2) user_access_token + page_id — يُستخرج توكن الصفحة تلقائياً.
وإذا وُجدت صورة: `/photos` (نشر أوضح) بدل `/feed`.
"""
from __future__ import annotations

import json
import time

from ..images import ArticleImage
from .base import Publisher

GRAPH = "https://graph.facebook.com/v21.0"


class FacebookPublisher(Publisher):
    platform = "facebook"

    def validate(self) -> str:
        page_id = self.channel.env("page_id")
        token = self.channel.env("page_access_token")
        user_token = self.channel.env("user_access_token")
        if not page_id:
            return "متغيّر البيئة الخاص بـ page_id مفقود"
        if not token and not user_token:
            return "لا يوجد page_access_token ولا user_access_token في البيئة"
        return ""

    # ------------------------------------------------------------- التوكن
    def _resolve_page_token(self) -> str:
        direct = self.channel.env("page_access_token")
        if direct:
            return direct

        user_token = self.channel.env("user_access_token")
        page_id = self.channel.env("page_id")
        url = f"{GRAPH}/me/accounts?fields=id,name,access_token&access_token={user_token}"
        _, body = self._request(url, method="GET")
        data = json.loads(body or "{}")
        if "error" in data:
            raise RuntimeError(f"فيسبوك: {data['error'].get('message', 'خطأ غير معروف')}")
        for page in data.get("data", []):
            if str(page.get("id")) == str(page_id) and page.get("access_token"):
                return str(page["access_token"])
        raise RuntimeError(
            "تعذّر الحصول على توكن الصفحة — تأكّد من صلاحية pages_manage_posts وأن page_id صحيح")

    # ------------------------------------------------------------- الإرسال
    def _send(self, text: str, image: ArticleImage | None = None) -> str:
        page_id = self.channel.env("page_id")
        token = self._resolve_page_token()

        # ---- مع صورة: /photos ----
        if image and image.ok:
            try:
                _, body = self._multipart(
                    f"{GRAPH}/{page_id}/photos",
                    {"caption": text, "access_token": token, "published": "true"},
                    file_field="source", file_path=image.path, mime=image.mime,
                )
                data = json.loads(body or "{}")
                if "error" in data:
                    raise RuntimeError(data["error"].get("message", "خطأ فيسبوك"))
                ref = str(data.get("post_id") or data.get("id") or "")
                if ref:
                    return ref
            except RuntimeError as exc:
                self.last_error = str(exc)
                from ..utils import get_logger

                get_logger("autonews.publish").warning(
                    "تعذّر نشر صورة فيسبوك في %s — سأنشر نصاً: %s", self.channel.id, str(exc)[:160])

        # ---- نص فقط: /feed ----
        link = str(self.channel.extra.get("link_override") or "")
        form = {"message": text, "access_token": token}
        if link:
            form["link"] = link
        _, body = self._request(f"{GRAPH}/{page_id}/feed", form=form)
        data = json.loads(body or "{}")
        if "error" in data:
            err = data["error"]
            if err.get("code") in (4, 17, 32, 613):     # حدود معدّل الطلبات
                time.sleep(20)
                _, body = self._request(f"{GRAPH}/{page_id}/feed", form=form)
                data = json.loads(body or "{}")
                if "error" not in data:
                    return str(data.get("id", ""))
                err = data["error"]
            raise RuntimeError(f"فيسبوك رفض المنشور: {err.get('message', body[:200])}")
        return str(data.get("id", ""))
