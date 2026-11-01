"""أدوات مساعدة عامة: تسجيل، بيئة، وقت، تجزئة نصية."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
#  التسجيل (Logging)
# --------------------------------------------------------------------------- #
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """تهيئة تسجيل موحّد على stdout (متوافق مع GitHub Actions)."""
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # تقليل ضجيج مكتبات الطرف الثالث
    for noisy in ("urllib3", "requests", "feedparser", "google_genai", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# --------------------------------------------------------------------------- #
#  البيئة والمتغيّرات
# --------------------------------------------------------------------------- #
_PLACEHOLDER_HINTS = ("your_", "xxxx", "changeme", "todo", "<", "example.com")


def env_str(name: str, default: str = "") -> str:
    """قراءة متغيّر بيئة نصّي مع تنظيف المسافات وعلامات الاقتباس."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().strip("'\"")
    return value or default


def env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, str(default))).strip().lower()
    return raw in ("1", "true", "yes", "y", "on", "نعم")


def looks_like_placeholder(value: str) -> bool:
    """كشف القيم الوهمية (placeholder) حتى لا ينهار البوت بسببها."""
    if not value or len(value) < 8:
        return True
    low = value.lower()
    return any(h in low for h in _PLACEHOLDER_HINTS)


def split_env_list(raw: str) -> list[str]:
    """تقسيم قائمة مفاتيح/معرّفات مفصولة بفاصلة أو سطر أو مسافة أو ';'."""
    if not raw:
        return []
    parts = re.split(r"[,\n;]+|\s{2,}", raw)
    return [p.strip().strip("'\"") for p in parts if p and p.strip()]


# --------------------------------------------------------------------------- #
#  الوقت
# --------------------------------------------------------------------------- #
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    """تحويل تواريخ RSS/JSON إلى datetime واعٍ بالمنطقة الزمنية."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    # ISO 8601 مع أجزاء الثانية (‎2026-11-01T04:42:34.734885+00:00) — كان يفشل سابقاً
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def human_age(dt: datetime | None) -> str:
    if not dt:
        return "غير معروف"
    delta = utcnow() - dt
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "الآن"
    if mins < 60:
        return f"قبل {mins} دقيقة"
    hours = mins // 60
    if hours < 24:
        return f"قبل {hours} ساعة"
    return f"قبل {hours // 24} يوم"


# --------------------------------------------------------------------------- #
#  النصوص
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def strip_html(text: str) -> str:
    """إزالة وسوم HTML وفكّ الترميزات الأساسية."""
    if not text:
        return ""
    import html as html_lib

    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(text))
    cleaned = re.sub(r"(?i)<br\s*/?>|</p>", "\n", cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html_lib.unescape(cleaned)
    cleaned = _WS_RE.sub(" ", cleaned)
    return _MULTI_NL_RE.sub("\n\n", cleaned).strip()


def normalize_arabic(text: str) -> str:
    """توحيد أشكال الحروف العربية (ألف/ياء/تاء مربوطة) لمقارنة أفضل."""
    if not text:
        return ""
    table = str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ى": "ي", "ئ": "ي", "ؤ": "و",
        "ة": "ه",
    })
    text = text.translate(table)
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)  # التشكيل
    return text


def fingerprint(text: str) -> str:
    """بصمة محتوى (SHA-256 مختصرة) للكشف عن التكرار."""
    return hashlib.sha256(normalize_arabic(text).encode("utf-8")).hexdigest()[:20]


def url_fingerprint(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:20]


def truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[: max(1, limit - len(ellipsis))]
    # لا تقطع في منتصف كلمة إن أمكن
    if " " in cut[int(len(cut) * 0.7):]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip("،,. ") + ellipsis


def estimate_tokens(text: str) -> int:
    """تقدير تقريبي لعدد التوكنات (عربي ≈ 2 حرف/توكن)."""
    text = text or ""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_chars
    return int(ascii_chars / 4 + other / 2) + 1


def chunk_text(text: str, limit: int) -> list[str]:
    """تجزئة نص طويل عند حدود الجمل/الأسطر مع احترام حدّ المنصّة."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        # الكتلة نفسها أطول من الحد => تقسيم عند الجمل
        sentences = re.split(r"(?<=[.!؟?])\s+|\n", block)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            cand = f"{current} {sentence}".strip() if current else sentence
            if len(cand) <= limit:
                current = cand
            else:
                if current:
                    chunks.append(current)
                while len(sentence) > limit:
                    chunks.append(sentence[:limit])
                    sentence = sentence[limit:]
                current = sentence
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def jaccard(a: str, b: str) -> float:
    """تشابه تقريبي بين نصين عبر مجموعات الكلمات (للكشف عن الأخبار المتشابهة)."""
    wa = set(re.findall(r"\w{3,}", normalize_arabic(a).lower()))
    wb = set(re.findall(r"\w{3,}", normalize_arabic(b).lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# كلمات وظيفية لا تُحتسب في تشابه الموضوع
_STOPWORDS = {
    "في", "من", "على", "عن", "الى", "إلى", "مع", "هذا", "هذه", "ذلك", "التي", "الذي",
    "بعد", "قبل", "خلال", "بين", "كل", "جميع", "أعلن", "اعلن", "قال", "أكد", "اكد",
    "اليوم", "غدا", "غداً", "أمس", "امس", "أن", "ان", "إن", "كان", "كانت", "كما",
    "هو", "هي", "تم", "يتم", "جدا", "جداً", "ايضا", "أيضاً", "حول", "ضد", "عند",
}
_WORD_RE = re.compile(r"[\w\u0600-\u06FF]{3,}")


def topic_tokens(text: str) -> set[str]:
    """كلمات الموضوع الجوهرية (بعد إزالة «ال» التعريف واللواحق الشائعة)."""
    tokens: set[str] = set()
    for raw in _WORD_RE.findall(normalize_arabic(text or "").lower()):
        word = raw
        if word.startswith("ال") and len(word) > 4:
            word = word[2:]
        elif word.startswith("و") and len(word) > 4:
            word = word[1:]
        for suffix in ("ات", "ون", "ين", "ها", "هم", "ية", "ه"):
            if len(word) > 5 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        if len(word) >= 3 and word not in _STOPWORDS:
            tokens.add(word)
    return tokens


def topic_overlap(a: str, b: str) -> float:
    """تشابه موضوعي (تقاطع الكلمات الجوهرية) — أدقّ من Jaccard لعناوين الأخبار.

    مثال: «عطلة رسمية يوم الخميس» و«تمديد العطلة الرسمية» ⇒ تشابه عالٍ
    رغم اختلاف الصيغ الصرفية (عطلة/العطلة، رسمية/الرسمية).
    """
    ta, tb = topic_tokens(a), topic_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def take(items: Iterable, limit: int) -> list:
    out = []
    for i, item in enumerate(items):
        if i >= limit:
            break
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
#  🖋 كتابة بصمة الملكية داخل config/channels.yml (من البوت بلا محرّر نصوص)
# --------------------------------------------------------------------------- #
def write_branding(channel_id: str, *, handle: str | None = None,
                   enabled: bool | None = None, stamp_on_image: bool | None = None,
                   hidden_mark: bool | None = None,
                   config_dir: str = "config") -> tuple[bool, str]:
    """تحديث كتلة `branding` لقناة داخل channels.yml مع الحفاظ على بقية الملف.

    لماذا كتابة نصّية لا YAML→YAML؟ لأن `channels.yml` يحمل تعليقات عربية مهمّة
    (شرح كل خيار). إعادة التوليد عبر yaml.safe_dump تمحوها كلها. لذلك نُعدّل
    الأسطر المستهدفة فقط، وإن لم تكن الكتلة موجودة نُدرجها بعد `credentials`.
    """
    from pathlib import Path

    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return False, f"لم أجد {path}"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # موضع القناة
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id:") and ln.split(":", 1)[1].strip() == channel_id), None)
    if start is None:
        return False, f"القناة «{channel_id}» غير موجودة في channels.yml"
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("- id:")), len(lines))

    updates = {
        "handle": f'"{handle}"' if handle is not None else None,
        "enabled": str(bool(enabled)).lower() if enabled is not None else None,
        "stamp_on_image": str(bool(stamp_on_image)).lower() if stamp_on_image is not None else None,
        "hidden_mark": str(bool(hidden_mark)).lower() if hidden_mark is not None else None,
    }
    updates = {k: v for k, v in updates.items() if v is not None}

    brand_at = next((i for i in range(start, end) if lines[i].strip() == "branding:"), None)
    if brand_at is None:
        # أدرج كتلة جديدة بعد credentials (أو بعد extra إن لم يوجد)
        insert_at = next((i for i in range(start, end)
                          if lines[i].strip() == "extra:"), None)
        if insert_at is None:
            insert_at = end
        block = ["    # 🖋 بصمة الملكية\n", "    branding:\n",
                 "      footer: \"📡 @{handle}\"\n", "      stamp_on_image: true\n",
                 "      position: bottom-right\n", "      hidden_mark: true\n"]
        block = [block[0], block[1]] + [
            f'      {k}: {v}\n' for k, v in updates.items()] + block[2:]
        lines[insert_at:insert_at] = block
        path.write_text("".join(lines), encoding="utf-8")
        return True, f"أُضيفت بصمة جديدة لـ {channel_id}"

    # عدّل الأسطر الموجودة داخل الكتلة
    i = brand_at + 1
    done: set[str] = set()
    while i < end and (lines[i].startswith("     ") or not lines[i].strip()):
        stripped = lines[i].strip()
        if stripped and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            if key in updates:
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                comment = ""
                if "#" in lines[i]:
                    comment = "   #" + lines[i].split("#", 1)[1].rstrip("\n")
                lines[i] = f"{indent}{key}: {updates[key]}{comment}\n"
                done.add(key)
        i += 1
    missing = [k for k in updates if k not in done]
    for key in missing:
        lines.insert(i, f"      {key}: {updates[key]}\n")
        i += 1
    path.write_text("".join(lines), encoding="utf-8")
    return True, f"حُدِّثت بصمة {channel_id}: " + "، ".join(updates)


def write_channel_extra(channel_id: str, key: str, value: str,
                        config_dir: str = "config") -> tuple[bool, str]:
    """كتابة قيمة في `extra` لقناة داخل channels.yml (لتجاوز اسم متغيّر البيئة).

    الاستخدام العملي: صفحة فيسبوك. `page_id` **ليس سرّاً** (رقم عام)، فيمكن حفظه
    في المستودع — بينما `page_token` سرّ يبقى في GitHub Secrets وحده.
    """
    from pathlib import Path

    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return False, f"لم أجد {path}"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id:") and ln.split(":", 1)[1].strip() == channel_id), None)
    if start is None:
        return False, f"القناة «{channel_id}» غير موجودة"
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("- id:")), len(lines))

    env_key = f"ENV_{key.upper()}"
    extra_at = next((i for i in range(start, end) if lines[i].strip() == "extra:"), None)
    if extra_at is None:
        insert_at = next((i for i in range(start, end)
                          if lines[i].strip() == "credentials:"), end)
        lines[insert_at:insert_at] = ["    extra:\n", f'      {env_key}: "{value}"\n']
    else:
        # موجودة بالفعل؟
        found = None
        indent = "      "
        for i in range(extra_at + 1, end):
            if not lines[i].startswith("     ") and lines[i].strip():
                break
            if lines[i].strip().startswith(f"{env_key}:"):
                found = i
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                break
        if found is not None:
            lines[found] = f'{indent}{env_key}: "{value}"\n'
        else:
            lines.insert(extra_at + 1, f'{indent}{env_key}: "{value}"\n')

    new_text = "".join(lines)
    try:
        import yaml

        data = yaml.safe_load(new_text)
        if "channels" not in (data or {}):
            return False, "التعديل أفسد الملف — أُلغي"
    except Exception as exc:  # noqa: BLE001
        return False, f"YAML تالف: {str(exc)[:120]}"
    path.write_text(new_text, encoding="utf-8")
    return True, f"حُفظ {env_key} = {value} لقناة {channel_id}"
