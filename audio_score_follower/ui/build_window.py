#!/usr/bin/env python3
"""ui/build_window.py - Offline reference-build screen (Issue #24).

Opened from the startup launcher (``ui/launcher.py``) so an operator can
run the offline build (``asf-build``) and generate a matching
``config.json`` entirely inside the GUI — no command line needed.

Design notes
------------
* The build itself runs as a **subprocess**
  (``python -m audio_score_follower.cli.build_reference``), not in-process.
  This mirrors the existing pattern where ``build_reference._synth_score_wav``
  spawns ``tasks/generate_score_wav.py``, keeps the heavy
  numba/librosa/synctoolbox imports out of the launcher Tk process, and
  isolates a build crash from the launcher. Its stdout+stderr are streamed
  line-by-line into a log pane.
* Tk is not thread-safe, so the subprocess reader runs on a daemon thread
  that only pushes lines into a ``queue.Queue``; the Tk side drains the
  queue from a ``after()`` poll.
* The command-assembly and config-generation logic is factored into pure
  functions (``build_command`` / ``generate_config_dict`` / ``write_config``)
  so they can be unit-tested headlessly (see tests/test_build_window.py),
  matching the launch_options.py split.
"""

from __future__ import annotations

import logging
import os
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import Optional

from audio_score_follower.launch_options import atomic_write_json
from audio_score_follower.ui.common import apply_base_style

logger = logging.getLogger(__name__)

_WINDOW_GEOMETRY = "960x820"

# CLI defaults (audio_score_follower/cli/build_reference.py). A tuning
# field is only appended to the command when it differs from these, to
# keep the invocation clean and let the CLI own the canonical default.
_DEFAULT_SAMPLE_RATE = 22050
_DEFAULT_HOP_LENGTH = 2048
_DEFAULT_CENS_WIN = 41

_BUILD_MODULE = "audio_score_follower.cli.build_reference"

_SCORE_FILETYPES = [
    ("楽譜ファイル", "*.mxl *.xml *.musicxml"),
    ("すべて", "*.*"),
]
_REFERENCE_FILETYPES = [
    ("音声ファイル", "*.wav *.mp3 *.flac *.ogg *.m4a"),
    ("すべて", "*.*"),
]


# ---------------------------------------------------------------- pure logic

# Movement-table rows shown before the table scrolls.
_MAX_VISIBLE_MOVEMENTS = 5


@dataclass
class MovementBuild:
    """One movement of the selected config, as the build screen shows it.

    ``output_name`` is the folder name under ``data/built/`` — the only
    place this screen builds into; None when the movement's built_dir
    lives elsewhere (it can be shown but not loaded into the form).
    ``reference`` / ``built_score`` come from the existing build's
    build_meta.json: None for builds made before they were recorded.
    """

    movement_id: object
    score: Optional[Path]
    built_dir: Path
    output_name: Optional[str]
    built: bool
    reference: Optional[Path] = None
    built_score: Optional[Path] = None


def read_build_meta_inputs(built_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    """(MusicXML, reference recording) an existing build was made from.

    Both None when the build predates their recording in build_meta.json
    or the file is missing / unreadable.
    """
    try:
        meta = json.loads((Path(built_dir) / "build_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    score = meta.get("score_path")
    reference = meta.get("reference_path")
    return (Path(score) if score else None), (Path(reference) if reference else None)


def config_movement_builds(config_path: Path, built_root: Path) -> list[MovementBuild]:
    """List the builds the movements of ``config_path`` point at.

    Raw JSON like the launcher (no ConfigLoader: a config whose builds are
    missing must still be listable — that is when you open this screen).
    Paths are resolved against the config file, as ConfigLoader does.
    Raises ValueError for unreadable JSON.
    """
    config_path = Path(config_path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 構文エラー: {exc.msg} (行 {exc.lineno})") from exc
    base = config_path.resolve().parent
    built_root = Path(built_root).resolve()
    rows = []
    movements = data.get("movements") if isinstance(data.get("movements"), list) else []
    for i, m in enumerate(movements):
        if not isinstance(m, dict) or not m.get("built_dir"):
            continue
        built_dir = (base / str(m["built_dir"])).resolve()
        score = (base / str(m["xml_file"])).resolve() if m.get("xml_file") else None
        built_score, reference = read_build_meta_inputs(built_dir)
        rows.append(MovementBuild(
            movement_id=m.get("id", i + 1),
            score=score,
            built_dir=built_dir,
            output_name=built_dir.name if built_dir.parent == built_root else None,
            built=(built_dir / "build_meta.json").is_file(),
            reference=reference,
            built_score=built_score,
        ))
    return rows


def overwrite_message(output_dir: Path) -> str:
    """Confirmation text for building into an existing output folder."""
    built_score, reference = read_build_meta_inputs(output_dir)
    lines = [f"{output_dir.name} は既にあります（{output_dir}）。"]
    if built_score or reference:
        lines.append("前回のビルドの入力:")
        lines.append(f"  楽譜: {built_score.name if built_score else '不明'}")
        lines.append(f"  参照録音: {reference.name if reference else '不明'}")
    else:
        lines.append("前回のビルドの入力は記録されていません（記録はこの機能の追加後のビルドから）。")
    lines.append("")
    lines.append("上書きしてビルドしますか？")
    return "\n".join(lines)

def build_command(
    score: Path,
    reference: Path,
    output: Path,
    *,
    python_exe: str = sys.executable,
    score_bpm: Optional[float] = None,
    start_offset: Optional[float] = None,
    end_trim: Optional[float] = None,
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    hop_length: int = _DEFAULT_HOP_LENGTH,
    cens_win: int = _DEFAULT_CENS_WIN,
    plot: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Assemble the ``asf-build`` subprocess argv.

    Absolute paths are used for --score/--reference/--output so the build
    does not depend on the subprocess CWD (the CLI leaves absolute paths
    unchanged; only bare filenames get the data/ prefix). Optional tuning
    flags are appended only when they deviate from the CLI default:

    * ``score_bpm`` None    → omit (CLI auto-estimates from the reference)
    * ``start_offset`` None → omit (CLI auto-detects head noise by
      comparing against the score synthesis); an explicit 0 IS passed
      (it means "disable trimming")
    * ``end_trim`` None     → omit (CLI auto-detects trailing silence);
      an explicit 0 IS passed (it means "disable trimming")
    """
    cmd = [
        python_exe, "-m", _BUILD_MODULE,
        "--score", str(Path(score).resolve()),
        "--reference", str(Path(reference).resolve()),
        "--output", str(Path(output).resolve()),
    ]
    if score_bpm is not None:
        cmd += ["--score-bpm", str(score_bpm)]
    if start_offset is not None:
        cmd += ["--start-offset", str(start_offset)]
    if end_trim is not None:
        cmd += ["--end-trim", str(end_trim)]
    if sample_rate != _DEFAULT_SAMPLE_RATE:
        cmd += ["--sample-rate", str(sample_rate)]
    if hop_length != _DEFAULT_HOP_LENGTH:
        cmd += ["--hop-length", str(hop_length)]
    if cens_win != _DEFAULT_CENS_WIN:
        cmd += ["--cens-win", str(cens_win)]
    if plot:
        cmd.append("--plot")
    if verbose:
        cmd.append("-v")
    return cmd


def _rel_or_abs(target: Path, base: Path) -> str:
    """Path of ``target`` relative to ``base`` in forward-slash form.

    Falls back to the absolute forward-slash path when the two live on
    different drives (Windows ``os.path.relpath`` raises ValueError). The
    forward-slash convention matches the existing hand-written configs
    (e.g. ``"../data/scores/foo.mxl"``); ConfigLoader resolves either via
    pathlib so both work at runtime.
    """
    target = Path(target).resolve()
    base = Path(base).resolve()
    try:
        rel = os.path.relpath(target, base)
    except ValueError:
        return target.as_posix()
    return Path(rel).as_posix()


def generate_config_dict(score: Path, built_dir: Path, config_dir: Path) -> dict:
    """Build a minimal, ConfigLoader-valid config dict for a fresh build.

    ``xml_file`` / ``built_dir`` are stored relative to ``config_dir`` so
    the generated file matches the project's existing layout. A single
    measure-1 trigger is scaffolded (note + empty author placeholder);
    the operator edits triggers afterwards.
    """
    return {
        "settings": {
            "cooldown_seconds": 3.0,
            "mic_device": None,
        },
        "movements": [
            {
                "id": 1,
                "xml_file": _rel_or_abs(score, config_dir),
                "built_dir": _rel_or_abs(built_dir, config_dir),
                "triggers": [
                    {
                        "measure": 1,
                        "action": "right",
                        "note": "開始（トリガーを編集してください）",
                        # Optional free text; scaffolded empty so the key is
                        # visible to an operator editing the file by hand.
                        # Empty stays out of the fire log (TriggerEngine
                        # appends author= only when non-empty).
                        "author": "",
                    }
                ],
            }
        ],
    }


def write_config(config_path: Path, config_dict: dict) -> None:
    """Atomically write ``config_dict`` to ``config_path`` (UTF-8, indent 2).

    Delegates to ``launch_options.atomic_write_json`` (tempfile +
    os.replace, ensure_ascii=False) so Japanese notes are kept and a
    partial write can never corrupt an existing config.
    """
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(config_path, config_dict)
    logger.info("Generated config written to %s", config_path)


# ------------------------------------------------------------------- window

class BuildWindow:
    """Toplevel offline-build screen. Set ``result`` before destroying.

    ``result`` is the Path of a generated config.json (or None) — the
    launcher uses it to pre-select the fresh config on return.
    """

    def __init__(
        self, root: tk.Tk, config_dir: Path, config_path: Optional[Path] = None
    ) -> None:
        self.root = root
        self.config_dir = Path(config_dir).resolve()
        # The config selected in the launcher: its movements are listed so
        # a rebuild is made knowing what it overwrites.
        self.config_path = Path(config_path) if config_path else None
        # config/ and data/ are siblings at the project root (existing
        # convention: built_dir is "../data/built/..." relative to config/).
        self.project_root = self.config_dir.parent
        self.data_dir = self.project_root / "data"
        self.result: Optional[Path] = None

        self._proc: Optional[subprocess.Popen] = None
        self._log_queue: "queue.Queue[tuple]" = queue.Queue()
        self._cancelled = False
        self._generated_config: Optional[Path] = None
        # True once the operator edits the config name manually (stops the
        # output→config-name auto-mirror in _on_output_changed).
        self._config_name_touched = False
        # Config path to generate after a successful build (None = skip).
        self._pending_config_path: Optional[Path] = None

        self.top = tk.Toplevel(root)
        self.top.title("オフラインビルド作成")
        self.top.geometry(_WINDOW_GEOMETRY)
        self._font, self._font_small = apply_base_style(self.top, font_source=root)

        self._movement_builds: list[MovementBuild] = []
        self._build_widgets()
        self._load_movement_table()
        self._on_gen_config_toggled()
        self.top.protocol("WM_DELETE_WINDOW", self._on_back)

    # -------------------------------------------------------------- widgets
    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 4}
        body = ttk.Frame(self.top, padding=10)
        body.pack(fill="both", expand=True)

        # --- movements of the selected config -----------------------------
        title = (
            f"選択中の config: {self.config_path.name}" if self.config_path
            else "選択中の config: なし"
        )
        frm_mv = ttk.LabelFrame(body, text=title)
        frm_mv.pack(fill="x", **pad)
        columns = ("movement", "score", "reference", "output")
        # Treeview rows do not grow with the font: without an explicit
        # rowheight the 12pt text overlaps the next row.
        linespace = tkfont.Font(root=self.top, font=self._font).metrics("linespace")
        ttk.Style(self.top).configure("Movements.Treeview", rowheight=linespace + 6)
        self.tree_movements = ttk.Treeview(
            frm_mv, columns=columns, show="headings", selectmode="browse",
            height=1, style="Movements.Treeview",
        )
        for col, text, width in (
            ("movement", "楽章", 50), ("score", "楽譜", 300),
            ("reference", "参照録音（前回のビルド）", 310), ("output", "出力名", 170),
        ):
            self.tree_movements.heading(col, text=text, anchor="w")
            self.tree_movements.column(col, width=width, anchor="w", stretch=col != "movement")
        mv_scroll = ttk.Scrollbar(frm_mv, orient="vertical", command=self.tree_movements.yview)
        self.tree_movements.configure(yscrollcommand=mv_scroll.set)
        self.tree_movements.grid(row=0, column=0, sticky="we", padx=(8, 0), pady=(4, 0))
        mv_scroll.grid(row=0, column=1, sticky="ns", pady=(4, 0))
        frm_mv.columnconfigure(0, weight=1)
        self.tree_movements.bind("<Double-1>", lambda _e: self._on_load_movement())
        frm_mv_btn = ttk.Frame(frm_mv)
        frm_mv_btn.grid(row=1, column=0, columnspan=2, sticky="we", padx=8, pady=4)
        self.button_load_movement = ttk.Button(
            frm_mv_btn, text="選択した楽章をフォームに読み込む",
            command=self._on_load_movement,
        )
        self.button_load_movement.pack(side="left")
        self.label_movements = ttk.Label(
            frm_mv_btn, text="", foreground="#888", font=self._font_small,
            wraplength=600, justify="left",
        )
        self.label_movements.pack(side="left", padx=(12, 0))

        # --- inputs ------------------------------------------------------
        frm_in = ttk.LabelFrame(body, text="入力")
        frm_in.pack(fill="x", **pad)
        frm_in.columnconfigure(1, weight=1)

        ttk.Label(frm_in, text="楽譜 (MusicXML/MXL):").grid(
            row=0, column=0, sticky="w", padx=8, pady=4
        )
        self.var_score = tk.StringVar()
        ttk.Entry(frm_in, textvariable=self.var_score).grid(
            row=0, column=1, sticky="we", padx=8, pady=4
        )
        ttk.Button(frm_in, text="参照…", command=self._browse_score).grid(
            row=0, column=2, padx=8, pady=4
        )
        ttk.Label(
            frm_in,
            text="※リピートを省略した参照録音に合わせる場合は、繰り返し記号を削除した MXL を選んでください",
            foreground="#888", font=self._font_small, wraplength=900, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=8)

        ttk.Label(frm_in, text="参照録音 (WAV/MP3/…):").grid(
            row=2, column=0, sticky="w", padx=8, pady=4
        )
        self.var_reference = tk.StringVar()
        ttk.Entry(frm_in, textvariable=self.var_reference).grid(
            row=2, column=1, sticky="we", padx=8, pady=4
        )
        ttk.Button(frm_in, text="参照…", command=self._browse_reference).grid(
            row=2, column=2, padx=8, pady=4
        )

        ttk.Label(frm_in, text="出力名:").grid(
            row=3, column=0, sticky="w", padx=8, pady=4
        )
        self.var_output = tk.StringVar()
        self.var_output.trace_add("write", self._on_output_changed)
        ttk.Entry(frm_in, textvariable=self.var_output).grid(
            row=3, column=1, sticky="we", padx=8, pady=4
        )
        ttk.Label(
            frm_in, text=f"→ {self.data_dir / 'built'} 以下に生成",
            foreground="#888", font=self._font_small,
        ).grid(row=4, column=1, sticky="w", padx=8)

        # --- advanced ----------------------------------------------------
        frm_adv = ttk.LabelFrame(body, text="詳細設定（未入力なら自動）")
        frm_adv.pack(fill="x", **pad)
        self.var_bpm = tk.StringVar()
        self.var_start = tk.StringVar()
        self.var_endtrim = tk.StringVar()
        self.var_cens = tk.StringVar(value=str(_DEFAULT_CENS_WIN))
        self.var_hop = tk.StringVar(value=str(_DEFAULT_HOP_LENGTH))
        self.var_sr = tk.StringVar(value=str(_DEFAULT_SAMPLE_RATE))
        self._adv_field(frm_adv, 0, 0, "定テンポ BPM (空=楽譜のテンポを自動伸縮):", self.var_bpm)
        self._adv_field(frm_adv, 0, 3, "先頭トリム start-offset (空=自動検出/0=無効):", self.var_start)
        self._adv_field(frm_adv, 1, 0, "末尾トリム end-trim (空=自動/0=無効):", self.var_endtrim)
        self._adv_field(frm_adv, 1, 3, "CENS 窓 cens-win:", self.var_cens)
        self._adv_field(frm_adv, 2, 0, "hop-length:", self.var_hop)
        self._adv_field(frm_adv, 2, 3, "sample-rate:", self.var_sr)
        self.var_plot = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm_adv, text="warp_path.png を出力 (--plot)", variable=self.var_plot
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        self.var_verbose = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm_adv, text="詳細ログ (-v)", variable=self.var_verbose
        ).grid(row=3, column=3, columnspan=3, sticky="w", padx=8, pady=4)

        # --- config generation ------------------------------------------
        frm_cfg = ttk.LabelFrame(body, text="config.json の生成")
        frm_cfg.pack(fill="x", **pad)
        frm_cfg.columnconfigure(1, weight=1)
        # Off by default: rebuilding a movement of an existing config is the
        # common case, and generating would replace that config with a
        # one-movement one. Tick it only when starting a new piece.
        self.var_gen_config = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm_cfg, text="ビルド成功後に対応する config を生成する",
            variable=self.var_gen_config, command=self._on_gen_config_toggled,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        ttk.Label(frm_cfg, text="config 名:").grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        self.var_config_name = tk.StringVar()
        self.entry_config_name = ttk.Entry(frm_cfg, textvariable=self.var_config_name)
        self.entry_config_name.grid(row=1, column=1, sticky="we", padx=8, pady=4)
        # Manual typing here stops the output→config-name auto-mirror.
        self.entry_config_name.bind(
            "<KeyRelease>", lambda _e: setattr(self, "_config_name_touched", True)
        )
        ttk.Label(frm_cfg, text=".json").grid(row=1, column=2, sticky="w", padx=(0, 8))

        # --- buttons -----------------------------------------------------
        frm_btn = ttk.Frame(body)
        frm_btn.pack(fill="x", pady=8)
        self.button_build = ttk.Button(
            frm_btn, text="ビルド実行", command=self._on_build
        )
        self.button_build.pack(side="right", padx=10, ipadx=16)
        self.button_back = ttk.Button(frm_btn, text="戻る", command=self._on_back)
        self.button_back.pack(side="right", padx=10)
        self.label_status = ttk.Label(frm_btn, text="", font=self._font_small)
        self.label_status.pack(side="left", padx=10)

        # --- log ---------------------------------------------------------
        frm_log = ttk.LabelFrame(body, text="ログ")
        frm_log.pack(fill="both", expand=True, **pad)
        self.text_log = tk.Text(
            frm_log, height=10, wrap="none", state="disabled",
            font=("Consolas", 10),
        )
        self.text_log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll = ttk.Scrollbar(frm_log, command=self.text_log.yview)
        scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)
        self.text_log.configure(yscrollcommand=scroll.set)

    def _adv_field(self, parent, row, col, label, var) -> None:
        ttk.Label(parent, text=label).grid(
            row=row, column=col, sticky="w", padx=8, pady=4
        )
        ttk.Entry(parent, textvariable=var, width=10).grid(
            row=row, column=col + 1, sticky="w", padx=(0, 8), pady=4
        )

    # ------------------------------------------------------- movement table
    def _load_movement_table(self) -> None:
        tree = self.tree_movements
        tree.delete(*tree.get_children())
        self._movement_builds = []
        if self.config_path is None:
            self._set_movement_note("ランチャーで config を選ぶと、その楽章のビルドがここに並びます")
            return
        try:
            self._movement_builds = config_movement_builds(
                self.config_path, self.data_dir / "built"
            )
        except (ValueError, OSError) as exc:
            self._set_movement_note(f"{self.config_path.name} を読めません: {exc}")
            return
        for i, mb in enumerate(self._movement_builds):
            reference = mb.reference.name if mb.reference else (
                "不明（記録なし）" if mb.built else "—")
            output = mb.output_name or str(mb.built_dir)
            if not mb.built:
                output += "（未ビルド）"
            tree.insert("", "end", iid=str(i), values=(
                mb.movement_id, mb.score.name if mb.score else "（自動検出）",
                reference, output,
            ))
        tree.configure(height=max(1, min(len(self._movement_builds), _MAX_VISIBLE_MOVEMENTS)))
        if self._movement_builds:
            tree.selection_set("0")
        self._set_movement_note(
            "読み込むと、その楽章のビルドを上書きする内容がフォームに入ります（ビルド実行時に上書き確認あり）"
        )

    def _set_movement_note(self, text: str) -> None:
        self.label_movements.configure(text=text)
        state = "normal" if self._movement_builds else "disabled"
        self.button_load_movement.configure(state=state)

    def _on_load_movement(self) -> None:
        selected = self.tree_movements.selection()
        if not selected:
            return
        mb = self._movement_builds[int(selected[0])]
        if mb.output_name is None:
            messagebox.showwarning(
                "読み込めません",
                f"この楽章のビルド先は {self.data_dir / 'built'} の外にあります:\n{mb.built_dir}\n"
                "この画面は data/built/ の下にしかビルドできません。",
                parent=self.top,
            )
            return
        self.var_score.set(str(mb.score) if mb.score else "")
        self.var_reference.set(str(mb.reference) if mb.reference else "")
        self.var_output.set(mb.output_name)
        # The config already points at this build: generating one would
        # replace it with a single-movement config.
        self.var_gen_config.set(False)
        self._on_gen_config_toggled()
        if mb.reference is None:
            messagebox.showinfo(
                "参照録音を選んでください",
                "この楽章の前回のビルドには参照録音が記録されていません。\n"
                "参照録音を選んでからビルドしてください（次回からは記録されます）。",
                parent=self.top,
            )

    # ----------------------------------------------------------- callbacks
    def _browse_score(self) -> None:
        initial = self.data_dir / "scores"
        chosen = filedialog.askopenfilename(
            parent=self.top, title="楽譜ファイルを選択",
            initialdir=str(initial if initial.exists() else self.project_root),
            filetypes=_SCORE_FILETYPES,
        )
        if chosen:
            self.var_score.set(chosen)

    def _browse_reference(self) -> None:
        initial = self.data_dir / "reference_audio"
        chosen = filedialog.askopenfilename(
            parent=self.top, title="参照録音を選択",
            initialdir=str(initial if initial.exists() else self.project_root),
            filetypes=_REFERENCE_FILETYPES,
        )
        if chosen:
            self.var_reference.set(chosen)

    def _on_output_changed(self, *_args) -> None:
        # Mirror the output name into the config name until the operator
        # edits the config name themselves.
        if not self._config_name_touched:
            self.var_config_name.set(self.var_output.get().strip())

    def _on_gen_config_toggled(self) -> None:
        state = "normal" if self.var_gen_config.get() else "disabled"
        self.entry_config_name.configure(state=state)

    # ------------------------------------------------------------- build
    def _resolved_output(self) -> Path:
        return (self.data_dir / "built" / self.var_output.get().strip()).resolve()

    def _collect_command(self) -> Optional[list[str]]:
        """Validate the form and return the subprocess argv, or None."""
        score = self.var_score.get().strip()
        reference = self.var_reference.get().strip()
        output = self.var_output.get().strip()
        if not score or not Path(score).exists():
            messagebox.showerror("入力エラー", "楽譜ファイルを選択してください", parent=self.top)
            return None
        if not reference or not Path(reference).exists():
            messagebox.showerror("入力エラー", "参照録音を選択してください", parent=self.top)
            return None
        if not output:
            messagebox.showerror("入力エラー", "出力名を入力してください", parent=self.top)
            return None

        def _opt_float(var):
            raw = var.get().strip()
            if not raw:
                return None
            return float(raw)

        try:
            score_bpm = _opt_float(self.var_bpm)
            start_offset = _opt_float(self.var_start)
            end_trim = _opt_float(self.var_endtrim)
            cens_win = int(self.var_cens.get().strip() or _DEFAULT_CENS_WIN)
            hop_length = int(self.var_hop.get().strip() or _DEFAULT_HOP_LENGTH)
            sample_rate = int(self.var_sr.get().strip() or _DEFAULT_SAMPLE_RATE)
        except ValueError:
            messagebox.showerror(
                "入力エラー", "詳細設定の数値が不正です", parent=self.top
            )
            return None

        return build_command(
            Path(score), Path(reference), self._resolved_output(),
            score_bpm=score_bpm, start_offset=start_offset, end_trim=end_trim,
            sample_rate=sample_rate, hop_length=hop_length, cens_win=cens_win,
            plot=bool(self.var_plot.get()), verbose=bool(self.var_verbose.get()),
        )

    def _on_build(self) -> None:
        if self._proc is not None:  # button is in 中止 state
            self._cancel_build()
            return
        cmd = self._collect_command()
        if cmd is None:
            return
        output_dir = self._resolved_output()
        if output_dir.exists() and any(output_dir.iterdir()) and not messagebox.askyesno(
            "上書き確認", overwrite_message(output_dir), parent=self.top,
        ):
            return
        if self.var_gen_config.get():
            name = self.var_config_name.get().strip()
            if not name:
                messagebox.showerror(
                    "入力エラー", "config 名を入力してください", parent=self.top
                )
                return
            config_path = (self.config_dir / f"{name}.json").resolve()
            if config_path.exists() and not messagebox.askyesno(
                "確認",
                f"{config_path.name} は既に存在します。上書きしますか？",
                parent=self.top,
            ):
                return
            self._pending_config_path = config_path
        else:
            self._pending_config_path = None

        self._start_build(cmd)

    def _start_build(self, cmd: list[str]) -> None:
        self._cancelled = False
        self._generated_config = None
        self._clear_log()
        self._append_log("$ " + " ".join(cmd) + "\n")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=str(self.project_root),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self._append_log(f"\n[起動失敗] {exc}\n")
            messagebox.showerror("起動エラー", f"ビルドを開始できません: {exc}", parent=self.top)
            self._proc = None
            return

        self.button_build.configure(text="中止")
        self.button_back.configure(state="disabled")
        self.label_status.configure(text="ビルド中…", foreground="#06c")
        threading.Thread(
            target=self._reader, args=(self._proc,), daemon=True
        ).start()
        self.top.after(100, self._poll_log)

    def _reader(self, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._log_queue.put(("line", line.rstrip("\n")))
        finally:
            proc.wait()
            self._log_queue.put(("done", proc.returncode))

    def _poll_log(self) -> None:
        if not self.top.winfo_exists():
            return  # window torn down (戻る during build); drop pending tick
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "line":
                    self._append_log(payload + "\n")
                elif kind == "done":
                    self._on_build_finished(payload)
                    return
        except queue.Empty:
            pass
        if self._proc is not None:
            self.top.after(100, self._poll_log)

    def _on_build_finished(self, returncode: int) -> None:
        self._proc = None
        self.button_build.configure(text="ビルド実行")
        self.button_back.configure(state="normal")
        if self._cancelled:
            self.label_status.configure(text="中止しました", foreground="#c60")
            self._append_log("\n[中止しました]\n")
            return
        if returncode != 0:
            self.label_status.configure(
                text=f"ビルド失敗 (exit {returncode})", foreground="#c00"
            )
            self._append_log(f"\n[ビルド失敗 exit {returncode}]\n")
            messagebox.showerror(
                "ビルド失敗",
                f"ビルドが失敗しました (exit {returncode})。ログを確認してください。",
                parent=self.top,
            )
            return
        self.label_status.configure(text="ビルド完了", foreground="#080")
        self._append_log("\n[ビルド完了]\n")
        self._maybe_generate_config()

    def _maybe_generate_config(self) -> None:
        config_path = self._pending_config_path
        if config_path is None:
            messagebox.showinfo(
                "完了",
                f"ビルドが完了しました。\n出力: {self._resolved_output()}",
                parent=self.top,
            )
            return
        try:
            cfg = generate_config_dict(
                Path(self.var_score.get().strip()),
                self._resolved_output(),
                self.config_dir,
            )
            write_config(config_path, cfg)
        except OSError as exc:
            self._append_log(f"\n[config 生成失敗] {exc}\n")
            messagebox.showwarning(
                "config 生成失敗",
                f"ビルドは成功しましたが config の生成に失敗しました: {exc}",
                parent=self.top,
            )
            return
        self._generated_config = config_path
        self._append_log(f"[config 生成] {config_path}\n")
        messagebox.showinfo(
            "完了",
            f"ビルドと config 生成が完了しました。\n"
            f"出力: {self._resolved_output()}\n"
            f"config: {config_path}\n\n"
            f"トリガー（小節→操作）は生成された config を編集してください。",
            parent=self.top,
        )

    def _cancel_build(self) -> None:
        if self._proc is None:
            return
        self._cancelled = True
        self.label_status.configure(text="中止中…", foreground="#c60")
        try:
            self._proc.terminate()
        except OSError as exc:
            logger.warning("Failed to terminate build process: %s", exc)

    # -------------------------------------------------------------- log io
    def _clear_log(self) -> None:
        self.text_log.configure(state="normal")
        self.text_log.delete("1.0", "end")
        self.text_log.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.text_log.configure(state="normal")
        self.text_log.insert("end", text)
        self.text_log.see("end")
        self.text_log.configure(state="disabled")

    # --------------------------------------------------------------- close
    def _on_back(self) -> None:
        if self._proc is not None:
            if not messagebox.askyesno(
                "確認", "ビルドを中止して戻りますか？", parent=self.top
            ):
                return
            self._cancel_build()
            # Wait for the process to actually exit before tearing down so
            # the reader thread doesn't touch a destroyed widget.
            try:
                self._proc.wait(timeout=5.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except OSError:
                    pass
            self._proc = None
        self.result = self._generated_config
        self.top.destroy()


def run_build_window(
    root: tk.Tk, config_dir: Path, config_path: Optional[Path] = None
) -> Optional[Path]:
    """Open the build screen modally over ``root``; return generated config.

    Hides the launcher root while the build screen is up (screen-to-screen
    navigation) and restores it on return. ``root.wait_window`` runs a
    local event loop until the Toplevel is destroyed. Returns the Path of
    a config.json generated during the session, or None.
    """
    window = BuildWindow(root, config_dir, config_path)
    root.withdraw()
    try:
        root.wait_window(window.top)
    finally:
        try:
            root.deiconify()
        except tk.TclError:
            pass
    return window.result
