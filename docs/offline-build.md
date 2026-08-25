# オフラインビルド（asf-build）

スコア合成 → MrMsDTW → warp path の生成と、その検証・自動トリム。
CLI フラグの一覧は README.md が正本。実測値は [calibration.md](calibration.md)。

## オフラインビルド

### スコア・参照音源の構造整合性（必須前提）

**スコア、参照音源、本番ライブ入力の 3 つは繰り返し構造と総小節数が一致していなければならない。**

- なぜ: スコアに繰り返しがあってリファレンスが省略していると、MrMsDTW が多くのスコア小節を数秒の ref_time に押し込む極端な勾配の warp path を生成する。OLTW がその区間を通過すると 1 フレームで数十小節ジャンプとして観測される
- **検出可能範囲に注意**: `スコア ↔ 参照音源` の不一致はビルド時 `validate()` が検出できるが、**`参照音源 ↔ 本番ライブ` の不一致（当日の指揮者がリピートの取り方を変える等）はビルド時にもランタイムにも検出・回復する仕組みがない**。OLTW は黙って stall → rapid reset → 誤追随のいずれかに陥る。本番前に「当日はどのリピートを取るか」を確認し、参照録音と一致しない場合はリピート構造を合わせた参照で再ビルドするのが唯一の対策
- MusicXML の繰り返し記号は合成時に展開される（合成 WAV が約 2 倍長になる実測あり）。参照録音が繰り返しを省略しているなら**繰り返し記号を削除した MXL を別ファイルで用意**し、`asf-build --score` と config の `xml_file` の両方に指定する

### ビルド時検証とロード時検証（`WarpLookup.validate()`）

`asf-build` の最終ステップと `movement_loader.load_movement()`（asf-follow 起動時）の両方で `warp.validate(score_mapper)` が呼ばれる。後者はビルド後にスコアだけ差し替えるミスを防ぐためで、失敗すると `MovementLoadError` → `state.set_load_error()` で OLTW が起動しない。

1. **勾配チェック**（`max_slope=4.0×`）: 1 秒幅の参照時間窓でスコア時間の進み量が 4× を超えたら ValueError。繰り返し省略・カット・構造違いを検出する
2. **カバレッジチェック**（`max_coverage_diff_measures=5`）: warp path 末尾の小節番号とスコア総小節数の差が 5 を超えたら ValueError

失敗時は exit code 1（ビルド成果物は保存されるが使用禁止）。

**ランタイムジャンプ検出**: 正常ビルドでも 3 小節を超える突発ジャンプが起きたら `result_handler.on_result` が ERROR ログを出す（`_MAX_FRAME_MEASURE_JUMP`、ユーザー操作直後の 2 秒はグレースピリオド。`main._record_seek` が seek 時刻を記録し handler が参照）。

### 合成 BPM の自動推定

`--score-bpm` 未指定なら、スコアの総ビート数と参照録音の duration から四分音符 BPM を逆算する（`_estimate_score_bpm()` / `_probe_reference_duration()`）。楽譜指示の BPM で固定合成すると実演奏とのテンポ差が MrMsDTW のスキップを大量発生させ warp 検証に落ちる（→ [calibration.md](calibration.md#4-合成-bpm-の自動推定)）。

- `--score-bpm` 明示指定 → その値。省略 & `--score-wav` なし → 推定して `build_meta.json` に永続化
- `--score-bpm` 省略 & `--score-wav` 指定 → **エラー終了**（事前合成 WAV のテンポは録音から逆推定できない）
- 推定値が sanity range 外、または参照 duration が短すぎる場合は ERROR 停止し明示指定を促す

> **触ってはいけない**: `build_reference()` の `score_bpm` 引数シグネチャは変更せず、CLI 側で解決した値を渡すだけにする。`tasks/generate_score_wav.py` の `--bpm` も既存 IF を流用する

### 末尾無音の自動トリム（`--end-trim`）

参照録音の末尾無音・拍手は `asf-build` が自動検出してビルド前にカットする（`_detect_tail_silence_sec()`）。**トリムしないと 2 つの故障が同時に起きる**:

1. BPM 推定の分母が水増しされ合成テンポが遅くなる
2. MrMsDTW がスコアの最終小節群を無音尾部にマップする → **runtime はどの入力でも最後の数小節に到達できない**

トリム量は `build_meta.json` の `reference_end_trim_sec` に永続化。実測値は → [calibration.md](calibration.md#5-末尾無音の自動トリム)

**症状からの逆引き**: 「追随は最後まで正常なのに最終小節の数小節手前で頭打ち」ならまずこれを疑う。eval CSV の末尾で conf が高く 1:1 前進のまま入力が尽きていたら、参照側の warp が無音に食われている。

### 先頭雑音の自動トリム（`--start-offset`）

指揮者のブレス・椅子の音・チューニング A 音等の**先頭雑音はエネルギーだけでは検出できない**（雑音自体が鳴っているため RMS ゲートに引っかからない。かつスコアに無いので warp が外れやすい）。`detect_start_offset_sec()` は**スコア合成の冒頭とリファレンス録音の冒頭を比較**して解決する: 雑音はスコアと一致しないが演奏開始は一致し始めるため、比較コストの「高→低」の落差（knee）が境界になる。

- 前提: 「演奏は再生開始から数秒以内に始まる」
- ランタイム FeatureConfig とも MrMsDTW の 50Hz 特徴量とも独立な**第三の特徴量経路**（検出専用の使い捨て計算。build_meta やランタイムには影響しない）
- **安全側設計**: 落差が `_HEAD_DETECT_CONTRAST_MIN` 未満なら「確信なし」としてトリム量 0 を返す。閾値交差点はさらに `_HEAD_DETECT_BACKOFF_SEC` 手前にバックオフする（過剰トリムで冒頭小節を切り落とすより、雑音を少し残す方を選ぶ）
- トリム量は `build_meta.json` の `reference_start_offset_sec` に永続化
- **未校正の限界**: 実際に頭雑音が入ったリハ/ゲネプロ録音での動作点の最終校正はまだ。違和感があれば `--start-offset` で明示上書きする。検証内容は → [calibration.md](calibration.md#6-先頭雑音の自動トリム)

### 合成は FluidSynth で行う（Windows ネイティブ）

- フロー: MusicXML → music21 でテンポ正規化（XML のテンポマーキングを剥がし冒頭に単一 MetronomeMark を入れる）→ MIDI → FluidSynth → WAV。12 分の曲で約 1 分
- **検出は `core/synth_locator.py` に一本化**（`find_fluidsynth()` / `find_soundfont()`。build_reference.py と generate_score_wav.py の両方がここを呼ぶ）
- **インストール推奨**: GitHub releases の zip をプロジェクトの `vendor/FluidSynth/` に展開する。LocalAppData 配置は Windows Defender / Controlled Folder Access に削除された実例あり（`vendor/` は `.gitignore` 済み）
- MuseScore 4 の CLI は使えない（→ [experiments.md](experiments.md#musescore-4-の-cli-バッチモード)）
