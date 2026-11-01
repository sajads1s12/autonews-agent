"""التنبيهات: أقوى حماية تشغيلية في النظام.

بلا تنبيهات، قد يصمت البوت أسابيع (توكن منتهٍ، كل المفاتيح مستنفدة) دون أن تدري.
هنا نرسل رسالة إلى قناة إدارية عبر تيليجرام عند:

- فشل تشغيل كامل / أخطاء نشر.
- تشغيلات متتالية بلا أي نشر (مؤشّر على عطل خفي).
- تعطيل مصدر تلقائياً.
- استنفاد كل مفاتيح Gemini.
- ملخّص يومي عند منتصف النهار (اختياري).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .config_loader import AlertSettings
from .utils import get_logger, iso_now

log = get_logger("autonews.notify")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"


@dataclass
class Notifier:
    settings: AlertSettings
    dry_run: bool = False
    sent: list[str] = field(default_factory=list)
    _failures: int = 0

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.bot_token and self.settings.chat_id)

    def _send(self, text: str) -> bool:
        if not self.enabled:
            log.warning("تنبيه غير مُرسَل (لا توكن/معرّف إداري): %s", text.splitlines()[0][:90])
            return False
        if self.dry_run:
            log.info("[DRY-RUN تنبيه] %s", text.replace("\n", " ⏎ ")[:200])
            self.sent.append("dry-run")
            return True
        payload = json.dumps({
            "chat_id": self.settings.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            TG_API.format(token=self.settings.bot_token), data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ok = bool(json.loads(resp.read().decode("utf-8", "replace")).get("ok"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            self._failures += 1
            # نستخدم print لا logger.error: فشل التنبيه لا يجب أن يُفشل التشغيل
            log.warning("فشل إرسال تنبيه تيليجرام: %s", exc)
            return False
        # إن فشل HTML (وسوم مكسورة) نعيد الإرسال كنص عادي
        if not ok:
            try:
                plain = json.dumps({"chat_id": self.settings.chat_id, "text": text[:4000]},
                                   ensure_ascii=False).encode("utf-8")
                req2 = urllib.request.Request(
                    TG_API.format(token=self.settings.bot_token), data=plain,
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req2, timeout=20) as resp:
                    ok = bool(json.loads(resp.read().decode()).get("ok"))
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            self.sent.append(iso_now())
        return ok

    # ------------------------------------------------------------ v6: ملخّص ذكي
    def digest(self, *, title: str, sections: list[tuple[str, list[str]]]) -> bool:
        """رسالة واحدة تجمع كل ما يحتاج انتباهك — بدل ثلاث رسائل متفرّقة.

        sections: [(عنوان القسم، [أسطر])]. الأقسام الفارغة تُحذف.
        """
        if not self.settings.enabled:
            return False
        body: list[str] = [f"<b>{title}</b>"]
        for name, lines in sections:
            if not lines:
                continue
            body.append("")
            body.append(f"<b>{name}</b>")
            body.extend("• " + _esc(line)[:160] for line in lines[:6])
        if len(body) <= 1:
            return False
        return self._send("\n".join(body))

    # ------------------------------------------------------------------ أحداث
    def run_failed(self, *, status: str, published: int, failed: int, errors: list[str]) -> bool:
        if not self.settings.on_errors:
            return False
        icon = "❌" if status == "failed" else "⚠️"
        lines = [
            f"{icon} <b>AutoNews — تشغيل غير سليم</b>",
            f"الحالة: <code>{status}</code>",
            f"نُشر: {published} · فشل: {failed} · {iso_now()}",
        ]
        for err in errors[:5]:
            lines.append(f"• <code>{_esc(err)[:180]}</code>")
        return self._send("\n".join(lines))

    def zero_publish(self, consecutive: int) -> bool:
        """تنبيه التشغيلات الصامتة — أهم إنذار مبكّر."""
        if consecutive < self.settings.on_zero_runs:
            return False
        return self._send(
            "😴 <b>AutoNews صامت</b>\n"
            f"مرّ {consecutive} تشغيلاً متتالياً بلا أي نشر.\n"
            "أسباب محتملة: انتهاء حصص كل المفاتيح · توكن منتهٍ · كل الأخبار مكرّرة.\n"
            "راجع: Actions → آخر تشغيل → logs/report.md"
        )

    def ai_exhausted(self) -> bool:
        if not self.settings.on_errors:
            return False
        return self._send(
            "🔑 <b>نفدت مفاتيح Gemini</b>\n"
            "كل الأزواج (مفاتيح × نماذج) مستنفدة أو مجمّدة.\n"
            "النظام سيكمل بقالب احتياطي، لكن الجودة ستنخفض حتى تجديد الحصة."
        )

    def source_disabled(self, source_id: str, source_name: str) -> bool:
        if not self.settings.on_errors:
            return False
        return self._send(
            f"🚫 <b>تعطيل مصدر تلقائياً</b>\n"
            f"المصدر: <code>{_esc(source_id)}</code> ({_esc(source_name)})\n"
            "تكرّر فشل الجلب — تحقّق من الرابط أو عطّله في settings.yml"
        )

    def review_requested(self, review_id: str, channel: str, title: str) -> bool:
        return self._send(
            f"📝 <b>مسوّدة بانتظار موافقتك</b>\n"
            f"القناة: <code>{_esc(channel)}</code>\n"
            f"المعرّف: <code>{_esc(review_id)}</code>\n"
            f"العنوان: {_esc(title[:120])}\n\n"
            "للموافقة: <code>/approve " + _esc(review_id) + "</code>"
        )

    def daily_summary(self, *, published: int, failed: int, top_channel: str, runs: int) -> bool:
        if not self.settings.daily_summary:
            return False
        return self._send(
            "📊 <b>ملخّص AutoNews اليومي</b>\n"
            f"تشغيلات: {runs} · منشورات: {published} · فشل: {failed}\n"
            f"أكثر قناة نشاطاً: <code>{_esc(top_channel)}</code>"
        )


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
