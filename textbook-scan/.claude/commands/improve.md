---
description: 実写真ベンチマークで誤読の原因を調べ、仮説を1つ実装し、関所(tools/gate.sh)で採否を決める。改善ループの1回分。
---

教科書OCR(textbook-scan)の改善ループを **1回だけ** 回してください。
採否を決めるのはあなたではなく `tools/gate.sh`(数値による機械判定)です。
あなたの仕事は、良い仮説を選び、正直に実装し、関所に通すことです。

前提: カレントディレクトリは改善ループ用の worktree の `textbook-scan/`、
環境変数 `TSCAN_BENCH_DIR` が採点用フォルダ(写真・manifest・基準・成績表)を指しています。
`$TSCAN_BENCH_DIR` が未設定なら `~/tscan_bench` です。

## 手順

1. **始める前の確認**: `git status --short` で `src tests config` に変更が残っていないこと。
   残っていたら `git checkout -- src tests config` で消してから始める。
2. **原因調査**: サブエージェント `failure-analyst` に調査を頼む。
   その際 `$TSCAN_BENCH_DIR/LEDGER.md` の最近の行(試した案と結果)を渡し、同じ案を避けるよう伝える。
3. **仮説を1つ選ぶ**: 返ってきた仮説から1つ選ぶ。選ぶ基準は
   「効く写真が多い」>「悪化の心配が小さい」>「変更が小さい」。
   LEDGER で不合格になった案と実質同じものは選ばない。
4. **実装**: サブエージェント `fixer` に、選んだ仮説(原因・証拠・直す場所・直し方)をそのまま渡す。
   fixer が失敗を報告したら、手順7に進んで記録だけ残す(`tools/gate.sh --veto "<仮説>" "実装できず: <理由>"`)。
5. **監査**: サブエージェント `auditor` に、仮説の一行説明を渡して変更を監査してもらう。
   `AUDIT=NG` なら `tools/gate.sh --veto "<仮説>" "<監査の理由>"` を実行して終わる。
6. **関所**: `tools/gate.sh "<仮説の一行説明>"` を実行する。
   `GATE=PASS` なら commit 済み、`GATE=FAIL` なら変更は自動で戻されている。
   **関所の結果を覆そうとしないこと。** 同じ回の中で別の案を試したり、再実行したりしない。
7. **学びを残す**: `$TSCAN_BENCH_DIR/NOTES.md` の末尾に、次の回の自分へのメモを2〜4行追記する
   (ファイルが無ければ作る)。書くのは「何を試し、どうなり、何が分かったか」。例:
   `- [不合格] 照明補正のカーネルを1/8に: iPhoneは改善、APS-Cの影の濃い写真で白飛びが増えた → 影の濃さで切り替える案が次の候補`

## してはいけないこと

- `tools/`、`.claude/`、`src/tscan/bench.py`、`src/tscan/evaluate.py`、manifest、基準のJSONを変更する
- `$TSCAN_BENCH_DIR/manifest.json`、`holdout_runs/`、`baseline_holdout.json` を開く
- `git commit` / `git reset` / `git push` を自分で実行する(commit は関所が行う)
- 1回の中で複数の仮説を実装する

## 最後の報告(3〜5行)

選んだ仮説、関所の結果(GATE= の行)、NOTES.md に書いたこと。
