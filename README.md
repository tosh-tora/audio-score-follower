# audio-score-follower

オーケストラ向けライブ追随アプリ。
スコア合成 WAV と実演奏録音 (プロ録音 / リハ録音) を **オフラインで対応付け**、
本番ではマイク入力を **同じリファレンス録音に Online DTW で追随** させる。

姉妹プロジェクト [live-score-sync](../live-score-sync) は pymatchmaker (note-based matcher) を
使っていたが、オーケストラの密音響で破綻する弱点があった。本プロジェクトは audio-to-audio で
全てを行うことでその問題を回避する。

## アーキテクチャ

```
オフライン (asf-build):
  MusicXML ─(synth)─▶ score_synth.wav
                            │
                            │ MrMsDTW (synctoolbox)
                            ▼
  reference.wav ◀───────────┘
                            │
                            ▼
                   data/built/<name>/
                     ├─ warping_path.npz   (score_time ↔ reference_time)
                     └─ reference_cens.npy (本番 OLTW 用 CENS)

オンライン (asf-follow):
  マイク or WAV ─▶ CENS ─▶ Online DTW ─▶ reference_time
                                              │
                                              ▼ warp 逆引き
                                         score_time → 小節 → Slides
```

## セットアップ

オフラインビルド・本番追随の両方が **Windows ネイティブ**で動く (WSL2 不要)。
合成は MuseScore 4 CLI に委ねる。

### 1. 前提

- Python 3.10+
- MuseScore 4 ([公式インストーラ](https://musescore.org/ja/download)、無料)
  - 自動検出パス: `C:\Program Files\MuseScore 4\bin\MuseScore4.exe`
  - 別の場所にある場合は環境変数 `MSCORE_EXE` か `--mscore-exe` で指定

### 2. Python 環境

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
```

**synctoolbox の注意**: 上記 `pip install` で synctoolbox は古い numpy / pandas / music21 を
要求して解決に失敗することがある (1.4.1 時点)。失敗した場合は `--no-deps` で入れ直す:

```powershell
pip install --no-deps synctoolbox libfmp ipython
```

実行時の numpy 2.x 互換問題は `reference_builder.py` 側でモンキーパッチ済み。

### 3. ディレクトリ配置

```
data/
├── scores/<piece>.xml          # MusicXML
├── reference_audio/<piece>.mp3 # プロ録音 or リハ録音 (WAV / MP3)
└── built/<piece>/              # asf-build の出力 (gitignore 対象)
```

### 4. オフラインビルド

```powershell
asf-build `
    --score data/scores/beethoven5.xml `
    --reference data/reference_audio/karajan_1977.mp3 `
    --output data/built/beethoven5_karajan/ `
    [--start-offset 0.5] `
    [--plot]
```

### 5. ライブ追随

```powershell
# 通常 (マイク + Google Slides)
python -m audio_score_follower.main config/beethoven5.json `
    --slide-url "https://docs.google.com/presentation/d/<ID>/present" `
    --verbose

# ドライラン (マイクあり、Slides なし。動作確認用)
python -m audio_score_follower.main config/beethoven5.json --verbose

# 診断モード (マイク経由を完全に外して、ファイル音源で動作確認)
python -m audio_score_follower.main config/beethoven5.json `
    --input-wav data/reference_audio/karajan_1977.mp3 `
    --verbose
```

`--input-wav` モードでは silence gate が自動で無効化され、ファイルを実時間で
CENS → OLTW に流す。アルゴリズムの検証、リファレンス音源との自己整合確認、
別演奏 (alt recording) でのカバレッジ測定に使う。

## config.json スキーマ

`config/` ディレクトリに JSON ファイルを置く。`asf-follow` の第1引数に渡す。

```json
{
  "settings": {
    "cooldown_seconds": 3.0,
    "silence_threshold_db": -55.0,
    "mic_device": null,
    "oltw_kwargs": {
      "search_width": 240,
      "back_inhibit_frames": 30,
      "init_search_width": 30,
      "step_penalty": 0.06,
      "max_advance_per_frame": 50,
      "stuck_dp_reset_seconds": 12.0,
      "stuck_rematch_seconds": 0.0
    }
  },
  "movements": [
    {
      "id": 1,
      "xml_file": "../data/scores/piece.mxl",
      "built_dir": "../data/built/piece_recording",
      "triggers": [
        {"measure": 1,  "action": "right", "note": "開始"},
        {"measure": 17, "action": "right", "note": "第二主題"}
      ]
    }
  ]
}
```

### settings: 全般

| フィールド | デフォルト | 説明 |
|---|---|---|
| `cooldown_seconds` | `3.0` | トリガ発火後、次のトリガが有効になるまでの最短間隔（秒）。 |
| `silence_threshold_db` | `-55.0` | この dBFS 以下が続いたら OLTW を一時停止（フェルマータ・休符対策）。`--input-wav` モードでは自動無効。 |
| `mic_device` | `null` | マイク入力デバイス名または番号。`null` = OS デフォルト。 |
| `oltw_kwargs` | (下記) | OLTW のチューニングパラメータ。通常はデフォルトのまま。 |

### settings.oltw_kwargs: OLTW チューニング

通常変更不要。デフォルトはオーケストラ的密音響 + 別演奏追従を想定して調整済み。

| フィールド | デフォルト | 説明 |
|---|---|---|
| `search_width` | `240` | 探索 band の **前方** 幅（frame、≈22 秒）。広いほどテンポ差を吸収、狭いほどドリフト耐性。 |
| `back_inhibit_frames` | `30` | 探索 band の **後方** 幅（frame、≈2.8 秒）。前方より狭く非対称にすることで、繰り返しテーマでの後退ロックを防止。 |
| `init_search_width` | `30` | 初フレームのみの探索幅。狭くしないと冒頭で誤位置 (5〜20 秒先) にロックする。 |
| `step_penalty` | `0.06` | DP の現状維持・stay にかかる罰則。低いと stuck plateau から脱出できない、高いと race-ahead。 |
| `max_advance_per_frame` | `50` | 1 ライブフレームで進める ref frame の上限（≈4.6s）。「20 小節先に瞬間ジャンプ」を構造的に防止。 |
| `stuck_dp_reset_seconds` | `12.0` | DP が累積コストで後退ロックされた時の救済発動秒数。`0` で無効。 |
| `stuck_rematch_seconds` | `0.0` | スコア全体探索による遠方ジャンプ機構。**デフォルト無効**（繰り返しテーマで誤テレポートするため）。 |

### movements

| フィールド | 必須 | 説明 |
|---|---|---|
| `id` | ○ | 楽章番号（任意の整数。順序の識別用）。 |
| `xml_file` | ○ | MusicXML / MXL ファイルのパス。config ファイルからの相対パス可。 |
| `built_dir` | ○ | `asf-build` の出力ディレクトリ。 |
| `triggers` | ○ | スライド操作の定義リスト。`measure`（小節番号）と `action`（`"right"` / `"left"`）が必須。`note` は任意メモ。 |

## ライブ操作

GUI 起動後の操作：

| キー | 動作 |
|------|------|
| **→** / Space | 次のスライド + OLTW を次の trigger 小節に sync + 「post-seek catchup」で実演奏位置を自動検出 |
| **←** | 前のスライド + OLTW を直前 trigger の手前に sync |
| **N** | 次の楽章をロード |
| **R** | 楽章を再ロード (OLTW reset) |

### 演奏とカウントがずれた時の対処

OLTW が遅延・先行した場合、人間が ← / → で補正できる。これらのキーは **単なるスライド送り
ではなく、OLTW 内部位置の再同期も行う**：

**シナリオ: 実演奏 m=20、OLTW がまだ m=10**

1. **→ を 1 回押す**
   - スライドが「次のトリガ位置」（例: m=17）の slide content へ
   - OLTW 位置が m=17 の ref_t に jump
   - その trigger を「発火済み」マーク（自動発火と二重発火しない）
   - **直後のライブフレームで「post-seek catchup」**: m=17〜m=17+22s の範囲を chroma 直接検索、
     実演奏位置 (m=20 付近) で強い match が出れば再 anchor
   - 結果: 1 押下で OLTW が m=20 まで自動追従

2. **目視で確認**：verbose ログに以下が出る
   ```
   OLTW seek: ref_frame=183 (16.99s) [catchup armed]
   Slide right [manual] measure=17 note=...
   Manual sync: OLTW re-anchored to measure 17
   OLTW post-seek catchup: 183→215 (local cost 0.412→0.023, discrim_ratio 0.91, +2.97s forward)
   ```

3. **catchup でも追いつかない場合（演奏が search_width=22s 超先）**：→ を再度押す。
   各押下で「次の未発火 trigger」に進むので、複数回押せば任意の位置まで catch up 可能。

**シナリオ: OLTW が誤って先行**

- **← を 1 回押す**
- スライドが戻る + OLTW が直近発火 trigger の **0.2 秒手前** に jump
- その trigger の発火マーク解除（音楽が再到達したら再発火される）
- catchup は **無効化される** (← は「演奏は手前」の signal なので、forward scan は本末転倒)

### ログ書式

`verbose` モードでは、すべてのスライド操作に発火源が付く：

```
Slide right [auto]   measure=17 note=テーマA      ← OLTW 追従による自動発火
Slide right [manual] measure=17 note=テーマA      ← 人手で →
Slide left  [manual] measure=17 note=テーマA      ← 人手で ←
```

`grep '\[manual\]\|\[auto\]\|Manual sync\|post-seek catchup\|stuck-rematch\|DP reset'` で
後からどう発火したかが追える。

## OLTW 設計メモ

オーケストラ的密音響 + 別演奏追従に必要だった、5 つの独立な失敗モードへの対処：

| 失敗モード | 対処 |
|-----------|------|
| 初期フレームが誤位置に高確信でロック | `init_search_width=30` (初フレームのみ探索範囲を絞る) |
| 自己類似テーマで band 内後退方向に引きずられる | `back_inhibit_frames=30`（band を非対称化） |
| Band-DP の vert chain で 1 フレームで 20 小節先にジャンプ | `max_advance_per_frame=50` で argmin の探索範囲をキャップ |
| 累積コストの「後退アトラクタ」で stuck | `stuck_dp_reset_seconds=12` で過去メモリ wipe（位置は据置） |
| 人手 → 押下後に OLTW が live 位置に追いつけない | `seek(allow_catchup=True)` で post-seek の局所 forward 再 match |

「**スコア全体を見て位置を特定**」する設計は繰り返し曲で破綻するため避けている。
すべての探索は **`search_width=240` (≈22 秒) 以内** または操作者明示の seek 直後のみ。

## 診断ワークフロー

問題切り分けの順序：

1. **A. 同録音 file-input**: `--input-wav <リファレンスと同じ音源>` で 100% カバレッジ・confidence 0.95+ になるはず → OLTW 自体は OK
2. **B. 別演奏 file-input**: `--input-wav <別演奏>` で 96-100% カバレッジ目安。conf 0.3〜0.5 は仕様（chroma の差で margin が出にくい）
3. **C. マイク経由**: A/B が OK でこれが NG → マイク経路の SNR 問題が主因
   - マイクゲイン上げ、スピーカーに近づける、または **VB-Audio Cable 等のループバック** でデジタル経路に切り替える

`grep 'OLTW' verbose.log | head -50` で起動直後の挙動が追える。`mic=-20dBFS 以上` を確認。

## プロジェクト構成

```
audio_score_follower/
├── core/
│   ├── score_mapper.py       # 拍 ↔ 小節 (アウフタクト対応)
│   ├── slide_controller.py   # Playwright Slides
│   ├── state_manager.py      # GUI 状態管理
│   ├── audio_level.py        # silence gate
│   ├── cooldown_timer.py     # クールダウン + 手動 unmark
│   ├── feature_extractor.py  # CENS 特徴抽出 (librosa)
│   ├── reference_builder.py  # オフライン MrMsDTW
│   ├── oltw_follower.py      # Online DTW 本体 + seek() / post-seek catchup
│   ├── warp_lookup.py        # ref_time ↔ score_time ↔ measure (双方向)
│   └── follower_worker.py    # マイク (FollowerWorker) + ファイル (FileWorker)
├── ui/                       # Tkinter GUI シェル
├── config/
├── cli/
│   ├── build_reference.py    # オフラインビルド CLI
│   └── follow.py             # ライブ追随 CLI
└── main.py                   # GUI エントリ + キーバインディング
tasks/
└── generate_score_wav.py     # XML → 合成 WAV (MuseScore 4 CLI, Windows ネイティブ)
tests/                        # pytest (現在 36 テスト)
data/                         # gitignore
```

## ライセンス

MIT
