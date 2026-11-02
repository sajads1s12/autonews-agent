"""أدوات تحرير الإعدادات من البوت — وسوم القنوات · إضافة قناة · إضافة مصدر.

الفكرة الحاكمة: **لا تفتح ملفاً ولا تستخدم حاسوباً.** كل ما تحتاجه من telefonك:

    🏷 وسوم القناة   → أضف/احذف وسماً بنقرة، مع فحص تداخل فوري
    ➕ قناة جديدة    → اسم + وسوم → تُكتب في channels.yml وتُربط فوراً
    📡 مصدر جديد     → رابط RSS → يُختبر الآن → يُضاف للمصادر

لماذا كتابة نصّية لا YAML→YAML؟
--------------------------------
`channels.yml` و`settings.yml` مليئان بتعليقات عربية تشرح كل خيار (وهي توثيقك).
`yaml.safe_dump` يمحوها كلها. لذلك نُعدّل الأسطر المستهدفة فقط، ونحتفظ بالباقي
حرفياً. وكل عملية تُختبر قبل الحفظ (`safe_load` بعد التعديل) وتُراجَع عند الفشل.

نقطة تصميم ثانية: **الوسوم تُستخرج من نفس مصدر الحقيقة** الذي يستخدمه
`config_loader` (تطبيع + مقارنة)، وإلّا صار المحرّر يقول «أُضيف» والتوجيه لا يرى شيئاً.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .utils import get_logger

log = get_logger("autonews.editor")

# --------------------------------------------------------------------------- #
#  أدوات عامة
# --------------------------------------------------------------------------- #
def _channel_bounds(lines: list[str], channel_id: str) -> tuple[int, int] | None:
    """حدود كتلة قناة داخل channels.yml: [بداية، نهاية)."""
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id:")
                  and ln.split(":", 1)[1].strip() == channel_id), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("- id:")), len(lines))
    return start, end


def _verify_yaml(text: str, *, key: str) -> str:
    """تحقّق أن الملف ما زال YAML صالحاً (نُعيد رسالة خطأ مقروءة أو "")."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return f"الملف تالف بعد التعديل: {str(exc)[:160]}"
    if key not in (data or {}):
        return f"المفتاح «{key}» اختفى — أُلغي التعديل"
    return ""


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
#  🏷 محرّر الوسوم
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TagEdit:
    ok: bool
    message: str
    tags: list[str] = None          # type: ignore[assignment]
    warnings: list[str] = None      # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.tags = list(self.tags or [])
        self.warnings = list(self.warnings or [])


def _block_list_end(lines: list[str], start: int, end: int) -> int:
    """نهاية قائمة YAML عمودية تبدأ عند `start`:
        sources:
        - a
        - b
    ترجع أول سطر لا ينتمي إليها (سطر أعمق بعناصر ليست `-`).
    """
    base = len(lines[start]) - len(lines[start].lstrip())
    i = start + 1
    last = start
    while i < end:
        raw = lines[i]
        if not raw.strip():
            break
        indent = len(raw) - len(raw.lstrip())
        #  YAML يسمح بالعناصر على نفس مسافة المفتاح:
        #      sources:
        #      - a
        #  فالمعيار: لا يكون السطر أعمق **ولا** عنصر قائمة ⇒ نهاية الكتلة.
        if indent < base or not raw.lstrip().startswith("- "):
            break
        last = i
        i += 1
    return last


def _render_inline(items: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def _read_channel_tags(path: Path, channel_id: str) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for item in data.get("channels") or []:
        if str(item.get("id")) == channel_id:
            return [str(t) for t in (item.get("tags") or [])]
    return []


def update_channel_sources(channel_id: str, sources: list[str], *,
                           config_dir: str = "config") -> "TagEdit":
    """ضبط مصادر القناة (قائمة معرّفات أو `["*"]`).

    يستخدم نفس أسلوب الوسوم: تعديل نصّي + تحقّق YAML قبل الكتابة،
    حتى تبقى تعليقات الملف العربية سليمة.
    """
    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return TagEdit(False, f"لم أجد {path}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    bounds = _channel_bounds(lines, channel_id)
    if bounds is None:
        return TagEdit(False, f"القناة «{channel_id}» غير موجودة")
    start, end = bounds

    clean = [str(s).strip() for s in sources if str(s).strip()]
    line_value = "[" + ", ".join(f'"{s}"' if s != "*" else '"*"' for s in clean) + "]"
    src_at = next((i for i in range(start, end)
                   if re.match(r"\s*sources:", lines[i])), None)
    if src_at is None:
        insert_at = next((i for i in range(start, end)
                          if re.match(r"\s*(tags|style|credentials|extra):", lines[i])), end)
        lines.insert(insert_at, f"    sources: {line_value}\n")
    else:
        indent = lines[src_at][:len(lines[src_at]) - len(lines[src_at].lstrip())]
        comment = ""
        if "#" in lines[src_at]:
            comment = "   #" + lines[src_at].split("#", 1)[1].rstrip("\n")
        value = lines[src_at].split(":", 1)[1].split("#")[0].strip()
        #  الصيغة العمودية:
        #      sources:
        #      - a
        #  سطر `sources:` وحده فارغ ⇒ نستبدل الكتلة كلها بسطر واحد،
        #  وإلا بقيت أسطر `- a` بعد سطر جديد فصار الملف تالفاً.
        if not value:
            tail = _block_list_end(lines, src_at, end)
            lines[src_at:tail + 1] = [f"{indent}sources: {line_value}{comment}\n"]
        else:
            lines[src_at] = f"{indent}sources: {line_value}{comment}\n"

    new_text = "".join(lines)
    problem = _verify_yaml(new_text, key="channels")
    if problem:
        return TagEdit(False, problem)
    path.write_text(new_text, encoding="utf-8")
    log.info("📡 مصادر %s ← %s", channel_id, clean)
    return TagEdit(True, f"✅ مصادر {channel_id}: {len(clean)}", clean)



def update_channel_tags(channel_id: str, *, add: list[str] | None = None,
                        remove: list[str] | None = None,
                        config_dir: str = "config") -> TagEdit:
    """إضافة/حذف وسوم قناة مع الحفاظ على تعليقات الملف.

    يُرجع كائن `TagEdit` فيه الوسوم النهائية وتحذيرات التداخل
    (مثلاً: وسم مضاف لقناة عامة يجعل الفصل بلا معنى).
    """
    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return TagEdit(False, f"لم أجد {path}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    bounds = _channel_bounds(lines, channel_id)
    if bounds is None:
        return TagEdit(False, f"القناة «{channel_id}» غير موجودة")
    start, end = bounds

    current = _read_channel_tags(path, channel_id)
    norm = lambda t: t.strip().lower()          # noqa: E731 — نفس تطبيع المحمّل
    before = {norm(t) for t in current}

    wanted = list(current)
    for tag in add or []:
        tag = tag.strip()
        if tag and norm(tag) not in before:
            wanted.append(tag)
            before.add(norm(tag))
    removed: list[str] = []
    for tag in remove or []:
        tag = tag.strip()
        if norm(tag) in before:
            wanted = [t for t in wanted if norm(t) != norm(tag)]
            before.discard(norm(tag))
            removed.append(tag)

    line_value = "[" + ", ".join(f'"{t}"' for t in wanted) + "]"
    tag_at = next((i for i in range(start, end)
                   if re.match(r"\s*tags:", lines[i])), None)
    if tag_at is None:
        # لا يوجد سطر وسوم: نُدرجه قبل style أو قبل credentials
        insert_at = next((i for i in range(start, end)
                          if re.match(r"\s*(style|credentials|extra):", lines[i])), end)
        lines.insert(insert_at, f"    tags: {line_value}\n")
    else:
        indent = lines[tag_at][:len(lines[tag_at]) - len(lines[tag_at].lstrip())]
        comment = ""
        if "#" in lines[tag_at]:
            comment = "   #" + lines[tag_at].split("#", 1)[1].rstrip("\n")
        value = lines[tag_at].split(":", 1)[1].split("#")[0].strip()
        if not value:
            #  صيغة عمودية (tags: ثم - a) ⇒ استبدال الكتلة كلها
            tail = _block_list_end(lines, tag_at, end)
            lines[tag_at:tail + 1] = [f"{indent}tags: {line_value}{comment}\n"]
        else:
            lines[tag_at] = f"{indent}tags: {line_value}{comment}\n"

    new_text = "".join(lines)
    problem = _verify_yaml(new_text, key="channels")
    if problem:
        return TagEdit(False, problem, current)

    path.write_text(new_text, encoding="utf-8")
    warnings = tag_warnings(channel_id, wanted, config_dir=config_dir)
    verb = []
    if add:
        verb.append(f"أُضيف {len([a for a in add if norm(a) in {norm(t) for t in wanted}])}")
    if removed:
        verb.append(f"حُذف {len(removed)}")
    msg = f"✅ وسوم {channel_id}: " + "، ".join(wanted) if wanted else f"✅ حُذفت كل وسوم {channel_id}"
    log.info("🏷 %s ← %s", channel_id, wanted)
    return TagEdit(True, msg, wanted, warnings)


def tag_warnings(channel_id: str, tags: list[str], *, config_dir: str = "config") -> list[str]:
    """تحذيرات ذكية: قناة عامة بوسم = فصل مكسور · وسم غير مستخدم = لا يصل لأحد."""
    path = Path(config_dir) / "channels.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    channels = [c for c in (data.get("channels") or []) if isinstance(c, dict)]
    norm = lambda t: str(t).strip().lower()      # noqa: E731
    mine = {norm(t) for t in tags}
    out: list[str] = []

    me = next((c for c in channels if str(c.get("id")) == channel_id), {})
    if mine and not me.get("enabled", True):
        out.append("القناة معطّلة — الوسوم لن تُنشر حتى تُفعّلها")

    for other in channels:
        if str(other.get("id")) == channel_id or not other.get("enabled", True):
            continue
        its = {norm(t) for t in (other.get("tags") or [])}
        excl = {norm(t) for t in (other.get("exclude_tags") or [])}
        if not its and not excl and mine:
            out.append(f"⚠️ «{other.get('id')}» عامة (بلا وسوم) — ستستقبل هذه الوسوم أيضاً. "
                       f"أضف للعامة exclude_tags إن أردت فصلاً.")
            break
        clash = mine & its
        if clash:
            out.append(f"🔁 تشابه مع «{other.get('id')}»: {'، '.join(sorted(clash))} "
                       f"— نفس الخبر سيُنشر في الاثنتين")
        blocked = mine & excl
        if blocked:
            out.append(f"🚫 «{other.get('id')}» تستثني: {'، '.join(sorted(blocked))}")
    return out


def all_tags(config_dir: str = "config", *, channels: bool = True,
             sources: bool = True) -> dict[str, list[str]]:
    """قاموس: وسم → القنوات التي تستقبله (للمحرّر الذكي)."""
    base = Path(config_dir)
    ch_data = yaml.safe_load((base / "channels.yml").read_text(encoding="utf-8")) or {}
    out: dict[str, list[str]] = {}
    for item in ch_data.get("channels") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        for tag in item.get("tags") or []:
            out.setdefault(str(tag).strip(), []).append(str(item.get("id")))
    if sources:
        st = yaml.safe_load((base / "settings.yml").read_text(encoding="utf-8")) or {}
        for item in st.get("sources") or []:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            for tag in item.get("tags") or []:
                out.setdefault(str(tag).strip(), [])
    return out


# --------------------------------------------------------------------------- #
#  🧪 فحص مصدر RSS
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FeedCheck:
    ok: bool
    url: str
    title: str = ""
    items: int = 0
    fresh: int = 0
    sample: str = ""
    error: str = ""
    elapsed_ms: int = 0

    def summary(self) -> str:
        if not self.ok:
            return f"❌ {self.url}\n{self.error}"
        line = f"✅ <b>{self.title or self.url}</b>\n{self.items} خبراً"
        if self.fresh:
            line += f" · {self.fresh} حديث"
        if self.sample:
            line += f"\nمثال: {self.sample[:90]}"
        return line


def check_feed(url: str, *, timeout: int = 15, max_items: int = 5) -> FeedCheck:
    """اختبار رابط RSS فعلياً قبل إضافته (لا نضيف مصدراً ميتاً)."""
    started = time.time()
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return FeedCheck(False, url, error="الرابط يجب أن يبدأ بـ http:// أو https://")
    try:
        import feedparser   # نفس المكتبة المستخدمة في المحرّك — لا فرق في السلوك
    except ImportError:          # pragma: no cover
        return FeedCheck(False, url, error="feedparser غير مثبّتة")

    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": "AutoNewsAgent/1.0"})
    except Exception as exc:  # noqa: BLE001
        return FeedCheck(False, url, error=f"تعذّر القراءة: {str(exc)[:140]}")

    entries = getattr(parsed, "entries", []) or []
    title = str((getattr(parsed, "feed", {}) or {}).get("title", "") or "")
    if not entries:
        err = str(getattr(parsed, "bozo_exception", "") or "لا يوجد أي خبر في الرابط")
        return FeedCheck(False, url, title=title, error=f"RSS فارغ أو غير صالح — {err[:120]}")

    fresh = 0
    now = time.time()
    for entry in entries:
        stamp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if stamp:
            try:
                age_h = (now - time.mktime(stamp)) / 3600
                if age_h <= 48:
                    fresh += 1
            except (OverflowError, ValueError):
                pass
    sample = str(getattr(entries[0], "title", "") or "")
    return FeedCheck(True, url, title=title, items=len(entries), fresh=fresh,
                     sample=sample, elapsed_ms=int((time.time() - started) * 1000))


# --------------------------------------------------------------------------- #
#  📡 إضافة مصدر
# --------------------------------------------------------------------------- #
SOURCE_TEMPLATE = """
  # ---- أُضيف من البوت: {stamp}
  - id: {sid}
    name: "{name}"
    url: {url}
    lang: {lang}
    max_items: {max_items}
    tags: [{tags}]
    enabled: true
"""


def _slug(text: str, fallback: str = "src") -> str:
    """معرّف لاتيني صالح من نصّ عربي/إنجليزي."""
    latin = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if latin and len(latin) >= 3:
        return latin[:32]
    # نصّ عربي: لا نستخرج حروفاً — نستخدم بصمة قصيرة ثابتة
    digest = abs(hash(text)) % 10_000
    return f"{fallback}_{digest:04d}"


def add_source(url: str, *, name: str = "", tags: list[str] | None = None,
               lang: str = "ar", max_items: int = 4,
               config_dir: str = "config", verify: bool = True) -> tuple[bool, str]:
    """إضافة مصدر RSS إلى settings.yml بعد اختباره فعلياً."""
    path = Path(config_dir) / "settings.yml"
    if not path.exists():
        return False, f"لم أجد {path}"

    check = check_feed(url) if verify else FeedCheck(True, url)
    if not check.ok:
        return False, ("❌ لم يُضف — الرابط لا يعمل:\n" + check.error
                       + "\n\nتأكّد أنه رابط RSS (لا صفحة عادية).")

    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    sources = [s for s in (data.get("sources") or []) if isinstance(s, dict)]
    clean_url = url.strip()
    if any(str(s.get("url", "")).strip() == clean_url for s in sources):
        return False, "⚠️ هذا المصدر موجود مسبقاً"

    sid = _slug(name or check.title or clean_url.rsplit("/", 1)[-1] or "feed")
    existing_ids = {str(s.get("id")) for s in sources}
    base_id, n = sid, 2
    while sid in existing_ids:
        sid = f"{base_id}_{n}"
        n += 1

    block = SOURCE_TEMPLATE.format(
        stamp=time.strftime("%Y-%m-%d"),
        sid=sid,
        name=(name or check.title or sid).replace('"', "'")[:60],
        url=clean_url,
        lang=lang,
        max_items=int(max_items),
        tags=", ".join(f'"{t}"' for t in (tags or [])) if tags else "",
    )
    new_text = text.rstrip() + "\n" + block
    problem = _verify_yaml(new_text, key="sources")
    if problem:
        return False, problem
    _write_atomic(path, new_text)

    msg = (f"✅ <b>أُضيف المصدر</b> · <code>{sid}</code>\n"
           f"{check.summary()}\n\nسيُسحب منه في التشغيل القادم.")
    if not tags:
        msg += "\n💡 بلا وسوم: سيصل لكل القنوات العامة. أضف وسماً لتصنيفه."
    log.info("📡 مصدر جديد %s ← %s (%d خبراً)", sid, clean_url, check.items)
    return True, msg


# --------------------------------------------------------------------------- #
#  ➕ إضافة قناة
# --------------------------------------------------------------------------- #
CHANNEL_TEMPLATE = """
  # ---- 🆕 أُضيفت من البوت: {stamp}
  - id: {cid}
    name: "{name}"
    platform: telegram
    enabled: true
    sources: []
    tags: [{tags}]
    style: >
      أسلوب متخصّص واضح: جملة واحدة قوية، بلا مقدمات ولا حشو،
      ابدأ بالمعلومة، واذكر ما يهمّ القارئ عملياً.
    language: ar
    max_chars: 4096
    add_hashtags: 3
    add_emoji: true
    include_source_link: true
    include_signature: true
    max_posts_per_run: 3
    with_image: true
    flash_eligible: true
    flash_priority: 6
    branding:
      handle: ""
      title: "{name}"
      footer: "📡 @{{handle}}"
      stamp_on_image: true
      position: bottom-right
      hidden_mark: true
    credentials:
      bot_token: TELEGRAM_BOT_TOKEN
      chat_id: {secret}
"""


def suggest_channel_id(name: str, *, existing: set[str] | None = None) -> str:
    """يولّد معرّفاً إنجليزياً من اسم/وصف عربي — المستخدم لا يكتب معرّفات.

    يعتمد على القطاع المكتشف: `tg_health` ثم `tg_health_2`... وإن لم يُعرف
    القطاع فـ `tg_new` ثم `tg_new_2`...
    """
    from .sectors import detect

    profile = detect(name)
    base = f"tg_{profile.key}" if profile is not None else "tg_new"
    base = re.sub(r"[^a-z0-9_]", "_", base.lower())[:20]
    if not re.match(r"^[a-z]", base):
        base = "tg_new"
    taken = existing or set()
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def add_channel(channel_id: str, *, name: str = "", description: str = "", tags: list[str] | None = None,
                sector: str = "", config_dir: str = "config") -> tuple[bool, str]:
    """إضافة قناة تيليجرام كاملة (بصمة + سرّ + وسوم) معطّلة الأمان لا مطلوب."""
    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return False, f"لم أجد {path}"

    cid = re.sub(r"[^a-z0-9_]", "_", channel_id.strip().lower())
    if not re.match(r"^[a-z][a-z0-9_]{2,20}$", cid):
        return False, ("❌ معرّف غير صالح: استخدم أحرفاً إنجليزية صغيرة وأرقاماً و_ "
                       "مثل <code>tg_crypto</code>")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    existing = {str(c.get("id")) for c in (data.get("channels") or []) if isinstance(c, dict)}
    if cid in existing:
        return False, f"⚠️ القناة «{cid}» موجودة مسبقاً"

    secret = f"TELEGRAM_{cid.upper().removeprefix('TG_')}_CHAT_ID"
    block = CHANNEL_TEMPLATE.format(
        stamp=time.strftime("%Y-%m-%d"),
        cid=cid,
        name=(name or sector or cid).replace('"', "'")[:50],
        tags=", ".join(f'"{t}"' for t in (tags or [])),
        secret=secret,
    )
    text = path.read_text(encoding="utf-8")
    new_text = text.rstrip() + "\n" + block
    problem = _verify_yaml(new_text, key="channels")
    if problem:
        return False, problem
    _write_atomic(path, new_text)

    msg = (f"✅ <b>أُضيفت القناة</b> <code>{cid}</code>\n\n"
           f"١) اربطها الآن: اضغط «🔗 اربط» ثم أرسل <code>@اسم_القناة</code>\n"
           f"٢) بلا سرّ ولا معرّف يدوي: الربط يُحفظ في المستودع الخاص\n\n"
           f"💡 المعرّف يُحفظ في <code>state/channels_setup.json</code> — لا حاجة للسرّ.")
    log.info("➕ قناة جديدة %s (وسوم=%s)", cid, tags)
    return True, msg


__all__ = [
    "TagEdit", "update_channel_tags", "tag_warnings", "all_tags",
    "FeedCheck", "check_feed", "add_source",
    "add_channel", "CHANNEL_TEMPLATE", "SOURCE_TEMPLATE",
    "update_channel_sources",
]


def update_channel_setting(channel_id: str, key: str, value: str | int | bool, *,
                           config_dir: str = "config") -> "TagEdit":
    """تعديل مفتاح عددي/منطقي في قناة (مثل `max_posts_per_run`).

    مفيد من البوت: «حدّد عدد المنشورات» بلا تحرير ملفات.
    يتحقّق من النوع قبل الكتابة حتى لا يُفسد الإعداد.
    """
    allowed_num = {"max_posts_per_run", "flash_priority", "add_hashtags", "max_chars"}
    allowed_bool = {"enabled", "with_image", "flash_eligible", "review_required",
                    "breaking_boost", "include_source_link", "include_signature",
                    "add_emoji"}
    if key not in allowed_num | allowed_bool:
        return TagEdit(False, f"❌ مفتاح غير مسموح: {key}")

    if key in allowed_num:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return TagEdit(False, "❌ القيمة يجب أن تكون رقماً")
        if not 0 <= value <= 100:
            return TagEdit(False, "❌ القيمة خارج المدى (0–100)")
        rendered = str(value)
    else:
        rendered = "true" if value in (True, "true", "1", 1) else "false"

    path = Path(config_dir) / "channels.yml"
    if not path.exists():
        return TagEdit(False, f"لم أجد {path}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    bounds = _channel_bounds(lines, channel_id)
    if bounds is None:
        return TagEdit(False, f"القناة «{channel_id}» غير موجودة")
    start, end = bounds

    line_at = next((i for i in range(start, end)
                    if re.match(rf"\s*{re.escape(key)}:", lines[i])), None)
    if line_at is None:
        insert_at = next((i for i in range(start, end)
                          if re.match(r"\s*(credentials|extra|branding):", lines[i])), end)
        lines.insert(insert_at, f"    {key}: {rendered}\n")
    else:
        indent = lines[line_at][:len(lines[line_at]) - len(lines[line_at].lstrip())]
        comment = ""
        if "#" in lines[line_at]:
            comment = "   #" + lines[line_at].split("#", 1)[1].rstrip("\n")
        lines[line_at] = f"{indent}{key}: {rendered}{comment}\n"

    new_text = "".join(lines)
    problem = _verify_yaml(new_text, key="channels")
    if problem:
        return TagEdit(False, problem)
    path.write_text(new_text, encoding="utf-8")
    log.info("⚙️ %s.%s = %s", channel_id, key, rendered)
    return TagEdit(True, f"✅ {channel_id}: {key} = {rendered}")


def set_channel_enabled(channel_id: str, enabled: bool, *,
                        config_dir: str = "config") -> "TagEdit":
    """تفعيل/تعطيل قناة بنقرة — أوضح رسالة من تعديل خام."""
    res = update_channel_setting(channel_id, "enabled", enabled, config_dir=config_dir)
    if res.ok:
        return TagEdit(True, "✅ القناة مفعّلة — ستنشر من التشغيل القادم."
                       if enabled else
                       "⏸ القناة معطّلة — لن تُنشر ولن تُحسب في التوزيع.", [])
    return res


__all__ += ["update_channel_setting", "set_channel_enabled"]
