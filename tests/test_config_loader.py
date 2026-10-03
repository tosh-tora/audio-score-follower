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


# ---- video movements -----------------------------------------------------

def test_video_path_is_resolved_relative_to_config(tmp_path):
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "01.mp4").write_bytes(b"")
    config_path = _write_config(tmp_path, [{"measure": 1, "video": "scenes/01.mp4"}])
    loader = ConfigLoader(str(config_path))
    trig = loader.movements[0]["triggers"][0]
    assert trig["video"] == str((tmp_path / "scenes" / "01.mp4").resolve())
    # A video trigger carries no key action.
    assert "action" not in trig
    assert loader.has_video_movements()


def test_missing_video_file_is_rejected(tmp_path):
    config_path = _write_config(tmp_path, [{"measure": 1, "video": "nope.mp4"}])
    with pytest.raises(ConfigError, match="ファイルが見つかりません"):
        ConfigLoader(str(config_path))


def test_video_and_action_together_are_rejected(tmp_path):
    (tmp_path / "01.mp4").write_bytes(b"")
    config_path = _write_config(
        tmp_path, [{"measure": 1, "video": "01.mp4", "action": "right"}]
    )
    with pytest.raises(ConfigError, match="同時に指定できません"):
        ConfigLoader(str(config_path))


def test_mixing_video_and_slide_triggers_in_a_movement_is_rejected(tmp_path):
    # 聴衆画面は楽章ごとにスライドか動画の一方しか出さない
    (tmp_path / "01.mp4").write_bytes(b"")
    config_path = _write_config(
        tmp_path, [{"measure": 1, "video": "01.mp4"}, {"measure": 5}]
    )
    with pytest.raises(ConfigError, match="混在"):
        ConfigLoader(str(config_path))


def test_slides_only_config_has_no_video_movements(tmp_path):
    config_path = _write_config(tmp_path, [{"measure": 1}])
    assert not ConfigLoader(str(config_path)).has_video_movements()
