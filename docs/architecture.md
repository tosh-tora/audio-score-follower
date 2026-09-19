# アーキテクチャと全体像

このシステムが何をどう実現しているかの地図。個別の深掘りは
[oltw.md](oltw.md) / [features.md](features.md) /
[offline-build.md](offline-build.md) / [launcher.md](launcher.md) を参照。

## このシステムが実現すること（目的）

**本番のオーケストラ演奏をマイクでリアルタイム追跡し、指定小節に到達したら Google Slides を自動でページ送りして聴衆向けの解説を表示する。** 追跡アルゴリズム（OLTW）はこの目的のための手段であり、最終出力はスライド操作である。

```
マイク → OLTW 追随 → 小節番号 → トリガー判定 (core/trigger_engine.py)
      → SlideController (Playwright/Chromium) → Google Slides にキー送出
AppState → ui/audience_panel.py → SlideController → 聴衆向け画面の右パネル
```

## このプロジェクトのコア発想

`live-score-sync` で使っていた pymatchmaker (matcher) はオーケストラの密音響では破綻する (暴走/停止/2x 先走り; Issue #28 系)。根本原因は **リファレンスがスコア合成波形 (単一音色) で、本番のオケ音響と特徴空間が乖離している** こと。

1. **オフライン**: スコア合成 WAV (music21 → MIDI → FluidSynth) と実演奏 (プロ録音 → リハ録音) を **synctoolbox の MrMsDTW** で対応付け、`warping_path` (score_time ↔ reference_time) を保存
2. **オンライン**: マイク入力を **同じリファレンス録音** に対して Online DTW で追随。出力 reference_time を warp で逆引きして score_time → 小節へ
3. **特徴量は CENS + onset の融合**（リポ名 `-onset` の由来）: 局所コストは chroma の cosine 距離と spectral-flux onset の絶対差の加重和。自己類似パッセージ（同和声の再現部・反復主題）は chroma を共有するが attack envelope は共有しないため、onset が曖昧性を解消する

本番 OLTW のリファレンスが「実演奏の音響」になるため特徴空間の SN 比が上がる。全工程 Windows ネイティブで完結する (WSL2 不要)。

## コード地図

| 触る目的 | 場所 |
|---|---|
| OLTW 本体・DP recurrence・lock-in / inertia 状態機械・mismatch 検知 | `core/oltw_follower.py` |
| 検出結果 `FollowResult` の構造 | 同上、ファイル上部 dataclass |
| 特徴量 (CENS + onset) の抽出・融合コスト | `core/feature_extractor.py`（**オフラインビルドと本番が共有する唯一の経路**） |
| ref_time ↔ score_time ↔ measure の変換・warp path 検証 | `core/warp_lookup.py` / `core/score_mapper.py` |
| マイク dBFS 監視 + silence 判定 | `core/audio_level.py` |
| マイクの NC（ノイズ抑制）フィルター検出 | `core/mic_effects_probe.py` |
| Tkinter 起動・composition root・キーバインド・silence-gate poll | `main.py`（各エンジンを配線する薄い orchestrator） |
| トリガー発火ループ・手動 →/← オーバライド | `core/trigger_engine.py` |
| OLTW 結果処理（小節マッピング・AppState 反映・viz push・表示確信度・ジャンプ検出） | `core/result_handler.py`（worker スレッドから毎フレーム呼ばれる） |
| 楽章ロードの純構築部（失敗は `MovementLoadError`） | `core/movement_loader.py` |
| 聴衆向け画面（`ui/audience/host.html`: 左に Slides `/embed` の iframe、右に追随パネル）の Playwright 操作。外部モニターへの全画面配置、キュー経由の thread-safe キー送出・パネル更新・1 枚目リセット | `core/slide_controller.py` |
| 聴衆向けパネルの表示内容（状態判定・確信度 1 秒更新・「人が調整！」）、モニター選択、`/embed` URL 変換（Tk / Playwright 非依存の純ロジック） | `ui/audience_panel.py` |
| トリガーの小節単位クールダウン | `core/cooldown_timer.py` |
| FluidSynth / SoundFont 検出の一本化 | `core/synth_locator.py`（**検出順を変えるときはここだけ触る**） |
| GUI ↔ ワーカースレッド間の atomic 状態 | `core/state_manager.py` (`AppState`) |
| マイク / ファイル / WASAPI ループバックのスレッド管理 | `core/follower_worker.py` |
| config.json のパース・`oltw_kwargs` デフォルト | `config/loader.py` |
| GUI のレイアウト・モード表示・演奏開始ボタン | `ui/gui_tkinter.py` |
| UI 共通ユーティリティ（CJK フォント・ttk スタイル・確信度色の閾値） | `ui/common.py` |
| 起動ランチャー GUI（config 引数なし起動時） | `ui/launcher.py` |
| オフラインビルド GUI（`asf-build` をサブプロセス起動） | `ui/build_window.py` |
| 起動オプションの検証・CLI/ランチャー共通ロジック | `launch_options.py`（Tk/sounddevice 非依存の純ロジック） |
| リアルタイム可視化 `--viz` のデータ供給層 | `core/viz_feed.py`（**将来の観客用画面も同じ `VizFeed` を読む別描画クラスとして `ui/` に足す**） |
| 同上の描画層 | `ui/viz_window.py` |
| オフラインビルド（warp 検証・BPM 推定・前後トリム） | `cli/build_reference.py` + `core/reference_builder.py` |
| スコア合成 WAV 生成 | `tasks/generate_score_wav.py` |
| 追従品質のヘッドレス計測 | `tasks/eval_tracking.py` |
| 実験資産: 全域観測ベイズフィルタ（**本番不使用**、eval 専用） | `core/posterior_follower.py` → [experiments.md](experiments.md#全域観測ベイズフィルタ-posterior_follower) |

（パスはすべて `audio_score_follower/` 起点。`asf-follow` は `cli/follow.py` の薄い shim）

症状からの逆引き:

- 「**スライドが送られない / 二重に送られる**」→ `core/trigger_engine.py` と `core/cooldown_timer.py`、Playwright 側なら `core/slide_controller.py`
- 「**ボタンの挙動を変えたい**」→ `ui/gui_tkinter.py` 単発で済むことが多い
- 「**追随ロジックが暴走する**」→ `core/oltw_follower.py` の DP recurrence と band 計算
- 「**測度がずれる**」→ `core/warp_lookup.py` か `core/score_mapper.py`
- 「**マイクで動かない**」→ `core/audio_level.py` と `_check_silence_gate` (main.py)
- 「**warp path 検証が落ちる**」→ `core/warp_lookup.py` の `validate()` と、スコアの繰り返し構造
- 「**最終小節の数小節手前で頭打ち**」→ 参照録音の末尾無音トリム（下記オフラインビルド）

## トリガーシステム

- トリガーは `config.json` の `movements[].triggers[]` で定義: `{"measure": N, "action": "right"}`。action は `slide_controller._KEY_MAP` でブラウザキーに変換（未知の action はそのままキー名として送出）
- `TriggerEngine` の専用スレッドが `_TRIGGER_POLL_HZ` (20Hz) で現在小節を監視
- **発火条件（全て AND）**: ①小節一致 ②smoothed confidence ≥ `_TRIGGER_CONFIDENCE_FLOOR` (0.30) ③mismatch フラグが立っていない ④`CooldownTimer` が許可 ⑤その小節が未発火 ⑥`state.performance_ended` が False
- `_TRIGGER_CONFIDENCE_FLOOR` (0.30) が `lock_in_confidence` (0.45) より**意図的に低い**理由: 旧 InertiaEngine が担っていた「整列が安定するまで発火しない」ガードの代替で、起動直後の measure-1 誤発火だけ防げばよいため
- `--slide-url` 省略時は `NullSlideController`（no-op）で**ドライラン**起動
- 手動オーバライド: →/Space で次の未発火トリガーへ進めて発火済みにマーク + OLTW seek、← で直前の発火を取り消して戻る（発火順は小節順管理）

### 演奏終了（「■ 演奏終了」ボタン / E キー、Issue #44）

演奏が終わっても follower は追随を続け、拍手・環境音で小節が進みトリガーが出てしまう。押下で **OLTW ワーカーを停止**（`_stop_worker` でフレーム供給を絶つ）+ `state.performance_ended=True`。

**`freeze()` は使わない** — lock-in 後の freeze は慣性進行を始めてしまい停止にならない（→「二段構えの lock-in」）。当該楽章に対して終端的で、再追随は R（再ロード）/ N（次楽章）。フラグは `_load_movement` / `AppState.set_movement` でクリア。silence gate poll は `_performance_ended` 時に freeze/unfreeze を発行しない（レベル表示は継続）。

## GUI の状態反映パス

```
OLTW worker thread
  └─ OltwResultHandler.on_result (core/result_handler.py)
       ├─ state.update_beat_measure(...)
       ├─ state.set_confidence(...)
       └─ state.set_follower_mode(is_locked_in, is_in_inertia, ...)

GUI main thread (100ms poll)
  └─ FollowerGUI._poll_state → update_display → _render_follower_mode(state)
       ├─ mode ラベルの色/文字を切り替え
       └─ 「▶ 演奏開始」ボタンを is_locked_in に応じて pack/forget
```

手動スタートの待機状態は `state.waiting_for_start` で GUI に伝わり、`_render_follower_mode` が「⏸ 開始待ち」表示とボタンを制御する。

> **触ってはいけない #1**: `AppState` は複数スレッドから atomic に更新される。フィールドを足すときは `set_xxx()` メソッドを 1 つ追加して原子化する（直接代入は避ける）。`get_all()` の戻り辞書にも新フィールドを入れる
>
> **触ってはいけない #2**: GUI ボタンを毎フレーム pack/forget しない。100ms ごとに geometry recalc が走って重い。`_button_visible` フラグで差分時のみ操作する
