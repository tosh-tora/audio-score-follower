#!/usr/bin/env python3
"""
core/audio_player.py - Recording played along with a playback movement's video

A playback movement's video is silent. In file-input mode with play-audio
on (rehearsal / demo without the orchestra) its ``input_wav`` is played
through the default output device from the start press, in step with the
video. Nothing here tracks anything.

Decoding a 15-minute mp3 takes seconds, so ``load()`` starts it in the
background when the movement loads. A start press that comes before the
decode finishes is remembered: playback then begins as soon as the audio
is ready, skipping the time already elapsed since the press, so the sound
stays in step with the video that started on the press.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _decode(path: Path) -> tuple[np.ndarray, int]:
    """(frames, channels) float32 at the file's own rate — same decoder as
    FileWorker, but without resampling or downmixing (this is for ears)."""
    import librosa  # type: ignore — heavy

    audio, sr = librosa.load(str(path), sr=None, mono=False)
    audio = np.asarray(audio, dtype=np.float32)
    return (audio.T if audio.ndim == 2 else audio), int(sr)


def _sd():
    # Lazy, like FileWorker: the app still runs without sounddevice.
    import sounddevice as sd  # type: ignore

    return sd


class AudioPlayer:
    """Decode one recording in the background; play / stop it on demand."""

    def __init__(
        self,
        path: Path,
        *,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        self.path = Path(path)
        self._on_error = on_error
        self._lock = threading.Lock()
        self._audio: Optional[np.ndarray] = None
        self._sr = 0
        # time.monotonic() of a play request still waiting for the decode.
        self._pending_since: Optional[float] = None
        self._thread: Optional[threading.Thread] = None

    def load(self) -> None:
        """Start decoding in a background thread (returns at once)."""
        self._thread = threading.Thread(
            target=self._load, name="playback-audio-load", daemon=True
        )
        self._thread.start()

    def play(self) -> None:
        """Play from the top now (or, if still decoding, as soon as it is
        decoded — from where it would be by then)."""
        with self._lock:
            ready = self._audio is not None
            self._pending_since = None if ready else time.monotonic()
        if ready:
            self._start(0.0)
        else:
            logger.info("Playback audio still loading; it will start in step with the video")

    def stop(self) -> None:
        with self._lock:
            self._pending_since = None
        try:
            _sd().stop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ private
    def _load(self) -> None:
        t0 = time.monotonic()
        try:
            audio, sr = _decode(self.path)
        except Exception as exc:  # noqa: BLE001
            logger.error("音源を読み込めません: %s (%s)", self.path, exc, exc_info=True)
            if self._on_error is not None:
                self._on_error(exc)
            return
        with self._lock:
            self._audio, self._sr = audio, sr
            pending, self._pending_since = self._pending_since, None
        logger.info(
            "Playback audio loaded: %s (%.1fs, decoded in %.1fs)",
            self.path.name, len(audio) / sr, time.monotonic() - t0,
        )
        if pending is not None:
            self._start(time.monotonic() - pending)

    def _start(self, offset_sec: float) -> None:
        audio = self._audio[int(offset_sec * self._sr):]
        try:
            _sd().play(audio, self._sr)
        except Exception as exc:  # noqa: BLE001
            logger.error("音源を再生できません: %s (%s)", self.path, exc)
            if self._on_error is not None:
                self._on_error(exc)
            return
        logger.info("Playing audio with the video: %s (from %.1fs)", self.path.name, offset_sec)
