"""partitura の楽譜読み込み警告の抑制。

pytest はテストごとに warnings フィルタを差し替えるので、本番と同じ状態を見るには
別プロセスで確認する必要がある。
"""

import subprocess
import sys

_WARN = (
    "import warnings; "
    "warnings.warn_explicit('w', UserWarning, 'f.py', 1, module='partitura.io.importmusicxml'); "
    "warnings.warn_explicit('other', UserWarning, 'f.py', 1, module='audio_score_follower.x')"
)


def _stderr(code: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stderr


def test_partitura_warning_is_visible_without_score_mapper():
    assert "UserWarning: w" in _stderr(_WARN)


def test_score_mapper_suppresses_only_partitura_warnings():
    stderr = _stderr(f"import audio_score_follower.core.score_mapper; {_WARN}")
    assert "UserWarning: w" not in stderr
    assert "UserWarning: other" in stderr
