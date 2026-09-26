---
paths:
  - "audio_score_follower/ui/**"
  - "audio_score_follower/launch_options.py"
  - "audio_score_follower/main.py"
  - "tests/test_launch_options.py"
  - "tests/test_silence_gate.py"
  - "tests/test_build_window.py"
---

# GUI・ランチャーを触るときの規約

- **`LaunchOptions.input_wav` を直接 app に渡さない。必ず `effective_input_wav` を使う。** ランチャーは wav 欄の前回値をソース切替後も保持するため、マイクモードでも `input_wav` は非 None になり得る。渡すと「マイクを選んだのに監視無効で自動追随」になる（実際に出たバグ）
- **無音測定・NC チェックのボタンを入力ソース連動で disable しない。** 測定は常にマイクデバイスから行う。ソース連動は「保存済み config が wav モードだとボタンが押せない」混乱を生んだ実績がある
- **無音判定閾値 `silence_threshold_db` は config に保存しない**（マイク・会場依存のセッション値）。永続化するのは `margin_db` だけ
- `launch_options.py` は Tk / sounddevice を import しない純ロジック層に保つ（ヘッドレステストの前提）
- GUI は 100ms ポーリング。ウィジェットの pack/forget は状態が変わったときだけ行う
- **追随状態（待機中 / 曲を捕捉中 / 追随中 / 確認中 / 見失い中）の判定と語は `ui/audience_panel.resolve_status` が正本。** 操作コンソールも聴衆パネルもこれを呼ぶ。操作コンソール側で状態を作り直さない — ピットの画面が「追随中」で客席の画面が「見失い中」という食い違いは、操作者が介入を判断すべきまさにその瞬間に起きる（Issue #51）。操作者にしか要らない情報（復帰キー・慣性の残り秒数）は括弧で足すだけにする
- **操作コンソールは 1200x800 に収まること。** フォントサイズ・行の追加は縦の予算を食う。「ピットから読めるように」2 倍化した結果 1400x1000 を要求し、実機で最大化してもキーヒントが切れていた実績がある（Issue #51）。`tests/test_gui_layout.py` が最悪ケース（警告バナー 3 種同時）込みで寸法を固定しているので、数字だけ緩めない
- **ランチャーも 1280x800 に収まること**（`tests/test_gui_layout.py` が最悪ケースの要求サイズを固定）。固定 geometry をやめて要求サイズで開く方式なので、行を足すと即ウィンドウが伸びる。新しい設定は既存セクションに足し、説明文は本文に書かず `launcher._HELP`（ⓘ ツールチップ）に置く。寸法は `"p"`（ポイント）単位で書く — px だと高 DPI でラベル列が入力欄に重なった
- 長い文字列を出すラベル（ファイル名・警告バナー・キーヒント）は `wraplength` を付け、`FollowerGUI._wrapped_labels` に登録する。付け忘れると横方向に画面外へはみ出す

詳細は [docs/launcher.md](../../docs/launcher.md)、silence gate の状態機械は [docs/oltw.md](../../docs/oltw.md)。

```bash
python -m pytest tests/test_launch_options.py tests/test_silence_gate.py tests/test_build_window.py tests/test_gui_layout.py -q
```
