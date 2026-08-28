# 入力モードとランチャー

マイク / ファイル / ループバックの入力経路、起動ランチャー GUI、無音測定、NC 検出。
CLI フラグと config スキーマは README.md が正本。

## 入力モードとランチャー

CLI フラグと config スキーマの詳細は **README.md が正本**。ここには設計上の制約だけ置く。

- **`--loopback` と `--input-wav` は相互排他**（どちらも実 OLTW 入力源だから）。`--play-audio` は `--input-wav` が前提。どちらも `main.py` が起動時に検証してエラーにする
- ループバックは `sd.InputStream(..., extra_settings=sd.WasapiSettings(loopback=True))`。**Windows WASAPI 専用**
- **無音判定閾値 `silence_threshold_db` は config に保存しない**（マイク・会場依存のセッション値）。ランチャーで測った値を constructor 引数でメモリ渡しし、`save_launcher_settings` は旧キーを `pop` で除去する。CLI モードは `DEFAULT_SILENCE_THRESHOLD_DB` にフォールバックして実行時に ↑/↓ で調整。`ConfigLoader.get_silence_threshold_db()` は**廃止**（config から読む経路自体を消した）
- **CLI モード（config 引数あり）は `settings.launcher` を無視する**（後方互換のための意図的な仕様。README にも明記済み）
- `launch_options.py` は Tk / sounddevice を import しない純ロジック層。CLI とランチャーの検証を共有する（`tests/test_launch_options.py` でヘッドレステスト可能）
- config の保存は raw JSON の read→update→write（`ConfigLoader` 経由にしない — movements 検証で保存がブロックされるため）。`ensure_ascii=False` / `indent=2` / tempfile + `os.replace` でアトミック
- デバイスは index + 名前スナップショットの両方を保存し、次回起動時に名前一致で index を再マッチする（Windows はデバイス番号が変動するため）

> **触ってはいけない**: **`LaunchOptions.input_wav` を直接 app に渡さない。** ランチャーは wav 欄の前回値をソース切替後も保持・保存するため、`input_source=mic` でも `input_wav` が非 None になり得る。必ず `opts.effective_input_wav`（wav モード以外は None）を使う。渡すと wav モードで起動し「マイクを選んだのに監視無効・ボタンなしで自動追随」になる（実際に発生したバグ）

### 無音測定ボタン

閾値式 `median + (median − p10) + margin_db`（既定 2dB）。**式は据え置き**、調整は `margin_db`（`settings.launcher.silence_margin_db`、範囲 −20〜+20）で行う。閾値そのものは永続化せず毎セッション測り直す前提。

式の設計根拠・スプレッド床を撤廃した理由は → [calibration.md](calibration.md#7-無音判定閾値の式)、代替案が不採用になった経緯は → [experiments.md](experiments.md#無音判定--silence-gate)

**入力ソースの選択にかかわらず常に押せる**（測定は常にマイクデバイスから）。ソース連動の disable は混乱を生んだ実績があるため**再導入しないこと**。

### マイクの NC（ノイズ抑制）フィルター検出

特徴量は**未加工のマイク信号**を前提とする。OS/ドライバの NC が掛かると chroma と attack envelope の両方が歪み、追随品質が黙って劣化する。

- **検出**: WinRT `AudioEffectsManager` を pywinrt 経由で呼び、適用効果を照会。MediaCategory は `Other` / `Media` / `Communications` の 3 つ全てを問い合わせ、いずれかで検出されれば警告（安全側）
- **無効化はプログラムからは実質不可**（4 経路調査済み・実装しない → [experiments.md](experiments.md#マイク-nc-の無効化)）。**検出して警告し `ms-settings:sound` を開くボタンで手動オフを促す**方針で確定
- **統合箇所**: ランチャーの「NCチェック」ボタンと `main._check_mic_effects()`（マイクモード起動時に一度だけ、`AppState.mic_effects_warning` → GUI 警告バナー）
- **検出の限界（解消不能）**: ①マイク/ヘッドセット**内蔵**のハードウェア DSP はどの Windows API からも見えない ②サードパーティ仮想マイク（NVIDIA Broadcast、Krisp 等）はデバイス名ヒューリスティックでしか警告できない。この 2 点は**警告のみ**にとどめ、ブロッキングエラーにしない（偽陽性で起動不能になり得るため、誤検知より見逃しの方が安全）
- pywinrt 未インストール・非 Windows・デバイス不一致では `probe_available=False` で**警告なしに degrade**する
- CLI 単体診断: `python -m audio_score_follower.core.mic_effects_probe [device]`
