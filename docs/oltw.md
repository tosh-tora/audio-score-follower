# OLTW の状態機械

`core/oltw_follower.py` の lock-in / 慣性 / DP リセット / mismatch 検知の全体像。
ここを理解せずに触ると安全弁を壊しやすい。閾値の校正根拠は
[calibration.md](calibration.md)、過去に試して捨てた案は
[experiments.md](experiments.md)。

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

**低 DP confidence 単独では慣性に入らない。絶対に追加しない** — 別演奏カバレッジが 100% → 34% に落ちる regression を実測済み（→ [experiments.md](experiments.md#低-conf-自動-inertia-入り)）。

### `_current_ref_pos` / `_inertia_ref_pos` / `_display_ref_pos` の三層分離

- **`_current_ref_pos`**（int）: DP-owned anchor。DP の更新ロジックのみが変更する
- **`_inertia_ref_pos`**（float）: 慣性中の表示位置。`_advance_inertia()` のみが変更する
- **`_display_ref_pos`**（float）: **表示スルー層**。通常追従中、表示が DP 位置を追う速度を `max(display_min_advance, rate × display_slew_factor)` frame/frame に制限する出力段レートリミッタ。stall 後の DP キャッチアップが表示上のテレポートにならない。フレーム駆動なので live を追い越せない（慣性の安全弁 #1 と同じ論法）。`display_slew_factor: 0` で無効化
- **公開プロパティ `current_ref_frame`**: 慣性 active なら `int(_inertia_ref_pos)`、slew 有効なら `int(_display_ref_pos)`、それ以外は `_current_ref_pos`。生 DP 位置は `dp_ref_frame`

**snap-vs-slew ルール**: `reset()` / `seek()` / 初期アライメント / global rematch / post-seek catchup / 慣性 resync という**意図的テレポートでは表示も即スナップ**する。スルーがかかるのは通常追従の DP 前進のみ。frozen 中・慣性中のフレームでは `_display_ref_pos` を出力値に同期させ stale gap を残さない。`freeze()` の慣性開始位置は（slew 有効時）`_display_ref_pos` から始める — DP anchor から始めると freeze 境界で表示が前方ジャンプする。

**低 conf 適応キャップ** `low_conf_advance_frames` は既定 0（無効）。実測で有意差がなかったため（→ [experiments.md](experiments.md#low_conf_advance_frames-低-conf-適応キャップ)）。

> **触ってはいけない**: `_advance_inertia()` で `_current_ref_pos` を書き換えると DP の band がずれ、stuck_dp_reset 後の D_prev 初期化が狂って DP が壊れる。過去にこの罠にハマった。

### 慣性中の安全弁

`live-score-sync` の `inertia_engine.py` は「テンポ外挿が演奏を追い越して measure 1 に snap back する」regression で捨てられた。本実装はこれを構造的に防ぐ 4 つの安全弁を持つ:

1. **物理上限**: live frame 1 つにつき ref を `+inertia_rate` だけ進める。経過時間ではなくフレーム数駆動なので、構造的に live より速くなれない
2. **rate の制限**: clamp `[0.3, 2.0]`、`_last_good_rate` キャッシュで quick re-entry にも安定
3. **絶対 cap**: `max_inertia_seconds` (10.0) を越えたら位置固定に戻す
4. **慣性中は `_try_global_rematch` を抑制**: スコア全体探索による遠方ジャンプを禁止（自己類似テーマへの誤テレポート防止）。`if not self._inertia_active` ガードを必ず維持する

> **触ってはいけない**: 経過時間ベースのテンポ外挿に戻さない。`live-score-sync` の `inertia_engine.py` を踏襲したくなる衝動は捨てること（→ [experiments.md](experiments.md#live-score-sync-の-inertia_engine-踏襲)）。`_pos_history` が clear されて rate が 1.0 fallback すると overshoot が蓄積し、resync gap > `search_width` で永続復帰不能になる — `_last_good_rate` キャッシュがこれを救済している

### stuck_dp_reset と rapid_dp_reset

後退アトラクタ（`D_prev[pos-1] < D_prev[pos] + penalty`）が形成されると DP は monotonicity clamp で固定されたまま前進できなくなる。逃げルートは 2 つ:

| 機構 | 発火条件 | タイミング |
|---|---|---|
| `stuck_dp_reset` | 直近ウィンドウで前進 < 3 フレーム **かつ** 後退試行 ≥ ウィンドウ/4 | `stuck_dp_reset_seconds`（既定 12s）後 |
| `rapid_dp_reset` | 10 フレーム**連続**で argmin が後退を指す | ~0.93s 後（即時） |

`rapid_dp_reset` は「純後退アトラクタ」の確定シグナルにのみ発火する。slow-forward（DP がゆっくり前進しながら偶発的に後退を試みる）では `_consecutive_backward_frames` が非後退フレームでリセットされるため発火しない。

> **触ってはいけない**: rapid reset 発火後は `D_prev[:current_ref_pos]=inf, D_prev[current_ref_pos]=0` にリシードされる。省くと後退アトラクタが残り、次フレームでまた即 rapid reset が発火する。

rapid reset 後の前方 catchup は**実装して撤去した**（→ [experiments.md](experiments.md#rapid-reset-後の前方-catchup)）。stall ごとの ~10 frame の永続遅延は当面受け入れる。

### mismatch 検知 + 有界前方リカバリ

stuck/rapid reset は「前進が止まった」ときしか発火しない。**前進しながらずれている**状態（junk 入力上の marching、大きなオフセット）は絶対コストで検知する（`_update_mismatch`）:

- **検知**: smoothed cost > `mismatch_cost_threshold`(0.18) が `mismatch_seconds`(8s) **連続**で `_mismatch_active=True`。lock-in 済み・非 frozen・非慣性のフレームのみカウント
- **フラグ中**: `FollowResult.is_mismatched` → トリガー抑止 + GUI「⚠ 追随ずれ疑い」。解除はヒステリシス（threshold−0.03 を ~1s）または任意の意図的テレポート
- **リカバリ**: 1s ごとに `_probe_decisive_forward_match` を前方 10s 窓で呼ぶ。大きなずれは操作者が手動で先に補正する前提で、自動リカバリは手動補正後の残差や早期の緩やかなドリフトを拾う用途（窓を狭めるほど自己類似露出も減る）。**四重ガード**で守る（→ [calibration.md](calibration.md#リカバリ-probe-の四重ガード)）
- **クリア箇所**: `_anchor_dp_at`（全テレポート経路）・`freeze()`・`reset()`・ヒステリシス解除の全てで streak/flag/pending をクリア。手動 seek 後に残ったずれは 8s 後に再検知され probe がリトライする

> **触ってはいけない**: `mismatch_seconds` は「別演奏の閾値超え最長連続秒数」から誤検知ゼロになるよう校正されている。**閾値を緩めない。** 白色ノイズと自己類似箇所へのずれは原理的に検知不能で、緩和してもそこは破れず誤検知だけが復活する。曲を変えたら再校正すること（→ [calibration.md](calibration.md#1-mismatch-検知の閾値)）

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
