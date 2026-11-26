#!/usr/bin/env python3
"""
audience_panel.py - Pure logic behind the audience-facing screen (Issue #8)

The audience sees one Chromium window: the Google Slides deck (iframe
``/embed``) on the left and a tracking panel on the right, rendered by
``ui/audience/host.html``. Everything that decides WHAT the panel shows
lives here so it can be tested headless; the page only draws the view
dict it is handed (``window.asfUpdate``).

``resolve_status()`` is the SINGLE SOURCE OF TRUTH for the tracking
status both screens show (Issue #51). The operator console used to grade
itself — it said 「追随中」 while the audience screen next to it already
said 「見失い中」, which is exactly the moment the operator has to decide
whether to intervene. ``ui/gui_tkinter.py`` now calls this and only adds
operator-facing detail (慣性の残り秒数, 復帰キー) in parentheses.

No Tk / Playwright dependencies (the thresholds import from ui.common
only pulls in the stdlib tkinter module, no display needed).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from audio_score_follower.config.loader import is_video_movement
from audio_score_follower.ui.common import confidence_level

HOST_HTML = Path(__file__).parent / "audience" / "host.html"

# Issue #8: the confidence number is refreshed once per second — the
# per-frame value flickers too fast for an audience to read.
CONFIDENCE_REFRESH_SEC = 1.0
# Issue #8: 「人が調整！」 stays up for about a second after a correction.
MANUAL_FLASH_SEC = 1.0
# How long the confidence has to stay in the red band before the panel
# calls it 見失い中. Short dips happen in quiet passages; see
# docs/calibration.md 「追随状態の表示」.
LOST_CONFIDENCE_SEC = 3.0


@dataclass(frozen=True)
class PanelView:
    movement: str
    measure: str
    beat: str
    confidence: str
    confidence_level: str  # "good" | "mid" | "low" | "none"
    status: str
    status_level: str  # "tracking" | "checking" | "acquiring" | "lost" | "waiting"
    manual: bool
    # The movement's theme colour (movements[].theme_color) and the label
    # colour drawn on it; both None when the movement has none.
    theme: Optional[str] = None
    theme_text: Optional[str] = None
    # Not sent to the page: when ``confidence`` was last sampled, and
    # since when it has been in the red band (None = it is not).
    confidence_sampled_at: float = 0.0
    low_since: Optional[float] = None

    def to_js(self) -> dict:
        d = asdict(self)
        d.pop("confidence_sampled_at")
        d.pop("low_since")
        return d


def resolve_status(
    state: dict,
    *,
    conf_level: str,
    now: float,
    low_since: Optional[float] = None,
) -> tuple[str, str, Optional[float]]:
    """Decide the tracking status shown on the audience panel AND the
    operator console.

    Args:
        state: an ``AppState.get_all()`` snapshot.
        conf_level: "good" / "mid" / "low" from
            ``ui.common.confidence_level``, or "none" while idle.
        now: ``time.monotonic()``.
        low_since: the caller's previous ``low_since`` (the monotonic
            time confidence entered the red band), or None.

    Returns ``(label, level, low_since)``. ``level`` is one of
    waiting / acquiring / tracking / checking / lost — the caller maps it
    to its own palette; the LABELS are deliberately identical on both
    screens so the operator never reads a rosier word than the audience.

    While tracking, the confidence itself grades the status, so neither
    screen shows 「追随中」 next to a red number. The red band has to
    persist (LOST_CONFIDENCE_SEC) before we admit to 見失い中 — short dips
    happen in quiet passages (docs/calibration.md 「追随状態の表示」).
    """
    if state.get("performance_ended") or state.get("waiting_for_start"):
        return "待機中", "waiting", None
    if not state.get("is_locked_in"):
        return "曲を捕捉中", "acquiring", None
    if state.get("is_in_inertia") or state.get("is_mismatched"):
        return "見失い中", "lost", None
    if conf_level == "mid":
        return "確認中", "checking", None
    if conf_level == "low":
        since = low_since if low_since is not None else now
        if now - since >= LOST_CONFIDENCE_SEC:
            return "見失い中", "lost", since
        return "確認中", "checking", since
    return "追随中", "tracking", None


def _luminance(hex_color: str) -> float:
    """WCAG 2 relative luminance of "#RRGGBB"."""
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def theme_text_color(theme: str) -> str:
    """Black or white, whichever reads better on ``theme``.

    The movement number sits ON the theme colour, and the themes run from
    near-white to near-black, so no fixed label colour works for all of
    them. Picking the higher-contrast of the two guarantees at least 4.5:1
    (WCAG AA) for any colour — legibility comes first on this screen.
    """
    black, white = "#000000", "#FFFFFF"
    return black if contrast_ratio(theme, black) >= contrast_ratio(theme, white) else white


def _confidence(conf: float) -> tuple[str, str]:
    return f"{int(conf * 100)}%", confidence_level(conf)


def build_panel_view(
    state: dict, now: float, last: Optional[PanelView] = None
) -> PanelView:
    """Map an AppState snapshot (``get_all()``) to what the panel shows.

    ``now`` is time.monotonic() (the clock ``manual_adjust_at`` uses).
    ``last`` is the previously built view; it carries the confidence
    sample so the number only changes every CONFIDENCE_REFRESH_SEC.
    """
    idle = bool(state.get("performance_ended") or state.get("waiting_for_start"))

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

    # The status is graded off the SAMPLED confidence level, not the live
    # one, so the word and the number on this screen always agree.
    status, status_level, low_since = resolve_status(
        state,
        conf_level=conf_level,
        now=now,
        low_since=last.low_since if last is not None else None,
    )

    adjusted_at = state.get("manual_adjust_at")
    manual = adjusted_at is not None and 0.0 <= now - adjusted_at < MANUAL_FLASH_SEC

    theme = state.get("movement_theme_color")

    return PanelView(
        # Not blanked while idle: which movement is about to start (or
        # just ended) is already known, unlike the measure and beat.
        movement=str(state.get("movement_number", 1)),
        measure="--" if idle else str(state.get("measure", "")),
        beat="--" if idle else str(int(state.get("beat_in_measure", 1.0))),
        confidence=conf_text,
        confidence_level=conf_level,
        status=status,
        status_level=status_level,
        manual=manual,
        # Like the movement number, shown while idle too.
        theme=theme,
        theme_text=theme_text_color(theme) if theme else None,
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


def resolve_decks(
    movements: list[dict], default_url: Optional[str]
) -> tuple[list[str], list[Optional[int]]]:
    """Decide which deck each movement shows.

    Returns ``(decks, index)``: the distinct embed URLs to preload, in
    first-use order, and per movement the position of its deck in
    ``decks`` (None for a video movement, or a slides movement with
    neither its own ``slide_url`` nor a default — the dry-run case).

    Movements naming the same deck (in any URL form) share one entry, so
    the deck keeps its page across them, as with a single-deck config.
    Raises ValueError naming the movement whose URL is not a Slides deck.
    """
    decks: list[str] = []
    index: list[Optional[int]] = []
    for i, movement in enumerate(movements):
        url = movement.get("slide_url") or default_url
        if is_video_movement(movement) or not url:
            index.append(None)
            continue
        try:
            embed = to_embed_url(url)
        except ValueError as exc:
            where = f"movements[{i}].slide_url" if movement.get("slide_url") else "--slide-url"
            raise ValueError(f"{where}: {exc}") from exc
        if embed not in decks:
            decks.append(embed)
        index.append(decks.index(embed))
    return decks, index
