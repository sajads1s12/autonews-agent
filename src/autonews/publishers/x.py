"""ناشر X (تويتر سابقاً) — API v2 مع دعم OAuth 1.0a / OAuth 2.0 وسلاسل (Threads).

السلاسل: تُنشر التغريدة الأولى ثم كل تغريدة ردّاً على السابقة (reply.in_reply_to_tweet_id)
فتظهر كسلسلة واحدة مترابطة بدل منشورات منفصلة.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse

from ..images import ArticleImage
from .base import Publisher

TWEET_URL = "https://api.x.com/2/tweets"
MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
X_WEIGHTED_LIMIT = 280


class XPublisher(Publisher):
    platform = "x"

    def validate(self) -> str:
        if self._has_oauth1() or self.channel.env("bearer_token"):
            return ""
        return ("لا توجد اعتمادات X — يحتاج إمّا "
                "(consumer_key/consumer_secret/access_token/access_secret) أو bearer_token")

    def _has_oauth1(self) -> bool:
        return all(self.channel.env(k) for k in
                   ("consumer_key", "consumer_secret", "access_token", "access_secret"))

    # ------------------------------------------------------- OAuth 1.0a
    def _oauth1_header(self, method: str, url: str, extra: dict[str, str] | None = None) -> str:
        params = {
            "oauth_consumer_key": self.channel.env("consumer_key"),
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.channel.env("access_token"),
            "oauth_version": "1.0",
        }
        encoded = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted(params.items())
        )
        base = f"{method.upper()}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(encoded, safe='')}"
        signing_key = (
            f"{urllib.parse.quote(self.channel.env('consumer_secret'), safe='')}&"
            f"{urllib.parse.quote(self.channel.env('access_secret'), safe='')}"
        )
        signature = base64.b64encode(
            hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
        ).decode()
        params["oauth_signature"] = signature
        return "OAuth " + ", ".join(
            f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
            for k, v in sorted(params.items())
        )

    def _headers(self, method: str, url: str) -> dict[str, str]:
        if self._has_oauth1():
            return {"Authorization": self._oauth1_header(method, url),
                    "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {self.channel.env('bearer_token')}",
                "Content-Type": "application/json"}

    # ------------------------------------------------------------- الوسائط
    def _upload_media(self, image: ArticleImage) -> str:
        """رفع الصورة إلى X (v1.1 media/upload) وإرجاع media_id."""
        boundary = f"----AutoNewsX{secrets.token_hex(8)}"
        with open(image.path, "rb") as fh:
            payload = fh.read()
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; "
            f"filename=\"image.jpg\"\r\nContent-Type: {image.mime}\r\n\r\n".encode(),
            payload,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        headers = {
            "Authorization": self._oauth1_header("POST", MEDIA_UPLOAD_URL) if self._has_oauth1()
            else f"Bearer {self.channel.env('bearer_token')}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        import urllib.error
        import urllib.request

        req = urllib.request.Request(MEDIA_UPLOAD_URL, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=max(self.timeout, 60)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        media_id = str(data.get("media_id_string") or data.get("media_id") or "")
        if not media_id:
            raise RuntimeError(f"فشل رفع وسائط X: {str(data)[:200]}")
        return media_id

    # ------------------------------------------------------------- الإرسال
    def _tweet(self, text: str, *, reply_to: str = "", media_id: str = "") -> str:
        payload: dict = {"text": text[:X_WEIGHTED_LIMIT]}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        if media_id:
            payload["media"] = {"media_ids": [media_id]}
        _, body = self._request(TWEET_URL, data=payload, headers=self._headers("POST", TWEET_URL))
        data = json.loads(body or "{}")
        tweet_id = str(((data.get("data") or {}).get("id")) or "")
        if not tweet_id:
            raise RuntimeError(f"X رفض المنشور: {body[:300]}")
        return tweet_id

    def _send(self, text: str, image: ArticleImage | None = None) -> str:
        media_id = ""
        if image and image.ok:
            try:
                media_id = self._upload_media(image)
            except Exception as exc:  # noqa: BLE001 — فشل الصورة لا يمنع التغريدة
                self.last_error = str(exc)
                from ..utils import get_logger

                get_logger("autonews.publish").warning(
                    "تعذّر رفع صورة X في %s — سأنشر نصاً: %s", self.channel.id, str(exc)[:140])
        return self._tweet(text, media_id=media_id)

    def _send_thread(self, texts: list[str], image: ArticleImage | None = None) -> str:
        """سلسلة مترابطة: كل تغريدة ردّ على السابقة."""
        refs: list[str] = []
        media_id = ""
        if image and image.ok:
            try:
                media_id = self._upload_media(image)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
        for i, part in enumerate(texts):
            reply_to = refs[-1] if refs else ""
            use_media = media_id if i == 0 else ""
            refs.append(self._tweet(part, reply_to=reply_to, media_id=use_media))
            if i < len(texts) - 1:
                time.sleep(1.2)      # تلطيف حدود X
        return ",".join(refs)
