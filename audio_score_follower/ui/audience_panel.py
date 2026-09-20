#!/usr/bin/env python3
"""
audience_panel.py - Pure logic behind the audience-facing screen (Issue #8)

The audience sees one Chromium window: the Google Slides deck (iframe
``/embed``) on the left and a tracking panel on the right, rendered by
``ui/audience/host.html``. Everything that decides WHAT the panel shows
lives here so it can be tested headless; the page only draws the view
dict it is handed (``window.asfUpdate``).

No Tk / Playwright dependencies (the thresholds import from ui.common
only pulls in the stdlib tkinter module, no display needed).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from audio_score_follower.ui.common import (
    CONFIDENCE_GOOD_THRESHOLD,
    CONFIDENCE_MID_THRESHOLD,
)

HOST_HTML = Path(__file__).parent / "audience" / "host.html"

# Issue #8: the confidence number is refreshed once per second — the
# per-frame value flickers too fast for an audience to read.
CONFIDENCE_REFRESH_SEC = 1.0
# Issue #8: 「人が調整！」 stays up for about a second after a correction.
MANUAL_FLASH_SEC = 1.0
# How long the confidence has to stay in the red band before the panel
# calls it 見失い中. Short dips happen in quiet passages; see
# docs/calibration.md 「聴衆パネルの状態表示」.
LOST_CONFIDENCE_SEC = 3.0


@dataclass(frozen=True)
class PanelView:
    measure: str
    beat: str
    confidence: str
    confidence_level: str  # "good" | "mid" | "low" | "none"
    status: str
    status_level: str  # "tracking" | "checking" | "acquiring" | "lost" | "waiting"
    manual: bool
    # Not sent to the page: when ``confidence`` was last sampled, and
    # since when it has been in the red band (None = it is not).
    confidence_sampled_at: float = 0.0
    low_since: Optional[float] = None

    def to_js(self) -> dict:
        d = asdict(self)
        d.pop("confidence_sampled_at")
        d.pop("low_since")
        return d


def _status(state: dict) -> tuple[str, str]:
    # Same precedence as gui_tkinter._update_mode_display, reworded for
    # the audience.
    if state.get("performance_ended") or state.get("waiting_for_start"):
        return "待機中", "waiting"
    if not state.get("is_locked_in"):
        return "曲を捕捉中", "acquiring"
    if state.get("is_in_inertia") or state.get("is_mismatched"):
        return "見失い中", "lost"
    return "追随中", "tracking"


def _confidence(conf: float) -> tuple[str, str]:
    # Same breakpoints (and > comparison) as the operator GUI's label.
    if conf > CONFIDENCE_GOOD_THRESHOLD:
        level = "good"
    elif conf > CONFIDENCE_MID_THRESHOLD:
        level = "mid"
    else:
        level = "low"
    return f"{int(conf * 100)}%", level


def build_panel_view(
    state: dict, now: float, last: Optional[PanelView] = None
) -> PanelView:
    """Map an AppState snapshot (``get_all()``) to what the panel shows.

    ``now`` is time.monotonic() (the clock ``manual_adjust_at`` uses).
    ``last`` is the previously built view; it carries the confidence
    sample so the number only changes every CONFIDENCE_REFRESH_SEC.
    """
    status, status_level = _status(state)
    idle = status_level == "waiting"

    if idle:
        conf_text, conf_level, sampled_at = "--", "none", now
    elif (
        last is not None
        and last.confidence_level != "none"
        and now - last.confidence_sampled_at < CONFIDENCE_REFRESH_SEC
    ):
        conf_text, conf_level = last.confidence, last.confidence_level
        sampled_at = last.confidence_sampled_at
    else:
        conf = float(state.get("display_confidence", state.get("confidence", 0.0)))
        conf_text, conf_level = _confidence(conf)
        sampled_at = now

    # While tracking, the confidence itself grades the status, so the
    # audience never sees 「追随中」 next to a red number. The red band
    # has to persist (LOST_CONFIDENCE_SEC) before we admit to 見失い中.
    low_since = None
    if status_level == "tracking":
        if conf_level == "mid":
            status, status_level = "確認中", "checking"
        elif conf_level == "low":
            low_since = last.low_since if last is not None and last.low_since is not None else now
            if now - low_since >= LOST_CONFIDENCE_SEC:
                status, status_level = "見失い中", "lost"
            else:
                status, status_level = "確認中", "checking"

    adjusted_at = state.get("manual_adjust_at")
    manual = adjusted_at is not None and 0.0 <= now - adjusted_at < MANUAL_FLASH_SEC

    return PanelView(
        measure="--" if idle else str(state.get("measure", "")),
        beat="--" if idle else str(int(state.get("beat_in_measure", 1.0))),
        confidence=conf_text,
        confidence_level=conf_level,
        status=status,
        status_level=status_level,
        manual=manual,
        confidence_sampled_at=sampled_at,
        low_since=low_since,
    )


def pick_presentation_screen(screens: list[dict]) -> Optional[dict]:
    """Choose the projector screen from ``getScreenDetails()`` entries.

    The laptop panel shows the operator console, so the slides go to a
    screen that is not built in. Preference: external and non-primary
    (desktop with two monitors), then external (laptop whose projector
    was made primary), then non-primary. None when only the operator's
    screen exists — the caller then does NOT go fullscreen, or
    rehearsing on a bare laptop would bury the console.
    """
    if len(screens) < 2:
        return None
    external = [s for s in screens if s.get("isInternal") is False]
    secondary = [s for s in screens if not s.get("isPrimary", False)]
    for candidates in (
        [s for s in external if s in secondary],
        external,
        secondary,
    ):
        if candidates:
            return candidates[0]
    return None


_DECK_ID_RE = re.compile(r"docs\.google\.com/presentation/d/(e/)?([A-Za-z0-9_-]+)")


def to_embed_url(slide_url: str) -> str:
    """Turn any Google Slides URL of a deck into its embeddable viewer URL.

    ``/present`` (and ``/edit``) refuse to be framed (X-Frame-Options:
    sameorigin), so the host page must iframe ``/embed``. ``rm=minimal``
    hides the viewer's control bar; ``start=false`` disables autoplay.
    Raises ValueError for URLs that are not a Slides deck.
    """
    m = _DECK_ID_RE.search(slide_url or "")
    if not m:
        raise ValueError(f"Google Slides の URL として解釈できません: {slide_url!r}")
    published, deck_id = m.groups()
    prefix = "e/" if published else ""
    return (
        f"https://docs.google.com/presentation/d/{prefix}{deck_id}"
        "/embed?rm=minimal&start=false&loop=false"
    )
