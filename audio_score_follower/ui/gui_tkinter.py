#!/usr/bin/env python3
"""
ui/gui_tkinter.py - Operator console (操作パネル)

Tkinter GUI showing, grouped into cards:
- 曲            : 現在の曲 / 全曲数、次の曲
- 現在位置      : 小節（大きな数字）/ 全小節、拍
- 追随の質      : 確信度
- トリガー      : 前のトリガー / 次のトリガー、クールダウン
- マイク        : 入力レベルと無音判定閾値
- 追随モード    : waiting / tracking / inertia / capped + 開始・終了ボタン

Layout constraint (Issue #51): the console must fit a 1200x800 screen
WITHOUT clipping the bottom hint line. Every font size here is part of
that budget — the natural height is ~646px clean and ~768px with all
three warning banners up at once (the worst case). Bumping a size means
re-checking that budget with tests/test_gui_layout.py, not just
"looks bigger". Note the two columns are coupled: 小節 can only grow
until the left card passes the right column's height, after which every
extra point costs the window directly.
"""

import logging
import time
import tkinter as tk
from tkinter import font
from typing import Callable, List, Optional

from audio_score_follower.core.state_manager import AppState
from audio_score_follower.ui.audience_panel import resolve_status
from audio_score_follower.ui.common import confidence_level, pick_font_family

logger = logging.getLogger(__name__)

# Font sizes. Tuned together against the 1200x800 budget documented in
# the module docstring: the operator reads this from ~5m across the pit,
# so everything that matters mid-performance (小節・確信度・モード) stays
# large and the scaffolding (見出し・ヒント) shrinks to pay for it.
_HEADER_FONT_SIZE = 13      # console title + loaded file name
_MOVEMENT_FONT_SIZE = 28    # 「1曲目 / 全3曲」
_NEXT_MOVEMENT_FONT_SIZE = 16
_CARD_TITLE_FONT_SIZE = 12
_MEASURE_FONT_SIZE = 100
_MEASURE_TOTAL_FONT_SIZE = 30
_BEAT_FONT_SIZE = 26
_CONFIDENCE_FONT_SIZE = 40
_TRIGGER_FONT_SIZE = 22
_COOLDOWN_FONT_SIZE = 14
_WARN_FONT_SIZE = 14
_MIC_FONT_SIZE = 16
_MODE_FONT_SIZE = 22
_BUTTON_FONT_SIZE = 17
_HINT_FONT_SIZE = 12

# Palette. Cards are white on a light slate background so the groups read
# as separate blocks without drawing borders the operator has to parse.
_BG = "#eceff1"
_CARD_BG = "#ffffff"
_CARD_BORDER = "#cfd8dc"
_CARD_TITLE_FG = "#78909c"
_MUTED_FG = "#607d8b"

# Colour palette for the follower-mode panel, keyed by the status level
# ``audience_panel.resolve_status`` returns. Picked for high contrast from
# the operator's reading distance (~5m across the pit), and ordered the
# same way as the audience page's st-* classes (ui/audience/host.html) so
# the two screens grade a moment the same colour as well as the same word.
_MODE_COLORS = {
    "waiting": ("#888888", "white"),    # gray
    "acquiring": ("#a8811a", "white"),  # dark amber
    "tracking": ("#2a7", "white"),      # green
    "checking": ("#e87b00", "white"),   # orange
    "lost": ("#c00", "white"),          # red
}
_MODE_COLOR_FLASH = ("#2255ff", "white")      # blue bg for start-button flash

# Confidence number colours, keyed by ui.common.confidence_level so the
# number and the status word above it are graded by the same breakpoints.
_CONFIDENCE_COLORS = {"good": "green", "mid": "orange", "low": "red"}

# Window geometry. 1200x800 is the smallest screen this has to run on
# (Issue #51: at 1400x1000 the hint line fell off the bottom even
# maximised). minsize keeps the operator from shrinking it below the
# point where the big measure number starts getting clipped.
_WINDOW_GEOMETRY = "1180x760"
_WINDOW_MIN_SIZE = (1040, 700)
# Horizontal padding the wrapping labels sit inside (card padx + margin).
_WRAP_MARGIN_PX = 60

# dB step for the silence-threshold −/＋ buttons next to the mic level.
# Coarser than the ↑/↓ keys (±0.2 dB) — a click is a deliberate nudge,
# keys auto-repeat for fine adjustment.
_THRESHOLD_BUTTON_STEP_DB = 1.0


class FollowerGUI:
    """
    Operator console for Audio Score Follower.

    Displays playback status in real-time without blocking.
    """

    def __init__(
        self,
        root: tk.Tk,
        state: AppState,
        *,
        on_start: Optional[Callable[[], None]] = None,
        on_end: Optional[Callable[[], None]] = None,
        on_adjust_threshold: Optional[Callable[[float], None]] = None,
    ):
        """
        Initialize GUI.

        Args:
            root: tkinter root window
            state: Shared AppState object
            on_start: callback fired when the operator clicks
                the "▶ 演奏開始" button. Wired by ``main.py`` to
                ``AudioScoreFollowerApp.manual_start``. Optional;
                passes a no-op if omitted so the GUI can run standalone
                in tests.
            on_end: callback fired when the operator clicks the
                "■ 演奏終了" button. Wired by ``main.py`` to
                ``AudioScoreFollowerApp.end_performance`` (stops the
                follower so tracking/triggers halt). Optional; no-op if
                omitted.
            on_adjust_threshold: callback fired with a dB delta when
                the operator clicks the silence-threshold −/＋ buttons
                next to the mic level readout. Wired by ``main.py`` to
                ``AudioScoreFollowerApp.adjust_silence_threshold``
                (no-op in wav/loopback modes where no gate runs).
                Optional for standalone/test use.
        """
        self.root = root
        self.state = state
        self._on_start = on_start or (lambda: None)
        self._on_end = on_end or (lambda: None)
        self._on_adjust_threshold = on_adjust_threshold or (lambda _d: None)

        self.root.title("Audio Score Follower 操作コンソール")
        self.root.geometry(_WINDOW_GEOMETRY)
        self.root.minsize(*_WINDOW_MIN_SIZE)
        self.root.configure(bg=_BG)

        # Pick a font family that can actually render Japanese.  The previous
        # hard-coded "Arial" has no CJK glyphs, so Japanese filenames (e.g.
        # "運命_冒頭_guide.mxl") rendered as tofu boxes.
        self._font_family = pick_font_family(self.root)

        # Start button flash state (drives a 1s blue label immediately
        # after the button is pressed, then reverts to the regular
        # mode rendering).
        self._flash_until_ms: int = 0

        # Carries ``resolve_status``'s low-confidence hysteresis between
        # poll ticks (the audience panel keeps the same value in its
        # PanelView). Without it the 3s 見失い中 delay never elapses.
        self._status_low_since: Optional[float] = None

        # Labels whose wraplength follows the window width (long file
        # names, warning banners, the key-hint line). Without this the
        # hint line runs off both edges — the original Issue #51 report.
        self._wrapped_labels: List[tk.Label] = []
        self._wrap_width: int = 0

        # Create widgets
        self._create_widgets()

        self.root.bind("<Configure>", self._on_root_configure)

        # Start polling for state updates
        self._poll_state()

        logger.info("GUI initialized")

    # ------------------------------------------------------------ layout
    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        """A titled white card on the slate background.

        Returns the inner frame callers pack their content into; the
        1px border frame around it is already placed by the caller's
        pack/grid call on the returned widget's master.
        """
        outer = tk.Frame(parent, bg=_CARD_BORDER, padx=1, pady=1)
        inner = tk.Frame(outer, bg=_CARD_BG)
        inner.pack(fill="both", expand=True)
        if title:
            tk.Label(
                inner,
                text=title,
                font=(self._font_family, _CARD_TITLE_FONT_SIZE),
                bg=_CARD_BG,
                fg=_CARD_TITLE_FG,
            ).pack(anchor="w", padx=12, pady=(5, 0))
        # Callers need both: the frame to place, and the frame to fill.
        inner.outer = outer  # type: ignore[attr-defined]
        return inner

    def _create_widgets(self):
        """Create and layout tkinter widgets."""
        family = self._font_family

        # ----- ヘッダー: コンソール名（小さく）+ 読込中のファイル名 -----
        # Issue #51: the old 28pt "Sequential Live Follower" banner ate
        # vertical budget for information the operator never needs
        # mid-performance. It is now a caption, and the file name moved
        # up beside it instead of occupying a row of its own.
        header = tk.Frame(self.root, bg=_BG)
        header.pack(fill="x", padx=16, pady=(6, 2))

        tk.Label(
            header,
            text="Audio Score Follower 操作コンソール",
            font=(family, _HEADER_FONT_SIZE),
            bg=_BG,
            fg=_MUTED_FG,
        ).pack(side=tk.LEFT)

        self.label_file = tk.Label(
            header,
            text="[ファイル未読込]",
            font=(family, _HEADER_FONT_SIZE),
            bg=_BG,
            fg="#90a4ae",
            justify="right",
        )
        self.label_file.pack(side=tk.RIGHT)

        # ----- 曲カード: 「1曲目 / 全3曲」と「次の曲: …」 -----
        # 「次の曲」は N キーで送れる曲があるか、それとも最終曲で N が
        # 効かないのかを常時可視化する（押しても無反応で「壊れた?」と
        # 不安になるのを防ぐ）。
        movement_card = self._card(self.root, "")
        movement_card.outer.pack(fill="x", padx=16, pady=(0, 6))  # type: ignore[attr-defined]

        self.label_movement = tk.Label(
            movement_card,
            text="曲を読込中…",
            font=font.Font(family=family, size=_MOVEMENT_FONT_SIZE, weight="bold"),
            bg=_CARD_BG,
            fg="#263238",
        )
        self.label_movement.pack(anchor="w", padx=12, pady=(4, 0))

        self.label_next_movement = tk.Label(
            movement_card,
            text="次の曲: --",
            font=(family, _NEXT_MOVEMENT_FONT_SIZE),
            bg=_CARD_BG,
            fg=_MUTED_FG,
        )
        self.label_next_movement.pack(anchor="w", padx=12, pady=(0, 6))

        # ----- 警告バナー領域 -----
        # mismatch / mic effects / slide controller の 3 種をここに縦積み
        # する。個別の pack(before=...) をやめてこの器に集約したので、
        # 上流のレイアウトを変えても警告の出る位置が動かない。
        self.warn_frame = tk.Frame(self.root, bg=_BG)
        self.warn_frame.pack(fill="x", padx=16)

        # ずれ検知警告 — mismatch detector が「カウントが演奏からずれた疑い」
        # を立てている間だけ表示する。操作者は ←/→ で手動補正できる。
        # 毎フレーム pack/forget しない（差分時のみ。.claude/rules/ui.md）。
        self.label_mismatch = tk.Label(
            self.warn_frame,
            text="⚠ 追随ずれ疑い — ←/→ で補正可",
            font=(family, _WARN_FONT_SIZE, "bold"),
            bg="#c62828", fg="white", padx=12, pady=3,
        )
        self._mismatch_visible = False

        # マイクのノイズ抑制フィルター警告 — 起動時に一度だけ判定される
        # mic_effects_warning が非 None の間表示する（mismatch と同じ
        # 差分時のみ pack/forget パターン。.claude/rules/ui.md）。
        self.label_mic_effects = tk.Label(
            self.warn_frame,
            text="",
            font=(family, _WARN_FONT_SIZE, "bold"),
            bg="#e65100", fg="white", padx=12, pady=3,
            justify="left",
        )
        self._mic_effects_visible = False

        # SlideController 起動失敗警告 — 起動時に一度だけ判定される
        # slide_controller_warning が非 None の間表示する（mic_effects と同じ
        # 差分時のみ pack/forget パターン。.claude/rules/ui.md）。
        self.label_slide_warning = tk.Label(
            self.warn_frame,
            text="",
            font=(family, _WARN_FONT_SIZE, "bold"),
            bg="#e65100", fg="white", padx=12, pady=3,
            justify="left",
        )
        self._slide_warning_visible = False

        # ----- 本体: 左に現在位置、右に追随の質とトリガー -----
        body = tk.Frame(self.root, bg=_BG)
        body.pack(fill="both", expand=True, padx=16, pady=(6, 0))
        body.columnconfigure(0, weight=3, uniform="body")
        body.columnconfigure(1, weight=2, uniform="body")
        body.rowconfigure(0, weight=1)

        position_card = self._card(body, "現在位置（小節 / 拍）")
        position_card.outer.grid(row=0, column=0, sticky="nsew", padx=(0, 6))  # type: ignore[attr-defined]

        # 小節と拍は 1 つの塊としてカードの中央に置く（別々に pack すると
        # ウィンドウを縦に広げたとき数字と拍が離れて読みにくくなる）。
        position_center = tk.Frame(position_card, bg=_CARD_BG)
        position_center.pack(expand=True)

        measure_frame = tk.Frame(position_center, bg=_CARD_BG)
        measure_frame.pack()

        self.label_measure = tk.Label(
            measure_frame,
            text="--",
            font=font.Font(family=family, size=_MEASURE_FONT_SIZE, weight="bold"),
            bg=_CARD_BG,
            fg="#1565c0",
        )
        self.label_measure.pack(side=tk.LEFT)

        self.label_measure_total = tk.Label(
            measure_frame,
            text="/ --",
            font=(family, _MEASURE_TOTAL_FONT_SIZE),
            bg=_CARD_BG,
            fg="#246",
        )
        self.label_measure_total.pack(side=tk.LEFT, padx=(8, 0), anchor="s", pady=(0, 24))

        self.label_beat = tk.Label(
            position_center, text="♩ --", font=(family, _BEAT_FONT_SIZE),
            bg=_CARD_BG, fg="#468",
        )
        self.label_beat.pack(pady=(0, 6))

        right = tk.Frame(body, bg=_BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        conf_card = self._card(right, "追随の確信度")
        conf_card.outer.pack(fill="x")  # type: ignore[attr-defined]
        self.label_confidence = tk.Label(
            conf_card,
            text="--%",
            font=font.Font(family=family, size=_CONFIDENCE_FONT_SIZE, weight="bold"),
            bg=_CARD_BG,
            fg="gray",
        )
        self.label_confidence.pack(anchor="w", padx=12, pady=(0, 6))

        trigger_card = self._card(right, "トリガー（スライド送り）")
        trigger_card.outer.pack(fill="x", pady=(8, 0))  # type: ignore[attr-defined]
        self.label_prev_trigger = tk.Label(
            trigger_card, text="前: -- 小節目", font=(family, _TRIGGER_FONT_SIZE),
            bg=_CARD_BG, fg=_MUTED_FG,
        )
        self.label_prev_trigger.pack(anchor="w", padx=12)
        self.label_next_trigger = tk.Label(
            trigger_card, text="次: -- 小節目",
            font=font.Font(family=family, size=_TRIGGER_FONT_SIZE, weight="bold"),
            bg=_CARD_BG, fg="#263238",
        )
        self.label_next_trigger.pack(anchor="w", padx=12)
        self.label_cooldown = tk.Label(
            trigger_card, text="", font=(family, _COOLDOWN_FONT_SIZE),
            bg=_CARD_BG, fg="#ef6c00",
        )
        self.label_cooldown.pack(anchor="w", padx=12, pady=(0, 8))

        # ----- マイクカード -----
        # 確信度はマイク入力に直結するので、本体のすぐ下・モードパネルの
        # すぐ上という「必ず目に入る」位置に置く。閾値の −/＋ ボタンを併設
        # （Issue #41: ↑/↓ キーだけでは発見性が低い。wav/loopback では
        # コールバック側が no-op なので disable する）。
        mic_card = self._card(self.root, "")
        mic_card.outer.pack(fill="x", padx=16, pady=(6, 0))  # type: ignore[attr-defined]
        self.mic_frame = tk.Frame(mic_card, bg=_CARD_BG)
        self.mic_frame.pack(fill="x", padx=12, pady=6)

        self.label_mic_level = tk.Label(
            self.mic_frame, text="マイク: -- dBFS",
            font=(family, _MIC_FONT_SIZE), bg=_CARD_BG, fg="#444",
        )
        self.label_mic_level.pack(side=tk.LEFT)
        self.button_thr_up = tk.Button(
            self.mic_frame,
            text="＋ 閾値",
            font=(family, _HINT_FONT_SIZE),
            command=lambda: self._on_adjust_threshold(_THRESHOLD_BUTTON_STEP_DB),
            padx=6,
        )
        self.button_thr_up.pack(side=tk.RIGHT, padx=(4, 0))
        self.button_thr_down = tk.Button(
            self.mic_frame,
            text="− 閾値",
            font=(family, _HINT_FONT_SIZE),
            command=lambda: self._on_adjust_threshold(-_THRESHOLD_BUTTON_STEP_DB),
            padx=6,
        )
        self.button_thr_down.pack(side=tk.RIGHT, padx=(4, 4))
        # Track enabled/disabled so we only reconfigure on change
        # (100ms poll — .claude/rules/ui.md).
        self._thr_buttons_enabled = True

        # ----- 追随モード表示パネル + 演奏開始/終了ボタン -----
        # 「いま OLTW が音を追えているのか / 慣性で進めているのか / 慣性 cap
        # で止まっているのか」を運用者が一目で判別できるようにする。
        # waiting (lock-in 前) / tracking (通常) / inertia / capped の 4 状態を
        # 背景色付きラベルで明示。「▶ 演奏開始」ボタンは指揮者の振り出しに
        # 合わせて押すと OLTW を強制 lock-in する — lock-in 成立後は
        # no-op になるため `_render_follower_mode` で自動的に非表示に切り替える
        # （音楽が進行しているのに「演奏開始」ボタンが残っていると運用上
        # 違和感があるため）。
        self.mode_frame = tk.Frame(self.root, bg=_BG)
        self.mode_frame.pack(fill="x", padx=16, pady=(6, 0))

        self.label_mode = tk.Label(
            self.mode_frame,
            text="⏸ 待機中",
            font=(family, _MODE_FONT_SIZE, "bold"),
            bg=_MODE_COLORS["waiting"][0],
            fg=_MODE_COLORS["waiting"][1],
            padx=16, pady=8,
            anchor="w",
        )
        self.label_mode.pack(side=tk.LEFT, fill="x", expand=True)

        self.button_start = tk.Button(
            self.mode_frame,
            text="▶ 演奏開始",
            font=(family, _BUTTON_FONT_SIZE, "bold"),
            command=self._on_start_clicked,
            padx=12, pady=6,
        )
        self.button_start.pack(side=tk.RIGHT, padx=(8, 0))
        # Track current button visibility so we only call pack/forget when
        # the state actually changes (cheap; avoids spurious geometry work
        # on every 100ms poll tick).
        self._button_visible = True

        # 「■ 演奏終了」 button (Issue #44): the follower keeps tracking after
        # the music stops, so the operator needs an explicit halt. Shown
        # while the performance is running; hidden while waiting-for-start
        # and after the performance has ended. Packed to the RIGHT before
        # the start button so it sits inboard of it.
        self.button_end = tk.Button(
            self.mode_frame,
            text="■ 演奏終了",
            font=(family, _BUTTON_FONT_SIZE, "bold"),
            command=self._on_end_clicked,
            padx=12, pady=6,
        )
        self._end_button_visible = False

        # ----- キーヒント -----
        self.label_hints = tk.Label(
            self.root,
            text=(
                "スライド  → / Space: 進める   ←: 戻す　│　"
                "曲  N: 次の曲   R: 再ロード　│　"
                "演奏  L: 開始   E: 終了　│　"
                "無音閾値  ↑ / ↓: ±0.2 dB"
            ),
            font=(family, _HINT_FONT_SIZE),
            bg=_BG,
            fg="#90a4ae",
            justify="center",
        )
        self.label_hints.pack(fill="x", padx=16, pady=(5, 6))

        self._wrapped_labels = [
            self.label_file,
            self.label_mic_effects,
            self.label_slide_warning,
            self.label_hints,
        ]
        # Seed the wrap width from the default geometry. Waiting for the
        # first <Configure> would let a long warning banner ask for a
        # window wider than the screen before the event lands.
        self._apply_wrap_width(
            int(_WINDOW_GEOMETRY.split("x")[0]) - _WRAP_MARGIN_PX
        )

    def _on_root_configure(self, event: "tk.Event") -> None:
        """Keep wrapping labels inside the window as it is resized.

        Only fires work when the width actually changed — <Configure>
        also arrives for child widgets and for pure moves.
        """
        if event.widget is not self.root:
            return
        self._apply_wrap_width(int(event.width) - _WRAP_MARGIN_PX)

    def _apply_wrap_width(self, width: int) -> None:
        """Re-wrap the long labels, but only when the width really changed."""
        width = max(400, width)
        if width == self._wrap_width:
            return
        self._wrap_width = width
        for label in self._wrapped_labels:
            label.config(wraplength=width)

    # ----------------------------------------------------------- handlers
    def _on_start_clicked(self) -> None:
        """Button handler — forward to the application callback and
        trigger a brief blue flash on the mode label as visual
        confirmation that the press registered."""
        try:
            self._on_start()
        except Exception as exc:  # noqa: BLE001
            logger.error("force_lock_in callback raised: %s", exc, exc_info=True)
        # Schedule a 1-second flash so the operator sees the click landed.
        now_ms = int(self.root.tk.call("clock", "milliseconds"))
        self._flash_until_ms = now_ms + 1000

    def _on_end_clicked(self) -> None:
        """Button handler — forward to the end-performance callback. The
        'ended' mode is rendered on the next poll from the state flag, so
        no flash is needed here (the panel visibly changes to the stopped
        banner)."""
        try:
            self._on_end()
        except Exception as exc:  # noqa: BLE001
            logger.error("end_performance callback raised: %s", exc, exc_info=True)

    def _mode_text(self, state: dict, label: str, level: str) -> str:
        """Operator wording for a status the audience screen also shows.

        The LABEL comes from ``resolve_status`` and is never reworded —
        the operator has to be able to glance at the projector and read
        the same word. Everything in parentheses is operator-only detail
        (why we are in this state, which key gets out of it), which the
        audience must not see.
        """
        if state.get('performance_ended'):
            return f"⏹ {label}（演奏終了 — R で再追随 / N で次の曲）"
        if state.get('waiting_for_start'):
            return f"⏸ {label}（▶ 演奏開始 を押してください）"

        if level == "acquiring":
            if state.get('awaiting_first_sound', False):
                # Start pressed, performance not confirmed yet (Issue #41):
                # tell the operator what happens if the opening stays below
                # the gate threshold.
                timeout = float(state.get('start_gate_timeout_sec', 0.0))
                if timeout > 0:
                    return f"🎧 {label}（無音でも {timeout:.0f} 秒後に自動開始）"
                return f"🎧 {label}（閾値超えの音で開始）"
            return f"🎧 {label}"

        if state.get('is_in_inertia', False):
            elapsed = float(state.get('inertia_elapsed_sec', 0.0))
            cap = float(state.get('inertia_cap_sec', 10.0))
            if elapsed >= cap:
                return f"⛔ {label}（慣性停止・位置固定）　手動 → / L で復帰してください"
            remaining = max(0.0, cap - elapsed)
            return (
                f"🌀 {label}（慣性進行中　残り {remaining:.1f}s / {cap:.0f}s"
                f" — 音が戻れば自動復帰）"
            )
        if state.get('is_mismatched'):
            return f"⚠ {label}（追随ずれ疑い — ←/→ で補正可）"
        if level == "lost":
            return f"⚠ {label}（確信度が低いまま — ←/→ で補正可）"
        if level == "checking":
            return f"🔎 {label}（確信度が低下しています）"
        return f"🎵 {label}"

    def _render_follower_mode(self, state: dict) -> None:
        """Render the follower-mode panel based on the AppState snapshot.

        The status itself comes from ``audience_panel.resolve_status`` —
        the same function that drives the audience screen — so the two
        never disagree about whether we are 追随中 / 確認中 / 見失い中
        (Issue #51). This console only picks the icon, the colour and the
        operator-facing parenthetical. The one state that is ours alone is
        the 1s blue flash confirming the start button press.

        Also toggles the "▶ 演奏開始" button visibility: shown while
        waiting for the operator start and pre-lock-in (first press
        starts tracking; a second press force-arms lock-in at the
        downbeat). After lock-in the button is a no-op so we hide it;
        the L keybind is still available if the operator ever needs
        to re-arm after a reset.
        """
        now_ms = int(self.root.tk.call("clock", "milliseconds"))
        if now_ms < self._flash_until_ms:
            bg, fg = _MODE_COLOR_FLASH
            self.label_mode.config(text="🎯 開始を受け付けました", bg=bg, fg=fg)
            # Don't touch button visibility during the flash — it will be
            # re-evaluated on the next poll tick once the flash expires.
            return

        is_locked = state.get('is_locked_in', False)
        waiting_for_start = state.get('waiting_for_start', False)
        performance_ended = state.get('performance_ended', False)

        conf = state.get('display_confidence', state.get('confidence', 0.0))
        label, level, self._status_low_since = resolve_status(
            state,
            conf_level=confidence_level(float(conf)),
            now=time.monotonic(),
            low_since=self._status_low_since,
        )
        bg, fg = _MODE_COLORS[level]
        self.label_mode.config(text=self._mode_text(state, label, level), bg=bg, fg=fg)

        # Button visibility (差分時のみ pack/forget — .claude/rules/ui.md):
        #   ended            → neither (re-follow is R / N)
        #   waiting-for-start→ start only
        #   running          → end always; start while pre-lock-in so the
        #                      2nd press can still force lock-in
        # The "▶ 演奏開始" button hides after lock-in because it becomes a
        # no-op there; it re-shows if lock-in drops (e.g. reload via R).
        if performance_ended:
            show_start = False
            show_end = False
        elif waiting_for_start:
            show_start = True
            show_end = False
        else:
            show_start = not is_locked
            show_end = True

        # Both pack to the RIGHT of the mode label; relative order is
        # cosmetic (only pre-lock-in shows both at once).
        if show_end and not self._end_button_visible:
            self.button_end.pack(side=tk.RIGHT, padx=(8, 0))
            self._end_button_visible = True
        elif not show_end and self._end_button_visible:
            self.button_end.pack_forget()
            self._end_button_visible = False

        if show_start and not self._button_visible:
            self.button_start.pack(side=tk.RIGHT, padx=(8, 0))
            self._button_visible = True
        elif not show_start and self._button_visible:
            self.button_start.pack_forget()
            self._button_visible = False

    def update_display(self):
        """Update GUI with current state."""
        try:
            state = self.state.get_all()

            # 曲表示（例: 1曲目 / 全3曲）。config 上は movements だが、
            # 操作者が見るのは「何曲目か」なので表示はこちらに揃える。
            mv_num = state.get('movement_number', 1)
            mv_total = state.get('total_movements', 1)
            self.label_movement.config(text=f"{mv_num}曲目 / 全{mv_total}曲")

            # 「次の曲」インジケータ: 次があれば番号を、無ければ最終曲で
            # N が効かないことを明示する。
            if mv_num < mv_total:
                self.label_next_movement.config(
                    text=f"次の曲: {mv_num + 1}曲目（N キーで移動）", fg=_MUTED_FG
                )
            else:
                self.label_next_movement.config(
                    text="次の曲: なし（最終曲）", fg="#b0bec5"
                )

            # ファイル名 or ロードエラーメッセージ
            load_error = state.get('load_error')
            if load_error:
                self.label_file.config(text=f"⚠ {load_error}", fg="red")
            else:
                filename = state['xml_file'] or "[ファイル未読込]"
                if isinstance(filename, str):
                    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
                self.label_file.config(text=filename, fg="#90a4ae")

            # 小節番号（大きな数字）＋ /全小節数
            measure = state['measure']
            self.label_measure.config(text=str(measure))
            total = state.get('total_measures', 0)
            self.label_measure_total.config(text=f"/ {total}" if total > 0 else "/ --")

            # 拍位置
            beat_in_measure = state.get('beat_in_measure', 1.0)
            self.label_beat.config(text=f"♩ {int(beat_in_measure)}")

            # 確信度（色分け）— 表示は絶対コスト由来の display_confidence を
            # 使う。OLTW 内部の confidence は band 相対値で、無関係な音でも
            # 0.6-0.8 に張り付くため操作者を誤解させる(実測: 無関係なピアノ
            # BGM で内部 conf ~0.4-0.7 / display ~0)。内部値は lock-in・
            # トリガー床の判定用としてそのまま state に残っている。
            conf = state.get('display_confidence', state['confidence'])
            color = _CONFIDENCE_COLORS[confidence_level(float(conf))]
            self.label_confidence.config(text=f"{int(conf*100)}%", fg=color)

            # ずれ検知警告 — 差分時のみ pack/forget（.claude/rules/ui.md）
            mismatched = bool(state.get('is_mismatched'))
            if mismatched != self._mismatch_visible:
                self._mismatch_visible = mismatched
                if mismatched:
                    self.label_mismatch.pack(fill="x", pady=(0, 4))
                else:
                    self.label_mismatch.pack_forget()

            # マイクのノイズ抑制フィルター警告（起動時 one-shot 判定）
            mic_warning = state.get('mic_effects_warning')
            mic_warning_active = bool(mic_warning)
            if mic_warning_active != self._mic_effects_visible:
                self._mic_effects_visible = mic_warning_active
                if mic_warning_active:
                    self.label_mic_effects.config(text=mic_warning)
                    self.label_mic_effects.pack(fill="x", pady=(0, 4))
                else:
                    self.label_mic_effects.pack_forget()

            # SlideController 起動失敗警告
            slide_warning = state.get('slide_controller_warning')
            slide_warning_active = bool(slide_warning)
            if slide_warning_active != self._slide_warning_visible:
                self._slide_warning_visible = slide_warning_active
                if slide_warning_active:
                    self.label_slide_warning.config(text=slide_warning)
                    self.label_slide_warning.pack(fill="x", pady=(0, 4))
                else:
                    self.label_slide_warning.pack_forget()

            # トリガー（前 / 次）。「前」は現在位置以前で最も後ろのトリガー
            # 小節で、まだ 1 つも通過していなければ 1（= 冒頭のスライド）。
            # 操作者が「いまスライドは何枚目のはず」を逆算できるようにする。
            prev_trig = state.get('prev_trigger_measure')
            self.label_prev_trigger.config(
                text=f"前: {prev_trig} 小節目" if prev_trig else "前: -- 小節目"
            )
            next_trig = state['next_trigger_measure']
            if next_trig:
                self.label_next_trigger.config(text=f"次: {next_trig} 小節目")
            else:
                self.label_next_trigger.config(text="次: なし")

            # ----- 追随モード表示の更新 -----
            self._render_follower_mode(state)

            # クールダウン
            if state['cooldown_active']:
                self.label_cooldown.config(text="🔒 クールダウン中")
            else:
                self.label_cooldown.config(text="")

            # マイクレベル（実測 dBFS + 判定閾値を併記）
            mic_available = state.get('mic_monitor_available', False)
            mic_db = state.get('mic_level_db', -120.0)
            gate = state.get('silence_gate_active', False)
            threshold = state.get('silence_threshold_db')
            thr_part = (
                f"（閾値 {threshold:.1f}）" if threshold is not None else ""
            )
            if not mic_available:
                mic_text = "マイク: 監視無効（silence gate 無効）— ログを確認"
                mic_color = "#c60"
            elif gate:
                mic_text = f"マイク: {mic_db:.1f} dBFS{thr_part}  ⚠ 無音（閾値未満）"
                mic_color = "red"
            else:
                mic_text = f"マイク: {mic_db:.1f} dBFS{thr_part}  ✓ 入力検出"
                mic_color = "#2a7"
            self.label_mic_level.config(text=mic_text, fg=mic_color)

            # 閾値 −/＋ ボタンは gate が動くモード（マイク監視あり）でのみ
            # 有効。差分時のみ config（.claude/rules/ui.md）。
            if mic_available != self._thr_buttons_enabled:
                self._thr_buttons_enabled = mic_available
                btn_state = tk.NORMAL if mic_available else tk.DISABLED
                self.button_thr_down.config(state=btn_state)
                self.button_thr_up.config(state=btn_state)

        except Exception as e:
            logger.error(f"GUI update error: {e}")

    def _poll_state(self):
        """Poll state for updates every 100ms."""
        try:
            self.update_display()
        except Exception as e:
            logger.error(f"Polling error: {e}")

        # Schedule next poll
        self.root.after(100, self._poll_state)
