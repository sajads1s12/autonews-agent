"""ناشر تيليجرام — Bot API (بلا مكتبات خارجية) مع دعم الصور والتقسيم."""
from __future__ import annotations

import json

from ..ai import Post
from ..images import ArticleImage
from ..utils import chunk_text
from .base import Publisher

TG_TEXT_LIMIT = 4096
TG_CAPTION_LIMIT = 1024


class TelegramPublisher(Publisher):
    platform = "telegram"

    # ------------------------------------------------------------------ تحقّق
    def validate(self) -> str:
        token = self.channel.env("bot_token")
        chat_id = self.channel.env("chat_id")
        if not token:
            return "متغيّر البيئة الخاص بـ bot_token مفقود"
        if ":" not in token:
            return "صيغة bot_token غير صحيحة (يجب أن تحتوي ':')"
        if not chat_id:
            return "متغيّر البيئة الخاص بـ chat_id مفقود"
        return ""

    # ------------------------------------------------------------------ إرسال
    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.channel.env('bot_token')}/{method}"

    def _send(self, text: str, image: ArticleImage | None = None) -> str:
        chat_id = self.channel.env("chat_id")
        disable_preview = bool(self.channel.extra.get("disable_web_page_preview", False))
        parse_mode = str(self.channel.extra.get("parse_mode") or "").upper()

        # ---- مع صورة: sendPhoto (التعليق بحد 1024) ثم ما تبقّى كرسالة ----
        if image and image.ok:
            caption = text[:TG_CAPTION_LIMIT]
            rest = text[TG_CAPTION_LIMIT:].strip()
            try:
                msg_id = self._send_photo(chat_id, image, caption)
                if rest:
                    for chunk in chunk_text(rest, TG_TEXT_LIMIT):
                        self._send_message(chat_id, chunk, parse_mode, disable_preview)
                return str(msg_id)
            except RuntimeError as exc:
                # فشل الصورة لا يجب أن يمنع نشر النص
                self.last_error = str(exc)
                from ..utils import get_logger

                get_logger("autonews.publish").warning(
                    "تعذّر إرسال الصورة في %s — سيُنشر النص فقط: %s", self.channel.id, str(exc)[:160])

        refs: list[str] = []
        for chunk in chunk_text(text, TG_TEXT_LIMIT):
            refs.append(str(self._send_message(chat_id, chunk, parse_mode, disable_preview)))
        return ",".join(r for r in refs if r)

    # ------------------------------------------------------------------ أجزاء
    def _send_message(self, chat_id: str, text: str, parse_mode: str, disable_preview: bool) -> str:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": disable_preview}
        if parse_mode in ("HTML", "MARKDOWNV2", "MARKDOWN"):
            payload["parse_mode"] = parse_mode
        _, body = self._request(self._api("sendMessage"), data=payload)
        data = json.loads(body or "{}")
        if not data.get("ok") and parse_mode and data.get("error_code") == 400:
            payload.pop("parse_mode", None)     # وسوم مكسورة => نص عادي
            _, body = self._request(self._api("sendMessage"), data=payload)
            data = json.loads(body or "{}")
        if not data.get("ok"):
            raise RuntimeError(f"تيليجرام رفض الطلب: {data.get('description', body[:200])}")
        return str((data.get("result") or {}).get("message_id", ""))

    def _send_photo(self, chat_id: str, image: ArticleImage, caption: str) -> str:
        """إرسال الصورة كملف (multipart) — أكثر موثوقية من تمرير الرابط."""
        fields = {"chat_id": chat_id, "caption": caption}
        if image.alt:
            fields["caption"] = caption
        _, body = self._multipart(self._api("sendPhoto"), fields,
                                  file_field="photo", file_path=image.path, mime=image.mime)
        data = json.loads(body or "{}")
        if not data.get("ok"):
            # محاولة بديلة: تمرير رابط الصورة بدل الملف
            if image.url:
                _, body = self._request(self._api("sendPhoto"),
                                        data={"chat_id": chat_id, "photo": image.url, "caption": caption})
                data = json.loads(body or "{}")
            if not data.get("ok"):
                raise RuntimeError(f"تيليجرام رفض الصورة: {data.get('description', body[:200])}")
        return str((data.get("result") or {}).get("message_id", ""))

    # --------------------------------------------------------- (توافق)
    def publish(self, post: Post, image: ArticleImage | None = None):  # type: ignore[override]
        return super().publish(post, image)
