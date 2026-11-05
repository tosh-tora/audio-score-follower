"""audio-score-follower — audio-to-audio orchestral score following."""

import os
from pathlib import Path

__version__ = "0.1.0"

# librosa が読み込む numba は既定でパッケージ横の __pycache__ にキャッシュを作ろうと
# する。全ユーザー向け Python（C:\Program Files）では書き込めず、Windows の tempfile
# が PermissionError をリトライし続けるので、エラーにならず import が永久に戻らない
# （ファイル入力でもマイクでも追随が始まらない）。numba より先に読まれるここで指定する。
os.environ.setdefault(
    "NUMBA_CACHE_DIR",
    str(
        Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
        / "audio-score-follower"
        / "numba"
    ),
)
