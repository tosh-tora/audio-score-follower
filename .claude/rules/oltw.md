---
paths:
  - "audio_score_follower/core/oltw_follower.py"
  - "audio_score_follower/core/result_handler.py"
  - "audio_score_follower/core/follower_worker.py"
  - "tests/test_oltw_follower.py"
  - "tests/test_mismatch_detector.py"
---

# OLTW を触るときの規約

追随ロジックの安全弁は微妙に噛み合っている。**変更前に [docs/oltw.md](../../docs/oltw.md) を読むこと。**

要点だけ（詳細と根拠は docs/oltw.md）:

- **lock-in は二段構え**で、`freeze()` の意味が前後で変わる。lock-in 前 = 位置固定、lock-in 後 = 慣性進行
- **慣性に入るトリガーは `freeze()` のみ。** 低 confidence 単独で慣性に入れる変更は入れない（別演奏カバレッジ 100%→34% の regression 実績）
- `_current_ref_pos`（DP 所有）/ `_inertia_ref_pos`（慣性表示）/ `_display_ref_pos`（表示スルー）の**三層は混ぜない**
- 閾値の変更提案は、緩めた側の失敗が本番で致命的になり得る。数値の根拠は [docs/calibration.md](../../docs/calibration.md) にあり、曲を変えたら再校正手順に従う

## 変更後に必ず走らせるもの

```bash
python -m pytest tests/test_oltw_follower.py tests/test_mismatch_detector.py -q
```

さらに **`tasks/eval_tracking.py` を同録音と別演奏の 2 種で流す**。同録音だけでは追随品質の regression を検出できない（別演奏でのみ出る劣化が過去に複数ある）。ベースライン値は [docs/calibration.md](../../docs/calibration.md#8-eval_tracking-のベースライン)。

新しい実測値を得たら `docs/calibration.md` に、不採用にした案は `docs/experiments.md` に記録してから消すこと。
