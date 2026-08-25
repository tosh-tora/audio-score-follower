# Claude 開発ガイダンス (audio-score-follower-onset)

姉妹プロジェクト `live-score-sync` の方針を踏襲しつつ、本プロジェクト固有の事情を追記する。

このファイルは **別セッションの Claude が冷えた状態から仕様を把握するための地図**。載せるのは「どのファイルが何を担当しているか」「なぜそうなっているか」「触ってはいけない箇所」の 3 つだけに絞る。

**書き分けルール（このファイルを編集するときも守る）**:

| 内容 | 正本 |
|---|---|
| CLI・運用手順・config スキーマ（ユーザー向けの使い方） | `README.md` |
| 閾値の校正根拠・実測値 | [`docs/calibration.md`](docs/calibration.md) |
| 試して捨てた案 | [`docs/experiments.md`](docs/experiments.md) |
| 規則・制約・コード地図 | このファイル |

CLAUDE.md は毎セッション自動ロードされる。ここに実測値や実験ログを書き足すと全セッションのコンテキストを食うので、**数値は docs/ に置いてここからリンクする**。

**制約の一覧が欲しいときは `触ってはいけない` で grep する。** 索引は作らない（本文と二重管理になるため）。

## このシステムが実現すること（目的）

**本番のオーケストラ演奏をマイクでリアルタイム追跡し、指定小節に到達したら Google Slides を自動でページ送りして聴衆向けの解説を表示する。** 追跡アルゴリズム（OLTW）はこの目的のための手段であり、最終出力はスライド操作である。

```
マイク → OLTW 追随 → 小節番号 → トリガー判定 (core/trigger_engine.py)
      → SlideController (Playwright/Chromium) → Google Slides にキー送出
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
| Google Slides 自動操作（Playwright、キュー経由の thread-safe キー送出） | `core/slide_controller.py` |
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
| 実験資産: 全域観測ベイズフィルタ（**本番不使用**、eval 専用） | `core/posterior_follower.py` → [experiments.md](docs/experiments.md#全域観測ベイズフィルタ-posterior_follower) |

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

## OLTW の状態機械（最重要・別セッション必読）

これを理解せずに触ると lock-in / inertia の安全弁を壊しやすい。

### 二段構えの lock-in

| 段階 | `_locked_in` | `freeze()` の意味 |
|---|---|---|
| 冒頭・初期化中 | `False` | **位置固定**。冒頭ノイズで誤った位置から慣性外挿しないため |
| 曲の開始を捉えた以降 | `True` | **慣性進行**（直近 rate で位置を前進） |

lock-in 判定:

- `_live_frame_idx > init_search_width`（既定 30 フレーム = 冒頭探索完了）
- かつ **smoothed** confidence ≥ `lock_in_confidence` (0.45) が `lock_in_frames` (30) 連続成立
- **単調ラッチ**（一度立てたら降りない。降ろすには `reset()` のみ）
- 「▶ 演奏開始」/ L キーで強制的に立てられるが、**マイクモードの初回押下は例外**（→「silence gate / 手動スタート」）

### 慣性 (inertia) のトリガー

**`freeze()` のみ** が慣性入りのトリガー。lock-in 後に `freeze()` されると `_inertia_active=True` になり、以後 `process_frame()` は DP を裏で走らせつつ表示位置を慣性で前進させる。

**低 DP confidence 単独では慣性に入らない。絶対に追加しない** — 別演奏カバレッジが 100% → 34% に落ちる regression を実測済み（→ [experiments.md](docs/experiments.md#低-conf-自動-inertia-入り)）。

### `_current_ref_pos` / `_inertia_ref_pos` / `_display_ref_pos` の三層分離

- **`_current_ref_pos`**（int）: DP-owned anchor。DP の更新ロジックのみが変更する
- **`_inertia_ref_pos`**（float）: 慣性中の表示位置。`_advance_inertia()` のみが変更する
- **`_display_ref_pos`**（float）: **表示スルー層**。通常追従中、表示が DP 位置を追う速度を `max(display_min_advance, rate × display_slew_factor)` frame/frame に制限する出力段レートリミッタ。stall 後の DP キャッチアップが表示上のテレポートにならない。フレーム駆動なので live を追い越せない（慣性の安全弁 #1 と同じ論法）。`display_slew_factor: 0` で無効化
- **公開プロパティ `current_ref_frame`**: 慣性 active なら `int(_inertia_ref_pos)`、slew 有効なら `int(_display_ref_pos)`、それ以外は `_current_ref_pos`。生 DP 位置は `dp_ref_frame`

**snap-vs-slew ルール**: `reset()` / `seek()` / 初期アライメント / global rematch / post-seek catchup / 慣性 resync という**意図的テレポートでは表示も即スナップ**する。スルーがかかるのは通常追従の DP 前進のみ。frozen 中・慣性中のフレームでは `_display_ref_pos` を出力値に同期させ stale gap を残さない。`freeze()` の慣性開始位置は（slew 有効時）`_display_ref_pos` から始める — DP anchor から始めると freeze 境界で表示が前方ジャンプする。

**低 conf 適応キャップ** `low_conf_advance_frames` は既定 0（無効）。実測で有意差がなかったため（→ [experiments.md](docs/experiments.md#low_conf_advance_frames-低-conf-適応キャップ)）。

> **触ってはいけない**: `_advance_inertia()` で `_current_ref_pos` を書き換えると DP の band がずれ、stuck_dp_reset 後の D_prev 初期化が狂って DP が壊れる。過去にこの罠にハマった。

### 慣性中の安全弁

`live-score-sync` の `inertia_engine.py` は「テンポ外挿が演奏を追い越して measure 1 に snap back する」regression で捨てられた。本実装はこれを構造的に防ぐ 4 つの安全弁を持つ:

1. **物理上限**: live frame 1 つにつき ref を `+inertia_rate` だけ進める。経過時間ではなくフレーム数駆動なので、構造的に live より速くなれない
2. **rate の制限**: clamp `[0.3, 2.0]`、`_last_good_rate` キャッシュで quick re-entry にも安定
3. **絶対 cap**: `max_inertia_seconds` (10.0) を越えたら位置固定に戻す
4. **慣性中は `_try_global_rematch` を抑制**: スコア全体探索による遠方ジャンプを禁止（自己類似テーマへの誤テレポート防止）。`if not self._inertia_active` ガードを必ず維持する

> **触ってはいけない**: 経過時間ベースのテンポ外挿に戻さない。`live-score-sync` の `inertia_engine.py` を踏襲したくなる衝動は捨てること（→ [experiments.md](docs/experiments.md#live-score-sync-の-inertia_engine-踏襲)）。`_pos_history` が clear されて rate が 1.0 fallback すると overshoot が蓄積し、resync gap > `search_width` で永続復帰不能になる — `_last_good_rate` キャッシュがこれを救済している

### stuck_dp_reset と rapid_dp_reset

後退アトラクタ（`D_prev[pos-1] < D_prev[pos] + penalty`）が形成されると DP は monotonicity clamp で固定されたまま前進できなくなる。逃げルートは 2 つ:

| 機構 | 発火条件 | タイミング |
|---|---|---|
| `stuck_dp_reset` | 直近ウィンドウで前進 < 3 フレーム **かつ** 後退試行 ≥ ウィンドウ/4 | `stuck_dp_reset_seconds`（既定 12s）後 |
| `rapid_dp_reset` | 10 フレーム**連続**で argmin が後退を指す | ~0.93s 後（即時） |

`rapid_dp_reset` は「純後退アトラクタ」の確定シグナルにのみ発火する。slow-forward（DP がゆっくり前進しながら偶発的に後退を試みる）では `_consecutive_backward_frames` が非後退フレームでリセットされるため発火しない。

> **触ってはいけない**: rapid reset 発火後は `D_prev[:current_ref_pos]=inf, D_prev[current_ref_pos]=0` にリシードされる。省くと後退アトラクタが残り、次フレームでまた即 rapid reset が発火する。

rapid reset 後の前方 catchup は**実装して撤去した**（→ [experiments.md](docs/experiments.md#rapid-reset-後の前方-catchup)）。stall ごとの ~10 frame の永続遅延は当面受け入れる。

### mismatch 検知 + 有界前方リカバリ

stuck/rapid reset は「前進が止まった」ときしか発火しない。**前進しながらずれている**状態（junk 入力上の marching、大きなオフセット）は絶対コストで検知する（`_update_mismatch`）:

- **検知**: smoothed cost > `mismatch_cost_threshold`(0.18) が `mismatch_seconds`(8s) **連続**で `_mismatch_active=True`。lock-in 済み・非 frozen・非慣性のフレームのみカウント
- **フラグ中**: `FollowResult.is_mismatched` → トリガー抑止 + GUI「⚠ 追随ずれ疑い」。解除はヒステリシス（threshold−0.03 を ~1s）または任意の意図的テレポート
- **リカバリ**: 1s ごとに `_probe_decisive_forward_match` を前方 10s 窓で呼ぶ。大きなずれは操作者が手動で先に補正する前提で、自動リカバリは手動補正後の残差や早期の緩やかなドリフトを拾う用途（窓を狭めるほど自己類似露出も減る）。**四重ガード**で守る（→ [calibration.md](docs/calibration.md#リカバリ-probe-の四重ガード)）
- **クリア箇所**: `_anchor_dp_at`（全テレポート経路）・`freeze()`・`reset()`・ヒステリシス解除の全てで streak/flag/pending をクリア。手動 seek 後に残ったずれは 8s 後に再検知され probe がリトライする

> **触ってはいけない**: `mismatch_seconds` は「別演奏の閾値超え最長連続秒数」から誤検知ゼロになるよう校正されている。**閾値を緩めない。** 白色ノイズと自己類似箇所へのずれは原理的に検知不能で、緩和してもそこは破れず誤検知だけが復活する。曲を変えたら再校正すること（→ [calibration.md](docs/calibration.md#1-mismatch-検知の閾値)）

### unfreeze 後の DP 復帰経路

`unfreeze()` は `_frozen=False` のみ立てて、**`_inertia_active=True` のまま残す**。これが「前後マッチングして復帰」の実体:

1. DP は通常通り走る（裏で `_current_ref_pos` が進む）
2. 表示は引き続き `_inertia_ref_pos` を見せる
3. `_maybe_resync_from_dp()` が、DP confidence ≥ `lock_in_confidence` を `inertia_exit_frames` (3) 連続成立 **かつ** DP 位置と慣性位置のギャップが `inertia_resync_max_gap_frames`（None なら `search_width`）以内のときに `seek(dp_pos, allow_catchup=True)` を呼ぶ
4. `seek()` が post-seek catchup を armed にして DP を慣性位置に再 anchor
5. `_inertia_active=False` に戻り、通常追従に snap back

> **触ってはいけない**: `unfreeze()` で `_inertia_active` を即時クリアすると、表示位置が freeze 時点から DP の停滞位置へ backward ジャンプして見える。慣性は DP resync 経由でのみ抜けさせる。

### silence gate / 手動スタート（3 段構えの誤スタート防御）

マイクモードの「演奏前の誤追随」は 3 層で防ぐ。

**1. 手動スタート（マイクモードのみ、`main.manual_start()`）**

- 起動・楽章ロード直後は `_performance_started=False` で OLTW を常時 freeze し gate を無視（「▶ 演奏開始」/ L を押すまで一切動かない）。押下で gate 統治に移行
- **初回押下では `force_lock_in()` を呼ばない**（早押し時に慣性が無音上を走るため。lock-in は音楽を捉えてから自動ラッチ）。2 回目以降の押下・wav/loopback モードでは従来の強制 lock-in として機能
- 押しズレ補正: 早押しは gate が持続音まで freeze 維持、遅押しは pre-lock-in unfreeze の armed catchup + `start_search_seconds`（既定 10s）の初回探索幅拡大で実位置に着地

**2. gate ヒステリシス + one-shot 統治 + 見切りタイムアウト**（`audio_level._callback` の状態機械 + `main._check_silence_gate`）

- 開くには `gate_activation_sec`（既定 0.7s）の連続音、閉じるには `gate_release_sec`（既定 0.3s）の連続無音。断続ノイズは連続条件のリセットで蓄積しない
- **gate が freeze/unfreeze を統治するのはスタート押下から最初の gate 開放まで**（Issue #13）: 最初の持続音で `_performance_confirmed=True` になり、以後 gate は freeze を一切発火しない（レベル表示のみ更新）
- **見切りスタート**（Issue #41、`settings.start_gate_timeout_sec` 既定 3.0s / 0 で無効）: 押下から timeout 秒たっても gate が開かない（= 冒頭が閾値より弱い）場合、`_check_silence_gate` が演奏確定 + unfreeze する。閾値の測定ミス・弱音の冒頭で「永遠に開始されない」致命的失敗を防ぐ。**ここでも `force_lock_in()` は呼ばない**。トレードオフとして「早押しはコストゼロ」の保証は timeout 秒までに変わった（押下は指揮者の振り出しに合わせるのが前提）
- 理由: 静かに始まる楽章は音量が閾値を跨いで上下し、gate close のたびに pre-lock-in rewind が前進を破棄して永遠に lock-in できない。確定後の弱奏・休符は DP がそのまま追う（pp passage は DP が低マージンで正しく追える）
- `_performance_confirmed` / `_start_press_time` は `_load_movement` でリセット。押下〜確定の間は `state.awaiting_first_sound` が GUI に「無音でも N 秒後に自動開始」を表示させる。テストは `tests/test_silence_gate.py`

**3. pre-lock-in rewind**（`oltw_follower.py`）

- lock-in 前の gate 開放中の前進は「仮」。`unfreeze()` 時に `_pre_lockin_resume_pos` をスナップショットし、次の pre-lock-in `freeze()` で前進していたら `_reseed_at(snapshot)` で巻き戻す（conf streak もクリア、`_post_seek_catchup_pending` を arm）
- **lock-in（自動 or 強制）= point of no return**。以後は巻き戻さない。`seek()` は snapshot をクリアする（手動 seek を gate close が黙って巻き戻さないため）
- one-shot 統治の導入後は実際に発火するのは稀だが、OLTW 側の安全弁として機構とテストを維持する

補足:

- マイク dBFS が `silence_threshold_db` を下回ると `AudioLevelMonitor` が gate を立て、`main._check_silence_gate` 経由で `oltw.freeze()` を呼ぶ。`freeze()` の意味は lock-in の前後で変わる
- `--input-wav` / `--loopback` モードでは silence gate を完全に無効化する（マイクが開かれていないため polling すると常に -120 dBFS になる）

> **触ってはいけない #1**: **pre-lock-in の `unfreeze()` は DP を anchor に再シードする**（`_reseed_at`）。マイクモードは初フレーム前から frozen になるため `_D_prev` が全 inf のままで、再シードなしだと argmin が均一 inf band の rightmost tie-break になり**音声と無関係に毎フレーム +max_advance_per_frame 暴走**する（「カウントが止まらない」の主犯）。post-lock-in の unfreeze は DP 状態を保持（慣性 resync の前提）
>
> **触ってはいけない #2**: `freeze()` / `unfreeze()` の中から `seek()` を呼ぶと**デッドロック**する（両者とも `_state_lock` を取るが `threading.Lock` は非再入）。ロック保持中の再アンカーは必ず `_reseed_at()`（lock-free ヘルパー、呼び出し元がロック保持）を使う

## 特徴量

### 同一性制約と CENS+onset 融合

- **同一性制約の適用範囲は「OLTW の live ↔ reference_cens マッチング」のみ**。ライブ側とリファレンス側で **CENS と onset の両方のパラメータ**（sr, hop_length, win_length, log compression 係数, onset の正規化窓）が完全一致していないと DTW が外れる
- `core/feature_extractor.py` を **唯一の経路** として両側から呼ぶ
- パラメータを変えたらビルド済み `reference_cens.npy` / `reference_onset.npy` も作り直す（`asf-build` 再実行）
- `WarpLookup` は `built_dir` から `feature_config` を読んで OLTW に注入する（手で渡さない）
- **オフラインの score↔reference アラインメント（MrMsDTW）は別系統**: synctoolbox 標準の 50 Hz パイプライン（`reference_builder._compute_alignment_features`）を使い、ランタイム FeatureConfig とは独立。`--cens-win` / `--hop-length` はランタイム特徴のみに影響し warp path 精度には影響しない

> **触ってはいけない #0**: ランタイムの低フレームレート CENS を `sync_via_mrmsdtw` に流して経路を一本化しない。synctoolbox の multiscale smoothing は 50 Hz 前提で、過剰平滑により **warp path が「平均テンポの対角線」に退化する**（Issue #32、→ [experiments.md](docs/experiments.md#ランタイム-cens-を-sync_via_mrmsdtw-に流す-issue-32)）。回帰ガードは `tests/test_smoke_reference_builder.py::test_build_reference_recovers_known_tempo_warp`

融合コスト（`fused_local_cost`）:

```
cost[k] = chroma_weight × (1 − <cens_ref[:,k], live_cens>)
        + onset_weight  × |onset_ref[k] − live_onset|
```

- 重みは `settings.feature_fusion`（既定 **0.7 / 0.3**）。両方 ≥ 0 かつ和 > 0 が必須（違反時は warning + デフォルトに戻す）。この既定は A/B 済みで、動かしても改善しない（→ [experiments.md](docs/experiments.md#特徴量パラメータの-ab-テスト-onset-重みと-cens_win)）
- 参照側 onset は `asf-build` が `reference_onset.npy`（global-max 正規化済み）として保存。**欠損時は CENS-only に自動フォールバック**（旧ビルドとの後方互換）
- ライブ側 onset の正規化は `OnsetNormalizer.for_config()`（`LIVE_ONSET_WINDOW_SEC` = 5 秒の rolling-max 窓）に一本化。ワーカーは楽章ごとに作り直されるため窓は自然にリセットされる（長寿命化する場合は `reset()` が必要）

> **触ってはいけない #1**: `LIVE_ONSET_WINDOW_SEC` を変えると参照側（global-max）とライブ側の onset スケールの対応が崩れ、融合距離のバランスが黙って狂う
>
> **触ってはいけない #2**: fusion 非アクティブ時（`onset_weight=0` / reference_onset 欠損）のコストは **chroma_weight を掛けない生 cosine 距離のまま通す**。`step_penalty` / `lock_in_confidence` は cost ∈ [0, 2] のスケールでチューニングされており、ここで chroma_weight を掛けると閾値が黙ってリスケールされる（feature_extractor.py に理由コメントあり）

### 確信度は 2 本ある

| | 算出 | 用途 |
|---|---|---|
| 内部 confidence（`FollowResult.confidence`） | band 内の match_score × margin（band **相対**値） | lock-in・トリガー床・慣性復帰。**チューニング済みスケールなので触らない** |
| 表示 confidence（`state.display_confidence`） | 融合コストの平滑値を LO→HI で 1→0 に線形写像（**絶対**マッチ品質。`result_handler.display_confidence_from_cost`） | GUI 表示のみ |

分離した理由: 非負 chroma 同士の cosine には床があり、内部 confidence は**無関係な入力でも 0.6–0.8 に張り付く**。「無関係な BGM で確信度 70%」の正体はこれで、特徴量の失敗ではない。

LO/HI の校正値と特徴量の判別能の実測は → [calibration.md](docs/calibration.md#2-表示確信度の-lo-と-hi)。曲や録音条件が大きく変わったら eval CSV の `raw_local_cost` 分布で再校正する。

## オフラインビルド

### スコア・参照音源の構造整合性（必須前提）

**スコア、参照音源、本番ライブ入力の 3 つは繰り返し構造と総小節数が一致していなければならない。**

- なぜ: スコアに繰り返しがあってリファレンスが省略していると、MrMsDTW が多くのスコア小節を数秒の ref_time に押し込む極端な勾配の warp path を生成する。OLTW がその区間を通過すると 1 フレームで数十小節ジャンプとして観測される
- **検出可能範囲に注意**: `スコア ↔ 参照音源` の不一致はビルド時 `validate()` が検出できるが、**`参照音源 ↔ 本番ライブ` の不一致（当日の指揮者がリピートの取り方を変える等）はビルド時にもランタイムにも検出・回復する仕組みがない**。OLTW は黙って stall → rapid reset → 誤追随のいずれかに陥る。本番前に「当日はどのリピートを取るか」を確認し、参照録音と一致しない場合はリピート構造を合わせた参照で再ビルドするのが唯一の対策
- MusicXML の繰り返し記号は合成時に展開される（合成 WAV が約 2 倍長になる実測あり）。参照録音が繰り返しを省略しているなら**繰り返し記号を削除した MXL を別ファイルで用意**し、`asf-build --score` と config の `xml_file` の両方に指定する

### ビルド時検証とロード時検証（`WarpLookup.validate()`）

`asf-build` の最終ステップと `movement_loader.load_movement()`（asf-follow 起動時）の両方で `warp.validate(score_mapper)` が呼ばれる。後者はビルド後にスコアだけ差し替えるミスを防ぐためで、失敗すると `MovementLoadError` → `state.set_load_error()` で OLTW が起動しない。

1. **勾配チェック**（`max_slope=4.0×`）: 1 秒幅の参照時間窓でスコア時間の進み量が 4× を超えたら ValueError。繰り返し省略・カット・構造違いを検出する
2. **カバレッジチェック**（`max_coverage_diff_measures=5`）: warp path 末尾の小節番号とスコア総小節数の差が 5 を超えたら ValueError

失敗時は exit code 1（ビルド成果物は保存されるが使用禁止）。

**ランタイムジャンプ検出**: 正常ビルドでも 3 小節を超える突発ジャンプが起きたら `result_handler.on_result` が ERROR ログを出す（`_MAX_FRAME_MEASURE_JUMP`、ユーザー操作直後の 2 秒はグレースピリオド。`main._record_seek` が seek 時刻を記録し handler が参照）。

### 合成 BPM の自動推定

`--score-bpm` 未指定なら、スコアの総ビート数と参照録音の duration から四分音符 BPM を逆算する（`_estimate_score_bpm()` / `_probe_reference_duration()`）。楽譜指示の BPM で固定合成すると実演奏とのテンポ差が MrMsDTW のスキップを大量発生させ warp 検証に落ちる（→ [calibration.md](docs/calibration.md#4-合成-bpm-の自動推定)）。

- `--score-bpm` 明示指定 → その値。省略 & `--score-wav` なし → 推定して `build_meta.json` に永続化
- `--score-bpm` 省略 & `--score-wav` 指定 → **エラー終了**（事前合成 WAV のテンポは録音から逆推定できない）
- 推定値が sanity range 外、または参照 duration が短すぎる場合は ERROR 停止し明示指定を促す

> **触ってはいけない**: `build_reference()` の `score_bpm` 引数シグネチャは変更せず、CLI 側で解決した値を渡すだけにする。`tasks/generate_score_wav.py` の `--bpm` も既存 IF を流用する

### 末尾無音の自動トリム（`--end-trim`）

参照録音の末尾無音・拍手は `asf-build` が自動検出してビルド前にカットする（`_detect_tail_silence_sec()`）。**トリムしないと 2 つの故障が同時に起きる**:

1. BPM 推定の分母が水増しされ合成テンポが遅くなる
2. MrMsDTW がスコアの最終小節群を無音尾部にマップする → **runtime はどの入力でも最後の数小節に到達できない**

トリム量は `build_meta.json` の `reference_end_trim_sec` に永続化。実測値は → [calibration.md](docs/calibration.md#5-末尾無音の自動トリム)

**症状からの逆引き**: 「追随は最後まで正常なのに最終小節の数小節手前で頭打ち」ならまずこれを疑う。eval CSV の末尾で conf が高く 1:1 前進のまま入力が尽きていたら、参照側の warp が無音に食われている。

### 先頭雑音の自動トリム（`--start-offset`）

指揮者のブレス・椅子の音・チューニング A 音等の**先頭雑音はエネルギーだけでは検出できない**（雑音自体が鳴っているため RMS ゲートに引っかからない。かつスコアに無いので warp が外れやすい）。`detect_start_offset_sec()` は**スコア合成の冒頭とリファレンス録音の冒頭を比較**して解決する: 雑音はスコアと一致しないが演奏開始は一致し始めるため、比較コストの「高→低」の落差（knee）が境界になる。

- 前提: 「演奏は再生開始から数秒以内に始まる」
- ランタイム FeatureConfig とも MrMsDTW の 50Hz 特徴量とも独立な**第三の特徴量経路**（検出専用の使い捨て計算。build_meta やランタイムには影響しない）
- **安全側設計**: 落差が `_HEAD_DETECT_CONTRAST_MIN` 未満なら「確信なし」としてトリム量 0 を返す。閾値交差点はさらに `_HEAD_DETECT_BACKOFF_SEC` 手前にバックオフする（過剰トリムで冒頭小節を切り落とすより、雑音を少し残す方を選ぶ）
- トリム量は `build_meta.json` の `reference_start_offset_sec` に永続化
- **未校正の限界**: 実際に頭雑音が入ったリハ/ゲネプロ録音での動作点の最終校正はまだ。違和感があれば `--start-offset` で明示上書きする。検証内容は → [calibration.md](docs/calibration.md#6-先頭雑音の自動トリム)

### 合成は FluidSynth で行う（Windows ネイティブ）

- フロー: MusicXML → music21 でテンポ正規化（XML のテンポマーキングを剥がし冒頭に単一 MetronomeMark を入れる）→ MIDI → FluidSynth → WAV。12 分の曲で約 1 分
- **検出は `core/synth_locator.py` に一本化**（`find_fluidsynth()` / `find_soundfont()`。build_reference.py と generate_score_wav.py の両方がここを呼ぶ）
- **インストール推奨**: GitHub releases の zip をプロジェクトの `vendor/FluidSynth/` に展開する。LocalAppData 配置は Windows Defender / Controlled Folder Access に削除された実例あり（`vendor/` は `.gitignore` 済み）
- MuseScore 4 の CLI は使えない（→ [experiments.md](docs/experiments.md#musescore-4-の-cli-バッチモード)）

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

式の設計根拠・スプレッド床を撤廃した理由は → [calibration.md](docs/calibration.md#7-無音判定閾値の式)、代替案が不採用になった経緯は → [experiments.md](docs/experiments.md#無音判定--silence-gate)

**入力ソースの選択にかかわらず常に押せる**（測定は常にマイクデバイスから）。ソース連動の disable は混乱を生んだ実績があるため**再導入しないこと**。

### マイクの NC（ノイズ抑制）フィルター検出

特徴量は**未加工のマイク信号**を前提とする。OS/ドライバの NC が掛かると chroma と attack envelope の両方が歪み、追随品質が黙って劣化する。

- **検出**: WinRT `AudioEffectsManager` を pywinrt 経由で呼び、適用効果を照会。MediaCategory は `Other` / `Media` / `Communications` の 3 つ全てを問い合わせ、いずれかで検出されれば警告（安全側）
- **無効化はプログラムからは実質不可**（4 経路調査済み・実装しない → [experiments.md](docs/experiments.md#マイク-nc-の無効化)）。**検出して警告し `ms-settings:sound` を開くボタンで手動オフを促す**方針で確定
- **統合箇所**: ランチャーの「NCチェック」ボタンと `main._check_mic_effects()`（マイクモード起動時に一度だけ、`AppState.mic_effects_warning` → GUI 警告バナー）
- **検出の限界（解消不能）**: ①マイク/ヘッドセット**内蔵**のハードウェア DSP はどの Windows API からも見えない ②サードパーティ仮想マイク（NVIDIA Broadcast、Krisp 等）はデバイス名ヒューリスティックでしか警告できない。この 2 点は**警告のみ**にとどめ、ブロッキングエラーにしない（偽陽性で起動不能になり得るため、誤検知より見逃しの方が安全）
- pywinrt 未インストール・非 Windows・デバイス不一致では `probe_available=False` で**警告なしに degrade**する
- CLI 単体診断: `python -m audio_score_follower.core.mic_effects_probe [device]`

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

## テストと計測

### 単体テストの方針

- OLTW のテストは `np.zeros(12)` を chroma に流して「確実に低 confidence」を作る（ランダム unit vector はたまたま match することがある）
- 慣性挙動のテストは「freeze→unfreeze→低 conf chroma を 30 frame」のように **freeze 経由** で慣性に入れる
- `confidence_smoothing` の窓により freeze/unfreeze 直後 1〜2 frame に残響 confidence が残ることがあるので、resync の挙動を見るテストでは `_maybe_resync_from_dp` を mock する

### 回帰計測（eval_tracking）

```bash
python tasks/eval_tracking.py --built-dir <dir> --score <mxl> --input-wav <wav>
```

カバレッジ / ジャンプ / stall / 前進 stddev をヘッドレスに測る。**パラメータ変更の回帰確認は必ずこれで行い、同録音と別演奏の 2 種を流す。** ベースライン数値は → [calibration.md](docs/calibration.md#8-eval_tracking-のベースライン)（幻想4 固有の値なので、新曲では最初に曲固有のベースラインを取る）。

## ワークフロー管理

1. **プランモードのデフォルト**: 非自明なタスク（3 ステップ以上 / アーキテクチャ判断）は必ずプランモードに入る。問題が出たら STOP して再計画する
2. **サブエージェント戦略**: リサーチ・探索・並列分析はサブエージェントへ。1 タスク 1 担当
3. **完了前の検証（必須）**:
   - 動作を証明せずに「完了」と言わない。**修正 → 実行 → 確認ループ**
   - LLM/ツール呼び出し系の修正は `-v` で DEBUG ログを見る
   - テストが存在する箇所を触ったら `python -m pytest tests/ -q` を通す（uv は PATH に入っていない環境がある）
   - OLTW を変更したら少なくとも `pytest tests/test_oltw_follower.py -q` の全ケースが通ることを確認（テスト数はここに書かない — 腐るため）
4. **自律的なバグ修正**: バグ報告を受けたらそのまま修正する。CI が落ちていたら指示されなくても直す
5. **ドキュメント同期**: 機能変更（CLI オプション・出力構造・パイプライン）は**同 PR 内で README も更新**（別 PR / 別 Issue に分けない）。状態機械や制約を変えたらこのファイルも同時に更新する。新しい実測値・不採用の実験は `docs/` 側へ
6. **GitHub 同期**: フィーチャーブランチ → PR で `master` にマージする。ただし **push / PR 作成はユーザーが明示的に指示したときのみ**行う — タスク完了の一環として自律的に push しない。ローカルコミットまでは通常どおり進めてよい
7. **提案の独立検証**: ユーザの指示でも、コード・テスト・履歴を確認して前提が正しいか独立に検証する。特に OLTW の閾値変更提案（e.g.「lock_in_confidence を下げよう」）は、別演奏 file-input でカバレッジ regression が出ないかを必ず実測する

## 基本原則

- **シンプルさ優先**: 変更は最小限に
- **手抜きなし**: 根本原因を見つける
- **提案の独立検証**: 前提を自分で確かめてから動く
