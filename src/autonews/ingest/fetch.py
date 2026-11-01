"""طبقة HTTP موحّدة للتصفّح الآلي.

المشاكل الحقيقية التي يحلّها هذا الملف (كل واحدة ظهرت فعلياً في التشغيل):
1. **الترميز**: مواقع عربية تُرسل `windows-1256` بلا `charset` صحيح ⇒ نص مشوّه.
2. **الحجب**: بعض المواقع ترفض UA البوت ⇒ نجرّب عدة هويّات مهذّبة.
3. **الضغط**: gzip/deflate/br — بدون فكّها يظهر النص كطلاسم.
4. **البطء/التعليق**: مهلة صارمة + إعادة محاولة + احترام `Retry-After`.
5. **إعادة التوجيه**: الروابط القديمة تتحوّل لصفحة أخرى.
"""
from __future__ import annotations

import gzip
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass

from ..utils import get_logger

log = get_logger("autonews.fetch")

# هويّات متدرّجة: نبدأ ببوت واضح (مهذّب)، ثم متصفّح شائع عند الرفض
USER_AGENTS = (
    "Mozilla/5.0 (compatible; AutoNewsAgent/1.2; +https://github.com/)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
)

# ترميزات عربية شائعة على مواقع قديمة
_ARABIC_ENCODINGS = ("windows-1256", "iso-8859-6", "cp1256")
_META_CHARSET_RE = re.compile(rb'charset=["\']?([\w\-]+)', re.I)


@dataclass
class FetchResult:
    url: str
    status: int = 0
    raw: bytes = b""
    final_url: str = ""
    content_type: str = ""
    encoding: str = "utf-8"
    ok: bool = False
    error: str = ""
    attempts: int = 0

    @property
    def text(self) -> str:
        return decode_bytes(self.raw, self.encoding)


def decode_bytes(raw: bytes, declared: str = "") -> str:
    """فكّ ترميز ذكي: نجرب المُعلن، ثم UTF-8، ثم الترميزات العربية."""
    if not raw:
        return ""
    candidates: list[str] = []
    if declared:
        candidates.append(declared)
    # من وسوم meta داخل الصفحة
    head = raw[:3000]
    for match in _META_CHARSET_RE.finditer(head):
        enc = match.group(1).decode("ascii", "ignore").lower()
        if enc and enc not in candidates:
            candidates.append(enc)
    candidates += ["utf-8", *_ARABIC_ENCODINGS]

    for enc in candidates:
        try:
            text = raw.decode(enc, "strict")
        except (UnicodeDecodeError, LookupError):
            continue
        # فحص معقولية: نسبة الحروف العربية/اللاتينية مقابل حروف غريبة
        sample = text[:4000]
        weird = sum(1 for ch in sample if ch in "ÃØÙ�" or (ord(ch) < 32 and ch not in "\n\r\t"))
        if weird / max(1, len(sample)) < 0.02:
            return text
    return raw.decode("utf-8", "replace")


def _decompress(raw: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc and raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error) as exc:
        log.debug("تعذّر فكّ الضغط (%s): %s", enc, exc)
    return raw


class Fetcher:
    """عميل HTTP مهيّأ للتصفّح الآلي مع تدرّج الهويّات وإعادة المحاولة."""

    def __init__(self, *, timeout: int = 30, max_bytes: int = 3_000_000,
                 retries: int = 2, user_agent: str = "") -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.retries = retries
        self.user_agent = user_agent
        self.stats = {"requests": 0, "failures": 0, "redirects": 0, "blocked": 0}

    # ---------------------------------------------------------------- الطلب
    def get(self, url: str, *, referer: str = "", accept: str = "") -> FetchResult:
        result = FetchResult(url=url, final_url=url)
        agents = ([self.user_agent] if self.user_agent else []) + list(USER_AGENTS)

        for agent in agents:
            for attempt in range(1, self.retries + 1):
                result.attempts += 1
                self.stats["requests"] += 1
                headers = {
                    "User-Agent": agent,
                    "Accept": accept or "*/*",
                    "Accept-Language": "ar,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "close",
                }
                if referer:
                    headers["Referer"] = referer
                req = urllib.request.Request(url, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        body = resp.read(self.max_bytes + 1)
                        result.status = resp.status
                        result.final_url = resp.geturl() or url
                        result.content_type = resp.headers.get("Content-Type", "")
                        declared = ""
                        m = re.search(r"charset=([\w\-]+)", result.content_type, re.I)
                        if m:
                            declared = m.group(1)
                        result.raw = _decompress(body, resp.headers.get("Content-Encoding", ""))[: self.max_bytes]
                        result.encoding = declared or "utf-8"
                        result.ok = True
                        if result.final_url != url:
                            self.stats["redirects"] += 1
                        return result
                except urllib.error.HTTPError as exc:
                    result.status = exc.code
                    if exc.code in (401, 403, 429):
                        self.stats["blocked"] += 1
                        retry_after = exc.headers.get("Retry-After") if exc.headers else None
                        wait = min(float(retry_after), 10) if (retry_after or "").isdigit() else 1.5
                        log.debug("منع/تجاوز حدّ (%s) لـ %s — محاولة بهوية أخرى", exc.code, url)
                        time.sleep(wait * attempt)
                        break            # جرّب الهوية التالية
                    if exc.code == 404:
                        result.error = "404 غير موجود"
                        return result
                    if exc.code >= 500 and attempt < self.retries:
                        time.sleep(1.5 * attempt)
                        continue
                    result.error = f"HTTP {exc.code}"
                    return result
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    result.error = f"شبكة: {exc}"
                    if attempt < self.retries:
                        time.sleep(1.2 * attempt)
                        continue
                    break
        self.stats["failures"] += 1
        return result

    def __call__(self, url: str) -> bytes:
        """واجهة مختصرة تُستخدم في الاكتشاف التلقائي (ترفع استثناءً عند الفشل)."""
        res = self.get(url)
        if not res.ok:
            raise OSError(res.error or f"HTTP {res.status}")
        return res.raw

    def page(self, url: str) -> str:
        """تحميل صفحة HTML كنص (مع الترميز الصحيح)."""
        return self.get(url, accept="text/html,application/xhtml+xml").text

    def report(self) -> str:
        s = self.stats
        return (f"طلبات={s['requests']} · فشل={s['failures']} · "
                f"تحويلات={s['redirects']} · منع/حدود={s['blocked']}")
