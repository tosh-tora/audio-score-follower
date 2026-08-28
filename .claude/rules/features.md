---
paths:
  - "audio_score_follower/core/feature_extractor.py"
  - "tests/test_feature_extractor.py"
---

# 特徴量を触るときの規約

`feature_extractor.py` は**オフラインビルドと本番ランタイムが共有する唯一の経路**。ライブ側と参照側で CENS と onset の両方のパラメータ（sr, hop_length, win_length, log 圧縮係数, onset の正規化窓）が完全一致していないと DTW が外れる。

- パラメータを変えたら**ビルド済みの `reference_cens.npy` / `reference_onset.npy` を作り直す**（`asf-build` 再実行）。片側だけ変えると、テストは通るのに本番の追随だけが静かに壊れる
- オフラインの score↔reference アラインメント（MrMsDTW）は**別系統**。ランタイムの低フレームレート CENS をそこに流さない（Issue #32）
- コストのスケールは OLTW の `step_penalty` / `lock_in_confidence` と結合している

詳細と設計の根拠は [docs/features.md](../../docs/features.md)、A/B の結果は [docs/experiments.md](../../docs/experiments.md)。

```bash
python -m pytest tests/test_feature_extractor.py tests/test_oltw_follower.py -q
```
