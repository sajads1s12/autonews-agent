"""المراجعة البشرية الاختيارية عبر تيليجرام (مسوّدة + أزرار موافقة/رفض).

كيف تعمل؟
1) في التشغيل: يُرسل المنشور المصاغ إلى قناة/دردشة إدارية **خاصة** مع زرّين:
   ✅ نشر   ·   ❌ تجاهل   (واتساب/تيليجرام inline keyboard).
2) يُخزَّن المسوّد في الحالة (`pending_reviews`) بمعرّف قصير.
3) عند ضغطك على الزر، يستقبل بوت المراجعة الـ callback (عبر webhook أو polling)
   ويضع علامة قرار في الحالة، ثم التشغيل التالي ينشر المقبول فقط.

هذه الوحدة توفّر الطبقة الكاملة: الإرسال، التحقق من التوقيع، قراءة القرارات
(getUpdates)، والتنظيف. تعمل مع `review_worker.py` أو أي webhook خارجي.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from .ai import Post
from .config_loader import AlertSettings
from .state import State
from .utils import get_logger, iso_now, truncate

log = get_logger("autonews.review")

TG_API = "https://api.telegram.org/bot{token}/{method}"


class ReviewManager:
    """إدارة دورة حياة المسوّدات (إرسال/قرار/تنظيف)."""

    def __init__(self, settings: AlertSettings, state: State, *, dry_run: bool = False) -> None:
        self.settings = settings
        self.state = state
        self.dry_run = dry_run
        self.bot_token = settings.bot_token
        self.chat_id = settings.chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    # ------------------------------------------------------------------ إرسال
    def request_review(self, post: Post, channel_name: str) -> str:
        """إرسال المسوّدة للمراجعة، وإرجاع معرّفها (أو '' عند الفشل)."""
        review_id = hashlib.sha1(
            f"{post.channel_id}:{post.source_url}:{secrets.token_hex(4)}".encode()
        ).hexdigest()[:10]

        self.state.add_pending_review(review_id, {
            "review_id": review_id,
            "post": post.as_dict(),
            "channel": post.channel_id,
            "channel_name": channel_name,
            "status": "pending",
        })

        preview = truncate(post.body, 900)
        text = (
            f"📝 <b>مسوّدة بانتظار قرارك</b>\n"
            f"القناة: <b>{_esc(channel_name)}</b> · <code>{post.channel_id}</code>\n"
            f"الأسلوب: {post.variant} · نموذج: <code>{_esc(post.model or 'fallback')}</code>"
            f"{' · 🔴 عاجل' if post.breaking else ''}\n"
            f"{'─' * 22}\n"
            f"<b>{_esc(post.title)}</b>\n\n{_esc(preview)}\n"
            f"{'─' * 22}\n"
            f"معرّف المراجعة: <code>{review_id}</code>"
        )
        if not self.enabled:
            log.warning("المراجعة مطلوبة لكن لا توكن/دردشة إدارية — المسوّدة محفوظة فقط (%s)", review_id)
            return review_id
        if self.dry_run:
            log.info("[DRY-RUN مراجعة] %s | %s", review_id, post.title[:70])
            return review_id

        payload = {
            "chat_id": self.chat_id,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ نشر", "callback_data": f"approve:{review_id}"},
                    {"text": "❌ تجاهل", "callback_data": f"reject:{review_id}"},
                ]]
            },
        }
        ok = self._call("sendMessage", payload)
        log.info("📝 أُرسلت مسوّدة %s للمراجعة (%s)", review_id, "ناجح" if ok else "فشل الإرسال")
        return review_id

    # ------------------------------------------------------------------ قرارات
    def poll_decisions(self) -> dict[str, str]:
        """قراءة قرارات المشغّل من تيليجرام (getUpdates) وتطبيقها على الحالة.

        تُستدعى في بداية كل تشغيل. هذا يغني عن إنشاء سيرفر webhook.
        """
        if not self.enabled or self.dry_run:
            return {}
        offset = int(self.state.usage.get("review_offset", 0))
        data = self._get("getUpdates", {"offset": offset + 1, "timeout": 0,
                                        "allowed_updates": json.dumps(["callback_query"])})
        decisions: dict[str, str] = {}
        if not data:
            return decisions

        for update in data.get("result", []):
            offset = max(offset, int(update.get("update_id", 0)))
            cq = update.get("callback_query") or {}
            callback_id = cq.get("id", "")
            payload = str(cq.get("data") or "")
            if ":" not in payload:
                continue
            action, review_id = payload.split(":", 1)
            if action not in ("approve", "reject"):
                continue
            entry = self.state.pending_reviews.get(review_id)
            if entry:
                entry["status"] = "approved" if action == "approve" else "rejected"
                entry["decided_at"] = iso_now()
                entry["decided_by"] = str((cq.get("from") or {}).get("username", ""))
                decisions[review_id] = entry["status"]
                self._call("answerCallbackQuery", {
                    "callback_query_id": callback_id,
                    "text": "✅ سيُنشر في التشغيل القادم" if action == "approve" else "❌ تم التجاهل",
                })
                log.info("🗳 قرار مراجعة %s: %s", review_id, entry["status"])
            else:
                self._call("answerCallbackQuery", {
                    "callback_query_id": callback_id, "text": "انتهت صلاحية هذه المسوّدة"})

        self.state.usage["review_offset"] = offset
        return decisions

    def take_approved(self, limit: int = 5) -> list[dict]:
        """سحب المسوّدات الموافَق عليها (وتحويلها لمنشورات جاهزة)."""
        approved = [
            entry for entry in self.state.pending_reviews.values()
            if entry.get("status") == "approved"
        ][:limit]
        for entry in approved:
            self.state.pop_pending_review(entry["review_id"])
        return approved

    def cleanup_expired(self, timeout_hours: int) -> list[str]:
        """إزالة المسوّدات المنتهية (لم يُبتّ فيها)."""
        expired = self.state.expired_reviews(timeout_hours)
        for rid in expired:
            entry = self.state.pop_pending_review(rid)
            if entry:
                log.info("⌛ انتهت صلاحية مسوّدة %s (لم يُبتّ فيها)", rid)
        return expired

    # ------------------------------------------------------------------ HTTP
    def _call(self, method: str, payload: dict) -> bool:
        url = TG_API.format(token=self.bot_token, method=method)
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return bool(json.loads(resp.read().decode("utf-8", "replace")).get("ok"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            log.warning("فشل نداء تيليجرام (%s): %s", method, exc)
            return False

    def _get(self, method: str, params: dict) -> dict | None:
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{TG_API.format(token=self.bot_token, method=method)}?{query}"
        try:
            with urllib.request.urlopen(url, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            log.warning("فشل قراءة تحديثات تيليجرام: %s", exc)
            return None


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

