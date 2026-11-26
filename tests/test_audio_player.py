"""動画再生の楽章で動画と一緒に流す音源（core/audio_player.py）。

デコードと sounddevice を差し替え、音を出さずに再生要求の扱いだけを見る。
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from audio_score_follower.core import audio_player
from audio_score_follower.core.audio_player import AudioPlayer

SR = 1000


class _FakeSd:
    def __init__(self) -> None:
        self.played: list[tuple[int, int]] = []  # (frames, sr)
        self.stops = 0
        self.started = threading.Event()

    def play(self, audio, sr) -> None:
        self.played.append((len(audio), sr))
        self.started.set()

    def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def sd(monkeypatch):
    fake = _FakeSd()
    monkeypatch.setattr(audio_player, "_sd", lambda: fake)
    return fake


def _decoder(monkeypatch, gate: threading.Event | None = None, fail=False):
    def decode(_path):
        if gate is not None:
            gate.wait(3.0)
        if fail:
            raise RuntimeError("bad file")
        return np.zeros((10 * SR, 2), dtype=np.float32), SR
    monkeypatch.setattr(audio_player, "_decode", decode)


def _loaded(player: AudioPlayer) -> None:
    player._thread.join(3.0)


def test_play_after_load_starts_from_the_top(monkeypatch, sd, tmp_path):
    _decoder(monkeypatch)
    player = AudioPlayer(tmp_path / "a.mp3")
    player.load()
    _loaded(player)
    player.play()
    assert sd.played == [(10 * SR, SR)]


def test_play_during_load_starts_in_step_when_decoded(monkeypatch, sd, tmp_path):
    gate = threading.Event()
    _decoder(monkeypatch, gate)
    player = AudioPlayer(tmp_path / "a.mp3")
    player.load()
    player.play()  # 開始押下のときはまだデコード中
    time.sleep(0.3)
    assert sd.played == []
    gate.set()
    assert sd.started.wait(3.0)
    (frames, _sr), = sd.played
    # 押下から経過した ~0.3 秒ぶんを飛ばして鳴らす（映像とずれない）。
    # Windows の sleep は時計の粒度（~15ms）ぶん短く出ることがある
    assert 10 * SR - frames >= int(0.25 * SR)


def test_stop_before_decoded_cancels_the_pending_play(monkeypatch, sd, tmp_path):
    gate = threading.Event()
    _decoder(monkeypatch, gate)
    player = AudioPlayer(tmp_path / "a.mp3")
    player.load()
    player.play()
    player.stop()
    gate.set()
    _loaded(player)
    assert sd.played == [] and sd.stops == 1


def test_decode_failure_is_reported(monkeypatch, sd, tmp_path):
    _decoder(monkeypatch, fail=True)
    errors = []
    player = AudioPlayer(tmp_path / "a.mp3", on_error=errors.append)
    player.load()
    _loaded(player)
    player.play()  # 鳴らない（動画は無音で再生される）
    assert sd.played == [] and len(errors) == 1
