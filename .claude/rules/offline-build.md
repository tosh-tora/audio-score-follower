---
paths:
  - "audio_score_follower/core/reference_builder.py"
  - "audio_score_follower/cli/build_reference.py"
  - "audio_score_follower/core/warp_lookup.py"
  - "audio_score_follower/core/score_mapper.py"
  - "audio_score_follower/core/movement_loader.py"
  - "tasks/generate_score_wav.py"
  - "tests/test_smoke_reference_builder.py"
  - "tests/test_warp_lookup.py"
---

# オフラインビルドを触るときの規約

**必須前提**: スコア・参照音源・本番ライブ入力の 3 つは、繰り返し構造と総小節数が一致していなければならない。ずれた warp path の上では OLTW は原理的に正しく追随できない。

- `スコア ↔ 参照音源` の不一致は `WarpLookup.validate()` がビルド時とロード時に検出する
- **`参照音源 ↔ 本番ライブ` の不一致は検出も回復もできない。** 当日の指揮者がリピートの取り方を変えた場合、OLTW は黙って stall か誤追随に陥る。対策は本番前の確認と再ビルドだけ
- 自動トリム（先頭雑音・末尾無音）と BPM 自動推定は、外すと warp が壊れる方向に効く。無効化する前に [docs/offline-build.md](../../docs/offline-build.md) の失敗モードを読むこと

詳細は [docs/offline-build.md](../../docs/offline-build.md)、実測値は [docs/calibration.md](../../docs/calibration.md)。

```bash
python -m pytest tests/test_smoke_reference_builder.py tests/test_warp_lookup.py -q
```
