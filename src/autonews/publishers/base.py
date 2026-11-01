"""الواجهة الموحّدة للنشر على المنصّات.

كل ناشر يرث Publisher ويُنفّذ `_send()`، فيمكن إضافة منصّة جديدة بملف واحد.
يدعم الناشر الأساسي: نصاً واحداً، صورة مرفقة، أو سلسلة (Thread).
"""
from __future__ import annotations

import abc
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

from ..ai import Post
from ..config_loader import Channel
from ..images import ArticleImage
from ..utils import get_logger

log = get_logger("autonews.publish")


@dataclass(slots=True)
class PublishResult:
    ok: bool
    channel_id: str
    platform: str
    ref: str = ""
    error: str = ""
    skipped: bool = False
    dry_run: bool = False
    elapsed_ms: int = 0
    with_image: bool = False
    parts: int = 1

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "channel": self.channel_id, "platform": self.platform,
            "ref": self.ref, "error": self.error, "skipped": self.skipped,
            "dry_run": self.dry_run, "elapsed_ms": self.elapsed_ms,
            "with_image": self.with_image, "parts": self.parts,
        }


class Publisher(abc.ABC):
    """ناشر أساسي: يتحقّق من الاعتمادات ويوفّر طلب HTTP موحّد."""

    platform: str = "base"

    def __init__(self, channel: Channel, *, dry_run: bool = False, timeout: int = 30) -> None:
        self.channel = channel
        self.dry_run = dry_run
        self.timeout = timeout
        self.last_error = ""

    # ------------------------------------------------------------------ أدوات
    @abc.abstractmethod
    def validate(self) -> str:
        """يعيد رسالة خطأ إن كانت الاعتمادات ناقصة، أو نصاً فارغاً إن كانت سليمة."""

    @abc.abstractmethod
    def _send(self, text: str, image: ArticleImage | None = None) -> str:
        """الإرسال الفعلي (جزء واحد)، يعيد معرّف المنشور."""

    def _send_thread(self, texts: list[str], image: ArticleImage | None = None) -> str:
        """إرسال سلسلة — الافتراضي: منشورات منفصلة (X يعيد تعريفها كردود مترابطة)."""
        refs = []
        for i, part in enumerate(texts):
            refs.append(self._send(part, image if i == 0 else None))
        return ",".join(str(r) for r in refs if r)

    def render_texts(self, post: Post) -> list[str]:
        """تجهيز النصوص (منشور واحد أو سلسلة)."""
        if self.channel.threads and hasattr(post, "render_thread"):
            parts = post.render_thread(self.channel)
            if len(parts) > 1:
                return parts
        return [post.render(self.channel)]

    # ------------------------------------------------------------------ النشر
    def publish(self, post: Post, image: ArticleImage | None = None) -> PublishResult:
        texts = self.render_texts(post)
        if self.dry_run:
            for i, text in enumerate(texts, 1):
                prefix = f"[{i}/{len(texts)}] " if len(texts) > 1 else ""
                log.info("[DRY-RUN] %s/%s ← %d حرفاً %s%s\n%s",
                         self.platform, self.channel.id, len(text), prefix,
                         "🖼" if image and image.ok and i == 1 else "",
                         text[:400].replace("\n", " ⏎ "))
            return PublishResult(True, self.channel.id, self.platform, ref="dry-run",
                                 dry_run=True, with_image=bool(image and image.ok), parts=len(texts))

        problem = self.validate()
        if problem:
            log.error("قناة %s غير قابلة للنشر: %s", self.channel.id, problem)
            self.last_error = problem
            return PublishResult(False, self.channel.id, self.platform, error=problem, skipped=True)

        # 🖋 ختم الصورة قبل الإرسال (يعمل على كل منصّة وكل مسار نشر)
        try:
            from ..branding import brand_image

            image = brand_image(image, self.channel)
        except Exception as exc:  # noqa: BLE001 — الختم لا يُسقط النشر أبداً
            log.warning("تعذّر ختم الصورة في %s: %s", self.channel.id, exc)

        t0 = time.time()
        try:
            if len(texts) > 1:
                ref = self._send_thread(texts, image)
            else:
                ref = self._send(texts[0], image)
            elapsed = int((time.time() - t0) * 1000)
            log.info("📤 نُشر على %s/%s | ref=%s | %dms | أجزاء=%d | صورة=%s",
                     self.platform, self.channel.id, ref, elapsed, len(texts),
                     bool(image and image.ok))
            return PublishResult(True, self.channel.id, self.platform, ref=str(ref),
                                 elapsed_ms=elapsed, with_image=bool(image and image.ok),
                                 parts=len(texts))
        except Exception as exc:  # noqa: BLE001 — فشل منصّة لا يُسقط البقية
            elapsed = int((time.time() - t0) * 1000)
            log.error("❌ فشل النشر على %s/%s: %s", self.platform, self.channel.id, exc)
            self.last_error = str(exc)[:500]
            return PublishResult(False, self.channel.id, self.platform,
                                 error=str(exc)[:500], elapsed_ms=elapsed)

    # ------------------------------------------------------------------ HTTP
    def _request(self, url: str, *, method: str = "POST", data: dict | None = None,
                 headers: dict | None = None, form: dict | None = None) -> tuple[int, str]:
        hdrs = {"User-Agent": "AutoNewsAgent/1.0", **(headers or {})}
        body: bytes | None = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"خطأ شبكة: {exc}") from exc

    def _multipart(self, url: str, fields: dict[str, str], *, file_field: str,
                   file_path: str, mime: str = "image/jpeg",
                   headers: dict | None = None) -> tuple[int, str]:
        """رفع multipart/form-data (للصور) بلا مكتبات خارجية."""
        boundary = f"----AutoNews{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                f"{value}\r\n".encode("utf-8")
            )
        filename = file_path.rsplit("/", 1)[-1]
        guessed = mimetypes.guess_type(filename)[0] or mime
        with open(file_path, "rb") as fh:
            payload = fh.read()
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
             f"filename=\"{filename}\"\r\nContent-Type: {guessed}\r\n\r\n").encode("utf-8")
        )
        parts.append(payload)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        hdrs = {
            "User-Agent": "AutoNewsAgent/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **(headers or {}),
        }
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 60)) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"خطأ شبكة: {exc}") from exc


# --------------------------------------------------------------------------- #
#  المصنع + ناشر الطرفية
# --------------------------------------------------------------------------- #
def build_publisher(channel: Channel, *, dry_run: bool = False, timeout: int = 30) -> Publisher:
    """إنشاء الناشر المناسب لمنصّة القناة."""
    from .facebook import FacebookPublisher
    from .telegram import TelegramPublisher
    from .x import XPublisher

    registry = {
        "telegram": TelegramPublisher,
        "facebook": FacebookPublisher,
        "x": XPublisher,
        "console": ConsolePublisher,
    }
    cls = registry.get(channel.platform)
    if cls is None:
        raise ValueError(f"منصّة غير مدعومة: {channel.platform}")
    return cls(channel, dry_run=dry_run, timeout=timeout)


class ConsolePublisher(Publisher):
    """ناشر للتجربة المحلية: يطبع المنشور بدل إرساله."""

    platform = "console"

    def validate(self) -> str:
        return ""

    def _send(self, text: str, image: ArticleImage | None = None) -> str:
        print("\n" + "=" * 64)
        print(f"📰 [{self.channel.name}] {len(text)} حرفاً"
              + (f" · 🖼 {image.url[:60]}" if image and image.ok else ""))
        print("=" * 64)
        print(text)
        print("=" * 64 + "\n")
        return "console"


__all__ = ["Publisher", "PublishResult", "ConsolePublisher", "build_publisher"]
