"""P キー（前の曲へ戻る）の挙動。

test_end_performance.py と同じく Tk を起動せず ``object.__new__`` で
AudioScoreFollowerApp の骨組みだけを作り、_load_movement を差し替える。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from audio_score_follower.config.loader import ConfigLoader
from audio_score_follower.main import AudioScoreFollowerApp


def _config(tmp_path, n=3):
    movements = [
        {"id": i + 1, "xml_file": "d.xml", "built_dir": "d", "triggers": []}
        for i in range(n)
    ]
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"movements": movements}), encoding="utf-8")
    return ConfigLoader(str(path))


def _app(config, load_ok=True):
    app = object.__new__(AudioScoreFollowerApp)
    app.config = config
    app.slide_controller = MagicMock()
    app._load_movement = MagicMock(return_value=load_ok)
    return app


def test_prev_movement_on_config(tmp_path):
    cfg = _config(tmp_path)
    assert cfg.prev_movement() is False  # 1 曲目からは戻れない
    cfg.current_movement_idx = 2
    assert cfg.prev_movement() is True
    assert cfg.current_movement_idx == 1


def test_p_loads_previous_movement_and_resets_deck(tmp_path):
    cfg = _config(tmp_path)
    cfg.current_movement_idx = 1
    app = _app(cfg)
    app._load_prev_movement()
    assert cfg.current_movement_idx == 0
    app._load_movement.assert_called_once_with(cfg.movements[0])
    app.slide_controller.reset_to_first.assert_called_once()


def test_p_on_first_movement_is_noop(tmp_path):
    cfg = _config(tmp_path)
    app = _app(cfg)
    app._load_prev_movement()
    assert cfg.current_movement_idx == 0
    app._load_movement.assert_not_called()
    app.slide_controller.reset_to_first.assert_not_called()


def test_p_load_failure_keeps_current_movement(tmp_path):
    cfg = _config(tmp_path)
    cfg.current_movement_idx = 2
    app = _app(cfg, load_ok=False)
    app._load_prev_movement()
    assert cfg.current_movement_idx == 2
    app.slide_controller.reset_to_first.assert_not_called()
