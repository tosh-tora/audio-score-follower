#!/usr/bin/env python3
"""操作コンソールが 1200x800 の画面に収まることを機械的に守る（Issue #51）。

このリポジトリで実際に起きた劣化: フォントを「ピットから読めるように」
2 倍にした結果、ウィンドウが 1400x1000 を要求するようになり、1280x800 の
実機では最大化してもキーヒント行が下端で切れていた。見た目の調整は
目視でしか確認されないので、ここで寸法そのものを固定する。

高さが超えたらフォントを削るか行を減らす（gui_tkinter.py の
module docstring の予算表に従う）。数字だけ緩めると実機で切れる。
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

# 本番で想定する最小の画面。ウィンドウ枠・タスクバーぶんは
# geometry 側の既定値 (_WINDOW_GEOMETRY) が吸収する。
SCREEN_W, SCREEN_H = 1200, 800

_MIC_WARNING = (
    "⚠ マイクのノイズ抑制が有効の可能性があります — Windows のサウンド設定で"
    "「オーディオの拡張」を無効にしてから起動し直してください"
)
_SLIDE_WARNING = (
    "⚠ Playwright の起動に失敗しました（スライド操作は無効）— "
    "ログを確認してください"
)


@pytest.fixture(scope="module")
def _tk_app():
    """One hidden Tk root for the module.

    Creating and destroying a Tk() per test is flaky on Windows (the 4th
    one intermittently fails with a TclError and silently skipped the
    assertions). Each test gets its own Toplevel instead — FollowerGUI
    only needs title/geometry/minsize/bind/after, which Toplevel has.
    """
    try:
        app = tk.Tk()
    except tk.TclError as exc:  # headless CI
        pytest.skip(f"no display: {exc}")
    app.withdraw()
    yield app
    app.destroy()


@pytest.fixture()
def root(_tk_app):
    win = tk.Toplevel(_tk_app)
    yield win
    win.destroy()


def _build(root, *, warnings: bool):
    from audio_score_follower.core.state_manager import AppState
    from audio_score_follower.ui.gui_tkinter import FollowerGUI

    state = AppState()
    state.set_movement(
        movement_id=1,
        xml_file="data/幻想交響曲_第4楽章_リピート削除.mxl",
        triggers=[{"measure": 1}, {"measure": 17}, {"measure": 48}],
        movement_number=1,
        total_movements=3,
        total_measures=178,
    )
    state.update_beat_measure(20.0, 128, 2.0)
    state.set_display_confidence(0.98)
    state.set_next_trigger(48)
    state.set_prev_trigger(17)
    state.set_mic_level(-42.3, False, True)
    state.set_silence_threshold(-55.0)
    if warnings:
        # 最悪ケース: 3 種の警告バナーが同時に出ている状態。
        state.set_mismatch(True)
        state.set_mic_effects_warning(_MIC_WARNING)
        state.set_slide_controller_warning(_SLIDE_WARNING)

    gui = FollowerGUI(root, state)
    root.geometry(f"{SCREEN_W}x{SCREEN_H}")
    root.update_idletasks()
    gui.update_display()
    root.update_idletasks()
    return gui


@pytest.mark.parametrize("warnings", [False, True], ids=["clean", "all-warnings"])
def test_console_fits_a_1200x800_screen(root, warnings):
    _build(root, warnings=warnings)
    req_w, req_h = root.winfo_reqwidth(), root.winfo_reqheight()
    assert req_h <= SCREEN_H, (
        f"操作コンソールの必要高さが {req_h}px で {SCREEN_H}px を超えている"
        "（下端のキーヒントが切れる）。フォントを削るか行を減らすこと。"
    )
    assert req_w <= SCREEN_W, (
        f"操作コンソールの必要幅が {req_w}px で {SCREEN_W}px を超えている。"
    )


def test_minsize_stays_within_the_target_screen(root):
    _build(root, warnings=False)
    min_w, min_h = root.minsize()
    assert min_w <= SCREEN_W and min_h <= SCREEN_H, (
        f"minsize {min_w}x{min_h} が想定画面 {SCREEN_W}x{SCREEN_H} より大きい"
        "— 操作者がウィンドウを画面内に収められなくなる。"
    )


def test_labels_use_the_song_wording_not_movements(root):
    """Issue #51: 操作者向けの表示は「N曲目 / 全M曲」「次の曲」。"""
    gui = _build(root, warnings=False)
    assert gui.label_movement.cget("text") == "1曲目 / 全3曲"
    assert gui.label_next_movement.cget("text").startswith("次の曲:")


def test_both_trigger_markers_are_shown(root):
    """Issue #51: 「次のトリガー」に加えて「前のトリガー」も出す。"""
    gui = _build(root, warnings=False)
    assert "17" in gui.label_prev_trigger.cget("text")
    assert "48" in gui.label_next_trigger.cget("text")


def test_prev_trigger_defaults_to_the_opening_measure(root):
    from audio_score_follower.core.state_manager import AppState
    from audio_score_follower.ui.gui_tkinter import FollowerGUI

    state = AppState()
    gui = FollowerGUI(root, state)
    gui.update_display()
    assert gui.label_prev_trigger.cget("text") == "前: 1 小節目"


# ------------------------------------------- 聴衆画面との表示基準の一致
# Issue #51: 操作コンソールが「追随中」と言っている隣で、聴衆画面が
# 「見失い中」を出している状態を作らない。判定は resolve_status に一本化
# したので、ここではラベルが実際に一致することを固定する。

_STATES = {
    "waiting": {"waiting_for_start": True},
    "ended": {"performance_ended": True},
    "acquiring": {"is_locked_in": False},
    "inertia": {"is_locked_in": True, "is_in_inertia": True},
    "mismatch": {"is_locked_in": True, "is_mismatched": True},
    "low-conf": {"is_locked_in": True, "display_confidence": 0.1},
    "mid-conf": {"is_locked_in": True, "display_confidence": 0.5},
    "tracking": {"is_locked_in": True, "display_confidence": 0.9},
}


@pytest.mark.parametrize("name", sorted(_STATES))
def test_console_and_audience_show_the_same_status_word(root, name):
    from audio_score_follower.core.state_manager import AppState
    from audio_score_follower.ui.audience_panel import build_panel_view
    from audio_score_follower.ui.gui_tkinter import FollowerGUI

    snapshot = AppState().get_all()
    snapshot.update(measure=17, beat_in_measure=2.0, display_confidence=0.9)
    snapshot.update(_STATES[name])

    audience = build_panel_view(snapshot, now=0.0)

    gui = FollowerGUI(root, AppState())
    gui._render_follower_mode(snapshot)
    console = gui.label_mode.cget("text")

    assert audience.status in console, (
        f"{name}: 聴衆画面は {audience.status!r} なのに操作コンソールは "
        f"{console!r} — 表示基準がずれている"
    )


def test_console_adds_operator_detail_without_rewording_the_status(root):
    """操作者向けの情報（復帰キー・慣性の残り）は括弧の中だけに足す。"""
    from audio_score_follower.core.state_manager import AppState
    from audio_score_follower.ui.gui_tkinter import FollowerGUI

    snapshot = AppState().get_all()
    snapshot.update(
        is_locked_in=True, is_in_inertia=True,
        inertia_elapsed_sec=3.7, inertia_cap_sec=10.0, display_confidence=0.9,
    )
    gui = FollowerGUI(root, AppState())
    gui._render_follower_mode(snapshot)
    text = gui.label_mode.cget("text")
    assert "見失い中" in text and "慣性進行中" in text and "6.3s" in text


def test_low_confidence_needs_the_hysteresis_before_it_says_lost(root, monkeypatch):
    """低確信度の一瞬の落ち込みでいきなり「見失い中」にしない（3 秒持続）。"""
    from audio_score_follower.core.state_manager import AppState
    from audio_score_follower.ui import gui_tkinter

    snapshot = AppState().get_all()
    snapshot.update(is_locked_in=True, display_confidence=0.1)

    clock = {"t": 100.0}
    monkeypatch.setattr(gui_tkinter.time, "monotonic", lambda: clock["t"])

    gui = gui_tkinter.FollowerGUI(root, AppState())
    gui._render_follower_mode(snapshot)
    assert "確認中" in gui.label_mode.cget("text")

    clock["t"] += 3.0
    gui._render_follower_mode(snapshot)
    assert "見失い中" in gui.label_mode.cget("text")


# ------------------------------------------------------ 起動ランチャー
# 固定 760x840 の geometry に対して中身が幅 ~950px を要求し、右端（無音
# 測定マージン欄など）が黙って切れていた。ランチャーは要求サイズで開く
# ので、要求サイズそのものを想定画面に収める。

_LONG_DEVICE = "マイク配列 (Realtek(R) Audio) — 非常に長いデバイス名 " * 3


def _build_launcher(root, tmp_path, monkeypatch):
    from audio_score_follower.ui import launcher

    monkeypatch.setattr(
        launcher, "list_input_devices",
        lambda: [(i, _LONG_DEVICE, f"{i}: {_LONG_DEVICE} [MME]") for i in range(3)],
    )
    monkeypatch.setattr(
        launcher, "list_output_devices_wasapi",
        lambda: [(9, _LONG_DEVICE, f"9: {_LONG_DEVICE} [WASAPI]")],
    )
    # 空の config_dir → エラー行が出る状態（最悪ケースの 1 つ）
    return launcher._LauncherWindow(root, tmp_path)


def test_launcher_fits_the_target_screen_in_the_worst_case(root, tmp_path, monkeypatch):
    win = _build_launcher(root, tmp_path, monkeypatch)
    # 実際に出る文言のうち最長のもの（mic_effects_probe.headline_ja /
    # _finish_measure）を長いデバイス名で出す。
    win.label_nc.configure(text=(
        f"⚠ ノイズ抑制ソフトの仮想マイクの可能性があります（{_LONG_DEVICE}）"
        "— 物理マイクを直接選択してください"
    ))
    win.label_nc.grid()
    win.button_open_sound_settings.pack(side="left", padx=(8, 0))
    win.label_measure.configure(text=(
        "閾値を -48.3 dBFS に設定しました (中央値 -52.1 / p10 -55.9 / "
        "マージン +2.0 / n=1200)"
    ))
    win.var_config.set("C:/" + "very_long_directory_name/" * 8 + "config.json")
    root.update_idletasks()

    req_w, req_h = root.winfo_reqwidth(), root.winfo_reqheight()
    assert req_w <= SCREEN_W, f"ランチャーの必要幅 {req_w}px が {SCREEN_W}px を超える"
    # タイトルバー・タスクバーぶんの余白を残す
    assert req_h <= SCREEN_H - 40, f"ランチャーの必要高さ {req_h}px が画面に収まらない"


def test_every_launcher_help_badge_has_text(root, tmp_path, monkeypatch):
    from audio_score_follower.ui import launcher

    _build_launcher(root, tmp_path, monkeypatch)
    assert all(text.strip() for text in launcher._HELP.values())


def test_tooltip_shows_on_hover_and_hides_on_leave(root):
    from audio_score_follower.ui.common import Tooltip

    button = tk.Button(root, text="x")
    button.pack()
    root.update_idletasks()
    tip = Tooltip(button, "説明", delay_ms=0)
    tip.show()
    assert tip.tip is not None and tip.tip.winfo_exists()
    tip.hide()
    assert tip.tip is None
    tip.toggle()
    assert tip.tip is not None
    tip.toggle()
    assert tip.tip is None
