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


def test_trigger_markers_follow_the_current_measure():
    # 「前 / 次のトリガー」表示（Issue #51）。どちらも現在位置から導出する。
    engine = _make_engine()
    triggers = [{"measure": 1}, {"measure": 17}, {"measure": 48}]

    engine._update_trigger_markers(triggers, current_measure=1)
    snap = engine.state.get_all()
    assert snap["prev_trigger_measure"] == 1
    assert snap["next_trigger_measure"] == 17

    engine._update_trigger_markers(triggers, current_measure=20)
    snap = engine.state.get_all()
    assert snap["prev_trigger_measure"] == 17
    assert snap["next_trigger_measure"] == 48

    # 最終トリガーを過ぎたら「次」は無し、「前」は最後のトリガーのまま
    engine._update_trigger_markers(triggers, current_measure=60)
    snap = engine.state.get_all()
    assert snap["prev_trigger_measure"] == 48
    assert snap["next_trigger_measure"] is None


def test_prev_marker_moves_back_with_a_manual_rewind():
    # 手動 ← で小節が戻ったら「前」も戻る。fired set 由来にすると通過して
    # いないトリガーを指し続けてしまう（この挙動がその防波堤）。
    engine = _make_engine()
    triggers = [{"measure": 1}, {"measure": 17}, {"measure": 48}]
    engine._fired_trigger_measures.update({1, 17})

    engine._update_trigger_markers(triggers, current_measure=20)
    assert engine.state.get_all()["prev_trigger_measure"] == 17

    engine._update_trigger_markers(triggers, current_measure=5)
    assert engine.state.get_all()["prev_trigger_measure"] == 1


# ---- video movements -----------------------------------------------------

class _FakeVideoController(_FakeSlideController):
    def __init__(self):
        super().__init__()
        self.calls = []

    def play_video(self, path, next_path=None):
        self.calls.append(("play", path, next_path))

    def hold_video_end(self, path):
        self.calls.append(("hold", path))


_SCENES = [
    {"measure": 1, "video": "/s/01.mp4"},
    {"measure": 20, "video": "/s/02.mp4"},
    {"measure": 41, "video": "/s/03.mp4"},
]


def _make_video_engine():
    engine = _make_engine()
    engine.slide_controller = _FakeVideoController()
    engine.state.set_movement(
        movement_id=3, xml_file="x.mxl", triggers=_SCENES, total_measures=100
    )
    return engine


def test_video_trigger_plays_its_own_scene_and_preloads_the_next():
    # 「次のファイル」ではなく発火したトリガー自身の動画。小節が飛んで
    # 途中のトリガーが発火しなくても後続のシーンがずれない
    engine = _make_video_engine()
    engine.execute_action("right", source="auto", trigger=_SCENES[1])
    assert engine.slide_controller.calls == [("play", "/s/02.mp4", "/s/03.mp4")]
    assert engine.slide_controller.presses == []


def test_last_scene_has_nothing_to_preload():
    engine = _make_video_engine()
    engine.execute_action("right", source="auto", trigger=_SCENES[2])
    assert engine.slide_controller.calls == [("play", "/s/03.mp4", None)]


def test_manual_back_holds_the_previous_scene_end():
    # ← はシーン 2 を取り消し、シーン 1 の最終フレームで静止（再再生しない）
    engine = _make_video_engine()
    engine.execute_action("left", source="manual", trigger=_SCENES[1])
    assert engine.slide_controller.calls == [("hold", "/s/01.mp4")]


def test_manual_back_from_the_first_scene_goes_black():
    engine = _make_video_engine()
    engine.execute_action("left", source="manual", trigger=_SCENES[0])
    assert engine.slide_controller.calls == [("hold", None)]


def test_bare_key_in_a_video_movement_is_ignored():
    # 消費するトリガーが無い →/← はキーを隠れたデッキに送らない
    engine = _make_video_engine()
    engine.execute_action("right", source="manual")
    assert engine.slide_controller.calls == []
    assert engine.slide_controller.presses == []


def test_video_name_appears_in_the_fire_log(caplog):
    engine = _make_video_engine()
    with caplog.at_level(logging.INFO):
        engine.execute_action("right", source="auto", trigger=_SCENES[0])
    assert "measure=1 note= video=01.mp4" in caplog.text
