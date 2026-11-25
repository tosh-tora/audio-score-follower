#!/usr/bin/env python3
"""launch_options.py - Launch-time option resolution and persistence.

Pure logic shared by the CLI path (``main.py`` argparse) and the startup
launcher GUI (``ui/launcher.py``): option dataclass, validation, the
``--input-wav`` bare-filename resolution rule, and read/write of the
``settings.launcher`` block persisted into the selected config.json.

Deliberately imports neither tkinter nor sounddevice so unit tests can
exercise everything headlessly.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

INPUT_SOURCE_MIC = "mic"
INPUT_SOURCE_LOOPBACK = "loopback"
INPUT_SOURCE_WAV = "wav"
_VALID_INPUT_SOURCES = (INPUT_SOURCE_MIC, INPUT_SOURCE_LOOPBACK, INPUT_SOURCE_WAV)

# Directory that bare --input-wav filenames resolve into.
_REFERENCE_AUDIO_DIR = Path("data") / "reference_audio"

# Default margin (dB) for the 無音測定 threshold formula, and the sanity
# range the launcher accepts for the user-editable value. The formula
# already adapts to the venue's ambient spread via (median - p10);
# anything beyond ±20 dB of extra margin means a broken input, not
# tuning.
DEFAULT_SILENCE_MARGIN_DB = 2.0
SILENCE_MARGIN_DB_RANGE = (-20.0, 20.0)

# Default silence-gate threshold when the operator hasn't measured one.
# The threshold depends on the specific mic and venue, so it is NOT
# persisted to / read from config.json — it is measured per session in
# the launcher (無音測定) and passed to the app in memory. This constant
# is only the launcher form's starting value and the app's fallback.
DEFAULT_SILENCE_THRESHOLD_DB = -55.0

DeviceSpec = Union[int, str, None]


@dataclass
class LaunchOptions:
    """Everything needed to construct AudioScoreFollowerApp.

    ``input_source`` is the single source of truth for the input mode —
    there is no separate ``loopback`` flag, so the --input-wav ×
    --loopback conflict is structurally impossible here. main() derives
    ``loopback=(input_source == INPUT_SOURCE_LOOPBACK)`` for the app.

    ``silence_threshold_db`` is a per-session value measured in the
    launcher (無音測定) and handed to the app in memory — it is
    deliberately NOT persisted to / read from config.json because it
    depends on the specific mic and venue ambient level. None means "no
    session value" → the app uses ``DEFAULT_SILENCE_THRESHOLD_DB`` (CLI
    mode has no flag; adjust at runtime with ↑/↓). ``cooldown_seconds``
    is still a launcher-only config field (None = leave config untouched).

    ``input_wav`` is the CLI ``--input-wav`` (one recording for every
    movement). The launcher leaves it None: in wav mode each movement
    plays its own ``movements[].input_wav``, which the launcher's movement
    table writes into the config (``save_launcher_settings``). It may
    still be non-None while ``input_source`` is not "wav".
    Consumers MUST use ``effective_input_wav`` — passing a raw
    ``input_wav`` to the app while the source is mic silently launches
    file mode (auto-start, no silence gate), which manifested as
    「マイクを選んだのに監視無効・勝手に追随開始」.
    """

    config_path: Path
    slide_url: Optional[str] = None
    input_source: str = INPUT_SOURCE_MIC
    input_wav: Optional[Path] = None
    play_audio: bool = False
    mic_device: DeviceSpec = None
    loopback_device: DeviceSpec = None
    verbose: bool = False
    silence_threshold_db: Optional[float] = None
    cooldown_seconds: Optional[float] = None
    # Margin (dB) added by the 無音測定 threshold formula
    # (compute_silence_threshold). Launcher-only measurement tuning —
    # the app never reads it at runtime — persisted in the
    # settings.launcher block so a venue-specific calibration survives
    # relaunches (Issue #41).
    silence_margin_db: float = DEFAULT_SILENCE_MARGIN_DB
    # Operator/dev diagnostic: open the realtime feature/confidence
    # visualiser. Settable from both the CLI (--viz) and the launcher
    # checkbox, and persisted in the settings.launcher block.
    viz: bool = False

    @property
    def effective_input_wav(self) -> Optional[Path]:
        """The input_wav to actually run with (None unless wav mode)."""
        if self.input_source == INPUT_SOURCE_WAV:
            return self.input_wav
        return None


def default_config_dir() -> Path:
    """Locate the project's ``config/`` directory independent of CWD.

    The launcher is usually started as the ``asf-follow`` console script
    from an arbitrary working directory (a desktop shortcut, or a fresh
    terminal after a reboot). A bare ``Path("config")`` then resolves
    against that CWD and finds nothing, so every persisted launcher
    setting appears lost even though it is safely written in the
    project's ``config/*.json`` (Issue #7).

    Prefer a ``config/`` under the CWD when one actually exists (honours
    an intentional per-directory config set and preserves the old
    behaviour); otherwise fall back to the ``config/`` that ships at the
    repository root, located relative to this package.
    """
    cwd_config = Path("config")
    if cwd_config.is_dir():
        return cwd_config
    # launch_options.py lives at <repo>/audio_score_follower/, so parents[1]
    # is the repo root that holds the JSON config/ directory.
    pkg_config = Path(__file__).resolve().parents[1] / "config"
    if pkg_config.is_dir():
        return pkg_config
    # config/ is gitignored (it holds this operator's own production
    # config files), so a fresh checkout has neither directory yet.
    # Still return the absolute repo-root path rather than the bare
    # relative `cwd_config`: that relative path is exactly the CWD-
    # dependent behaviour Issue #7 exists to avoid, and callers already
    # tolerate a config_dir that doesn't exist yet (glob()/exists() on a
    # missing directory just come back empty).
    return pkg_config


def resolve_input_wav(raw: Optional[Path]) -> Optional[Path]:
    """Resolve a bare filename (no directory component) to data/reference_audio/."""
    if raw is None:
        return None
    if raw.parent == Path("."):
        return _REFERENCE_AUDIO_DIR / raw
    return raw


def coerce_device(value: DeviceSpec) -> DeviceSpec:
    """Coerce a numeric device string to an int index; keep names as-is."""
    if value is None or isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return value


def validate(
    opts: LaunchOptions, movement_rows: Optional[list["MovementRow"]] = None
) -> list[str]:
    """Return a list of Japanese error messages; empty when launchable.

    ``movement_rows``: the launcher's edited (not yet saved) movement
    table; None reads the rows from the config file (CLI path).
    """
    errors: list[str] = []
    if opts.config_path is None or not Path(opts.config_path).exists():
        errors.append(f"Config not found: {opts.config_path}")
    if opts.input_source not in _VALID_INPUT_SOURCES:
        errors.append(
            f"入力ソースが不正です: {opts.input_source!r} "
            f"(mic / loopback / wav のいずれか)"
        )
    if opts.config_path is not None and Path(opts.config_path).exists():
        errors.extend(_missing_movement_videos(Path(opts.config_path), movement_rows))
    if opts.input_source == INPUT_SOURCE_WAV:
        if opts.input_wav is not None:
            if not opts.input_wav.exists():
                errors.append(f"--input-wav not found: {opts.input_wav}")
        elif opts.config_path is not None and Path(opts.config_path).exists():
            errors.extend(_missing_movement_wavs(Path(opts.config_path), movement_rows))
    if opts.play_audio and opts.input_source != INPUT_SOURCE_WAV:
        errors.append("--play-audio は --input-wav と組み合わせて使用してください")
    lo, hi = SILENCE_MARGIN_DB_RANGE
    if not math.isfinite(opts.silence_margin_db) or not (
        lo <= opts.silence_margin_db <= hi
    ):
        errors.append(
            f"無音測定マージンが不正です: {opts.silence_margin_db!r} "
            f"({lo:.0f}〜{hi:.0f} dB の範囲で指定してください)"
        )
    return errors


def _rows_or_file(
    config_path: Path, rows: Optional[list["MovementRow"]]
) -> list["MovementRow"]:
    if rows is not None:
        return rows
    try:
        return read_movement_rows(config_path)
    except (ValueError, OSError):
        return []  # the launcher already shows config read errors


def _missing_movement_videos(
    config_path: Path, rows: Optional[list["MovementRow"]]
) -> list[str]:
    """Every playback movement needs its video, in every input mode."""
    errors = []
    for row in _rows_or_file(config_path, rows):
        if not row.is_playback:
            continue
        if not row.video:
            errors.append(f"楽章 {row.label}: 動画ファイルが指定されていません")
        elif not _resolve_against(config_path, row.video).is_file():
            errors.append(f"楽章 {row.label}: 動画ファイルが見つかりません: {row.video}")
    return errors


def _missing_movement_wavs(
    config_path: Path, rows: Optional[list["MovementRow"]]
) -> list[str]:
    """File-input mode without --input-wav: every movement needs its file.

    All movements, not just the first: a gap would only surface at the
    N press between movements, mid-rehearsal. A playback movement's
    recording is optional (blank = the video plays silently).
    """
    errors = []
    for row in _rows_or_file(config_path, rows):
        if row.is_playback and not row.input_wav:
            continue
        if not row.input_wav:
            errors.append(f"楽章 {row.label}: 音源ファイルが指定されていません")
        elif not _resolve_against(config_path, row.input_wav).is_file():
            errors.append(f"楽章 {row.label}: 音源ファイルが見つかりません: {row.input_wav}")
    return errors


def from_cli_args(args) -> LaunchOptions:
    """Map an argparse namespace (main.py parser) to LaunchOptions.

    The --input-wav × --loopback mutual exclusion must be checked by the
    caller BEFORE this mapping (both flags collapse into one enum here).
    """
    if args.input_wav is not None:
        input_source = INPUT_SOURCE_WAV
    elif args.loopback:
        input_source = INPUT_SOURCE_LOOPBACK
    else:
        input_source = INPUT_SOURCE_MIC
    return LaunchOptions(
        config_path=Path(args.config),
        slide_url=args.slide_url or None,
        input_source=input_source,
        input_wav=resolve_input_wav(args.input_wav),
        play_audio=args.play_audio,
        loopback_device=coerce_device(args.loopback_device),
        verbose=args.verbose,
        viz=getattr(args, "viz", False),
    )


# ---------------------------------------------------------------- persistence

_LAUNCHER_DEFAULTS = {
    "input_source": INPUT_SOURCE_MIC,
    "play_audio": False,
    "verbose": False,
    "viz": False,
    "mic_device_name": None,
    "loopback_device_name": None,
    "silence_margin_db": DEFAULT_SILENCE_MARGIN_DB,
}


def atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as UTF-8 JSON (ensure_ascii=False, indent=2) atomically.

    tempfile in the target directory + ``os.replace`` so a partial write
    can never corrupt an existing file; the temp file is removed on
    failure. Japanese strings are preserved (ensure_ascii=False); note
    the file's formatting is normalised to indent=2 on save. Shared by
    ``save_launcher_settings`` and ``ui.build_window.write_config``.
    """
    path = Path(path)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_launcher_settings(config_path: Path) -> dict:
    """Read launcher-relevant state from a config.json without validating it.

    Returns launcher defaults merged with ``settings.launcher`` plus the
    flat settings keys the launcher also edits (mic_device,
    loopback_device, silence_threshold_db, cooldown_seconds). Raises
    ValueError on JSON syntax errors (the launcher shows these early
    instead of letting ConfigLoader fail at app start). Movement-schema
    validation stays ConfigLoader's job.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON 構文エラー: {exc.msg} (行 {exc.lineno}, 列 {exc.colno})"
            ) from exc
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
    launcher = settings.get("launcher", {})
    if not isinstance(launcher, dict):
        launcher = {}
    merged = dict(_LAUNCHER_DEFAULTS)
    merged.update({k: launcher[k] for k in _LAUNCHER_DEFAULTS if k in launcher})
    merged["mic_device"] = settings.get("mic_device")
    merged["loopback_device"] = settings.get("loopback_device")
    # The silence threshold is mic/venue-dependent and no longer linked to
    # config: never read it back: the launcher form always starts at the
    # default and the operator re-measures per session.
    merged["silence_threshold_db"] = DEFAULT_SILENCE_THRESHOLD_DB
    merged["cooldown_seconds"] = settings.get("cooldown_seconds", 3.0)
    return merged


@dataclass
class MovementRow:
    """One row of the launcher's per-movement table (slide deck + recording).

    Blank values are None. ``is_video``: the movement is shown on the video
    layer, so it has no deck. ``is_playback``: it only plays one video,
    untracked — ``video`` is that file (shown in the slide URL column) and
    ``input_wav`` the optional recording played along with it.
    """

    label: str
    slide_url: Optional[str] = None
    input_wav: Optional[str] = None
    is_video: bool = False
    is_playback: bool = False
    video: Optional[str] = None


def _resolve_against(config_path: Path, raw: str) -> Path:
    # Same rule as ConfigLoader.resolve_path: relative to the config file.
    p = Path(raw)
    return p if p.is_absolute() else Path(config_path).resolve().parent / p


def _blank_to_none(value) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_movement_rows(config_path: Path) -> list[MovementRow]:
    """Read the per-movement slide URL / recording rows from a config.json.

    Raw JSON (no ConfigLoader validation), like read_launcher_settings.
    Migrates the pre-table launcher fields: a legacy ``settings.launcher``
    slide URL fills every slides movement without its own (it used to be
    their shared deck); a legacy recording fills the movement only when
    there is exactly one — in a multi-movement config it belonged to one
    movement, and guessing which would play the wrong music.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON 構文エラー: {exc.msg} (行 {exc.lineno}, 列 {exc.colno})"
            ) from exc
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    launcher = settings.get("launcher") if isinstance(settings.get("launcher"), dict) else {}
    movements = data.get("movements", [])
    if not isinstance(movements, list):
        return []
    movements = [m for m in movements if isinstance(m, dict)]
    legacy_url = _blank_to_none(launcher.get("slide_url"))
    legacy_wav = _blank_to_none(launcher.get("input_wav")) if len(movements) == 1 else None

    rows = []
    for i, m in enumerate(movements):
        if "video" in m:  # playback movement (config.loader.is_playback_movement)
            rows.append(MovementRow(
                label=f"{m.get('id', i + 1)}: 動画 {Path(str(m['video'])).stem}",
                input_wav=_blank_to_none(m.get("input_wav")),
                is_video=True,
                is_playback=True,
                video=_blank_to_none(m.get("video")),
            ))
            continue
        triggers = m.get("triggers") if isinstance(m.get("triggers"), list) else []
        is_video = any(isinstance(t, dict) and "video" in t for t in triggers)
        score = Path(str(m["xml_file"])).stem if m.get("xml_file") else "楽譜自動検出"
        rows.append(MovementRow(
            label=f"{m.get('id', i + 1)}: {score}",
            slide_url=None if is_video else (_blank_to_none(m.get("slide_url")) or legacy_url),
            input_wav=_blank_to_none(m.get("input_wav")) or legacy_wav,
            is_video=is_video,
        ))
    return rows


def save_launcher_settings(
    config_path: Path,
    opts: LaunchOptions,
    *,
    mic_device_name: Optional[str] = None,
    loopback_device_name: Optional[str] = None,
    movement_rows: Optional[list[MovementRow]] = None,
) -> None:
    """Persist launcher selections back into the selected config.json.

    Raw read → update → atomic write so unrelated keys (movements,
    oltw_kwargs, unknown custom keys) survive untouched. Device indexes
    go into the existing flat keys the app already consumes; only the
    key for the active input_source is written so a stale device of the
    inactive mode is never clobbered. Japanese strings are preserved
    (ensure_ascii=False); note the file's formatting is normalised to
    indent=2 on save.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    settings = data.setdefault("settings", {})
    # Rows (read_movement_rows, as edited) go to movements[i].slide_url /
    # input_wav, where the app reads them; blank removes the key. The
    # legacy launcher-level slide URL / recording is not written back.
    if movement_rows is not None:
        movements = [m for m in data.get("movements", []) if isinstance(m, dict)]
        if len(movements) != len(movement_rows):
            raise ValueError(
                "楽章の数が読み込み時から変わっています。設定ファイルを選び直してください"
            )
        for movement, row in zip(movements, movement_rows):
            if row.is_playback:
                # Its video takes the deck's place; a blank one is refused
                # by validate() before saving, and never erased here.
                if _blank_to_none(row.video):
                    movement["video"] = row.video.strip()
            for key, value in (("slide_url", row.slide_url), ("input_wav", row.input_wav)):
                value = _blank_to_none(value)
                if value is None or (key == "slide_url" and row.is_video):
                    movement.pop(key, None)
                else:
                    movement[key] = value
    settings["launcher"] = {
        "input_source": opts.input_source,
        "play_audio": bool(opts.play_audio),
        "verbose": bool(opts.verbose),
        "viz": bool(opts.viz),
        "mic_device_name": mic_device_name,
        "loopback_device_name": loopback_device_name,
        "silence_margin_db": float(opts.silence_margin_db),
    }
    if opts.input_source == INPUT_SOURCE_MIC:
        settings["mic_device"] = opts.mic_device
    elif opts.input_source == INPUT_SOURCE_LOOPBACK:
        settings["loopback_device"] = opts.loopback_device
    # The silence threshold is mic/venue-dependent and no longer persisted.
    # Actively drop any legacy value so a config carried between venues can
    # never silently reapply a stale threshold (it is passed to the app in
    # memory instead — see LaunchOptions.silence_threshold_db).
    settings.pop("silence_threshold_db", None)
    if opts.cooldown_seconds is not None:
        settings["cooldown_seconds"] = opts.cooldown_seconds

    atomic_write_json(Path(config_path), data)
    logger.info("Launcher settings saved to %s", config_path)


# ---------------------------------------------------------- silence threshold

# Minimum number of dBFS samples for a trustworthy estimate (~2s at the
# launcher's 60ms poll rate).
MIN_SILENCE_SAMPLES = 30

@dataclass
class SilenceMeasurement:
    """Result of an ambient-noise measurement for the silence gate."""

    threshold_db: float
    median_db: float
    p10_db: float
    count: int


def compute_silence_threshold(
    samples_db, margin_db: float = DEFAULT_SILENCE_MARGIN_DB
) -> SilenceMeasurement:
    """Derive silence_threshold_db from ambient-noise dBFS samples.

    The gate freezes OLTW when level <= threshold, so the threshold must
    sit ABOVE (nearly) the whole ambient distribution. Formula::

        threshold = median + (median - p10) + margin_db

    The upper spread is estimated by mirroring the LOWER half of the
    distribution: incidental sounds during measurement (coughs, chairs,
    nearby talk) only contaminate the upper tail, so median and p10 stay
    clean while a direct high percentile would not. The (median - p10)
    term adapts the margin to how much the venue's ambient level
    fluctuates (HVAC cycling etc.).

    There is deliberately NO floor on the spread (Issue #19): a 6 dB
    floor put the threshold at median+9dB in steady rooms, and quiet
    playing never crossed it — the gate stayed shut and tracking never
    started, which is the fatal failure now that manual start + the
    one-shot gate governance (Issue #13) already suppress false starts.
    A spurious open only matters in the short window between the start
    press and the first real sound, and still requires
    ``gate_activation_sec`` of continuous level above threshold.

    Non-finite samples (monitor not yet delivering) are dropped. Raises
    ValueError when fewer than MIN_SILENCE_SAMPLES remain.
    """
    arr = np.asarray(
        [s for s in samples_db if math.isfinite(s)], dtype=float
    )
    if arr.size < MIN_SILENCE_SAMPLES:
        raise ValueError(
            f"サンプル不足 (n={arr.size}, 最低 {MIN_SILENCE_SAMPLES}) — "
            f"2 秒以上測定してください"
        )
    median = float(np.percentile(arr, 50))
    p10 = float(np.percentile(arr, 10))
    threshold = median + (median - p10) + margin_db
    threshold = min(max(threshold, -120.0), 0.0)
    return SilenceMeasurement(
        threshold_db=round(threshold, 1),
        median_db=median,
        p10_db=p10,
        count=int(arr.size),
    )


def rematch_device(
    stored_index: DeviceSpec,
    stored_name: Optional[str],
    devices: list[tuple[int, str]],
) -> Optional[int]:
    """Re-match a persisted device against the current enumeration.

    Windows reshuffles device indexes when USB devices move, so the name
    snapshot wins: an exact name match returns that (possibly new)
    index. Otherwise the stored index is accepted only when its current
    name still matches (or no snapshot was stored). None = fall back to
    the OS default device.
    """
    if stored_name is not None:
        for idx, name in devices:
            if name == stored_name:
                return idx
    if isinstance(stored_index, int):
        by_index = dict(devices)
        if stored_index in by_index and (
            stored_name is None or by_index[stored_index] == stored_name
        ):
            return stored_index
    return None
