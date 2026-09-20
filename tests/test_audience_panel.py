#!/usr/bin/env python3
"""聴衆向けパネル（Issue #8）の表示ロジック。Tk / Playwright なしで検証する。"""
import pytest

from audio_score_follower.core.state_manager import AppState
from audio_score_follower.ui.audience_panel import (
    build_panel_view,
    pick_presentation_screen,
    to_embed_url,
)


def _tracking(**over):
    state = AppState().get_all()
    state.update(is_locked_in=True, measure=17, beat_in_measure=2.7,
                 display_confidence=0.8)
    state.update(over)
    return state


# ---------------------------------------------------------------- 状態表示
@pytest.mark.parametrize("over, level", [
    ({"waiting_for_start": True}, "waiting"),
    ({"performance_ended": True}, "waiting"),
    ({"is_locked_in": False}, "acquiring"),
    ({"is_in_inertia": True}, "lost"),
    ({"is_mismatched": True}, "lost"),
    ({}, "tracking"),
])
def test_status_level(over, level):
    assert build_panel_view(_tracking(**over), now=0.0).status_level == level


def test_waiting_hides_numbers():
    v = build_panel_view(_tracking(waiting_for_start=True), now=0.0)
    assert (v.measure, v.beat, v.confidence, v.confidence_level) == ("--", "--", "--", "none")


def test_measure_and_beat():
    v = build_panel_view(_tracking(), now=0.0)
    assert (v.measure, v.beat) == ("17", "2")


# ---------------------------------------------------------------- 確信度
@pytest.mark.parametrize("conf, level", [
    (0.61, "good"), (0.6, "mid"), (0.41, "mid"), (0.4, "low"), (0.0, "low"),
])
def test_confidence_color_breakpoints(conf, level):
    v = build_panel_view(_tracking(display_confidence=conf), now=0.0)
    assert v.confidence_level == level


def test_confidence_refreshes_once_per_second():
    v0 = build_panel_view(_tracking(display_confidence=0.8), now=10.0)
    v1 = build_panel_view(_tracking(display_confidence=0.3), now=10.9, last=v0)
    assert v1.confidence == "80%"
    v2 = build_panel_view(_tracking(display_confidence=0.3), now=11.0, last=v1)
    assert (v2.confidence, v2.confidence_level) == ("30%", "low")


def test_confidence_samples_immediately_after_waiting():
    waiting = build_panel_view(_tracking(waiting_for_start=True), now=10.0)
    v = build_panel_view(_tracking(display_confidence=0.8), now=10.2, last=waiting)
    assert v.confidence == "80%"


# ---------------------------------------------------------------- 人が調整！
def test_manual_flash_lasts_about_a_second():
    state = _tracking(manual_adjust_at=100.0)
    assert build_panel_view(state, now=100.5).manual
    assert not build_panel_view(state, now=101.0).manual
    assert not build_panel_view(_tracking(), now=100.5).manual


def test_to_js_omits_internal_timestamp():
    assert "confidence_sampled_at" not in build_panel_view(_tracking(), now=0.0).to_js()


# ---------------------------------------------------------------- モニター選択
LAPTOP = {"left": 0, "top": 0, "isPrimary": True, "isInternal": True}
PROJECTOR = {"left": 1707, "top": 0, "isPrimary": False, "isInternal": False}


def test_single_screen_does_not_go_fullscreen():
    assert pick_presentation_screen([LAPTOP]) is None


def test_picks_external_screen():
    assert pick_presentation_screen([LAPTOP, PROJECTOR]) is PROJECTOR


def test_external_screen_made_primary_still_wins_over_laptop():
    laptop = {**LAPTOP, "isPrimary": False}
    projector = {**PROJECTOR, "isPrimary": True}
    assert pick_presentation_screen([laptop, projector]) is projector


def test_two_external_screens_pick_non_primary():
    main = {**PROJECTOR, "isPrimary": True, "left": 0}
    assert pick_presentation_screen([main, PROJECTOR]) is PROJECTOR


def test_unknown_internal_falls_back_to_non_primary():
    a = {"left": 0, "isPrimary": True}
    b = {"left": 1920, "isPrimary": False}
    assert pick_presentation_screen([a, b]) is b


# ---------------------------------------------------------------- embed URL
EMBED = "/embed?rm=minimal&start=false&loop=false"


@pytest.mark.parametrize("url, expected", [
    ("https://docs.google.com/presentation/d/AbC_1-x/present",
     "https://docs.google.com/presentation/d/AbC_1-x" + EMBED),
    ("https://docs.google.com/presentation/d/AbC_1-x/edit?usp=sharing#slide=id.p",
     "https://docs.google.com/presentation/d/AbC_1-x" + EMBED),
    ("https://docs.google.com/presentation/d/e/2PACX-1vQ/pub?start=false",
     "https://docs.google.com/presentation/d/e/2PACX-1vQ" + EMBED),
])
def test_to_embed_url(url, expected):
    assert to_embed_url(url) == expected


@pytest.mark.parametrize("url", ["", "https://example.com/slides", "not a url"])
def test_to_embed_url_rejects_non_slides(url):
    with pytest.raises(ValueError):
        to_embed_url(url)


# ------------------------------------------------ 確信度による状態の格下げ
def test_mid_confidence_shows_checking():
    v = build_panel_view(_tracking(display_confidence=0.5), now=0.0)
    assert (v.status, v.status_level) == ("確認中", "checking")


def test_low_confidence_becomes_lost_only_after_three_seconds():
    state = _tracking(display_confidence=0.1)
    v = build_panel_view(state, now=0.0)
    assert v.status_level == "checking"
    for now in (1.0, 2.0, 2.9):
        v = build_panel_view(state, now=now, last=v)
        assert v.status_level == "checking", now
    v = build_panel_view(state, now=3.0, last=v)
    assert (v.status, v.status_level) == ("見失い中", "lost")


def test_recovered_confidence_restarts_the_lost_timer():
    low, high = _tracking(display_confidence=0.1), _tracking(display_confidence=0.9)
    v = build_panel_view(low, now=0.0)
    v = build_panel_view(high, now=1.0, last=v)
    assert v.status_level == "tracking" and v.low_since is None
    v = build_panel_view(low, now=2.0, last=v)
    v = build_panel_view(low, now=4.0, last=v)
    assert v.status_level == "checking"
    v = build_panel_view(low, now=5.0, last=v)
    assert v.status_level == "lost"


def test_inertia_is_lost_immediately_regardless_of_confidence():
    v = build_panel_view(_tracking(is_in_inertia=True, display_confidence=0.9), now=0.0)
    assert v.status_level == "lost"


def test_low_since_is_not_sent_to_the_page():
    v = build_panel_view(_tracking(display_confidence=0.1), now=0.0)
    assert "low_since" not in v.to_js()
