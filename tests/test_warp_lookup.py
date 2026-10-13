"""Unit tests for WarpLookup."""

from __future__ import annotations

import numpy as np
import pytest

from audio_score_follower.core.feature_extractor import FeatureConfig
from audio_score_follower.core.warp_lookup import WarpLookup


def test_identity_lookup_returns_input():
    """A diagonal warp (no tempo distortion) must reproduce its input."""
    ref_times = np.linspace(0.0, 10.0, 101, dtype=np.float32)
    score_times = ref_times.copy()
    lookup = WarpLookup(
        ref_times=ref_times,
        score_times=score_times,
        score_bpm=120.0,
        feature_config=FeatureConfig(),
    )
    for t in (0.0, 1.234, 5.0, 9.9, 10.0):
        assert lookup.ref_to_score_time(t) == pytest.approx(t, abs=1e-3)


def test_linear_interpolation_between_anchors():
    """Two anchors → linear interp between them."""
    ref_times = np.array([0.0, 2.0, 4.0], dtype=np.float32)
    score_times = np.array([0.0, 1.0, 2.0], dtype=np.float32)  # half-speed reference
    lookup = WarpLookup(
        ref_times=ref_times,
        score_times=score_times,
        score_bpm=120.0,
        feature_config=FeatureConfig(),
    )
    # Halfway between anchors
    assert lookup.ref_to_score_time(1.0) == pytest.approx(0.5, abs=1e-4)
    assert lookup.ref_to_score_time(3.0) == pytest.approx(1.5, abs=1e-4)


def test_extrapolation_clamps_to_boundary():
    """np.interp clamps to first / last value — verify that contract."""
    ref_times = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    score_times = np.array([10.0, 20.0, 30.0], dtype=np.float32)
    lookup = WarpLookup(
        ref_times=ref_times,
        score_times=score_times,
        score_bpm=60.0,
        feature_config=FeatureConfig(),
    )
    assert lookup.ref_to_score_time(0.0) == pytest.approx(10.0)  # clamped to first
    assert lookup.ref_to_score_time(100.0) == pytest.approx(30.0)  # clamped to last


def test_score_time_to_beat_uses_bpm():
    """beat = score_time * bpm / 60."""
    lookup = WarpLookup(
        ref_times=np.array([0.0, 1.0], dtype=np.float32),
        score_times=np.array([0.0, 1.0], dtype=np.float32),
        score_bpm=120.0,
        feature_config=FeatureConfig(),
    )
    # 1 second @ 120 bpm = 2 beats
    assert lookup.score_time_to_beat(1.0) == pytest.approx(2.0)
    # 0.5 second @ 120 bpm = 1 beat
    assert lookup.score_time_to_beat(0.5) == pytest.approx(1.0)


def test_load_round_trip(tmp_path):
    """build artifact format: write via np.savez, load via WarpLookup.load."""
    cfg = FeatureConfig(sample_rate=22050, hop_length=2048, cens_win=41, norm=2.0)
    ref_times = np.linspace(0.0, 5.0, 51, dtype=np.float32)
    score_times = np.linspace(0.0, 5.0, 51, dtype=np.float32)

    np.savez(
        tmp_path / "warping_path.npz",
        ref_times=ref_times,
        score_times=score_times,
        score_bpm=np.float32(100.0),
        feature_config=np.array(
            [cfg.sample_rate, cfg.hop_length, cfg.cens_win, cfg.norm],
            dtype=np.float32,
        ),
        feature_config_quant_steps=np.array(cfg.quant_steps, dtype=np.int32),
    )

    loaded = WarpLookup.load(tmp_path)
    assert loaded.score_bpm == pytest.approx(100.0)
    assert loaded.feature_config.sample_rate == 22050
    assert loaded.feature_config.hop_length == 2048
    assert loaded.feature_config.cens_win == 41
    assert loaded.reference_duration_sec() == pytest.approx(5.0)


def test_mismatched_shapes_raise():
    with pytest.raises(ValueError, match="!="):
        WarpLookup(
            ref_times=np.array([0.0, 1.0], dtype=np.float32),
            score_times=np.array([0.0], dtype=np.float32),
            score_bpm=120.0,
            feature_config=FeatureConfig(),
        )


def test_inverse_lookups_roundtrip():
    """ref → score → ref should yield ~identity on the recorded grid."""
    ref_times = np.array([0.0, 2.0, 4.0, 7.0], dtype=np.float32)
    score_times = np.array([0.0, 1.0, 2.0, 4.0], dtype=np.float32)
    lookup = WarpLookup(
        ref_times=ref_times,
        score_times=score_times,
        score_bpm=120.0,
        feature_config=FeatureConfig(),
    )
    for ref_t in (0.0, 1.5, 3.0, 5.5, 7.0):
        score_t = lookup.ref_to_score_time(ref_t)
        ref_t_back = lookup.score_time_to_ref_time(score_t)
        assert ref_t_back == pytest.approx(ref_t, abs=1e-3)


def test_beat_to_ref_time():
    """beat → score_time → ref_time, all sec."""
    lookup = WarpLookup(
        ref_times=np.array([0.0, 4.0], dtype=np.float32),
        score_times=np.array([0.0, 2.0], dtype=np.float32),  # half-speed
        score_bpm=120.0,
        feature_config=FeatureConfig(),
    )
    # beat 2 at 120 bpm = score_time 1.0 = ref_time 2.0 (half-speed warp)
    assert lookup.beat_to_ref_time(2.0) == pytest.approx(2.0, abs=1e-3)


def test_nonpositive_bpm_raises():
    with pytest.raises(ValueError, match="positive"):
        WarpLookup(
            ref_times=np.array([0.0], dtype=np.float32),
            score_times=np.array([0.0], dtype=np.float32),
            score_bpm=0.0,
            feature_config=FeatureConfig(),
        )


def _two_tempo_lookup(**kw) -> WarpLookup:
    """Synth with a tempo map: beats 0-4 take 4 s (♩60), 4-12 take 2 s (♩240)."""
    return WarpLookup(
        ref_times=np.array([0.0, 6.0], dtype=np.float32),
        score_times=np.array([0.0, 6.0], dtype=np.float32),
        score_bpm=120.0,
        feature_config=FeatureConfig(),
        tempo_map=(np.array([0.0, 4.0, 12.0]), np.array([0.0, 4.0, 6.0])),
        **kw,
    )


def test_tempo_map_overrides_constant_bpm():
    """With a tempo map, score_time → beat follows the map, not score_bpm."""
    lookup = _two_tempo_lookup()
    assert lookup.score_time_to_beat(2.0) == pytest.approx(2.0)   # ♩60 section
    assert lookup.score_time_to_beat(5.0) == pytest.approx(8.0)   # ♩240 section
    assert lookup.beat_to_score_time(8.0) == pytest.approx(5.0)
    for beat in (0.0, 1.5, 4.0, 9.25, 12.0):
        assert lookup.score_time_to_beat(lookup.beat_to_score_time(beat)) == pytest.approx(beat)


def test_tempo_map_extrapolates_past_end():
    """The synth's release tail runs past the last beat; keep the end tempo."""
    lookup = _two_tempo_lookup()
    assert lookup.score_time_to_beat(6.5) == pytest.approx(14.0)


def test_tempo_map_round_trips_through_npz(tmp_path):
    cfg = FeatureConfig()
    np.savez(
        tmp_path / "warping_path.npz",
        ref_times=np.array([0.0, 6.0], dtype=np.float32),
        score_times=np.array([0.0, 6.0], dtype=np.float32),
        score_bpm=np.float32(120.0),
        tempo_map_beats=np.array([0.0, 4.0, 12.0]),
        tempo_map_seconds=np.array([0.0, 4.0, 6.0]),
        **cfg.to_npz_arrays(),
    )
    loaded = WarpLookup.load(tmp_path)
    assert loaded.tempo_map is not None
    assert loaded.ref_to_beat(5.0) == pytest.approx(8.0)


def test_legacy_npz_without_tempo_map_uses_bpm(tmp_path):
    cfg = FeatureConfig()
    np.savez(
        tmp_path / "warping_path.npz",
        ref_times=np.array([0.0, 6.0], dtype=np.float32),
        score_times=np.array([0.0, 6.0], dtype=np.float32),
        score_bpm=np.float32(120.0),
        **cfg.to_npz_arrays(),
    )
    loaded = WarpLookup.load(tmp_path)
    assert loaded.tempo_map is None
    assert loaded.ref_to_beat(5.0) == pytest.approx(10.0)


def test_non_increasing_tempo_map_raises():
    with pytest.raises(ValueError):
        WarpLookup(
            ref_times=np.array([0.0, 1.0], dtype=np.float32),
            score_times=np.array([0.0, 1.0], dtype=np.float32),
            score_bpm=120.0,
            feature_config=FeatureConfig(),
            tempo_map=(np.array([0.0, 4.0, 4.0]), np.array([0.0, 1.0, 2.0])),
        )


def _save_warp(path, *, offset_in_npz=None):
    cfg = FeatureConfig()
    extra = {} if offset_in_npz is None else {
        "reference_start_offset_sec": np.float64(offset_in_npz)}
    np.savez(
        path / "warping_path.npz",
        # Original-recording timeline: music starts 2 s in (head trimmed).
        ref_times=np.array([2.0, 12.0], dtype=np.float32),
        score_times=np.array([0.0, 10.0], dtype=np.float32),
        score_bpm=np.float32(60.0),
        **extra,
        **cfg.to_npz_arrays(),
    )


def test_load_shifts_ref_axis_to_trimmed_cens_timeline(tmp_path):
    """The follower's ref_time is frame/rate on the head-trimmed CENS, so
    ref_time 0 must be the first score beat — not 2 s (= ~1.5 beats of
    幻想1's Largo) earlier. Seeks round-trip on the same axis."""
    _save_warp(tmp_path, offset_in_npz=2.0)
    lookup = WarpLookup.load(tmp_path)
    assert lookup.ref_to_beat(0.0) == pytest.approx(0.0)
    assert lookup.ref_to_beat(5.0) == pytest.approx(5.0)
    assert lookup.beat_to_ref_time(5.0) == pytest.approx(5.0)


def test_load_reads_offset_from_build_meta_for_older_builds(tmp_path):
    import json

    _save_warp(tmp_path)
    (tmp_path / "build_meta.json").write_text(
        json.dumps({"reference_start_offset_sec": 2.0}), encoding="utf-8")
    lookup = WarpLookup.load(tmp_path)
    assert lookup.ref_to_beat(5.0) == pytest.approx(5.0)
