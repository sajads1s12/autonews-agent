"""توليد تقرير التشغيل (Markdown) للعرض في سجلّ GitHub Actions وملف محلي."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .utils import iso_now, truncate


@dataclass
class RunReport:
    started_at: str = field(default_factory=iso_now)
    duration_s: float = 0.0
    dry_run: bool = False
    articles_fetched: int = 0
    articles_processed: int = 0
    articles_skipped: int = 0
    posts_published: int = 0
    posts_failed: int = 0
    fallback_posts: int = 0
    posts_with_image: int = 0
    quality_rejections: int = 0
    retries_succeeded: int = 0
    updates_detected: int = 0
    # ---- v6: النتائج والتعريب ----
    outcome_posts: int = 0        # منشورات بطاقات النتائج 📋
    deferred_incidents: int = 0    # حوادث مؤجّلة (بلا نتائج)
    followups_resolved: int = 0    # تطوّرات أُغلقت 🔄
    translations: int = 0          # أخبار عُربّت
    translation_rejected: int = 0  # تعريبات مرفوضة (أرقام/جودة)
    per_channel: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)   # تفاصيل كل منشور
    ai_report: str = ""
    status: str = "success"

    def add(self, text: str) -> None:
        self.lines.append(text)

    def to_markdown(self) -> str:
        icon = {"success": "✅", "partial": "⚠️", "failed": "❌"}.get(self.status, "ℹ️")
        md = [f"# {icon} تقرير تشغيل AutoNews — {self.started_at}", ""]
        if self.dry_run:
            md.append("> 🧪 **تشغيل تجريبي (DRY_RUN)** — لم يُنشر أي محتوى فعلياً.\n")
        md += [
            "| المؤشّر | القيمة |",
            "|---|---|",
            f"| المدة | {self.duration_s:.1f} ثانية |",
            f"| مقالات مسحوبة | {self.articles_fetched} |",
            f"| مقالات معالَجة | {self.articles_processed} |",
            f"| مقالات متجاهَلة (مكرّرة) | {self.articles_skipped} |",
            f"| منشورات ناجحة | {self.posts_published} |",
            f"| منشورات فاشلة | {self.posts_failed} |",
            f"| منشورات بقالب احتياطي | {self.fallback_posts} |",
            f"| منشورات بصور | {self.posts_with_image} |",
            f"| رفض بوّابة الجودة | {self.quality_rejections} |",
            f"| نجاح إعادة المحاولة | {self.retries_succeeded} |",
            f"| تحديثات مشروعة (لا تكرار) | {self.updates_detected} |",
            f"| 📋 بطاقات نتائج منشورة | {self.outcome_posts} |",
            f"| ⏭ حوادث مؤجّلة (بلا نتائج) | {self.deferred_incidents} |",
            f"| 🔄 تطوّرات أُغلِقت بنتائجها | {self.followups_resolved} |",
            f"| 🌐 أخبار عُربّت · مرفوضة | {self.translations} · {self.translation_rejected} |",
        ]
        if self.per_channel:
            md += ["", "**التوزيع حسب القناة:** " + " · ".join(
                f"{k}={v}" for k, v in sorted(self.per_channel.items()))]
        if self.lines:
            md += ["", "## 📋 التفاصيل", ""] + [f"- {line}" for line in self.lines]
        if self.errors:
            md += ["", "## ⚠️ أخطاء", ""] + [f"- `{truncate(e, 300)}`" for e in self.errors]
        if self.ai_report:
            md += ["", self.ai_report]
        md += ["", "---", f"_تم التوليد آلياً بواسطة AutoNews Agent v1.0 — {iso_now()}_"]
        return "\n".join(md)

    def write(self, path: str | Path = "logs/report.md") -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_markdown(), encoding="utf-8")
        # ملخّص GitHub Actions (إن كنّا داخل Actions)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            try:
                with open(summary_path, "a", encoding="utf-8") as fh:
                    fh.write(self.to_markdown() + "\n")
            except OSError:
                pass
        # مخرجات للخطوة التالية في Actions
        out_env = os.environ.get("GITHUB_OUTPUT")
        if out_env:
            try:
                with open(out_env, "a", encoding="utf-8") as fh:
                    fh.write(f"published={self.posts_published}\n")
                    fh.write(f"status={self.status}\n")
            except OSError:
                pass
        return out
