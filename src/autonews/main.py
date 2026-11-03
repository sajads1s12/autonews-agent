"""نقطة الدخول: دورة العمل الكاملة.

    1) سحب المحتوى (مع صحة المصادر والتعطيل التلقائي)
    2) تصفية وتوجيه (تكرار داخل التشغيل + عبر التشغيلات) + ترتيب حسب الأولوية/العاجل
    3) صياغة ذكية + بوّابة جودة + صورة الخبر
    4) نشر مباشر للمحتوى الموثوق + طابور إعادة للفاشل + تنبيهات + تقرير

الاستخدام:
    python -m autonews.main                 # تشغيل عادي
    python -m autonews.main --dry-run       # معاينة بلا نشر
    python -m autonews.main --check         # فحص الصحة والإعدادات
    python -m autonews.main --check --probe # + فحص فعلي لكل مفتاح Gemini
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "autonews"

from .ai import AIUnavailable, ContentWriter, KeyRotator  # noqa: E402
from .breaking import is_breaking, priority_of  # noqa: E402
from .config_loader import Channel, ConfigError, Settings, load_config  # noqa: E402
from .control import (  # noqa: E402
    KIND_APPROVAL, KIND_SCHEDULED, ControlStore, decide, due_iso,
    local_hour, minutes_until_open, preview_text,
)
from .images import fetch_article_image  # noqa: E402
from .ingest import Article, fetch_all_with_status  # noqa: E402
from .notify import Notifier  # noqa: E402
from .occasions import OccasionCalendar, decide as decide_duplicate  # noqa: E402
from .publishers import PublishResult, build_publisher  # noqa: E402
from .quality import check_post  # noqa: E402
from .report import RunReport  # noqa: E402
from .router import ContentRouter  # noqa: E402
from .state import State, StateStore  # noqa: E402
from .utils import (  # noqa: E402
    get_logger, human_age, jaccard, setup_logging, truncate, utcnow,
)

log = get_logger("autonews.main")


def calendar_today(tz_offset: int = 3) -> str:
    """تاريخ اليوم بالتوقيت المحلي (YYYY-MM-DD)."""
    from datetime import timedelta

    return (utcnow() + timedelta(hours=tz_offset)).date().isoformat()


# --------------------------------------------------------------------------- #
#  بناء المكوّنات
# --------------------------------------------------------------------------- #
def build_engine(settings: Settings) -> KeyRotator | None:
    if not settings.ai.keys:
        log.warning("⚠️ لا توجد مفاتيح Gemini — سيعمل النظام بالقالب الاحتياطي فقط")
        return None
    return KeyRotator(
        keys=settings.ai.keys,
        models=settings.ai.models,
        cooldown_quota=settings.ai.cooldown_quota_seconds,
        cooldown_error=settings.ai.cooldown_error_seconds,
        request_timeout=settings.ai.request_timeout,
        max_retries_per_pair=settings.ai.max_retries_per_pair,
        health_path="state/ai_health.json",
        token_budget=settings.ai.token_budget_per_run,
    )


# --------------------------------------------------------------------------- #
#  فحص الصحة
# --------------------------------------------------------------------------- #
def run_check(settings: Settings, *, probe: bool = False) -> int:
    ok = True
    print("\n🩺 فحص صحة النظام\n" + "=" * 64)
    print(f"المصادر المفعّلة: {len(settings.sources)}")
    for s in settings.sources:
        print(f"  • {s.id:<22} {s.name[:30]:<32} ثقة={s.trust:<4} {s.url[:44]}")
    print(f"\nالقنوات المفعّلة: {len(settings.channels)}")
    for ch in settings.channels:
        pub = build_publisher(ch, dry_run=settings.dry_run, timeout=settings.request_timeout)
        problem = pub.validate()
        flags = "".join([
            "🖼" if ch.with_image else "",
            "📝" if ch.review_required else "",
            "🧵" if ch.threads else "",
            "🔴" if ch.breaking_boost else "",
            "🅰🅱" if ch.style_b else "",
        ])
        print(f"  {'✅' if not problem else '❌'} {ch.id:<20} [{ch.platform:<8}] {flags:<10} {problem}")
        if problem:
            ok = False

    # 🗺 فصل القطاعات: أي قناة تستقبل أي وسم
    print("\n🗺 توجيه القطاعات:")
    for ch in settings.channels:
        if ch.tags:
            print(f"  ✅ {ch.id:14} مشمولة: {'، '.join(ch.tags)}")
        else:
            print(f"  🌐 {ch.id:14} عامة (كل شيء)")
        if ch.exclude_tags:
            print(f"     🚫 تستثني: {'، '.join(ch.exclude_tags)}")

    print(f"\nمفاتيح Gemini: {len(settings.ai.keys)} " + ", ".join(
        f"key#{i + 1}" for i in range(len(settings.ai.keys))))
    print(f"النماذج: {', '.join(settings.ai.models)}")
    print(f"الميزانية/تشغيل: {settings.ai.token_budget_per_run or 'بلا حد'} توكن")
    print(f"بوّابة الجودة: {'مفعّلة' if settings.quality.require_numbers_match else 'متساهلة'}"
          f" · كشف الاختلاق={'نعم' if settings.quality.require_numbers_match else 'لا'}")
    print(f"الصور: {'مفعّلة' if settings.images.enabled else 'معطّلة'}"
          f" · المراجعة البشرية: {'مفعّلة' if settings.review.enabled else 'معطّلة'}"
          f" · العاجل: {'مفعّلة' if settings.breaking.enabled else 'معطّلة'}")
    notifier = Notifier(settings.alerts, dry_run=settings.dry_run)
    print(f"التنبيهات: {'جاهزة' if notifier.enabled else 'غير مهيّأة (ALERT_BOT_TOKEN/ALERT_CHAT_ID)'}")
    print(f"وضع التشغيل: {'DRY_RUN (معاينة)' if settings.dry_run else 'نشر فعلي'}")

    # ------------------------------------------------ 🧩 فحص تكامل الطبقات (v6)
    oc, tr = settings.outcomes, settings.translation
    print("\n🧩 تكامل الطبقات" + " " + "=" * 47)
    print(f"المسار السريع: {'مفعّل' if settings.flash.enabled else 'معطّل'}"
          f" · حداثة {settings.flash.max_age_minutes}د · سقف {settings.flash.max_per_hour}/ساعة"
          f" · Push-to-Channel: {'مهيّأ' if settings.flash.scoped_chat_id else 'غير مهيّأ'}")
    print(f"نمط النتائج: {oc.policy if oc.enabled else 'معطّل'}"
          f" · تحقّق مزدوج: {'نعم' if oc.dual_verify else 'لا'}"
          f" · الأثر: {'نعم' if oc.impact_line else 'لا'}"
          f" · الشفافية: {'نعم' if oc.source_line else 'لا'}")
    print(f"المتابعة: {'نعم' if oc.followup else 'لا'} ({oc.followup_max_age_hours:.0f}س)"
          f" · خريطة المحافظات: {'نعم' if oc.daily_matrix else 'لا'} الساعة {oc.daily_matrix_hour}"
          f" · تفاوت: {'نعم' if oc.contrast else 'لا'}"
          f" · صمت: {'نعم' if oc.silent_enabled else 'لا'} ({oc.silent_hours:.0f}س)")
    print(f"التعريب: {'مفعّل' if tr.enabled else 'معطّل'}"
          f" · لغات: {', '.join(tr.langs)} · سقف {tr.max_per_run}/دورة"
          f" · حماية الأرقام: {'نعم' if tr.require_numbers_match else 'لا'}")

    # حالة حيّة من ملف الحالة: هل النظام يصمت؟ هل تتراكم حوادث؟
    try:
        from .state import StateStore

        st = StateStore(settings.state_file).load()
        pending = len(st.deferred_incidents)
        last_flash = st.flash_log[-1].get("at", "") if st.flash_log else ""
        age = ""
        if last_flash:
            from .utils import human_age, parse_dt

            age = human_age(parse_dt(last_flash))
        print(f"\nالحالة: تشغيلات={st.runs} · منشورات={len(st.posted)}"
              f" · حوادث بانتظار نتائج={pending}"
              f" · آخر نشر فوري: {age or 'لا يوجد'}")
        if pending >= oc.followup_max_items * 0.8:
            print(f"  ⚠️ قائمة المتابعة تقترب من السقف ({pending}/{oc.followup_max_items})")
        for sid, health in st.source_health.items():
            if health.get("disabled"):
                print(f"  🚫 مصدر معطّل تلقائياً: {sid}")
    except Exception as exc:  # noqa: BLE001 — الفحص لا يفشل بسبب الحالة
        print(f"\n⚠️ تعذّرت قراءة الحالة: {exc}")

    if probe:
        engine = build_engine(settings)
        if engine is None:
            print("\n⚠️ لا مفاتيح لفحصها")
        else:
            print("\n🔬 فحص فعلي لكل مفتاح (يتصل بـ Gemini):")
            for row in engine.self_test():
                print(f"  {row['key']:<10} {row['masked']:<24} {row['status']:<12} "
                      f"{row['ms']:>5}ms  {row.get('detail', '')[:48]}")

    print("=" * 64 + "\n")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
#  دورة النشر الأساسية
# --------------------------------------------------------------------------- #
def run_once(settings: Settings, *, limit: int | None = None) -> RunReport:
    t_start = time.time()
    report = RunReport(dry_run=settings.dry_run)

    store = StateStore(settings.state_file)
    state = store.load()
    state.begin_run()

    from .router import register_sources

    register_sources(settings.sources)      # مصداقية المصادر + أولوية الرسمي
    router = ContentRouter(settings.channels)
    engine = build_engine(settings)
    writer = ContentWriter(engine, settings, state)
    notifier = Notifier(settings.alerts, dry_run=settings.dry_run)
    control = ControlStore.load() if settings.control.enabled else ControlStore()
    control.purge(max_hours=settings.control.queue_max_hours)
    tz_offset = settings.timezone_offset

    # سياق الأعياد والمناسبات (يغيّر: النبرة، الأولوية، وسياسة التكرار)
    calendar = OccasionCalendar.from_config(settings.occasions.config_file) \
        if settings.occasions.enabled else OccasionCalendar([])
    tz = settings.occasions.tz_offset
    context_hint = calendar.context_hint(tz_offset=tz)
    dedup_policy = calendar.dedup_policy(tz_offset=tz)
    if context_hint:
        log.info("🗓 سياق اليوم: %s", context_hint)
    if dedup_policy.get("relaxed"):
        log.info("🕊 تساهل التكرار مفعّل (%s) — نافذة %d ساعة، حتى %d تحديثات",
                 ", ".join(dedup_policy.get("occasions", [])),
                 dedup_policy.get("window_hours", 0), dedup_policy.get("max_updates", 0))

    # ------------------------------------------------- 0) لا توجد موافقات بشرية
    # تُترك عناصر الطابور القديمة للقراءة فقط؛ لا يُسحب منها أي منشور للنشر.

    # ------------------------------------------------- 1) سحب المحتوى
    log.info("─" * 26 + " 1/5 سحب المحتوى " + "─" * 26)
    articles, failed_sources = fetch_all_with_status(settings)
    report.articles_fetched = len(articles)

    # 🌐 تعريب المصادر الأجنبية (قبل التصفية حتى تدخل عربية)
    if settings.translation.enabled and engine is not None:
        from .i18n import apply_translation, needs_translation, translate_article

        translated: list[Article] = []
        done = 0
        for art in articles:
            if (done < settings.translation.max_per_run
                    and needs_translation(art, target=settings.translation.target)
                    and (art.lang or "").strip().lower().replace("_", "-").split("-", 1)[0]
                    in settings.translation.langs):
                tr = translate_article(art, engine, target=settings.translation.target)
                if tr.ok and tr.changed:
                    done += 1
                    log.info("🌐 عُرّب: %s → %s", truncate(art.title, 40), truncate(tr.title, 40))
                    translated.append(apply_translation(art, tr))
                    continue
            translated.append(art)
        articles = translated
        report.translations = done
        if done:
            report.add(f"🌐 عُرّب {done} خبراً أجنبياً")

    from .facts import has_independent_reports
    source_index = {source.id: source for source in settings.sources}
    incoming_articles = len(articles)
    articles = [article for article in articles if has_independent_reports(article, articles, source_index)]
    unconfirmed_articles = incoming_articles - len(articles)
    if unconfirmed_articles:
        report.articles_skipped += unconfirmed_articles
        report.add(f"🔎 استُبعد {unconfirmed_articles} خبراً لعدم وجود تأكيد مستقل")

    # صحة المصادر: تعطيل تلقائي للمصدر الذي يتكرّر فشله
    active_sources = []
    for source in settings.sources:
        failed = source.id in failed_sources
        disabled_now = state.record_source_result(
            source.id, ok=not failed, items=0, fail_threshold=5)
        if disabled_now:
            notifier.source_disabled(source.id, source.name)
            report.add(f"🚫 عُطِّل مصدر تلقائياً: {source.name}")
        if state.source_disabled(source.id):
            continue
        active_sources.append(source)

    # ------------------------------------------------- 2) تصفية وتوجيه
    log.info("─" * 26 + " 2/5 تصفية وتوجيه " + "─" * 26)
    recent_titles = state.recent_titles(settings.cross_run_lookback)
    candidates: list[tuple[Article, list[Channel], bool, int]] = []
    seen_titles: list[str] = []

    deferred_count = 0
    for art in articles:
        if state.has_article(art.fp, retry_failed_after_runs=settings.retry_failed_after_runs):
            report.articles_skipped += 1
            continue
        # منع إعادة نشر الخبر من نفس الرابط حتى لو تغيّرت معاملات UTM.
        if state.was_recently_published(art.url, art.title):
            log.info("🔗 رابط خبر منشور مسبقاً — تجاهل: %s", truncate(art.title, 60))
            state.mark(art.fp, "skipped", reason="duplicate:canonical_url")
            report.articles_skipped += 1
            continue
        # 🎬 الأنمي: الإعلان خبر بذاته — لا يمرّ على بوّابة «حدث بلا نتائج»
        is_anime_item = False
        if settings.verticals.anime.enabled:
            from .verticals import classify_anime, should_publish_anime

            av = classify_anime(art)
            if av.is_anime:
                ok_a, why_a = should_publish_anime(
                    av, min_score=settings.verticals.anime.min_score)
                if not ok_a:
                    log.info("🎬 رُفض أنمي — %s: %s", why_a, truncate(art.title, 55))
                    state.mark(art.fp, "skipped", reason=f"anime:{why_a[:60]}")
                    report.articles_skipped += 1
                    continue
                if "أنمي" not in art.tags:
                    art.tags.append("أنمي")
                is_anime_item = True

        # 📋 نمط النتائج: نفس القاعدة على الدورة العادية — لا حادث خام بلا نتائج
        if (not is_anime_item and settings.outcomes.enabled
                and settings.outcomes.policy != "raw"):
            from .outcomes import should_publish

            ok_pub, why_pub, an = should_publish(art, settings.outcomes)
            if not ok_pub:
                log.info("⏭ مؤجّل (بلا نتائج): %s | %s", truncate(art.title, 60), why_pub[:50])
                state.mark(art.fp, "deferred", title=truncate(art.title, 120), reason=why_pub[:120])
                if settings.outcomes.followup:
                    from .outcome_features import register_deferred

                    tmp: list[dict] = []
                    if register_deferred(an, art, store=tmp):
                        payload = dict(tmp[0])
                        payload["score"] = 2.0 + (2.0 if an.escalate else 0.0)
                        state.defer_incident(
                            payload, max_items=settings.outcomes.followup_max_items)
                report.articles_skipped += 1
                deferred_count += 1
                report.deferred_incidents = deferred_count
                continue
        if any(jaccard(art.title, t) >= settings.dedup_similarity for t in seen_titles):
            log.info("≈ تشابه عالٍ داخل التشغيل — تجاهل: %s", truncate(art.title, 60))
            state.mark(art.fp, "skipped", reason="similar")
            report.articles_skipped += 1
            continue
        # تكرار أم تحديث؟ (يفهم العطل الرسمية والمناسبات والتحديثات المشروعة)
        is_update = False
        previous_title = ""
        if settings.cross_run_dedup and recent_titles:
            verdict = decide_duplicate(
                new_title=art.title,
                new_summary=art.summary,
                past_titles=recent_titles,
                published_at=art.published,
                update_keywords=calendar.update_keywords,
                policy=dedup_policy,
                strict_similarity=settings.cross_run_similarity,
            )
            if verdict.is_update:
                # تحديث مشروع: يُنشر كخبر جديد مع شارة 🔄 وإشارة للنموذج
                is_update = True
                previous_title = next(
                    (t for t in recent_titles
                     if jaccard(art.title, t) >= (0.45 if dedup_policy.get("relaxed") else 0.5)), "")
                log.info("🔄 تحديث مشروع (%s) — %s", verdict.reason, truncate(art.title, 60))
                report.updates_detected += 1
            elif verdict.duplicate:
                log.info("♻️ تكرار — تجاهل: %s | %s", truncate(art.title, 55), verdict.reason)
                state.mark(art.fp, "skipped", reason=f"duplicate:{verdict.similarity:.2f}")
                report.articles_skipped += 1
                continue

        targets = router.targets(art)
        if not targets:
            state.mark(art.fp, "skipped", reason="no_target")
            report.articles_skipped += 1
            continue

        seen_titles.append(art.title)
        breaking, _hits = is_breaking(art, settings.breaking)
        priority = priority_of(art, settings.breaking, breaking=breaking)
        candidates.append((art, targets, breaking, priority))

    # ترتيب: الأولوية (عاجل + حداثة) ثم الأحدث نشراً
    candidates.sort(key=lambda c: (-c[3], -(c[0].published.timestamp() if c[0].published else 0)))
    candidates = candidates[: (limit or settings.max_items_per_run)]
    log.info("مرشّحون للنشر: %d (من %d مقال) — عاجل: %d",
             len(candidates), len(articles), sum(1 for c in candidates if c[2]))

    # ------------------------------------------------- 3) صياغة + جودة + صورة
    log.info("─" * 26 + " 3/5 صياغة ▸ جودة ▸ صورة " + "─" * 26)
    posts_per_channel: dict[str, int] = {ch.id: 0 for ch in settings.channels}
    chars_per_channel: dict[str, int] = {ch.id: 0 for ch in settings.channels}
    breaking_used = 0
    published_any = False
    ai_exhausted = False

    def gate(channel: Channel, post, art: Article, *, breaking: bool,
             verdict=None) -> str:
        """بوّابة النشر: نشر موثوق، رفض مشكوك، أو تأجيل بسبب الجدول.

        • لا توجد موافقات بشرية؛ الضعيف/الاحتياطي/الحساس يُرفض.
        • التحقق من الثقة والجودة يعمل حتى عند تعطيل أدوات الجدولة.
        • ساعات الهدوء تؤجّل المنشور النظيف ولا تُسقطه.
        """
        ctl = settings.control
        c = control.get(channel.id)

        # بوابة الثقة تعمل دائماً، حتى عند تعطيل ساعات الهدوء/السقوف.
        score = float(getattr(verdict, "score", 1.0) or 1.0)
        extra_reasons = list(getattr(verdict, "warnings", []) or [])
        action, why = decide(mode="smart", post=post, score=score, reasons=extra_reasons,
                             min_score=c.min_score or ctl.min_score, breaking=breaking)

        # لا موافقات: المحتوى غير الموثوق يُستبعد نهائياً.
        if action == "discard":
            reason = "; ".join(why[:4]) or "فشل فحص الثقة والجودة"
            log.warning("🗑 رُفض قبل النشر (%s): %s | %s",
                        channel.name, truncate(post.title, 55), reason)
            report.add(f"🗑 استُبعد ({channel.name}) — {truncate(post.title, 65)}")
            state.mark(art.fp, "skipped", reason=f"trust_gate:{reason[:140]}")
            return "discard"

        if not ctl.enabled:
            return "publish"

        # (١) ساعات الهدوء — تُؤجّل (إلّا العاجل)
        hour = local_hour(tz_offset)
        if not (breaking and ctl.breaking_bypass_quiet):
            from .control import is_quiet

            if is_quiet(hour, c.quiet_start, c.quiet_end):
                wait = minutes_until_open(hour, 0, c.quiet_start, c.quiet_end)
                control.enqueue(KIND_SCHEDULED, channel.id, post.as_dict(),
                                due_at=due_iso(wait), score=score,
                                reasons=["🌙 ساعات هدوء — يُنشر بعد فتح النافذة"],
                                preview=preview_text({"post": post.as_dict()}))
                report.add(f"🌙 أُجّل ({channel.name}) — هدوء حتى {c.quiet_end:02d}:00")
                state.mark(art.fp, "deferred", reason="quiet_hours")
                return "deferred"

        # (٢) فاصل زمني بين منشورين في القناة نفسها
        if ctl.min_gap_minutes and c.last_post_at and not breaking:
            from .utils import parse_dt, utcnow

            last = parse_dt(c.last_post_at)
            if last and (utcnow() - last).total_seconds() < ctl.min_gap_minutes * 60:
                wait = ctl.min_gap_minutes - int((utcnow() - last).total_seconds() // 60)
                control.enqueue(KIND_SCHEDULED, channel.id, post.as_dict(),
                                due_at=due_iso(max(1, wait)), score=score,
                                reasons=[f"⏱ فاصل {ctl.min_gap_minutes} دقيقة"],
                                preview=preview_text({"post": post.as_dict()}))
                report.add(f"⏱ أُجّل ({channel.name}) — فاصل زمني")
                return "deferred"

        # (٣) سقف الساعة لكل قناة
        ok_now, why_not = c.allow_now(tz_offset=tz_offset)
        if not ok_now and not breaking:
            if "هدوء" in why_not:
                return "publish"      # سبق التعامل معه
            report.add(f"🛑 لن يُنشر في {channel.name}: {why_not}")
            return "blocked"

        # fail closed: لا مسوّدات ولا طابور موافقات في هذا المسار.
        return "publish" if action == "publish" else "discard"

    def dispatch(post, channel: Channel, art: Article, *, breaking: bool) -> PublishResult:
        """نشر أو تحويل لمسوّدة، مع تطبيق كل السياسات."""
        nonlocal published_any, breaking_used
        # لا ننشئ مسودات موافقة يدوية؛ القرار مرّ مسبقاً عبر بوابة النشر.
        # (ب) الصورة — من الرابط الأصلي قبل إضافة وسوم الحملة
        image = None
        if channel.with_image and settings.images.enabled:
            image = fetch_article_image(post.source_url, settings=settings.images)

        # (ج) وسوم UTM (بعد الصورة كي لا نُلوّث رابط الجلب)
        if settings.links.enabled:
            from .links import UTMSettings, tag_post_url

            tag_post_url(post, platform=channel.platform, settings=UTMSettings(
                enabled=settings.links.enabled,
                source_override=settings.links.source_override,
                medium=settings.links.medium,
                campaign_prefix=settings.links.campaign_prefix,
                keep_existing=settings.links.keep_existing,
            ))

        # (د) النشر
        publisher = build_publisher(channel, dry_run=settings.dry_run,
                                    timeout=max(30, settings.request_timeout))
        try:
            result = publisher.publish(post, image)
        finally:
            if image:
                image.cleanup()

        if result.ok:
            published_any = True
            report.posts_published += 1
            if getattr(post, "is_outcome", False):
                report.outcome_posts += 1
            posts_per_channel[channel.id] += 1
            chars_per_channel[channel.id] += len(post.body)
            report.per_channel[channel.id] = report.per_channel.get(channel.id, 0) + 1
            if result.with_image:
                report.posts_with_image += 1
            if breaking:
                breaking_used += 1
            control.record_post(channel.id, tz_offset=tz_offset)
            state.record_post(channel=channel.id, article_fp=art.fp, title=post.title,
                              url=post.source_url, platform=channel.platform,
                              post_ref=result.ref, variant=post.variant,
                              source=getattr(post, "source_name", ""))
            note = " · 🔴عاجل" if breaking else ""
            note += f" · 🅰{post.variant}🅱" if channel.style_b and post.variant == "A" else ""
            note += " · 🖼" if result.with_image else ""
            report.add(f"**{channel.name}** ({channel.platform}) — {truncate(post.title, 85)}"
                       f" · {post.model or 'fallback'}{note} · {human_age(art.published)}")
        else:
            report.posts_failed += 1
            report.errors.append(f"{channel.id}: {result.error}")
            if settings.retry.enabled:
                state.queue_retry(channel_id=channel.id, post=post.as_dict(),
                                  reason=result.error or "unknown")
        return result

    for art, targets, breaking, _priority in candidates:
        report.articles_processed += 1
        eligible: list[Channel] = []
        for ch in targets:
            # سقوف النشر (يمكن تجاوزها للعاجل إن سُمح)
            cap = ch.max_posts_per_run + (settings.breaking.max_per_run
                                          if breaking and ch.breaking_boost else 0)
            if posts_per_channel[ch.id] >= cap:
                continue
            # سقف أحرف اختياري لكل قناة في التشغيل
            if ch.max_chars_per_run and chars_per_channel[ch.id] >= ch.max_chars_per_run:
                continue
            if breaking and ch.breaking_boost and breaking_used >= settings.breaking.max_per_run * len(settings.channels):
                continue
            eligible.append(ch)

        if not eligible:
            state.mark(art.fp, "skipped", reason="channel_quota")
            report.articles_skipped += 1
            continue

        log.info("%s %s | %s | → %s", "🔴" if breaking else "📰", art.source_id,
                 truncate(art.title, 60), ", ".join(ch.id for ch in eligible))

        failed_any = False
        sent_any = False
        reviewed_any = False
        for channel in eligible:
            post = writer.write(art, channel, breaking=breaking,
                                context_hint=context_hint,
                                is_update=is_update, previous_title=previous_title)
            if post.used_fallback:
                report.fallback_posts += 1

            # بوّابة الجودة
            verdict = check_post(post, art, channel, settings.quality)
            report.quality_rejections += 0 if verdict.ok else 1
            if not verdict.ok:
                state.mark(art.fp, "rejected", reason="; ".join(verdict.reasons)[:180])
                report.add(f"🚧 رُفض ({channel.name}): {' | '.join(verdict.reasons)[:120]}")
                continue

            # 🎛 بوّابة النشر: نشر موثوق / رفض / تأجيل
            decision = gate(channel, post, art, breaking=breaking, verdict=verdict)
            if decision in ("discard", "review", "deferred", "blocked"):
                if decision == "review":
                    reviewed_any = True
                continue

            result = dispatch(post, channel, art, breaking=breaking)
            if result.ok:
                if str(result.ref).startswith("review:"):
                    reviewed_any = True
                else:
                    sent_any = True
            elif not result.skipped:
                failed_any = True

        if failed_any:
            state.mark(art.fp, "failed", title=truncate(art.title, 120))
        elif sent_any:
            state.mark(art.fp, "posted", title=truncate(art.title, 120), via="regular")
        elif reviewed_any:
            pass          # حالة "review" سُجّلت مسبقاً داخل dispatch
        else:
            state.mark(art.fp, "skipped", reason="nothing_sent")

        if report.articles_processed % 3 == 0:
            store.save(state)

    # ---------------------------------------- 3.5) بطاقات المناسبات (اختياري)
    if settings.occasions.greetings and settings.review.enabled is not None:
        for occasion in calendar.active_greetings(tz_offset=tz):
            if state.has_article(f"greeting:{occasion.id}:{calendar_today(tz)}"):
                continue
            for channel in settings.channels:
                if channel.platform in ("console",):
                    continue
                card = writer.greeting(occasion, channel)
                if card is None:
                    continue
                publisher = build_publisher(channel, dry_run=settings.dry_run,
                                            timeout=max(30, settings.request_timeout))
                result = publisher.publish(card)
                report.add(f"🎉 بطاقة مناسبة ({occasion.name}) · {channel.name}"
                           f"{' ✅' if result.ok else ' ❌'}")
                if result.ok:
                    report.posts_published += 1
                    report.per_channel[channel.id] = report.per_channel.get(channel.id, 0) + 1
            state.mark(f"greeting:{occasion.id}:{calendar_today(tz)}", "posted",
                       title=occasion.name)

    # ------------------------------------------------- 4) طابور الإعادة
    log.info("─" * 26 + " 4/5 طابور الإعادة " + "─" * 26)
    if settings.retry.enabled:
        retries = state.take_retries(limit=settings.retry.per_run,
                                     max_attempts=settings.retry.max_attempts)
        for item in retries:
            channel = next((c for c in settings.channels if c.id == item["channel"]), None)
            if channel is None:
                continue
            from .ai.writer import Post

            post = Post.from_dict(item["post"])
            image = fetch_article_image(post.source_url, settings=settings.images) \
                if channel.with_image and settings.images.enabled else None
            publisher = build_publisher(channel, dry_run=settings.dry_run,
                                        timeout=max(30, settings.request_timeout))
            try:
                result = publisher.publish(post, image)
            finally:
                if image:
                    image.cleanup()
            if result.ok:
                report.retries_succeeded += 1
                report.posts_published += 1
                report.per_channel[channel.id] = report.per_channel.get(channel.id, 0) + 1
                report.add(f"♻️ نجحت إعادة المحاولة · {channel.name} — {truncate(post.title, 70)}")
                state.record_post(channel=channel.id, article_fp=post.article_fp,
                                  title=post.title, url=post.source_url,
                                  platform=channel.platform, post_ref=result.ref)
            else:
                state.queue_retry(channel_id=channel.id, post=item["post"],
                                  reason=result.error or "retry failed")
                report.add(f"❌ فشلت إعادة المحاولة · {channel.name}: {truncate(result.error, 90)}")
        if not retries:
            log.info("طابور الإعادة فارغ")

    # ------------------------------------- 4.5) نشر ما حان وقته من الطابور
    if settings.control.enabled:
        log.info("─" * 26 + " 4.5/5 طابور التحكّم " + "─" * 26)
        for item in control.due(kind=KIND_SCHEDULED):
            channel = next((c for c in settings.channels if c.id == item.get("channel")), None)
            if channel is None:
                control.take(item["id"])
                continue
            c = control.get(channel.id)
            ok_now, why_not = c.allow_now(tz_offset=tz_offset)
            if not ok_now:
                # ما زلنا في هدوء/سقف — نؤجّله قليلاً بدل إسقاطه
                item["due_at"] = due_iso(30)
                item["reasons"] = [why_not]
                control.save()
                continue
            from .ai.writer import Post as _Post

            post = _Post.from_dict(item.get("post") or {})
            image = fetch_article_image(post.source_url, settings=settings.images) \
                if channel.with_image and settings.images.enabled else None
            publisher = build_publisher(channel, dry_run=settings.dry_run,
                                        timeout=max(30, settings.request_timeout))
            try:
                result = publisher.publish(post, image)
            finally:
                if image:
                    image.cleanup()
            control.take(item["id"])
            if result.ok:
                published_any = True
                report.posts_published += 1
                report.per_channel[channel.id] = report.per_channel.get(channel.id, 0) + 1
                control.record_post(channel.id, tz_offset=tz_offset)
                report.add(f"🕒 نُشر بعد التأجيل · {channel.name} — {truncate(post.title, 70)}")
                state.record_post(channel=channel.id, article_fp=post.article_fp,
                                  title=post.title, url=post.source_url,
                                  platform=channel.platform, post_ref=result.ref)
            else:
                report.posts_failed += 1
                report.errors.append(f"{channel.id} (queued): {result.error}")

    # ------------------------------------------------- 5) انتهى المسار بلا موافقات بشرية

    # ------------------------------------------------- خاتمة
    report.duration_s = time.time() - t_start
    if report.posts_failed and not published_any:
        report.status = "failed"
    elif report.posts_failed:
        report.status = "partial"
    else:
        report.status = "success"

    state.finish_run(report.posts_published)

    # 📊 لوحة الأداء + أفضل أسبوع (تُكتب في كل تشغيل ناجح)
    if settings.dashboard.enabled:
        try:
            from .dashboard import write_reports

            dash, best = write_reports(
                state, output_file=settings.dashboard.output_file,
                best_of_file=settings.dashboard.best_of_file,
                days=settings.dashboard.days, top_n=settings.dashboard.top_n)
            report.add(f"📊 لوحة الأداء: `{dash}` · أفضل أسبوع: `{best}`")
        except Exception as exc:  # noqa: BLE001 — اللوحة لا تُسقط التشغيل
            log.warning("تعذّر بناء لوحة الأداء: %s", exc)

    if engine:
        report.ai_report = engine.markdown_report()
        if not engine.available_pairs():
            ai_exhausted = True

    store.save(state)
    path = report.write("logs/report.md")

    # ---- تنبيهات ----
    if ai_exhausted:
        notifier.ai_exhausted()
    if report.status != "success":
        notifier.run_failed(status=report.status, published=report.posts_published,
                            failed=report.posts_failed, errors=report.errors)
    if report.posts_published == 0 and state.consecutive_zero_runs >= settings.alerts.on_zero_runs:
        notifier.zero_publish(state.consecutive_zero_runs)

    log.info("─" * 60)
    log.info("انتهى التشغيل | الحالة=%s | نُشر=%d | فشل=%d | إعادة=%d | رفض جودة=%d | مدة=%.1fs",
             report.status, report.posts_published, report.posts_failed,
             report.retries_succeeded, report.quality_rejections, report.duration_s)
    log.info("تقرير: %s", path)
    return report


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autonews", description="AutoNews Agent — أتمتة إخبارية متعدّدة المنصّات")
    p.add_argument("--dry-run", action="store_true", help="معاينة بلا نشر فعلي")
    p.add_argument("--check", action="store_true", help="فحص الصحة والإعدادات فقط")
    p.add_argument("--probe", action="store_true", help="مع --check: فحص كل مفتاح Gemini فعلياً")
    p.add_argument("--limit", type=int, default=0, help="حدّ أقصى لعدد المقالات في التشغيل")
    p.add_argument("--config", default="config", help="مجلّد الإعدادات (افتراضي: config)")
    p.add_argument("--log-level", default="", help="DEBUG/INFO/WARNING")
    p.add_argument("--once", action="store_true", help="تشغيل واحد فقط (سلوك GitHub Actions)")
    p.add_argument("--version", action="version", version="AutoNews Agent 1.1.0")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_config(args.config, override_dry_run=True if args.dry_run else None)
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("❌ خطأ في الإعدادات: %s", exc)
        return 2

    setup_logging(args.log_level or settings.log_level)

    if args.check:
        return run_check(settings, probe=args.probe)

    try:
        report = run_once(settings, limit=args.limit or None)
    except AIUnavailable as exc:
        log.error("تعذّر التوليد: %s", exc)
        return 3
    except KeyboardInterrupt:  # pragma: no cover
        log.warning("أُوقف يدوياً")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.exception("فشل غير متوقّع أثناء التشغيل: %s", exc)
        try:
            Notifier(settings.alerts, dry_run=settings.dry_run).run_failed(
                status="crash", published=0, failed=1, errors=[str(exc)[:200]])
        except Exception:  # noqa: BLE001
            pass
        return 1

    return 0 if report.status in ("success", "partial") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
