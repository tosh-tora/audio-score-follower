"""NUMBA_CACHE_DIR の既定値。numba の設定は import 時に確定するので別プロセスで見る。"""

import os
import subprocess
import sys
from pathlib import Path

_PRINT_CACHE_DIR = "import audio_score_follower, numba; print(numba.config.CACHE_DIR)"


def _cache_dir(**env: str) -> str:
    full_env = {k: v for k, v in os.environ.items() if k != "NUMBA_CACHE_DIR"}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", _PRINT_CACHE_DIR],
        capture_output=True, text=True, check=True, env=full_env,
    ).stdout.strip()


def test_defaults_to_per_user_location(tmp_path):
    cache_dir = Path(_cache_dir(LOCALAPPDATA=str(tmp_path)))
    assert cache_dir.is_relative_to(tmp_path)


def test_explicit_env_var_wins(tmp_path):
    assert _cache_dir(NUMBA_CACHE_DIR=str(tmp_path)) == str(tmp_path)
