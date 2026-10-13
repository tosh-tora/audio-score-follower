"""
score_tempo.py - the score's own tempo marks as a beat ↔ seconds map.

The offline build synthesises the score and aligns that synth against a
real recording. Synthesising at one constant BPM breaks movements whose
tempo marks differ a lot (幻想1: Largo ♩≈50 vs Allegro ♩≈250 — a 5×
contrast): the synth plays the Largo ~3× too fast and MrMsDTW degenerates
toward the diagonal, so the warp path maps the Largo far too early.

Instead we keep the marks and multiply every one of them by a single
``scale`` chosen so the synth's total length matches the reference. The
resulting piecewise-constant tempo is persisted as a (beats, seconds)
polyline, which the runtime interpolates instead of ``beat = t*bpm/60``.

Beats are quarter-note offsets from the start of the score — the same
axis ``ScoreMapper`` builds its measure map on.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# music21's implicit tempo when a score has no mark at offset 0.
_MUSIC21_DEFAULT_QPM = 120.0


def parse_score(xml_path: Path):  # -> music21.stream.Score
    import music21  # type: ignore

    return music21.converter.parse(str(xml_path))


def tempo_map(score) -> tuple[np.ndarray, np.ndarray]:
    """Return (beats, seconds) polyline vertices of the score's tempo.

    ``seconds[0] == 0`` at ``beats[0] == 0``. Uses
    ``metronomeMarkBoundaries()`` — the same segmentation music21's MIDI
    export writes as tempo events — so the map matches the synth exactly.
    """
    bounds = score.metronomeMarkBoundaries()
    beats = [0.0]
    seconds = [0.0]
    for start, end, mm in bounds:
        qpm = mm.getQuarterBPM()
        if qpm is None or qpm <= 0:
            qpm = _MUSIC21_DEFAULT_QPM
        start, end = float(start), float(end)
        if end <= start:
            continue
        if start > beats[-1]:
            # Gap before the first mark: music21 plays it at its default.
            seconds.append(seconds[-1] + (start - beats[-1]) * 60.0 / _MUSIC21_DEFAULT_QPM)
            beats.append(start)
        seconds.append(seconds[-1] + (end - start) * 60.0 / qpm)
        beats.append(end)
    return np.asarray(beats, dtype=np.float64), np.asarray(seconds, dtype=np.float64)


def first_note_beat(score) -> float:
    """Offset (quarter beats) of the earliest sounding note or chord."""
    offsets = [float(n.getOffsetInHierarchy(score)) for n in score.recurse().notes]
    return min(offsets) if offsets else 0.0


def synth_tempo_map(score) -> tuple[np.ndarray, np.ndarray]:
    """``tempo_map`` re-based to the synth WAV's timeline.

    generate_score_wav trims the synth's leading silence, so its t=0 is
    the first sounding note, not beat 0 (a leading rest would otherwise
    shift every measure). Beats before the first note get negative
    seconds; the runtime only looks up t >= 0.
    """
    beats, seconds = tempo_map(score)
    t0 = float(np.interp(first_note_beat(score), beats, seconds))
    return beats, seconds - t0


def scale_tempo(score, scale: float) -> int:
    """Multiply every MetronomeMark in ``score`` by ``scale`` (in place).

    Marks are normalised to quarter-note referents so text-only marks
    ("Largo" with an implied number) are scaled too. If nothing is marked
    at offset 0, an explicit mark at music21's default × scale is
    inserted so the head of the piece is scaled as well. Returns the
    number of marks scaled.
    """
    import music21  # type: ignore

    if scale <= 0:
        raise ValueError(f"tempo scale must be positive, got {scale}")

    marks = list(score.recurse().getElementsByClass(music21.tempo.MetronomeMark))
    n = 0
    for mm in marks:
        qpm = mm.getQuarterBPM()
        if qpm is None or qpm <= 0:
            logger.debug("テンポ値を解決できない MetronomeMark を無視: %r", mm)
            continue
        mm.referent = music21.duration.Duration(1.0)
        mm.number = qpm * scale
        mm.numberImplicit = False
        # <sound tempo> without a printed mark imports as "playback only"
        # (numberSounding), which getQuarterBPM() — and the MIDI export —
        # prefers over number. Missing this left ~1/4 of 幻想1's marks
        # unscaled.
        if mm.numberSounding is not None:
            mm.numberSounding = qpm * scale
        n += 1
    if any(isinstance(el, music21.tempo.MetricModulation)
           for el in score.recurse()):
        logger.warning("MetricModulation はスケール対象外です（合成テンポがずれる可能性）")

    starts_marked = any(float(mm.getOffsetInHierarchy(score)) == 0.0 for mm in marks)
    if not starts_marked:
        head = music21.tempo.MetronomeMark(
            number=_MUSIC21_DEFAULT_QPM * scale,
            referent=music21.duration.Duration(1.0),
        )
        target = score
        if hasattr(score, "parts") and len(score.parts) > 0:
            measures = list(score.parts[0].getElementsByClass(music21.stream.Measure))
            if measures:
                target = measures[0]
        target.insert(0, head)
    return n


def marked_duration_sec(xml_path: Path) -> float:
    """Total length of the score played at its own (unscaled) tempo marks."""
    _beats, seconds = tempo_map(parse_score(xml_path))
    return float(seconds[-1])
