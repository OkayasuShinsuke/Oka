#!/usr/bin/env bash
# 自動改善ループの「関所」: 作業ツリーの変更を採点し、合格なら commit、不合格なら元に戻す。
#
#   使い方(改善ループ用の worktree の textbook-scan/ で実行):
#       tools/gate.sh "仮説の一行説明"
#       tools/gate.sh --veto "仮説の一行説明" "理由"   監査役が止めた変更を、採点せずに戻して記録する
#
# 採否を決めるのはこのスクリプト(機械)で、エージェント(AI)ではない。
# AIは「もっともらしい理由」で自分の変更を合格にしがちなので、判定は数値だけで行う。
#
#   1. 変更してよい場所か         src/tscan/ tests/ config/ 以外、採点に関わるファイルは不可
#   2. テストを弱めていないか     既存の assert を消す・テストファイルを消すのは不可
#   3. pytest が全部通るか
#   4. train で改善したか         bench --gate accept(全体0.5pt以上改善、どのセットも1pt超の悪化なし)
#   5. holdout で悪化していないか bench --gate no-worse(holdout の中身はAIに見せない)
#
# 合格なら train の基準を今回の結果に更新して commit する(基準がラチェットのように上がる)。
# holdout の基準は setup 時のまま固定する。毎回更新すると「1回あたり少しだけ悪化」が積み重なるため。
#
# macOS 標準の bash 3.2 でも動くように書いている(連想配列や timeout コマンドは使わない)。
set -u

VETO=""
if [ "${1:-}" = "--veto" ]; then
    VETO="${3:-理由の記載なし}"
    shift
fi
MSG="${1:-}"
if [ -z "$MSG" ]; then
    echo "使い方: tools/gate.sh \"仮説の一行説明\"" >&2
    exit 2
fi
MSG=$(printf '%s' "$MSG" | tr '\n|' '  ')  # LEDGER の表を壊さないよう改行と | を消す

cd "$(dirname "$0")/.." || exit 2
BENCH="${TSCAN_BENCH_DIR:-$HOME/tscan_bench}"
PY="${TSCAN_PYTHON:-.venv/bin/python}"
LEDGER="$BENCH/LEDGER.md"
TS=$(date +%Y%m%d_%H%M%S)
LOG="$BENCH/gate_logs/$TS"
mkdir -p "$LOG" "$BENCH/holdout_runs"

record() {  # record 結果 train holdout
    printf '| %s | %s | %s | %s | %s |\n' "$(date '+%Y-%m-%d %H:%M')" "$1" "$2" "$3" "$MSG" >> "$LEDGER"
}

revert() {
    git checkout -q -- src tests config 2>/dev/null
    git clean -fdq -- src tests config 2>/dev/null
}

fail() {  # fail 結果 train holdout 説明
    revert
    record "$1" "$2" "$3"
    echo "$4"
    echo "GATE=FAIL ($1) 変更は元に戻しました。記録: $LEDGER"
    exit 1
}

cer_of() {  # 結果JSONの全体誤り率
    "$PY" -c 'import json,sys; print("%.2f" % json.load(open(sys.argv[1]))["overall"]["cer"])' "$1" 2>/dev/null || echo "?"
}

if [ -n "$VETO" ]; then
    MSG="$MSG(監査: $(printf '%s' "$VETO" | tr '\n|' '  '))"
    fail "REJECT(監査)" "-" "-" "監査役の指摘: $VETO"
fi

# --- 1. 変更してよい場所か -------------------------------------------------
CHANGED=$( { git diff --name-only --relative HEAD; git ls-files --others --exclude-standard; } | sort -u )
if [ -z "$CHANGED" ]; then
    echo "GATE=FAIL 変更がありません"
    exit 1
fi
OUTSIDE=$(git status --porcelain | grep -v ' textbook-scan/' || true)
BAD=$(printf '%s\n' "$CHANGED" | grep -vE '^(src/tscan/|tests/|config/)' || true)
BAD="$BAD$(printf '%s\n' "$CHANGED" | grep -E '^src/tscan/(bench|evaluate)\.py$' || true)"
if [ -n "$OUTSIDE" ] || [ -n "$BAD" ]; then
    fail "REJECT(禁止領域)" "-" "-" "変更してはいけないファイルがあります: $BAD $OUTSIDE"
fi

# --- 2. テストを弱めていないか ---------------------------------------------
if git diff --relative HEAD -- tests | grep -qE '^-[^-].*assert'; then
    fail "REJECT(テスト削除)" "-" "-" "既存テストの assert が削除・変更されています"
fi
if [ -n "$(git diff --name-only --diff-filter=D --relative HEAD -- tests)" ]; then
    fail "REJECT(テスト削除)" "-" "-" "テストファイルが削除されています"
fi

# --- 3. pytest ------------------------------------------------------------
if ! "$PY" -m pytest tests -q -x -p no:cacheprovider > "$LOG/pytest.txt" 2>&1; then
    tail -30 "$LOG/pytest.txt"
    fail "REJECT(テスト失敗)" "-" "-" "pytest が失敗しました(全文: $LOG/pytest.txt)"
fi

# --- 4. train -------------------------------------------------------------
"$PY" -m tscan bench --split train --baseline "$BENCH/baseline_train.json" \
    --out "$LOG/train.json" > "$LOG/train.txt" 2>&1
TRAIN_VERDICT=$(grep '^VERDICT=' "$LOG/train.txt" | cut -d= -f2)
TRAIN_CER=$(cer_of "$LOG/train.json")
grep -E '全体:|^  [^ ]+: |判定:' "$LOG/train.txt"
if [ "$TRAIN_VERDICT" != "accept" ]; then
    fail "REJECT(train:${TRAIN_VERDICT:-エラー})" "$TRAIN_CER" "-" "train で十分な改善がありません(全文: $LOG/train.txt)"
fi

# --- 5. holdout(中身は表示しない) ---------------------------------------
"$PY" -m tscan bench --split holdout --baseline "$BENCH/baseline_holdout.json" \
    --out "$BENCH/holdout_runs/$TS.json" > "$BENCH/holdout_runs/$TS.txt" 2>&1
HOLD_VERDICT=$(grep '^VERDICT=' "$BENCH/holdout_runs/$TS.txt" | cut -d= -f2)
HOLD_CER=$(cer_of "$BENCH/holdout_runs/$TS.json")
case "$HOLD_VERDICT" in
    accept|neutral) ;;
    *) fail "REJECT(holdout:${HOLD_VERDICT:-エラー})" "$TRAIN_CER" "$HOLD_CER" "train では改善したが、未知の写真(holdout)で悪化しました。その写真にだけ効く修正の可能性があります" ;;
esac

# --- 合格: 基準を更新して commit --------------------------------------------
cp "$LOG/train.json" "$BENCH/baseline_train.json"
git add -A -- src tests config
git commit -q -m "改善ループ: $MSG

train 誤り率 $TRAIN_CER% / holdout $HOLD_CER%($HOLD_VERDICT)
採点ログ: $LOG"
record "ACCEPT $(git rev-parse --short HEAD)" "$TRAIN_CER" "$HOLD_CER"
echo "GATE=PASS train $TRAIN_CER% / holdout $HOLD_CER% で commit しました"
