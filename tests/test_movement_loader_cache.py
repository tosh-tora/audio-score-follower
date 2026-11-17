"""楽譜（ScoreMapper）のキャッシュと起動時の先読み。

partitura のパースは楽章あたり数秒かかるので、本物は使わず
カウンタ付きのダミーに差し替える。
"""
from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

from audio_score_follower.core import movement_loader as ml


class _FakeMapper:
    built: list = []
    delay = 0.0

    def __init__(self, path):
        time.sleep(self.delay)
        _FakeMapper.built.append(path)


@pytest.fixture(autouse=True)
def fake_mapper(monkeypatch):
    _FakeMapper.built = []
    _FakeMapper.delay = 0.0
    monkeypatch.setattr(ml, "ScoreMapper", _FakeMapper)
    monkeypatch.setattr(ml, "_score_cache", {})
    return _FakeMapper


def _score(tmp_path, name="a.mxl"):
    p = tmp_path / name
    p.write_bytes(b"x")
    return p


def test_same_score_is_parsed_once(tmp_path):
    p = _score(tmp_path)
    assert ml.get_score_mapper(p) is ml.get_score_mapper(p)
    assert len(_FakeMapper.built) == 1


def test_replaced_score_is_parsed_again(tmp_path):
    p = _score(tmp_path)
    ml.get_score_mapper(p)
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))
    ml.get_score_mapper(p)
    assert len(_FakeMapper.built) == 2


def test_prewarm_skips_missing_and_loads_the_rest(tmp_path):
    a, b = _score(tmp_path, "a.mxl"), _score(tmp_path, "b.mxl")
    config = SimpleNamespace(
        movements=[{"xml_file": "a.mxl"}, {"xml_file": "missing.mxl"},
                   {"id": 3}, {"xml_file": "b.mxl"}],
        resolve_path=lambda raw: str(tmp_path / raw),
    )
    ml.prewarm_scores(config)
    assert sorted(_FakeMapper.built) == sorted([str(a.resolve()), str(b.resolve())])


def test_concurrent_prewarm_and_load_parse_once(tmp_path):
    p = _score(tmp_path)
    _FakeMapper.delay = 0.2
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(ml.get_score_mapper(p)))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_FakeMapper.built) == 1
    assert results[0] is results[1]
