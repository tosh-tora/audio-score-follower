#!/usr/bin/env python3
"""execute_action() の発火ログ書式を固定するテスト。

trigger の任意項目 `author`（解説を書いた人）はログ末尾に付くが、
未指定の config では従来の書式のままであることを保証する。
"""
import logging
import threading

from audio_score_follower.core.cooldown_timer import CooldownTimer
from audio_score_follower.core.state_manager import AppState
from audio_score_follower.core.trigger_engine import TriggerEngine


class _FakeSlideController:
    def __init__(self):
        self.presses = []

    def press(self, action):
        self.presses.append(action)


def _make_engine():
    return TriggerEngine(
        state=AppState(),
        cooldown=CooldownTimer(3.0),
        slide_controller=_FakeSlideController(),
        stop_event=threading.Event(),
        get_oltw=lambda: None,
        get_warp_lookup=lambda: None,
        get_score_mapper=lambda: None,
        get_cooldown_seconds=lambda: 3.0,
        notify_seek=lambda: None,
    )


def test_author_appears_in_the_fire_log(caplog):
    engine = _make_engine()
    trigger = {"measure": 1, "note": "不気味な導入", "author": "平（指揮）"}
    with caplog.at_level(logging.INFO):
        engine.execute_action("right", source="auto", trigger=trigger)
    assert "measure=1 note=不気味な導入 author=平（指揮）" in caplog.text


def test_log_format_unchanged_without_author(caplog):
    engine = _make_engine()
    with caplog.at_level(logging.INFO):
        engine.execute_action("right", source="auto", trigger={"measure": 1, "note": "開始"})
    assert "Slide right [auto] measure=1 note=開始" in caplog.text
    assert "author=" not in caplog.text


def test_manual_action_marks_manual_adjustment():
    # 聴衆パネルの「人が調整！」は manual の送りでだけ点く
    engine = _make_engine()
    engine.execute_action("right", source="auto")
    assert engine.state.get_all()["manual_adjust_at"] is None
    engine.execute_action("left", source="manual")
    assert engine.state.get_all()["manual_adjust_at"] is not None
