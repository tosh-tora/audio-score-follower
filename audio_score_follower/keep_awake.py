"""Keep the PC and its displays awake while the app is running.

A concert runs for an hour or more with nobody touching the PC — the
operator only presses keys when something goes wrong. Windows' idle
timers would then turn the audience display off (or put the machine to
sleep, killing the mic stream and the slide window) in the middle of the
performance. Microphone audio does not count as user input, so the
follower itself never resets those timers.

``SetThreadExecutionState`` with ``ES_CONTINUOUS`` is per-thread and lasts
until reset or until the calling thread exits, so it must be called from
the main thread, which lives for the whole launcher ↔ console loop. The
OS drops the request automatically if the process dies, so a crash cannot
leave the PC unable to sleep.
"""
from __future__ import annotations

import contextlib
import logging
import sys
from typing import Iterator

logger = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
# The audience screen must stay lit too, not just the CPU.
ES_DISPLAY_REQUIRED = 0x00000002


def _set_state(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    prev = ctypes.windll.kernel32.SetThreadExecutionState(flags)
    return prev != 0  # 0 means the call failed


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """Block system sleep and display-off for the duration of the block."""
    active = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    if active:
        logger.info("スリープ・画面オフを抑止しました（アプリ終了で解除）")
    elif sys.platform == "win32":
        logger.warning("スリープ抑止に失敗しました。電源設定でスリープを無効にしてください")
    try:
        yield
    finally:
        if active:
            _set_state(ES_CONTINUOUS)
