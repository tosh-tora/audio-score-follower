# 試して捨てた案

**同じ実験を繰り返さないための記録。** 再挑戦する前に必ずここを読み、「何がダメだったか」を上回る根拠があるか確認すること。

数値の詳細は [calibration.md](calibration.md) を参照。

---

## OLTW / 追随ロジック

### 低 conf 自動 inertia 入り

**案**: DP confidence が低いフレームが続いたら自動的に慣性進行に切り替える。

**結果**: オーケストラの pp passage は DP が正しい位置を低マージンで追えている状態が多く、慣性 1.0 で上書きすると正解位置を破壊する。**別演奏のカバレッジが 100% → 34%** に落ちた。

**結論**: 完全削除。**絶対に再追加しない。** 慣性入りのトリガーは `freeze()` のみ。

### rapid reset 後の前方 catchup

**案**: `rapid_dp_reset` 発火後、stall 中に経過した live frame 数だけ前方をローカル探索して、最良マッチへジャンプする。

**結果**: `raw_cost ~ 0.20` の曖昧 chroma 区間では 15 frame 程度の探索でノイズと真のマッチを区別できず、**誤ジャンプ → 監視 → 再 rapid reset → 誤ジャンプ** のサイクルで遅延が悪化した（実測: 終端 m=176 → m=172 への regression）。

**結論**: 撤去。stall ごとの ~10 frame の永続遅延は当面受け入れる。

**未実装の代替案**: discriminability ratio ガードを足す / cost margin を厳しくする。なお mismatch リカバリの `_probe_decisive_forward_match` は、この失敗を踏まえて**絶対 ceiling と 2 連続 probe の位置整合**を加えた設計になっている（→ [calibration.md](calibration.md#リカバリ-probe-の四重ガード)）。

### 全域観測ベイズフィルタ posterior_follower

**案**: 「ずれの検知・訂正の根本解決」として、バンド拘束なしの全域観測ベイズフィルタで位置を推定する。

**結果**: OLTW との A/B で、以下すべてにおいて上回らなかった。

| 観点 | posterior | OLTW |
|---|---|---|
| 別演奏の滑らかさ（jump 数） | 8 | **0** |
| ノイズ耐性 | 劣る | — |
| オフセット復帰 | 劣る | — |

オケの自己類似（行進曲テーマの反復）では全域観測が誤マッチ源になる。対策（バンド拘束・近傍優先・有界リカバリ）を入れていくと **OLTW の設計に収束する**、というのが結論。

**結論**: 既定化見送り。ただしコードは**実験資産として残置**。本番経路は無改変（`main.py` は `OnlineDTWFollower` 固定）。テストは `tests/test_posterior_follower.py`、駆動は `eval_tracking --follower posterior` のみ。

### low_conf_advance_frames 低 conf 適応キャップ

**案**: 低 conf が連続したら `max_advance_per_frame` を `max(low_conf_advance_min, ceil(rate × low_conf_advance_factor))` に絞る。

**結果**: 幻想4 実測で有意差なし。

**結論**: 実装は残すが**既定 0 = 無効**で出荷。前フレームの streak カウンタを使うだけなので DP 再構成のコストはない。

### live-score-sync の inertia_engine 踏襲

**案**: 姉妹プロジェクトの慣性エンジンをそのまま持ち込む。

**結果**: あちらは「テンポ外挿が演奏を追い越して measure 1 に snap back する」regression で捨てられている。**経過時間ベースの外挿は live より速くなる罠にハマる。**

**結論**: 本実装は**フレーム駆動**（live frame 1 つにつき ref を `+inertia_rate` だけ進める）。この設計だと構造的に live より速くなれない。踏襲したくなる衝動は捨てること。

---

## 特徴量

### 特徴量パラメータの AB テスト onset 重みと cens_win

「同一オケの別の楽章」と「別演奏の正解楽章」のコスト帯の重なり（junk p10 0.095 vs matched p90 0.159）を、特徴パラメータで解消できないか試した。

| 変更 | separation |
|---|---|
| onset 重み 0.7/0.3（既定） | −0.064 |
| onset 重み 0.6/0.4 | −0.063 |
| onset 重み 0.5/0.5 | −0.072 |
| `cens_win` 41（既定） / 21 / 11 | 改善なし（matched と junk のコストが同時に上がるだけ） |

**結論**: どちらも改善しない。**既定 0.7/0.3 と `cens_win=41` を維持。** `asf-build --cens-win` は実験用に残置（build_meta 経由でランタイムへ自動伝播）。

この重なりは特徴量の問題ではなく**音楽そのものの自己類似**に由来するため、パラメータチューニングでは解決しない。

### ランタイム CENS を sync_via_mrmsdtw に流す Issue 32

**案**: オフラインアラインメントにもランタイムと同じ特徴量経路を使い、経路を一本化する。

**結果**: synctoolbox の multiscale smoothing デフォルト（`win_len_smooth=[201,101,21,1]` / `downsamp_smooth=[50,25,5,1]`）は **50 Hz 特徴量前提**。10.77 Hz + `cens_win=41`（既に ~3.8s 平滑済み）を渡すと全レベルが過剰平滑になり、**warp path が「平均テンポの対角線」に退化してテンポ揺れを一切捉えなくなった**。

実測: 同一音源入力でも小節カウントが最大 ±2 小節ずれた。幻想4 で **2947 ステップ中 2937 が完全対角**。

**結論**: アラインメントは `_compute_alignment_features`（50 Hz quantized chroma + DLNCO）を必ず使う。**ランタイム FeatureConfig とは別系統のまま維持する。**

回帰ガード: `tests/test_smoke_reference_builder.py::test_build_reference_recovers_known_tempo_warp`

---

## 無音判定 / silence gate

### スプレッド床 `MIN_SPREAD_DB=6dB`（Issue #19 で撤廃）

**結果**: 暗騒音が安定した環境では床が支配して閾値が median+9dB になり、弱音の入力が閾値を越えられず **gate が永遠に開かず追随が始まらない**（実測: threshold −12.9 dBFS / median −21.9 dBFS）。

**結論**: 撤廃。詳細は [calibration.md](calibration.md#7-無音判定閾値の式)

### 「最小 1 割平均 −1dB」式（Issue #41 で提案 → 不採用）

**結果**: 暗騒音の床より下になり、gate が押下後 0.7s で環境音に開く = **実質無効化**。早押し時の雑音追随リスクが復活する。

**結論**: 不採用。式は据え置き、調整は `margin_db` で行う。「開かない」側の致命的失敗は**見切りタイムアウト**（`start_gate_timeout_sec`）が防ぐ。

### 無音測定ボタンの入力ソース連動 disable

**結果**: 「保存済み config が wav モードだとボタンが押せない」という混乱を生んだ。

**結論**: 測定は常にマイクデバイスから行うので、入力ソースの選択にかかわらず**常に押せる**。**再導入しないこと。**

---

## マイク NC の無効化

プログラムからの無効化を 4 経路調査したが、**すべて実質不可**。

| 手法 | なぜダメか |
|---|---|
| WASAPI exclusive mode | `AudioLevelMonitor` が同じマイクに 2 本目のストリームを開く設計と衝突する |
| PortAudio raw-stream オプション（APO バイパス） | sounddevice 0.5.5 が公開していない |
| `IAudioEffectsManager::SetAudioEffectState` | PortAudio が内部に抱える `IAudioClient` に Python から到達できない |
| レジストリ `PKEY_AudioEndpoint_Disable_SysFx` | 管理者権限が必要、かつ Win11 では効かないケースがある |

**結論**: **検出して警告し、`ms-settings:sound` を開くボタンで操作者に手動オフを促す**方針で確定。実装しない。

---

## 合成

### MuseScore 4 の CLI バッチモード

**結果**: v4.6.5 で CLI バッチモードがハングするバグがあり使用不可。

**結論**: **FluidSynth** を使う（`tasks/generate_score_wav.py`）。
