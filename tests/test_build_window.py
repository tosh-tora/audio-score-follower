"""Tests for audio_score_follower.ui.build_window pure logic.

Only the Tk-free helpers (build_command / generate_config_dict /
write_config) are exercised — no Toplevel is created, matching the
headless split used by test_launch_options.
"""

from pathlib import Path

import pytest

from audio_score_follower.config.loader import ConfigLoader
from audio_score_follower.ui.build_window import (
    build_command,
    config_movement_builds,
    generate_config_dict,
    overwrite_message,
    write_config,
)

_MODULE = "audio_score_follower.cli.build_reference"


# ---------------------------------------------------------------- build_command
def test_build_command_minimal_omits_optional_flags():
    cmd = build_command(
        Path("score.mxl"), Path("ref.wav"), Path("out"),
        python_exe="python",
    )
    assert cmd[:3] == ["python", "-m", _MODULE]
    # required flags present with absolute paths
    assert "--score" in cmd and "--reference" in cmd and "--output" in cmd
    score_arg = cmd[cmd.index("--score") + 1]
    assert Path(score_arg).is_absolute()
    # nothing at CLI default is emitted
    for flag in (
        "--score-bpm", "--start-offset", "--end-trim",
        "--sample-rate", "--hop-length", "--cens-win", "--plot", "-v",
    ):
        assert flag not in cmd


def test_build_command_optional_flags_present_when_set():
    cmd = build_command(
        Path("s.mxl"), Path("r.wav"), Path("o"),
        python_exe="python",
        score_bpm=152.0, start_offset=1.5, end_trim=8.0,
        sample_rate=44100, hop_length=1024, cens_win=21,
        plot=True, verbose=True,
    )
    assert cmd[cmd.index("--score-bpm") + 1] == "152.0"
    assert cmd[cmd.index("--start-offset") + 1] == "1.5"
    assert cmd[cmd.index("--end-trim") + 1] == "8.0"
    assert cmd[cmd.index("--sample-rate") + 1] == "44100"
    assert cmd[cmd.index("--hop-length") + 1] == "1024"
    assert cmd[cmd.index("--cens-win") + 1] == "21"
    assert "--plot" in cmd
    assert "-v" in cmd


def test_build_command_explicit_zero_start_offset_is_passed():
    # start_offset=0 means "disable head-offset auto-detection" —
    # distinct from None (auto), mirroring end_trim's None-vs-0 split.
    cmd = build_command(Path("s"), Path("r"), Path("o"), start_offset=0.0)
    assert cmd[cmd.index("--start-offset") + 1] == "0.0"


def test_build_command_start_offset_none_omitted():
    cmd = build_command(Path("s"), Path("r"), Path("o"), start_offset=None)
    assert "--start-offset" not in cmd


def test_build_command_explicit_end_trim_zero_is_passed():
    # end_trim=0 means "disable trimming" — distinct from None (auto).
    cmd = build_command(Path("s"), Path("r"), Path("o"), end_trim=0.0)
    assert cmd[cmd.index("--end-trim") + 1] == "0.0"


def test_build_command_end_trim_none_omitted():
    cmd = build_command(Path("s"), Path("r"), Path("o"), end_trim=None)
    assert "--end-trim" not in cmd


# ------------------------------------------------------------ generate_config
def test_generate_config_uses_relative_forward_slash_paths(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    score = tmp_path / "data" / "scores" / "foo.mxl"
    built = tmp_path / "data" / "built" / "foo"
    cfg = generate_config_dict(score, built, config_dir)
    mv = cfg["movements"][0]
    assert mv["xml_file"] == "../data/scores/foo.mxl"
    assert mv["built_dir"] == "../data/built/foo"
    assert "\\" not in mv["xml_file"]  # forward slashes even on Windows


def test_generated_config_passes_configloader(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    score = tmp_path / "data" / "scores" / "foo.mxl"
    built = tmp_path / "data" / "built" / "foo"
    cfg = generate_config_dict(score, built, config_dir)
    config_path = config_dir / "foo.json"
    write_config(config_path, cfg)

    # ConfigLoader validates structure (movements/xml_file/built_dir/triggers)
    # without requiring the referenced files to exist.
    loader = ConfigLoader(str(config_path))
    assert loader.total_movements() == 1
    mv = loader.get_current_movement()
    assert mv["xml_file"] and mv["built_dir"]
    assert mv["triggers"][0]["measure"] == 1
    assert mv["triggers"][0]["action"] == "right"
    # 任意項目 author は空のプレースホルダとして雛形に出す（手編集の発見性）。
    assert mv["triggers"][0]["author"] == ""


def test_write_config_is_atomic_and_utf8(tmp_path: Path):
    config_path = tmp_path / "sub" / "夜想曲.json"
    cfg = generate_config_dict(
        tmp_path / "s.mxl", tmp_path / "b", tmp_path
    )
    write_config(config_path, cfg)
    text = config_path.read_text(encoding="utf-8")
    assert "トリガーを編集" in text  # Japanese note preserved (ensure_ascii=False)
    assert not list(config_path.parent.glob("*.tmp"))  # temp file cleaned up


# ------------------------------------------------- 選択中 config の楽章一覧
def _concert(tmp_path: Path) -> tuple[Path, Path]:
    """config/ と data/built/ が兄弟の、本番と同じ配置を作る。"""
    import json

    config_dir = tmp_path / "config"
    built_root = tmp_path / "data" / "built"
    config_dir.mkdir()
    (built_root / "幻想交響曲1").mkdir(parents=True)
    (built_root / "幻想交響曲1" / "build_meta.json").write_text(json.dumps({
        "score_path": str(tmp_path / "s1.mxl"), "reference_path": str(tmp_path / "r1.mp3"),
    }), encoding="utf-8")
    (built_root / "幻想交響曲4").mkdir()
    (built_root / "幻想交響曲4" / "build_meta.json").write_text("{}", encoding="utf-8")
    config = config_dir / "concert.json"
    config.write_text(json.dumps({"movements": [
        {"id": 1, "xml_file": "../data/scores/s1.mxl", "built_dir": "../data/built/幻想交響曲1"},
        {"id": 4, "xml_file": "../data/scores/s4.mxl", "built_dir": "../data/built/幻想交響曲4"},
        {"id": 5, "built_dir": "../data/built/幻想交響曲5"},
        {"id": 6, "built_dir": str(tmp_path / "elsewhere" / "b6")},
    ]}, ensure_ascii=False), encoding="utf-8")
    return config, built_root


def test_movement_builds_list_score_reference_and_output(tmp_path: Path):
    config, built_root = _concert(tmp_path)
    m1, m4, m5, m6 = config_movement_builds(config, built_root)
    assert (m1.movement_id, m1.output_name, m1.built) == (1, "幻想交響曲1", True)
    assert m1.score == (tmp_path / "data" / "scores" / "s1.mxl").resolve()
    assert m1.reference == tmp_path / "r1.mp3"
    # 参照録音の記録が無い古いビルド → None（画面では「不明」）
    assert (m4.output_name, m4.built, m4.reference) == ("幻想交響曲4", True, None)
    # 未ビルドの楽章も出す。楽譜省略（自動検出）は None
    assert (m5.output_name, m5.built, m5.score) == ("幻想交響曲5", False, None)
    # data/built の外は出力名なし（この画面ではそこへビルドできない）
    assert m6.output_name is None


def test_movement_builds_rejects_broken_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ oops", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        config_movement_builds(bad, tmp_path)


def test_overwrite_message_shows_previous_inputs(tmp_path: Path):
    _config, built_root = _concert(tmp_path)
    text = overwrite_message(built_root / "幻想交響曲1")
    assert "s1.mxl" in text and "r1.mp3" in text and "上書き" in text
    assert "記録されていません" in overwrite_message(built_root / "幻想交響曲4")


def test_config_generation_is_off_by_default(tmp_path: Path):
    # 既存 config の楽章の再ビルドが主用途。既定で生成すると 1 楽章 config で上書きしかねない
    tk = pytest.importorskip("tkinter")
    from audio_score_follower.ui.build_window import BuildWindow

    config, _ = _concert(tmp_path)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        win = BuildWindow(root, config.parent, config)
        assert win.var_gen_config.get() is False
        assert str(win.entry_config_name.cget("state")) == "disabled"
        assert len(win.tree_movements.get_children()) == 4
        # 楽章 1 を読み込むとフォームが埋まる
        win.tree_movements.selection_set("0")
        win._on_load_movement()
        assert win.var_output.get() == "幻想交響曲1"
        assert win.var_reference.get() == str(tmp_path / "r1.mp3")
    finally:
        root.destroy()
