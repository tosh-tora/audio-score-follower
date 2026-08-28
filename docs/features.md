# 特徴量（CENS + onset）

ライブ側と参照側で完全に一致していなければならない制約と、融合コスト・確信度の設計。
実測値は [calibration.md](calibration.md)、A/B の結果は [experiments.md](experiments.md)。

## 特徴量

### 同一性制約と CENS+onset 融合

- **同一性制約の適用範囲は「OLTW の live ↔ reference_cens マッチング」のみ**。ライブ側とリファレンス側で **CENS と onset の両方のパラメータ**（sr, hop_length, win_length, log compression 係数, onset の正規化窓）が完全一致していないと DTW が外れる
- `core/feature_extractor.py` を **唯一の経路** として両側から呼ぶ
- パラメータを変えたらビルド済み `reference_cens.npy` / `reference_onset.npy` も作り直す（`asf-build` 再実行）
- `WarpLookup` は `built_dir` から `feature_config` を読んで OLTW に注入する（手で渡さない）
- **オフラインの score↔reference アラインメント（MrMsDTW）は別系統**: synctoolbox 標準の 50 Hz パイプライン（`reference_builder._compute_alignment_features`）を使い、ランタイム FeatureConfig とは独立。`--cens-win` / `--hop-length` はランタイム特徴のみに影響し warp path 精度には影響しない

> **触ってはいけない #0**: ランタイムの低フレームレート CENS を `sync_via_mrmsdtw` に流して経路を一本化しない。synctoolbox の multiscale smoothing は 50 Hz 前提で、過剰平滑により **warp path が「平均テンポの対角線」に退化する**（Issue #32、→ [experiments.md](experiments.md#ランタイム-cens-を-sync_via_mrmsdtw-に流す-issue-32)）。回帰ガードは `tests/test_smoke_reference_builder.py::test_build_reference_recovers_known_tempo_warp`

融合コスト（`fused_local_cost`）:

```
cost[k] = chroma_weight × (1 − <cens_ref[:,k], live_cens>)
        + onset_weight  × |onset_ref[k] − live_onset|
```

- 重みは `settings.feature_fusion`（既定 **0.7 / 0.3**）。両方 ≥ 0 かつ和 > 0 が必須（違反時は warning + デフォルトに戻す）。この既定は A/B 済みで、動かしても改善しない（→ [experiments.md](experiments.md#特徴量パラメータの-ab-テスト-onset-重みと-cens_win)）
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

LO/HI の校正値と特徴量の判別能の実測は → [calibration.md](calibration.md#2-表示確信度の-lo-と-hi)。曲や録音条件が大きく変わったら eval CSV の `raw_local_cost` 分布で再校正する。
