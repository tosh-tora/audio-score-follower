#!/usr/bin/env python3
"""
ui/common.py - Shared UI utilities (fonts, base style, shared thresholds)

Single home for the small bits every Tk window in this project needs:

- CJK-capable font family detection (previously a private helper in
  gui_tkinter that three other modules imported).
- The ttk base-style bootstrap that launcher / build_window duplicated.
- The confidence colour breakpoints that the operator GUI and the viz
  window must keep in sync.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font, ttk
from typing import Optional

logger = logging.getLogger(__name__)

# Font families preferred for rendering Japanese filenames / labels.  We pick
# the first one that the local Tk installation actually has — falling back to
# the generic "TkDefaultFont" so the GUI still works (with tofu glyphs) when
# no CJK font is installed.  On WSL2/Ubuntu, `sudo apt install fonts-noto-cjk`
# makes "Noto Sans CJK JP" available.
PREFERRED_FONT_FAMILIES = (
    "Noto Sans CJK JP",
    "Noto Sans JP",
    "Yu Gothic UI",
    "Yu Gothic",
    "Meiryo",
    "MS Gothic",
    "TakaoPGothic",
    "TakaoGothic",
    "IPAexGothic",
    "IPAPGothic",
    "Hiragino Sans",
    "DejaVu Sans",
)

# Confidence colour breakpoints shared by the operator GUI's confidence
# label (gui_tkinter.update_display) and the viz window (_conf_color) so
# both screens tell the same story. The palettes differ per screen (Tk
# colour names on the light GUI, dark-pit hex on the viz window) and the
# boundary comparison is historical per-site (> vs >=) — only the
# breakpoints themselves are shared here.
CONFIDENCE_GOOD_THRESHOLD = 0.6
CONFIDENCE_MID_THRESHOLD = 0.4


def confidence_level(conf: float) -> str:
    """Grade a display confidence into "good" / "mid" / "low".

    One implementation so the operator console's colour, the audience
    panel's colour and the tracking status both screens show are driven
    by the same breakpoints. The ``>`` comparisons are the historical
    ones — do not switch them to ``>=`` on one side only.
    """
    if conf > CONFIDENCE_GOOD_THRESHOLD:
        return "good"
    if conf > CONFIDENCE_MID_THRESHOLD:
        return "mid"
    return "low"


def pick_font_family(root: tk.Tk) -> str:
    """Return the first available CJK-capable font family for this Tk root."""
    try:
        available = set(font.families(root=root))
    except Exception:  # noqa: BLE001 — Tk could be in a weird state
        available = set()
    for family in PREFERRED_FONT_FAMILIES:
        if family in available:
            logger.info("GUI font family: %s", family)
            return family
    logger.warning(
        "No CJK-capable font found among %s — Japanese text may render as tofu. "
        "Install fonts-noto-cjk (Ubuntu) or equivalent.",
        PREFERRED_FONT_FAMILIES,
    )
    return "TkDefaultFont"


def apply_base_style(
    target: tk.Misc, font_source: Optional[tk.Tk] = None
) -> tuple[tuple[str, int], tuple[str, int]]:
    """Apply the shared 12pt base font to ``target`` and return the fonts.

    ``target`` is the window whose ttk style / option database gets the
    font (the Tk root for the launcher, the Toplevel for the build
    window). ``font_source`` is the root used for font-family detection;
    defaults to ``target``.

    Returns ``(font, font_small)`` — the (family, 12) / (family, 10)
    tuples the callers keep for per-widget overrides.
    """
    family = pick_font_family(font_source if font_source is not None else target)
    base_font = (family, 12)
    small_font = (family, 10)
    ttk.Style(target).configure(".", font=base_font)
    target.option_add("*Font", base_font)
    return base_font, small_font


_TOOLTIP_BG = "#fffbe6"
_TOOLTIP_BORDER = "#c9b870"


class Tooltip:
    """Hover popup explaining a widget (launcher settings help).

    Shown ``delay_ms`` after the pointer enters the widget and destroyed
    on leave / click, so it never lingers over the control the operator
    is about to use. ``show()`` / ``hide()`` are public so a help icon
    can also toggle it on click (touch screens have no hover).
    """

    def __init__(
        self, widget: tk.Widget, text: str, *,
        delay_ms: int = 400, wraplength: str = "320p",
    ) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self.tip: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self.show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def show(self) -> None:
        self._cancel()
        if self.tip is not None or not self.widget.winfo_exists():
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(
            tip, text=self.text, justify="left", wraplength=self.wraplength,
            background=_TOOLTIP_BG, foreground="#222",
            highlightthickness=1, highlightbackground=_TOOLTIP_BORDER,
            padx=10, pady=6, font=_small_font_of(self.widget),
        ).pack()
        tip.update_idletasks()
        # Below the widget and inside its window (the help badges sit at
        # the right edge, so right-align the tip to them there), then
        # clamped to the screen; flips above the widget at the bottom.
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        window = self.widget.winfo_toplevel()
        if x + w > window.winfo_rootx() + window.winfo_width():
            x = self.widget.winfo_rootx() + self.widget.winfo_width() - w
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        x = max(0, min(x, sw - w - 4))
        if y + h > sh:
            y = max(0, self.widget.winfo_rooty() - h - 4)
        tip.wm_geometry(f"+{x}+{y}")
        self.tip = tip

    def hide(self, _event=None) -> None:
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def toggle(self, _event=None) -> None:
        if self.tip is None:
            self.show()
        else:
            self.hide()


def _small_font_of(widget: tk.Misc):
    """The 10pt variant of the option-database font apply_base_style set."""
    base = widget.option_get("font", "Font")
    try:
        family = font.Font(root=widget, font=base).actual("family") if base else None
    except tk.TclError:
        family = None
    return (family, 10) if family else "TkDefaultFont"


def help_icon(parent: tk.Widget, text: str) -> ttk.Label:
    """Small "?" badge that explains the setting next to it on hover/click."""
    small = _small_font_of(parent)
    ttk.Style(parent).configure(
        "Help.TLabel", foreground="#3a6db5",
        font=(small[0], 13) if isinstance(small, tuple) else small,
        padding=(2, 0),
    )
    icon = ttk.Label(parent, text="ⓘ", style="Help.TLabel", cursor="question_arrow")
    tooltip = Tooltip(icon, text, delay_ms=150)
    # Replaces Tooltip's click-to-hide binding: on the icon a click is
    # the touch-screen way to open the explanation.
    icon.bind("<ButtonPress>", tooltip.toggle)
    return icon


def attach_help(text: str, *widgets: tk.Widget) -> None:
    """Attach the same hover explanation to each of ``widgets``."""
    for w in widgets:
        Tooltip(w, text)
