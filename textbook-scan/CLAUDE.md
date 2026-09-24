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
- yomitoku連携は骨格のみ、実モデル未検証
- Mathpix連携は実装済みだが実APIキーでの疎通未検証
- 縦書きは実データでの検証未実施
- 表の構造化抽出・DeepSeek-OCR連携は未実装(方針としてv2送り/対象外)

## 次にやるべきタスク(優先順)
1. 実写真に対する正解データ(ground truth)を作り、厳密な本文CERを計測する
   (式番号の孤立防止・footer帯拡張の2修正は実写真でも機能を確認済み。合成画像では
   1.39%→9.76%→3.48%まで改善したが、実写真での定量値はまだ無い)
2. yomitokuを実モデルで検証し、縦書き・実データでの精度を確認する
3. 実写真でのノンブル認識の精度を上げる(誤読の抑制。紙面切り出しの下端拡張は改善済み)、
   ページ抜け検出を実写真で使えるようにする
4. Mathpixを実APIキーで疎通確認する
