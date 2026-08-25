# Claude 開発ガイダンス (audio-score-follower-onset)

本番のオーケストラ演奏をマイクでリアルタイム追跡し、指定小節に到達したら Google Slides を自動でページ送りして聴衆向けの解説を表示するシステム。追跡アルゴリズム（OLTW）は手段であり、最終出力はスライド操作。

```
マイク → OLTW 追随 → 小節番号 → トリガー判定 → Playwright → Google Slides
```

## コマンド

```bash
python -m pytest tests/ -q                      # 全テスト（uv は PATH に無い環境がある）
asf-build --score <mxl> --reference <wav> --output <dir>   # オフラインビルド
asf-follow <config.json>                        # ライブ追随（引数なしでランチャー GUI）
python tasks/eval_tracking.py --built-dir <dir> --score <mxl> --input-wav <wav>
```

## ドキュメントの正本

内容ごとに置き場所が決まっている。**書く前にどこが正本か確認すること。** 同じことを 2 箇所に書かない。

| 内容 | 正本 |
|---|---|
| CLI フラグ・config スキーマ・本番運用手順（人間の運用者向け） | `README.md` |
| 設計の解説（アーキテクチャ / OLTW / 特徴量 / ビルド / ランチャー） | `docs/*.md` |
| 閾値の校正根拠・実測値 | `docs/calibration.md` |
| 試して捨てた案 | `docs/experiments.md` |
| 特定コード地点の制約 | **そのコードのコメント**（このリポジトリは「なぜ」型コメントで制約を記述する慣習） |
| 領域ごとの作業規約・検証手順 | `.claude/rules/*.md`（該当ファイルを読むと自動で載る） |
| 全セッションに効く規則・ワークフロー | このファイル |

この表は `tests/test_docs_guard.py` が機械的に守っている（CLAUDE.md の行数上限・リンク切れ・校正値の食い違い・制約の消失）。**このファイルが長くなって上限に触れたら、行を削るのではなく上表に従って移動先を選ぶこと。**

作業を始める前に読むもの:

- 全体像がわからない → [docs/architecture.md](docs/architecture.md)
- 追随ロジック → [docs/oltw.md](docs/oltw.md)
- 特徴量 → [docs/features.md](docs/features.md)
- ビルド・warp path → [docs/offline-build.md](docs/offline-build.md)
- GUI・ランチャー・入力モード → [docs/launcher.md](docs/launcher.md)

## 全セッションに効く規則

1. **数値は `docs/calibration.md` に書く。** 実測値・閾値の根拠をこのファイルやコードコメントに散らさない。散らすと必ず食い違う（実際に mismatch の実測値がコード内 2 箇所で食い違っていた）
2. **不採用にした案は `docs/experiments.md` に記録してから消す。** 同じ実験を繰り返さないための記録が、このプロジェクトで最も価値のある資産
3. **OLTW の閾値を変える提案は、別演奏の file-input でカバレッジ regression が出ないか必ず実測してから採否を決める。** 同録音だけでは劣化を検出できない
4. **機能変更（CLI オプション・出力構造・パイプライン）は同 PR 内で README も更新する。** 別 PR / 別 Issue に分けない
5. **push / PR 作成はユーザーが明示的に指示したときのみ行う。** タスク完了の一環として自律的に push しない。ローカルコミットまでは通常どおり進めてよい

## ワークフロー管理

1. **プランモードのデフォルト**: 非自明なタスク（3 ステップ以上 / アーキテクチャ判断）は必ずプランモードに入る。問題が出たら STOP して再計画する
2. **サブエージェント戦略**: リサーチ・探索・並列分析はサブエージェントへ。1 タスク 1 担当
3. **完了前の検証（必須）**: 動作を証明せずに「完了」と言わない。修正 → 実行 → 確認ループを回す。テストが存在する箇所を触ったら `python -m pytest tests/ -q` を通す。LLM / ツール呼び出し系の修正は `-v` で DEBUG ログを見る
4. **自律的なバグ修正**: バグ報告を受けたらそのまま修正する。CI が落ちていたら指示されなくても直す
5. **提案の独立検証**: ユーザの指示でも、コード・テスト・履歴を確認して前提が正しいか独立に検証する。前提が誤っていれば、そのまま実装せず指摘する

## テストの書き方（このリポジトリ固有）

- OLTW のテストは `np.zeros(12)` を chroma に流して「確実に低 confidence」を作る（ランダム unit vector はたまたま match することがある）
- 慣性挙動のテストは「freeze→unfreeze→低 conf chroma を 30 frame」のように **freeze 経由** で慣性に入れる
- `confidence_smoothing` の窓により freeze/unfreeze 直後 1〜2 frame に残響 confidence が残るので、resync の挙動を見るテストでは `_maybe_resync_from_dp` を mock する
- テスト数のような変動する数値をドキュメントに書かない（腐るため）

## 基本原則

- **シンプルさ優先**: 変更は最小限に
- **手抜きなし**: 根本原因を見つける
- **提案の独立検証**: 前提を自分で確かめてから動く
