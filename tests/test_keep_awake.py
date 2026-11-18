"""keep_awake must request sleep/display-off suppression and release it."""
from __future__ import annotations

import pytest

from audio_score_follower import keep_awake as ka


def test_sets_and_clears_execution_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(ka, "_set_state", lambda flags: calls.append(flags) or True)

    with ka.keep_awake():
        assert calls == [ka.ES_CONTINUOUS | ka.ES_SYSTEM_REQUIRED | ka.ES_DISPLAY_REQUIRED]

    assert calls[-1] == ka.ES_CONTINUOUS


def test_releases_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(ka, "_set_state", lambda flags: calls.append(flags) or True)

    with pytest.raises(RuntimeError):
        with ka.keep_awake():
            raise RuntimeError

    assert calls[-1] == ka.ES_CONTINUOUS


def test_no_release_when_request_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(ka, "_set_state", lambda flags: calls.append(flags) and False)

    with ka.keep_awake():
        pass

    assert len(calls) == 1
