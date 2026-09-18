# Macでの導入と動作確認の手順

自分のMacで `tscan` を動かして、手持ちの写真を実際に処理するまでの手順です。
上から順に実行すれば動きます。**所要時間は15〜20分**(大半はインストール待ち)。

対象: macOS 13 (Ventura) 以降。Apple Siliconでも Intel でも動きます。

---

## 0. この手順で何ができるようになるか

| できること | 使うコマンド |
|---|---|
| 撮った写真を取り込んで連番にする | `tscan ingest` |
| 見開き写真を左右のページに自動で分ける | `tscan run`(自動) |
| 背景・手を消して湾曲を補正し、日本語と数式を読む | `tscan run` |
| 読み取り結果をブラウザで確認・手直しする | `tscan review` |
| 検索できるPDF / Markdown / 品質レポートを出す | `tscan export` |
| 正解データと突き合わせて精度を測る | `tscan evaluate` |

---

## 1. 下準備(Homebrew)

ターミナル(アプリケーション → ユーティリティ → ターミナル)を開いて、
Homebrew が入っているか確認します。

```bash
brew --version
```

`command not found` と出たら、Homebrew を入れます。

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

入れ終わると「Next steps」としてPATHを通すコマンドが表示されるので、
**その指示どおりに実行してください**(Apple Silicon と Intel で内容が違います)。

---

## 2. Python と OCRエンジンを入れる

```bash
brew install python@3.11 tesseract tesseract-lang
```

- `tesseract` が日本語OCRの本体、`tesseract-lang` が日本語(縦書き含む)のモデルです
- Apple Vision は macOS に最初から入っているので、追加インストールは不要です
  (連携用のライブラリは手順4で入れます)

日本語モデルが入ったか確認します。`jpn` と `jpn_vert` が並んでいればOKです。

```bash
tesseract --list-langs
```

---

## 3. ソースコードを取ってくる

```bash
cd ~
git clone https://github.com/OkayasuShinsuke/Oka.git
cd Oka
git checkout claude/textbook-scan-app
cd textbook-scan
```

> `git` が無いと言われたら `xcode-select --install` を実行してください。

---

## 4. Python環境を作ってインストール

`venv` は「このプロジェクト専用のPython置き場」です。Macに元から入っている
Pythonを汚さずに済むので、必ず作ります。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev,apple-vision]"
```

最後の行の意味:

| 部分 | 意味 |
|---|---|
| `-e` | ソースを書き換えたら即反映される入れ方(開発用) |
| `dev` | テストを走らせるための pytest |
| `apple-vision` | Apple Vision 連携に必要な pyobjc |

> 以降、**ターミナルを開き直すたびに** `cd ~/Oka/textbook-scan && source .venv/bin/activate`
> が必要です。プロンプトの先頭に `(.venv)` が付いていれば有効になっています。

---

## 5. 環境を診断する

```bash
tscan doctor
```

こう出れば成功です。

```
OS: Darwin 23.x / Python 3.11.x
依存ライブラリ(§14.2)        … すべて ✓ 利用可能
OCRエンジン(§9.2)
  apple_vision  ✓ 利用可能        ← これが出れば本命のエンジンが使えます
  tesseract     ✓ 利用可能
  yomitoku      — pip install yomitoku が必要
  mathpix       — MATHPIX_APP_ID / MATHPIX_APP_KEY の設定が必要
保存先 …: 空き容量 xxx GB 十分
```

`apple_vision` が `—` のままなら、次を確認してください。

| 表示 | 対処 |
|---|---|
| `macOS 13以降が必要です` | Visionの日本語対応がmacOS 13からのため。OSを上げる |
| `pip install "tscan[apple-vision]" が必要です` | 手順4の `apple-vision` を付け忘れ。もう一度実行 |

### エンジンが本当に読めるか確かめる

「一覧には出ているのに実際には読めない」状態を切り分けられます。

```bash
tscan doctor --test-ocr
```

同じ日本語1行をエンジンごとに読ませて、結果と文字誤り率を並べて表示します。

```
読ませる文: 磁場の中を運動する電荷には力がはたらく。
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ エンジン     ┃ 結果                                     ┃ 文字誤り率 ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ apple_vision │ 磁場の中を運動する電荷には力がはたらく。 │ 0.0%       │
│ tesseract    │ 磁場の中を運動する電荷には力がはたらく。 │ 0.0%       │
└──────────────┴──────────────────────────────────────────┴────────────┘
```

**誤り率が5%以下なら正常です。** `失敗:` と出た場合は、そこに表示される
メッセージがそのまま原因です(pyobjcの不足、macOSのバージョンなど)。

テストも通しておくと安心です(138件、1分弱)。

```bash
pytest tests/ -q
```

---

## 6. まず動作確認(教科書が無くても試せる)

本物の写真を使う前に、プログラムが生成する「撮影を模したページ」で一通り流します。
**ここまでの手順が正しければ必ず成功する**ので、切り分けに使えます。

```bash
# 撮影を模したサンプル3ページを作る(台形歪み・照明ムラ・ノイズ入り)
python tools/make_sample_page.py --out-dir ~/tscan_sample --pages 3

# 取り込み → 処理 → 精度測定
tscan ingest ~/tscan_sample/photos --book-id sample --title "動作確認"
tscan run sample --subject physics
tscan evaluate sample --ground-truth ~/tscan_sample/ground_truth.json \
                      --math-ground-truth ~/tscan_sample/ground_truth_math.json
```

Tesseractのみの環境では本文CERが約1.4%になります。
**Apple Visionが有効なら、ここがさらに下がるはずです。ここが今いちばん知りたい数字です。**

---

## 7. 自分の写真で試す

### 7.1 写真をMacに移す

**重要: 写真は縮小せずに元データのまま渡してください。** メールやSNS経由だと
勝手に縮小されることがあります(検証中、チャット添付では面積41%まで縮小されていました)。

| 方法 | 手順 |
|---|---|
| **USBケーブル**(おすすめ) | iPhoneをMacに繋ぐ → 「イメージキャプチャ」アプリ → 保存先を選んで読み込み |
| **AirDrop** | iPhoneの写真アプリで選択 → 共有 → AirDrop → Mac |
| **写真アプリ** | 読み込み後、「ファイル → 書き出し → オリジナルを書き出す」を選ぶ(「書き出す」だと再圧縮されます) |

HEIC のままで構いません。プログラムが読めます。

### 7.2 処理する

```bash
tscan ingest ~/Desktop/textbook_photos --book-id physics_2026 --title "電磁気学"
tscan run physics_2026 --subject physics
tscan check physics_2026          # ページ抜けの確認
tscan export physics_2026 --format pdf,md,report
```

`tscan run` は自動で次を行います。設定は要りません。

1. 写真の向きを判定して正立させる(本を横向きに構えて撮っていてもOK)
2. 見開きなら左右2ページに分割する
3. 机・マット・手など紙面の外を消す
4. ページの湾曲を補正する(補正で文字行がまっすぐにならない場合は見送る)
5. 白抜き文字の公式ボックスを反転して読めるようにする
6. OCR → レイアウト解析 → 5層の検証

### 7.3 確認と手直し

```bash
tscan review physics_2026
```

ブラウザで http://127.0.0.1:8000 が開きます。要確認マークの付いたブロックを
直していってください。止めるときはターミナルで `Control + C` です。

---

## 8. Apple Vision の効果を測る(いちばんの目的)

Tesseractだけのときとの差を見ます。同じ本を2回処理して比べます。

```bash
# Apple Vision + Tesseract の2エンジン(既定)
tscan run physics_2026 --subject physics
tscan export physics_2026 --format report

# 比較: 正解データを作ってあれば数値で出せる
tscan evaluate physics_2026 --ground-truth ~/ground_truth.json
```

正解データは、1〜2ページ分の本文を手で打ったJSONで十分です。

```json
{
  "1": "このページの本文をそのまま書き写したもの。改行は入れなくてよい。",
  "2": "2ページ目の本文…"
}
```

キーは `tscan pages list physics_2026` で表示される通し番号です。

---

## 9. うまくいかないとき

| 症状 | 原因と対処 |
|---|---|
| `command not found: tscan` | `source .venv/bin/activate` を忘れている |
| `tesseractが見つかりません` | `brew install tesseract tesseract-lang` を実行 |
| `apple_vision` が有効にならない | 手順5の表を参照 |
| HEICが読めない | `pip install pillow-heif` を実行(通常は手順4で入ります) |
| 見開きが1ページとして処理される | `tscan review` で確認し、分割できていなければ写真を送ってください。ノドの検出条件を調整します |
| 処理が極端に遅い | 他の重い処理と競合していないか確認。目安は1ページ8秒前後(12MP、2並列) |

診断に迷ったら、まず次の2つを実行して出力を送ってください。

```bash
tscan doctor --test-ocr
pytest tests/ -q
```

---

## 10. 既知の制約(2026年9月時点)

実写真13枚での検証(合計34ページ)で分かっている限界です。

- **ページ番号(ノンブル)はほとんど読めません。** 紙面の切り出しが実際のページ下端まで
  届かず、番号が切り出しの外に出てしまうためです。誤った番号で誤ったページ抜け警告を
  出さないよう、確信が持てない場合は「読めなかった」として扱います
- **ページ下端の余白に重なった指先は残ります。** 紙と同じ明るさ・彩度なので区別できません。
  本文にはかからないので読み取りには影響しません
- **湾曲補正が効くのは一部のページだけです。** 紙の縁がページの束や指で乱れていると
  推定が当てにならないため、補正の前後で文字行のまっすぐさを比べ、
  良くならない場合は補正を見送ります
- **数式はMathpixを設定しない限り必ず「要確認」になります**(仕様書 REQ-OCR-02 の規定動作)
