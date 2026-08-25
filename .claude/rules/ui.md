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

詳細は [docs/launcher.md](../../docs/launcher.md)、silence gate の状態機械は [docs/oltw.md](../../docs/oltw.md)。

```bash
python -m pytest tests/test_launch_options.py tests/test_silence_gate.py tests/test_build_window.py -q
```
