# CLAUDE.md

## アプリの目的
教科書などの紙面をスマホ写真から取り込み、日本語/数式OCR・品質検証を経て、検索可能PDF/Markdown+LaTeX/JSONとして出力するシステム(仕様書: `docs/textbook-scan-spec.md`)。

## ディレクトリ構成
- `src/tscan/cli.py` — CLIエントリポイント(`tscan ingest/calibrate/run/check/review/export/evaluate/pages/doctor`)
- `src/tscan/ingest.py` — 写真取り込み・EXIF順リネーム
- `src/tscan/preprocess.py` — 台形補正・デスキュー・見開き分割・照明補正・ノイズ除去
- `src/tscan/realphoto.py` — 実写真向け補正(彩度ベース紙面検出・湾曲補正・指/背景除去・白抜き反転)
- `src/tscan/enhance.py` — ブック型スキャナ相当の補正の統括
- `src/tscan/ocr/` — OCRエンジン群(`tesseract_engine.py`, `apple_vision.py`, `yomitoku_engine.py`, `mathpix.py`)と`registry.py`(自動検出・フォールバック)
- `src/tscan/layout.py` — レイアウト解析(縦書き判定・読み順・数式判定・ルビ・図領域)
- `src/tscan/verify.py` — 品質保証(アンサンブル照合・信頼度・LaTeX構文・SymPy検証・文脈辞書)
- `src/tscan/evaluate.py` — 精度評価(CER・数式行正解率・レビュー率)
- `src/tscan/export/` — 出力(`pdf.py`検索可能PDF, `markdown.py`, `report.py`品質レポート)
- `src/tscan/review/server.py` — レビュー・編集UI(FastAPI, `tscan review`で起動)
- `src/tscan/models.py`, `config.py` — データモデルと設定
- `config/default.yaml`, `config/confusion_pairs.yaml` — 既定設定・混同ペア台帳(誤読候補判定用)
- `tools/make_sample_page.py`, `tools/simulate_book_photo.py` — テスト用サンプル画像生成
- `tests/` — pytest一式(147件)
- `docs/textbook-scan-spec.md`(仕様書), `docs/mac-setup.md`(macOSセットアップ手順)

## 実行方法
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
brew install tesseract tesseract-lang   # OCRエンジン必須
tscan doctor                            # 環境確認
tscan ingest <photos_dir> --book-id X --title "書名"
tscan calibrate X && tscan run X --subject physics
tscan review X                          # http://127.0.0.1:8000
tscan export X --format pdf,md,json,report
```

## テスト方法
```bash
pytest tests/ -v   # tesseract未導入環境ではOCR関連テストは自動スキップ
```

## 完成済みの機能
取り込み・ページ管理・前処理・実写真向け補正(README「実装状況」表参照)・Tesseract OCR・
レイアウト解析・品質保証5層・レビューUI・各種出力(PDF/MD/JSON/レポート)・精度評価。
Apple Visionは`tscan run`の主エンジンとして統合済み・実機で動作確認(合成3ページCER 3.48%)。
yomitoku連携は実装済み・実モデル(yomitoku 0.15.0)でPIL合成の縦書き日本語テキストの認識を確認済み。

## 未完成・既知のバグ
- **実写真でのOCR精度が未達**(§11.8基準): Apple Vision統合後も合成画像でCER 3.48%どまり
  (§11.8基準0.5%未達)。θ→8のような数式記号の誤認識が主因。実写真では正解データが無く
  CER未計測だが、要確認率89.4%(SONY ILCE-6400・15枚・電磁気学の教科書)で実用域に遠い。
  実写真1ページで手作業正解データを作りCERを実測したところ10.94%(修正前)。原因を
  調査したところ、影・照明ムラのある写真で`stretch_contrast`(P7.5)のOtsu二値化が
  汚染され(文字画素比率が通常10〜20%のところ37%に膨張)、文字ストロークを破壊して
  いたと判明。ink画素比率が30%を超える場合は引き伸ばしをスキップするガードを追加し、
  同じ1ページでCER 7.51%まで改善(約31%削減、`preprocess.stretch_contrast`)。
  ただし§11.8の0.5%には遠く、図版内のラベル文字が読み順に本文へ混入する問題
  (レイアウト解析の図領域判定)や個別の文字混同も残っており、追加調査が必要
- **実写真のノンブル検出は改善したが低精度**: footer帯拡張後は8/29ページで検出(以前は0/14)
  だが`165`が3ページ連続するなど誤読も混在。ページ抜け検出はまだ実写真で使えない
- yomitoku連携は実モデルで縦書き合成テキストの認識を確認したが、実際の教科書写真での
  検証はまだ未実施(実写真データが無いため)
- Mathpix連携は実装済みだが実APIキーでの疎通未検証
- 縦書きは実データでの検証未実施
- 表の構造化抽出・DeepSeek-OCR連携は未実装(方針としてv2送り/対象外)

## 次にやるべきタスク(優先順)
1. 実写真に対する正解データ(ground truth)を作り、厳密な本文CERを計測する
   (式番号の孤立防止・footer帯拡張の2修正は実写真でも機能を確認済み。合成画像では
   1.39%→9.76%→3.48%まで改善したが、実写真での定量値はまだ無い)
2. 実写真でのyomitoku精度確認(合成テキストでの動作確認は完了。実際の教科書写真での
   縦書き・実データ精度はまだ未検証)
3. 実写真でのノンブル認識の精度を上げる(誤読の抑制。紙面切り出しの下端拡張は改善済み)、
   ページ抜け検出を実写真で使えるようにする

## 保留(コスト理由でスキップ、無料エンジンで代替検討)
- Mathpixを実APIキーで疎通確認する: API課金が発生するため保留。Apple Vision/yomitokuの
  組み合わせで数式行(§11.8基準の数式行正解率95%)にどこまで対応できるか要評価
  (現状は下記「未完成・既知のバグ」参照。verify.pyのLaTeX構文/SymPy検証は機能するが、
  Mathpixのような構造化LaTeX出力エンジンが無いと入力自体が得られない)

- **ローカルLLMによる数式OCR(設計方針、未実装)**: Qwen2.5-VLまたはGOT-OCR2.0をローカルで
  動かし、Mathpixの代替として構造化LaTeX出力を得る。ただし推論コストが高いため全ページ適用は
  せず、以下の2段階構成とする。

  1. **候補ページ検出(既存OCR結果を再利用、追加のOCR実行なし)**
     - `layout.py`の`score_math_block()`が既に行単位で math_block/body_text/ambiguous を
       判定している(かな文字比率・演算子・数式記号Unicode・行の中央寄せ・行高・単独変数密度
       などのヒューリスティック)。この行単位スコアをページ単位に集約し、以下のいずれかで
       「数式候補ページ」と判定する:
       - `math_block`判定行の割合が閾値(暫定案: ページ内行数の10%以上)を超える
       - `ambiguous`判定行が一定数以上ある(閾値スコアが境界に集中している=通常OCRが
         苦手な複雑な数式レイアウトの可能性が高い)
       - Apple Vision/Tesseractの行内信頼度(既存の`verify.py`アンサンブル照合結果)が
         math_block判定行で閾値未満(既存の×0.7ペナルティ対象行をそのまま流用可能)
     - この判定は既存パイプラインの出力(OCR結果・layout.pyのブロック分類)のみから計算でき、
       追加の画像処理・推論コストはゼロ。
     - ページ単位ではなく、判定基準を満たした「領域(bbox)」のみをローカルLLMに渡す
       (ページ全体の再OCRではなく、math_block矩形のクロップ画像を渡す方式。Mathpixの
       クロップ渡しと同じインターフェースを流用できる)。

  2. **ローカルLLM適用(候補領域のみ)**
     - 候補と判定されたbboxクロップ画像をQwen2.5-VL/GOT-OCR2.0に渡し、LaTeX文字列を取得。
     - 返ってきたLaTeXは既存の`verify.py`のLaTeX構文チェック・SymPy検証にそのまま接続する
       (`Block.latex`に代入する経路は既存のsplit_equation_number周りを流用)。
     - Apple Vision/yomitokuのプレーンテキスト結果は、LLM出力が構文エラー等で信頼できない
       場合のフォールバックとして残す。

  3. **1冊あたりの候補ページ割合・処理時間の見積もり(暫定、実測前の概算)**
     - 理系教科書(物理・数学)を想定: 数式を含むページは経験的に全体の30〜50%程度だが、
       上記の「math_block行割合10%以上」のような厳しめの閾値を使うことで、本格的な数式
       LaTeX化が必要なページ(グラフや単発の変数記号だけでなく、分数・積分・行列など
       構造化が必須なもの)に絞り込み、候補ページ割合は**全体の10〜20%程度**に収まると
       想定(要実測)。
     - ローカルLLM(Qwen2.5-VL 7B級 / GOT-OCR2.0)の1領域あたり推論時間は、Apple Silicon
       (M系Mac、MPS/CPU推論)想定でおおよそ数秒〜十数秒/領域(モデルサイズ・量子化・
       画像解像度に依存、要実測)。1ページあたり数式領域が2〜5個程度とすると、候補ページ
       1枚あたり追加処理は数十秒程度。
     - 300ページの教科書1冊で候補ページが10〜20%(30〜60ページ)なら、追加処理時間は
       合計で**数十分程度**(通常OCRパイプラインの処理時間に加算)。全ページ適用した場合と
       比べて処理時間を80〜90%削減できる想定。
     - 実測値が出るまでは全て仮の見積もりであり、実装前に少数ページでのプロトタイプ計測が
       必須。
