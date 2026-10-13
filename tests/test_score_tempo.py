"""Unit tests for core/score_tempo.py (tempo-mark-preserving synth map)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("music21")

from audio_score_follower.core import score_tempo


def _measure(number: int, direction: str, notes: str) -> str:
    attrs = ""
    if number == 1:
        attrs = (
            "<attributes><divisions>1</divisions>"
            "<time><beats>4</beats><beat-type>4</beat-type></time></attributes>"
        )
    return f'<measure number="{number}">{attrs}{direction}{notes}</measure>'


_WHOLE_NOTE = (
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>whole</type></note>"
)
_REST_THEN_C = (
    "<note><rest/><duration>2</duration><type>half</type></note>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>half</type></note>"
)


def _write_score(tmp_path, measures: list[str]):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<score-partwise version="3.1"><part-list>'
        '<score-part id="P1"><part-name>P</part-name></score-part>'
        '</part-list><part id="P1">' + "".join(measures) + "</part></score-partwise>"
    )
    path = tmp_path / "score.musicxml"
    path.write_text(xml, encoding="utf-8")
    return path


def _printed_mm(qpm: int) -> str:
    return (
        '<direction placement="above"><direction-type><metronome>'
        f"<beat-unit>quarter</beat-unit><per-minute>{qpm}</per-minute>"
        f'</metronome></direction-type><sound tempo="{qpm}"/></direction>'
    )


def _playback_only(qpm: int) -> str:
    # <sound tempo> with no printed mark — music21 imports this as a
    # "playback only" mark carried in numberSounding (幻想1 has many).
    return f'<direction><sound tempo="{qpm}"/></direction>'


@pytest.fixture
def two_tempo_score(tmp_path):
    """m1 ♩=60 (printed), m2 ♩=240 (playback only): 4 s + 1 s."""
    return _write_score(tmp_path, [
        _measure(1, _printed_mm(60), _WHOLE_NOTE),
        _measure(2, _playback_only(240), _WHOLE_NOTE),
    ])


def test_tempo_map_follows_marks(two_tempo_score):
    beats, secs = score_tempo.tempo_map(score_tempo.parse_score(two_tempo_score))
    assert np.interp(4.0, beats, secs) == pytest.approx(4.0)
    assert secs[-1] == pytest.approx(5.0)
    assert beats[-1] == pytest.approx(8.0)


def test_scale_tempo_scales_printed_and_playback_only_marks(two_tempo_score):
    """Both kinds must scale — missing numberSounding left 幻想1's
    playback-only marks unscaled (synth 26 s short)."""
    score = score_tempo.parse_score(two_tempo_score)
    score_tempo.scale_tempo(score, 0.5)
    beats, secs = score_tempo.tempo_map(score)
    assert np.interp(4.0, beats, secs) == pytest.approx(8.0)
    assert secs[-1] == pytest.approx(10.0)
    assert score_tempo.marked_duration_sec(two_tempo_score) == pytest.approx(5.0)


def test_scale_tempo_without_marks_inserts_scaled_default(tmp_path):
    """No marks → music21 plays 120; scaling must still apply."""
    path = _write_score(tmp_path, [
        _measure(1, "", _WHOLE_NOTE),
        _measure(2, "", _WHOLE_NOTE),
    ])
    score = score_tempo.parse_score(path)
    score_tempo.scale_tempo(score, 2.0)
    _beats, secs = score_tempo.tempo_map(score)
    assert secs[-1] == pytest.approx(8 * 60.0 / 240.0)


def test_synth_tempo_map_rebases_to_first_note(tmp_path):
    """The synth WAV is head-trimmed to its first sound."""
    path = _write_score(tmp_path, [
        _measure(1, _printed_mm(60), _REST_THEN_C),
        _measure(2, "", _WHOLE_NOTE),
    ])
    score = score_tempo.parse_score(path)
    assert score_tempo.first_note_beat(score) == pytest.approx(2.0)
    beats, secs = score_tempo.synth_tempo_map(score)
    assert np.interp(2.0, beats, secs) == pytest.approx(0.0)
    assert np.interp(4.0, beats, secs) == pytest.approx(2.0)
