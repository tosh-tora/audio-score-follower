"""Audience window closed mid-performance (Issue #64).

The SlideController worker must notice the closed page, open the screen
again and restore what the audience saw. Playwright is replaced by fakes
(``playwright.sync_api`` is stubbed in ``sys.modules``) so the real
``_run`` loop runs on its thread without a browser.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from audio_score_follower.core import slide_controller as sc_mod
from audio_score_follower.core.slide_controller import SlideController


class _FakeLocator:
    def focus(self) -> None:
        pass


class _FakeKeyboard:
    def __init__(self, page: "_FakePage") -> None:
        self.page = page

    def press(self, key: str) -> None:
        if self.page.closed:
            raise RuntimeError("Target page has been closed")
        self.page.calls.append(("press", key))


class _FakePage:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple] = []
        self.keyboard = _FakeKeyboard(self)

    def is_closed(self) -> bool:
        return self.closed

    def goto(self, url: str, **_kw) -> None:
        self.calls.append(("goto", url))

    def evaluate(self, js: str, arg=None):
        if self.closed:
            raise RuntimeError("Target page has been closed")
        self.calls.append(("eval", js, arg))
        if "asfPlay" in js or "asfHoldEnd" in js:
            return {"error": None, "cut": False}
        return None

    def locator(self, _sel: str) -> _FakeLocator:
        return _FakeLocator()

    def wait_for_timeout(self, _ms: int) -> None:
        pass


class _FakeContext:
    def __init__(self, browser: "_FakeBrowser") -> None:
        self.browser = browser

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.browser.pages.append(page)
        return page

    def close(self) -> None:
        pass


class _FakeBrowser:
    def __init__(self) -> None:
        self.connected = True
        self.pages: list[_FakePage] = []

    def is_connected(self) -> bool:
        return self.connected

    def new_context(self, **_kw) -> _FakeContext:
        return _FakeContext(self)

    def close(self) -> None:
        self.connected = False


class _FakePlaywright:
    def __init__(self) -> None:
        self.browsers: list[_FakeBrowser] = []
        self.chromium = self

    def launch(self, **_kw) -> _FakeBrowser:
        b = _FakeBrowser()
        self.browsers.append(b)
        return b

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        pass

    @property
    def pages(self) -> list[_FakePage]:
        return [p for b in self.browsers for p in b.pages]


@pytest.fixture
def fake_pw(monkeypatch):
    pw = _FakePlaywright()
    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: pw
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)
    # Window placement and deck loading talk to real Chromium; not under test.
    monkeypatch.setattr(SlideController, "_place_on_projector", staticmethod(lambda c, p: None))
    monkeypatch.setattr(SlideController, "_wait_slides_loaded", staticmethod(lambda p, startup=False: None))
    return pw


def _wait_until(cond, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _drain(sc: SlideController) -> None:
    _wait_until(lambda: sc._command_queue.empty())
    time.sleep(0.1)  # let the last dequeued command finish


def _presses(page: _FakePage) -> list[str]:
    return [c[1] for c in page.calls if c[0] == "press"]


def _start(fake_pw, **kw) -> tuple[SlideController, threading.Event]:
    reopened = threading.Event()
    sc = SlideController(
        slide_urls=["https://docs.google.com/presentation/d/A/edit",
                    "https://docs.google.com/presentation/d/B/edit"],
        on_reopened=reopened.set, **kw,
    )
    sc.start()
    assert sc.wait_ready(3.0)
    return sc, reopened


def test_closed_window_reopens_with_deck_position_and_panel(fake_pw):
    sc, reopened = _start(fake_pw)
    try:
        sc.press("right")
        sc.press("right")
        sc.show_deck(1)
        sc.press("right")
        sc.update_panel({"measure": "12"})
        _drain(sc)

        first = fake_pw.pages[0]
        first.closed = True
        assert reopened.wait(3.0)

        new = fake_pw.pages[-1]
        assert new is not first
        # Same browser (only the window was closed): no relaunch.
        assert len(fake_pw.browsers) == 1
        # Each deck gets its own key sequence back, then deck 2 is shown.
        evals = [c for c in new.calls if c[0] in ("eval", "press")]
        assert ("eval", "i => window.asfShowDeck(i)", 0) in evals
        assert _presses(new) == ["ArrowRight", "ArrowRight", "ArrowRight"]
        shows = [c[2] for c in evals if c[0] == "eval" and "asfShowDeck" in c[1]]
        assert shows[-1] == 1
        assert ("eval", "v => window.asfUpdate(v)", {"measure": "12"}) in evals
    finally:
        sc.stop()


def test_reset_clears_the_replayed_presses(fake_pw):
    sc, reopened = _start(fake_pw)
    try:
        sc.press("right")
        sc.press("right")
        sc.reset_to_first()
        sc.press("right")
        _drain(sc)

        fake_pw.pages[0].closed = True
        assert reopened.wait(3.0)
        assert _presses(fake_pw.pages[-1]) == ["ArrowRight"]
    finally:
        sc.stop()


def test_video_mode_restores_held_frame(fake_pw, tmp_path):
    clip = tmp_path / "scene1.mp4"
    sc, reopened = _start(fake_pw)
    try:
        sc.set_mode("video")
        sc.play_video(str(clip))
        _drain(sc)

        fake_pw.pages[0].closed = True
        assert reopened.wait(3.0)
        evals = fake_pw.pages[-1].calls
        assert ("eval", "m => window.asfSetMode(m)", "video") in evals
        assert ("eval", "u => window.asfHoldEnd(u)", clip.resolve().as_uri()) in evals
    finally:
        sc.stop()


def test_dead_browser_is_relaunched(fake_pw):
    sc, reopened = _start(fake_pw)
    try:
        sc.press("right")
        _drain(sc)
        fake_pw.browsers[0].connected = False
        assert reopened.wait(3.0)
        assert len(fake_pw.browsers) == 2
        assert _presses(fake_pw.pages[-1]) == ["ArrowRight"]
    finally:
        sc.stop()


def test_commands_sent_while_closed_apply_after_restore(fake_pw):
    sc, reopened = _start(fake_pw)
    try:
        fake_pw.pages[0].closed = True
        sc.press("right")
        assert reopened.wait(3.0)
        _drain(sc)
        assert _presses(fake_pw.pages[-1]) == ["ArrowRight"]
    finally:
        sc.stop()


def test_failed_reopen_is_retried(fake_pw, monkeypatch):
    monkeypatch.setattr(sc_mod, "_REOPEN_RETRY_SEC", 0.1)
    sc, reopened = _start(fake_pw)
    try:
        real_open = sc._open_screen
        fails = {"n": 1}

        def flaky(p, browser):
            if fails["n"]:
                fails["n"] -= 1
                raise RuntimeError("launch failed")
            return real_open(p, browser)

        sc._open_screen = flaky
        fake_pw.pages[0].closed = True
        assert reopened.wait(3.0)
        assert fails["n"] == 0
    finally:
        sc.stop()


def test_no_reopen_after_stop(fake_pw):
    sc, reopened = _start(fake_pw)
    sc.stop()
    n_pages = len(fake_pw.pages)
    fake_pw.pages[0].closed = True
    time.sleep(0.4)
    assert not reopened.is_set()
    assert len(fake_pw.pages) == n_pages
