"""ファイル入力モードで楽章ごとの音源（movements[].input_wav）を選ぶ部分。

test_end_performance.py と同じく Tk を起動せず ``object.__new__`` で
AudioScoreFollowerApp の骨組みだけを作る。音源が無い楽章はローダーを
呼ぶ前に弾く（今の楽章を止めない）ことを確かめる。
"""

from __future__ import annotations

import pytest

import audio_score_follower.main as main_mod
from audio_score_follower.core.state_manager import AppState
from audio_score_follower.main import AudioScoreFollowerApp


def _make_app(cli_wav=None):
    app = object.__new__(AudioScoreFollowerApp)
    app.state = AppState()
    app.input_wav = cli_wav
    app._file_input = True
    # Only passed through to the (faked) loader.
    app.config = app._mic_mode = app.viz = None
    app._stop_worker = lambda: None
    return app


@pytest.fixture
def loader_calls(monkeypatch):
    calls = []

    def fake_load_movement(*_a, **_kw):
        calls.append(1)
        raise main_mod.MovementLoadError(None)

    monkeypatch.setattr(main_mod, "load_movement", fake_load_movement)
    return calls


def test_movement_without_recording_is_rejected_before_loading(loader_calls):
    app = _make_app()
    assert app._load_movement({"id": 4, "triggers": []}) is False
    assert loader_calls == []  # 今の楽章のワーカーは止めない
    assert "楽章 4" in app.state.get_all()["load_error"]


def test_missing_recording_file_is_rejected(loader_calls, tmp_path):
    app = _make_app()
    missing = tmp_path / "nope.mp3"
    assert app._load_movement({"id": 5, "input_wav": str(missing)}) is False
    assert loader_calls == []
    assert str(missing) in app.state.get_all()["load_error"]


def test_existing_recording_proceeds_to_the_loader(loader_calls, tmp_path):
    rec = tmp_path / "4.mp3"
    rec.write_bytes(b"\x00")
    app = _make_app()
    app._load_movement({"id": 4, "input_wav": str(rec)})
    assert loader_calls == [1]


def test_cli_input_wav_wins_over_the_movement(loader_calls, tmp_path):
    rec = tmp_path / "cli.mp3"
    rec.write_bytes(b"\x00")
    app = _make_app(cli_wav=rec)
    # 楽章側の音源が無くても CLI の --input-wav で進む
    app._load_movement({"id": 4})
    assert loader_calls == [1]
