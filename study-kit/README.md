# study-kit:自分専用の参考書づくり(ローカルQwen × Claude × LaTeX)

教科書の写真・PDFから、**自分が理解するための解説書(PDF)** を作る作業場の雛形です。
`tscan`(textbook-scan)とは別の場所に作り、必要なときだけ `tscan` を部品として呼びます。

---

## 1. しくみ: 4人のチーム

```
 教科書(写真/PDF)
      │
      ▼
 ① 読む ────────── ローカルの Qwen2.5-VL 72B(Mac Studio)  ← 速記者:見たまま書き起こす
      │  ocr/<本>/*.md(数式は LaTeX、自信のない字は [?])       無料・写真が外に出ない
      ▼
 ② 書く ────────── Claude                                    ← 家庭教師:自分の言葉で解説
      │  notes/<単元>/main.tex
      ▼
 ③ 組版 ────────── ./build.sh(LuaLaTeX)+ Claude(tex-fixer)  ← 印刷所:通るまで直す
      │  notes/<単元>/<単元>.pdf
      ▼
 ④ 検査 ────────── Claude(checker)+ sympy                   ← 校正者:別の目で疑う
```

**役割を分けた理由**

- 「見たまま読む」仕事は量が多く機械的です。ローカルに任せれば、何百ページでも無料で夜通し回せます。
- 「意味を理解して書く」「エラーの原因を推理して直す」仕事は、Claudeのほうが強い分野です。
- ローカルの読み取りには誤りが混ざります。そこで、自信のない字には `[?]` を付けさせ、
  Claudeがその位置だけ元画像で確かめます(推測で埋めない)。

---

## 2. 準備(最初の1回)

### 2.1 LaTeX(MacTeX)を入れる — 約5GB

```bash
brew install --cask mactex-no-gui
```

入れたらターミナルを**開き直し**て確認します。

```bash
lualatex --version      # バージョンが出ればOK
```

### 2.2 ローカルのモデル(Ollama + Qwen2.5-VL 72B)を入れる — 約50GB

```bash
brew install ollama
ollama serve                      # このターミナルは開いたままにする(別のターミナルで続きを)
ollama pull qwen2.5vl:72b         # 初回だけ。50GB前後のダウンロード
```

- 192GBのメモリなら余裕で載ります。読み取り中のメモリ使用は60〜80GB前後が目安です。
- モデル名(`qwen2.5vl:72b`)は変わることがあります。`ollama list` で入ったものを確認してください。
- **LM Studio や mlx-vlm で動かしたい場合**は、サーバーを起動して
  `--api openai --url http://localhost:1234/v1 --model <モデル名>` を付けます(中身は同じ)。
  8bit版のほうが文字の精度は上がりますが、その分遅くなります。

### 2.3 作業場を作る

```bash
cd ~/Oka
git pull origin claude/textbook-scan-app
study-kit/init.sh ~/study            # 何度実行しても安全(既存ファイルは上書きしない)
cd ~/study
./build.sh notes/_example            # LaTeX の動作確認 → notes/_example/_example.pdf ができる
```

`uv` が無ければ `brew install uv`(`tscan` の準備で入れていれば不要です)。

### 2.4 モデルの動作確認

```bash
uv run tools/local_read.py --check
```

`OK: http://localhost:11434 / qwen2.5vl:72b` と出れば準備完了です。
`サーバーに接続できません` なら `ollama serve` が動いているかを確認してください。

---

## 3. 使い方

### 3.1 教科書を置く

```
~/study/sources/young_em/     ← 写真(HEICのまま可)や、PDF
```

**傾き・影・見開きが強い写真は、先に `tscan` で補正すると読み取りが安定します。**

```bash
cd ~/Oka/textbook-scan && source .venv/bin/activate
tscan ingest ~/study/sources/young_em --book-id young_em --output-dir ~/tscan_out
tscan run young_em --output-dir ~/tscan_out
# 補正済みの画像(見開きは左右のページに分割済み)ができる:
#   ~/tscan_out/young_em/work/stages/preprocessed/young_em_p0001.png ...
```

### 3.2 ① 読む(ローカルのQwen)

```bash
cd ~/study
# 補正済みの画像をまとめて
uv run tools/local_read.py ~/tscan_out/young_em/work/stages/preprocessed --book young_em
# PDF なら、ページを指定して
uv run tools/local_read.py sources/young.pdf --book young --pages 63-66
```

- 結果は `ocr/<本>/*.md`。**読み取り済みのページは飛ばす**ので、途中で止めても続きから再開できます。
- 1ページの目安は数十秒〜数分(72Bの実測で決まります。最初の数ページで確認してください)。
- `⚠ 同じ行のくり返し` と出たページは、モデルが暴走しています。`--force` で読み直すか、Claudeに元画像を見てもらいます。

### 3.3 ②〜④ 書く・ビルド・検査(Claude)

```bash
cd ~/study
claude
```

Claude Code の中で:

```
/note 2-6_分岐公式 young_em の p.63-66
```

Claudeが次を自動で行い、最後に報告します。

1. `ocr/` を読み、`[?]` と図・式の多いページは元画像で確認する
2. `dict/` を検索して既存の用語・記号を再利用し、`main.tex` を書く
3. `./build.sh` でビルドし、失敗したら `tex-fixer` が通るまで直す(最大5回)
4. `checker` が式・単位・導出を疑って検査する(誤り / 要確認 / 確認済み)
5. 新しい用語・定理を `dict/` に追記する

できあがるのは `notes/2-6_分岐公式/2-6_分岐公式.pdf` です。

### 3.4 育てていく

- **`dict/`** … 用語・定理・記号の辞書。単元を書くたびに増え、次の単元では再利用されます。
  分からなかったことも辞書に溜まるので、自分専用の参考書になっていきます。
- **`CLAUDE.md`** … Claudeへの取扱説明書。文体や構成の好みを足していけます。
- **使うモデルを変える** … `.claude/agents/*.md` の `model:` 行を変更します
  (`tex-fixer` は軽い作業なので `sonnet`、`checker` は `inherit`=呼び出し元と同じ)。

---

## 4. フォルダの中身

```
~/study/
├─ CLAUDE.md              Claudeへの取扱説明書(役割・守ること・単元の型・ビルド方法)
├─ build.sh               ビルド。失敗時は原因の行だけを表示
├─ sources/               教科書の元データ(gitに入らない)
├─ ocr/                   ローカルQwenの読み取り結果(gitに入らない)
├─ figures/               元画像から切り抜いた図
├─ notes/<単元>/main.tex  解説(ここが成果物)と、できあがったPDF
├─ dict/                  terms.md / theorems.md / notation.md
├─ template/              preamble.tex(共通設定・囲み・記法)/ unit.tex(単元の雛形)
├─ tools/local_read.py    画像・PDF → Markdown+LaTeX(ローカルモデル)
├─ tools/crop.py          図の切り抜き
└─ .claude/               /note コマンドと、tex-fixer・checker
```

---

## 5. 注意

- **解説には誤りが混ざります。** 読み取りの誤りに加え、解説を書く側のもっともらしい間違いもあります。
  `checker` は誤りを減らしますが、ゼロにはしません。試験勉強に使うなら、重要な式は教科書と見比べてください。
- **教科書の文章をそのまま写した PDF を、共有・公開しないでください。** 著作権に触れます。
  このキットは「自分の言葉で言い換え、ページ番号で参照する」ことを前提にしています。
  `sources/` と `ocr/` は `.gitignore` で除外してあります(GitHubに上げないため)。
- Qwenの出力は、日本語+数式が混ざるページで誤読が出ます。**どのくらい読めるかは、最初に数ページ試して
  元画像と見比べてください。** `textbook-scan` の `tscan bench`(誤り率の採点)を、この読み取り結果にも
  使えるようにするのが次のステップです。

---

## 6. うまくいかないとき

| 症状 | 対処 |
|---|---|
| `latexmk が見つかりません` | MacTeXを入れてターミナルを開き直す(2.1) |
| `サーバーに接続できません` | `ollama serve` を別のターミナルで起動する |
| `モデル ... が見つかりません` | `ollama list` で名前を確認し、`--model` で指定する |
| 読み取りが極端に遅い | 他のアプリでメモリを使っていないか確認。`--max-edge 1600` で画像を小さくする |
| 画像が大きすぎて途中で途切れる | `--num-ctx 32768` に増やす |
| ビルドが5回直しても通らない | Claudeが止まって原因を報告します。`notes/<単元>/build/main.log` を見て指示してください |
| 日本語が文字化け・豆腐になる | `lualatex` で組んでいるか確認(`build.sh` を使う。`pdflatex` は使わない) |
