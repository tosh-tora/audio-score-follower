"""動画再生の楽章（movement 直下の ``video``、追随なし）の操作。

test_prev_movement.py と同じく Tk を起動せず ``object.__new__`` で
AudioScoreFollowerApp の骨組みだけを作る。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from audio_score_follower.config.loader import ConfigLoader
from audio_score_follower.core.state_manager import AppState
from audio_score_follower.main import AudioScoreFollowerApp
from audio_score_follower.ui.gui_tkinter import can_return_to_launcher, playback_mode


def _app(tmp_path):
    video = tmp_path / "m3.mp4"
    video.write_bytes(b"\x00")
    movements = [
        {"id": 1, "xml_file": "d.xml", "built_dir": "d", "triggers": []},
        {"id": 3, "video": str(video)},
    ]
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"movements": movements}), encoding="utf-8")
    config = ConfigLoader(str(path))
    config.current_movement_idx = 1

    app = object.__new__(AudioScoreFollowerApp)
    app.config = config
    app.state = AppState()
    app.slide_controller = MagicMock()
    app.slide_controller.video_durations = {str(video): 600.0}
    app.cooldown = MagicMock()
    app.trigger_engine = MagicMock()
    app.result_handler = MagicMock()
    app.worker = MagicMock()  # the previous (tracked) movement's worker
    app._performance_ended = False
    app.oltw = MagicMock()
    app.warp_lookup = app.score_mapper = None
    return app, str(video)


def _loaded(tmp_path):
    app, video = _app(tmp_path)
    previous_worker = app.worker
    assert app._load_current_movement() is True
    return app, video, previous_worker


def test_load_shows_first_frame_without_tracking(tmp_path):
    app, video, previous_worker = _loaded(tmp_path)
    previous_worker.stop.assert_called_once()
    assert app.worker is None and app.oltw is None
    sc = app.slide_controller
    assert [c[0] for c in sc.method_calls] == ["set_mode", "hold_video_start"]
    sc.set_mode.assert_called_once_with("video")
    sc.hold_video_start.assert_called_once_with(video)
    s = app.state.get_all()
    assert s["waiting_for_start"]
    assert s["playback"] == {"duration": 600.0, "started_at": None, "paused_at": None}
    assert app.state.current_triggers == []
    assert not app.is_performing()
    assert can_return_to_launcher(s)


def test_start_plays_once_and_end_pauses(tmp_path):
    app, video, _ = _loaded(tmp_path)
    sc = app.slide_controller

    app.manual_start()
    sc.play_video.assert_called_once_with(video)
    assert app.is_performing()
    assert not app.state.get_all()["waiting_for_start"]
    assert not can_return_to_launcher(app.state.get_all())

    app.manual_start()  # 2 回目は再生し直さない
    sc.play_video.assert_called_once()

    app.end_performance()
    sc.pause_video.assert_called_once()
    assert not app.is_performing()
    assert app.state.get_all()["performance_ended"]

    app.manual_start()  # 一時停止後の開始は無視（R で冒頭へ）
    sc.play_video.assert_called_once()


def test_end_before_start_is_noop(tmp_path):
    app, _, _ = _loaded(tmp_path)
    app.end_performance()
    app.slide_controller.pause_video.assert_not_called()
    assert not app.state.get_all()["performance_ended"]


def test_reload_returns_to_first_frame_without_reset(tmp_path):
    # video モードの reset は黒にするので、R / P では呼ばない
    app, video, _ = _loaded(tmp_path)
    app.manual_start()
    app.end_performance()
    sc = app.slide_controller
    sc.reset_mock()
    assert app._load_current_movement()
    app._reset_screen_after_reload()
    sc.reset_to_first.assert_not_called()
    sc.hold_video_start.assert_called_once_with(video)
    assert not app.is_performing()
    assert app.state.get_all()["waiting_for_start"]


def test_tracked_movement_still_resets_its_deck(tmp_path):
    app, _ = _app(tmp_path)
    app._reset_screen_after_reload()
    app.slide_controller.reset_to_first.assert_called_once()


def test_console_mode_text():
    base = {"waiting_for_start": False, "performance_ended": False}
    pb = {"duration": 600.0, "started_at": None, "paused_at": None}
    text, _ = playback_mode({**base, "waiting_for_start": True, "playback": pb}, 0.0)
    assert "演奏開始" in text

    pb["started_at"] = 100.0
    text, level = playback_mode({**base, "playback": pb}, 165.0)
    assert text == "🎬 再生中 1:05 / 10:00" and level == "tracking"

    text, _ = playback_mode({**base, "playback": pb}, 800.0)
    assert "再生終了" in text

    pb["paused_at"] = 130.0
    text, _ = playback_mode({**base, "performance_ended": True, "playback": pb}, 800.0)
    assert text.startswith("⏸ 一時停止 0:30 / 10:00")
