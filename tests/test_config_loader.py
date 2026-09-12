#!/usr/bin/env python3
"""ConfigLoader._validate() の triggers/action まわりの挙動を固定するテスト。

Issue #26: action は省略可能で、省略時は "right" 扱い。
"""
import json

import pytest

from audio_score_follower.config.loader import ConfigError, ConfigLoader


def _write_config(tmp_path, triggers):
    config = {
        "movements": [
            {
                "id": 1,
                "xml_file": "dummy.xml",
                "built_dir": "dummy_built",
                "triggers": triggers,
            }
        ]
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def test_missing_action_defaults_to_right(tmp_path):
    config_path = _write_config(tmp_path, [{"measure": 1, "note": "開始"}])
    loader = ConfigLoader(str(config_path))
    assert loader.movements[0]["triggers"][0]["action"] == "right"


def test_explicit_left_is_preserved(tmp_path):
    config_path = _write_config(tmp_path, [{"measure": 1, "action": "left"}])
    loader = ConfigLoader(str(config_path))
    assert loader.movements[0]["triggers"][0]["action"] == "left"


def test_invalid_action_is_rejected(tmp_path):
    config_path = _write_config(tmp_path, [{"measure": 1, "action": "foo"}])
    with pytest.raises(ConfigError):
        ConfigLoader(str(config_path))


def test_optional_author_is_preserved(tmp_path):
    """`author`（解説を書いた人）は任意項目。検証せずそのまま通す。"""
    config_path = _write_config(
        tmp_path, [{"measure": 1, "note": "不気味な導入", "author": "平（指揮）"}]
    )
    loader = ConfigLoader(str(config_path))
    trig = loader.movements[0]["triggers"][0]
    assert trig["author"] == "平（指揮）"
    assert trig["action"] == "right"


def test_author_is_optional(tmp_path):
    config_path = _write_config(tmp_path, [{"measure": 1, "note": "開始"}])
    loader = ConfigLoader(str(config_path))
    assert "author" not in loader.movements[0]["triggers"][0]
