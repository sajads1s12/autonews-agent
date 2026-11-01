"""اختبارات الإحصاءات: تُحسب من الحالة الحقيقية، وتكشف القنوات الصامتة."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from autonews.stats import build_stats
from autonews.utils import iso_now, utcnow


def _write_state(path, posts, sources=None, stats=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "posted": posts,
        "source_health": sources or {},
        "stats": stats or {},
        "articles": {}, "runs": 1,
    }, ensure_ascii=False), encoding="utf-8")


def _post(channel, title="خبر", *, hours_ago=1.0):
    when = (utcnow() - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return {"at": when, "channel": channel, "platform": "telegram",
            "article_fp": f"fp{title}", "title": title, "url": "https://x/1", "ref": "1"}


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "state.json"
    control = tmp_path / "control.json"
    return state, control


def test_counts_today_and_total(env):
    state, control = env
    _write_state(state, [_post("tg_main", hours_ago=1), _post("tg_main", hours_ago=5),
                         _post("tg_main", hours_ago=100)])
    report = build_stats(known=[("tg_main", "📰 العامة")],
                         state_file=str(state), control_path=str(control))
    ch = report.channels[0]
    assert ch.total == 3
    assert ch.today == 2, "قبل ١٠٠ ساعة ليس اليوم"
    assert report.total_posts == 3


def test_silent_channel_flagged(env):
    state, control = env
    _write_state(state, [])
    report = build_stats(known=[("tg_anime", "🎬 الأنمي")],
                         state_file=str(state), control_path=str(control))
    ch = report.channels[0]
    assert ch.total == 0 and ch.status_icon() == "⬜"
    assert "لم تنشر" in report.text()
    assert any("لم تنشر" in a for a in report.advice())


def test_stale_channel_gets_alarm(env):
    state, control = env
    _write_state(state, [_post("tg_main", hours_ago=72)])
    report = build_stats(known=[("tg_main", "📰 العامة")],
                         state_file=str(state), control_path=str(control))
    assert report.channels[0].status_icon() == "🚨"
    assert any("صامتة" in a for a in report.advice())


def test_best_hour_computed(env):
    state, control = env
    _write_state(state, [_post("tg_main", "أ", hours_ago=1),
                         _post("tg_main", "ب", hours_ago=1.2),
                         _post("tg_main", "ج", hours_ago=9)])
    report = build_stats(known=[("tg_main", "📰")], state_file=str(state),
                         control_path=str(control), tz_offset=0)
    assert report.best_hour >= 0
    assert sum(report.by_hour) == 3


def test_dead_sources_counted(env):
    state, control = env
    _write_state(state, [], sources={
        "ok_src": {"failures": 0, "successes": 5},
        "dead_src": {"failures": 6, "disabled": True},
        "shaky": {"failures": 4},
    })
    report = build_stats(state_file=str(state), control_path=str(control))
    assert report.sources_ok == 1
    assert report.sources_dead == 2
    assert any("مصدراً لا يعمل" in a for a in report.advice())


def test_pending_and_scheduled_from_control(env):
    from autonews.control import KIND_APPROVAL, KIND_SCHEDULED, ControlStore

    state, control = env
    _write_state(state, [])
    store = ControlStore.load(control)
    store.enqueue(KIND_APPROVAL, "tg_main", {"title": "أ"})
    store.enqueue(KIND_SCHEDULED, "tg_main", {"title": "ب"})
    report = build_stats(state_file=str(state), control_path=str(control))
    assert report.pending_approvals == 1 and report.scheduled == 1
    assert "تنتظر موافقتك" in report.text()


def test_missing_state_file_does_not_crash(env):
    state, control = env
    report = build_stats(state_file=str(state), control_path=str(control))
    assert report.total_posts == 0


def test_sparkline_is_24_cells(env):
    state, control = env
    _write_state(state, [_post("tg_main")])
    report = build_stats(known=[("tg_main", "📰")], state_file=str(state),
                         control_path=str(control))
    assert report.text().count("▁") >= 0
    assert len(report.by_hour) == 24
