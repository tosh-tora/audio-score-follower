#!/usr/bin/env python3
"""
slide_controller.py - Audience screen (Google Slides + tracking panel) via Playwright

Owns a Chromium window showing the audience screen (Issue #8): the local
page ``ui/audience/host.html`` with the deck in an ``/embed`` iframe on the
left and the tracking panel on the right. Commands (key presses, panel
updates, back-to-slide-1) are received from other threads via a Queue and
applied from the dedicated worker thread (sync Playwright is
single-threaded by design).

Typical lifecycle::

    sc = SlideController(slide_url="https://docs.google.com/presentation/d/.../present")
    sc.start()
    sc.wait_ready(timeout=30.0)
    sc.press("right")           # advance one slide
    sc.update_panel(view.to_js())
    sc.reset_to_first()         # back to slide 1
    sc.stop()

Any URL of the deck works; it is rewritten to ``/embed`` because
``/present`` refuses to be framed. On startup the window moves to the
external monitor and goes fullscreen there, leaving the laptop screen to
the operator console. With no external monitor it stays a normal window
(rehearsal on a bare laptop).

Action mapping
--------------
The existing config.json uses short action names ("right", "left", ...).
These are mapped to Playwright key identifiers (ArrowRight, ArrowLeft, ...).
Unknown actions are passed through verbatim so any Playwright-valid key name
also works (e.g. "Space", "Enter", "F5").
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional
from urllib.parse import urlencode

from audio_score_follower.ui.audience_panel import (
    HOST_HTML,
    pick_presentation_screen,
    to_embed_url,
)

logger = logging.getLogger(__name__)


# config.json `action` strings → Playwright key identifiers.
# https://playwright.dev/python/docs/api/class-keyboard#keyboard-press
_KEY_MAP = {
    "right": "ArrowRight",
    "left": "ArrowLeft",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "space": "Space",
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "pgdn": "PageDown",
    "pagedown": "PageDown",
    "pgup": "PageUp",
    "pageup": "PageUp",
}


class SlideController:
    """
    Thread-backed Playwright wrapper for slide control.

    Browser lifetime is bound to the worker thread. Commands are FIFO via a
    queue; key presses are non-blocking from the caller's perspective.
    """

    def __init__(
        self,
        slide_url: str,
        *,
        headless: bool = False,
        viewport: Optional[dict] = None,
    ) -> None:
        """
        Args:
            slide_url: Google Slides URL (recommended: the ``/present?...``
                variant so the page opens in presentation mode automatically).
            headless: Run Chromium without a UI window. Default False; set
                True only for automated tests.
            viewport: Optional viewport dict (``{"width": ..., "height": ...}``).
                Default uses the OS window size.
        """
        self.slide_url = slide_url
        self.headless = headless
        self.viewport = viewport

        self._command_queue: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._fatal_error: Optional[BaseException] = None
        # Latest panel view not yet drawn. Only the newest matters, so
        # update_panel() overwrites it instead of queueing every tick.
        self._view_lock = threading.Lock()
        self._pending_view: Optional[dict] = None

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        """Spawn the Playwright worker thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("SlideController.start() called but thread already running")
            return

        self._stop_event.clear()
        self._ready_event.clear()
        self._fatal_error = None

        self._thread = threading.Thread(
            target=self._run,
            name="slide-controller",
            daemon=True,
        )
        self._thread.start()
        logger.info("SlideController worker thread started")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """Block until Chromium has loaded the slide URL (or has failed)."""
        return self._ready_event.wait(timeout)

    @property
    def last_error(self) -> Optional[BaseException]:
        """Fatal error from the worker, if any."""
        return self._fatal_error

    def press(self, action: str) -> None:
        """Queue a key press. Returns immediately; press happens in worker thread."""
        key = _KEY_MAP.get(action.lower(), action)
        self._command_queue.put(("press", key))
        logger.debug("Queued key press: %s → %s", action, key)

    def update_panel(self, view: dict) -> None:
        """Queue a tracking-panel redraw (``PanelView.to_js()``). Non-blocking."""
        with self._view_lock:
            already_queued = self._pending_view is not None
            self._pending_view = view
        if not already_queued:
            self._command_queue.put(("panel", None))

    def reset_to_first(self) -> None:
        """Queue a return to slide 1 (reloads the embed viewer)."""
        self._command_queue.put(("reset", None))

    def stop(self, timeout: float = 5.0) -> None:
        """Signal worker to close the browser and exit."""
        self._stop_event.set()
        # Wake the queue.get() if it's blocked
        self._command_queue.put(None)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "SlideController worker did not stop within %.1fs", timeout
                )
        self._thread = None
        logger.info("SlideController stopped")

    # ----------------------------------------------------------------- private
    def _run(self) -> None:
        """Worker thread main loop: own Playwright + process commands."""
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            self._fatal_error = exc
            logger.error(
                "Playwright is not installed. In the project venv, run:\n"
                "    pip install playwright\n"
                "    playwright install chromium"
            )
            self._ready_event.set()
            return

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--start-maximized",
                        # Reduce automation banner / detection so Google Slides
                        # behaves like a normal user session.
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                context_kwargs = {"no_viewport": self.viewport is None}
                if self.viewport is not None:
                    context_kwargs = {"viewport": self.viewport}
                context = browser.new_context(**context_kwargs)
                page = context.new_page()

                embed_url = to_embed_url(self.slide_url)
                host_url = f"{HOST_HTML.as_uri()}?{urlencode({'src': embed_url})}"
                logger.info("Opening audience screen with slides: %s", embed_url)
                page.goto(host_url, wait_until="domcontentloaded")
                self._place_on_projector(context, page)
                self._wait_slides_loaded(page)

                self._ready_event.set()
                logger.info("SlideController ready.")

                while not self._stop_event.is_set():
                    try:
                        cmd = self._command_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    if cmd is None:
                        # Sentinel from stop()
                        break

                    op, arg = cmd
                    try:
                        if op == "press":
                            # Re-focus every time: key events only reach the
                            # deck while its iframe holds focus.
                            _focus_slides(page)
                            page.keyboard.press(arg)
                            logger.info("Sent key press: %s", arg)
                        elif op == "panel":
                            with self._view_lock:
                                view, self._pending_view = self._pending_view, None
                            if view is not None:
                                page.evaluate("v => window.asfUpdate(v)", view)
                        elif op == "reset":
                            page.evaluate("() => window.asfResetSlides()")
                            self._wait_slides_loaded(page)
                            logger.info("Slides reset to slide 1")
                        else:
                            logger.warning("Unknown slide controller command: %s", op)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Slide command %s %s failed: %s", op, arg, exc, exc_info=True)

                try:
                    context.close()
                    browser.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Browser teardown raised: %s", exc)

        except Exception as exc:  # noqa: BLE001
            self._fatal_error = exc
            logger.error("SlideController fatal: %s", exc, exc_info=True)
        finally:
            self._ready_event.set()  # unblock waiters even on failure
            logger.info("SlideController worker exiting")

    @staticmethod
    def _wait_slides_loaded(page) -> None:
        """Block until the embed iframe has loaded, then give it focus."""
        # page.evaluate has no timeout of its own; a load that never fires
        # (network down) must not hang the worker forever.
        loaded = page.evaluate(
            """() => Promise.race([window.asfSlidesLoaded.then(() => true),
                new Promise(resolve => setTimeout(() => resolve(false), 20000))])"""
        )
        if not loaded:
            logger.warning("スライドの読み込みが 20 秒以内に完了しませんでした")
        # The viewer builds its slide DOM after the iframe load event.
        page.wait_for_timeout(1500)
        _focus_slides(page)

    @staticmethod
    def _place_on_projector(context, page) -> None:
        """Move the window to the external monitor and go fullscreen there.

        Launch flags (--kiosk / --start-fullscreen) are ignored under
        Playwright, so this goes through CDP. getScreenDetails() reports
        screens in the same DIP coordinates setWindowBounds takes, so
        Windows display scaling needs no conversion. Non-fatal: on any
        failure the slides still work in a normal window.
        """
        try:
            cdp = context.new_cdp_session(page)
            # Pre-grant so Chromium never shows the "manage windows on all
            # your displays" prompt — nobody is at this window to answer it,
            # and getScreenDetails() waits on it. browserContextId is
            # required: without it the grant targets the default context,
            # not Playwright's, and the prompt still appears intermittently
            # (Playwright's grant_permissions does not know this permission).
            context_id = cdp.send("Target.getTargetInfo")["targetInfo"]["browserContextId"]
            cdp.send("Browser.grantPermissions", {
                "permissions": ["windowManagement"], "browserContextId": context_id})
            # Last-resort guard: if the call still never settles, give up
            # after a few seconds and stay windowed instead of hanging.
            screens = page.evaluate(
                """() => Promise.race([
                    window.getScreenDetails().then(d => d.screens.map(s => ({
                        left: s.left, top: s.top, width: s.width, height: s.height,
                        isPrimary: s.isPrimary, isInternal: s.isInternal, label: s.label }))),
                    new Promise(resolve => setTimeout(() => resolve(null), 5000)),
                ])"""
            )
            if screens is None:
                logger.warning("モニター情報の取得がタイムアウトしました。全画面にしません")
                return
            target = pick_presentation_screen(screens)
            if target is None:
                logger.info("外部モニターが見つからないため全画面にしません (screens=%s)", screens)
                return
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            # A maximized window cannot be moved; restore it first.
            for bounds in (
                {"windowState": "normal"},
                {"left": target["left"] + 50, "top": target["top"] + 50,
                 "width": 800, "height": 600},
                {"windowState": "fullscreen"},
            ):
                cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": bounds})
            logger.info("Audience screen fullscreen on external monitor: %s", target)
        except Exception as exc:  # noqa: BLE001
            logger.warning("外部モニターへの全画面表示に失敗しました: %s", exc, exc_info=True)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        alive = self._thread is not None and self._thread.is_alive()
        return f"SlideController(url={self.slide_url!r}, running={alive})"


def _focus_slides(page) -> None:
    # focus(), never click(): a click inside the embed viewer advances the
    # deck by one slide.
    page.locator("#slide").focus()


class NullSlideController:
    """No-op slide controller used when --slide-url is omitted (dry-run / test mode)."""

    def start(self) -> None:
        logger.info("[dry-run] SlideController: start (no browser)")

    def wait_ready(self, timeout: float = 30.0) -> bool:  # noqa: ARG002
        return True

    def stop(self) -> None:
        logger.info("[dry-run] SlideController: stop")

    def update_panel(self, view: dict) -> None:  # noqa: ARG002
        return None

    def reset_to_first(self) -> None:
        return None

    def press(self, action: str) -> None:
        # No log here — the canonical "slide press" log is emitted by
        # TriggerEngine.execute_action so it can include the source tag
        # (manual/auto) and the triggering measure.
        return None

    @property
    def last_error(self) -> None:
        return None
