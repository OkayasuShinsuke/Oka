#!/usr/bin/env bash
# 自動改善ループの起動係(docs/improve-loop.md)。
#
#   tools/improve_loop.sh setup        改善ループ専用の作業場所(git worktree)を作り、基準を測る
#   tools/improve_loop.sh run [回数]   /improve を回数分(既定20)くり返す
#   tools/improve_loop.sh status       成績表(LEDGER.md)と、ループが積んだ commit を表示
#   tools/improve_loop.sh stop         実行中のループを、今の1回が終わったところで止める
#
# ふだん使っている作業ツリーには一切触らない。ループは $TSCAN_BENCH_DIR/loop に
# 別の作業ツリー(ブランチ improve/loop)を作り、そこだけを書き換える。
# 結果は improve/loop ブランチの commit として残るので、見て良ければ自分でマージする。
#
# macOS 標準の bash 3.2 でも動くように書いている。
set -u

BENCH="${TSCAN_BENCH_DIR:-$HOME/tscan_bench}"
export TSCAN_BENCH_DIR="$BENCH"
LOOP="$BENCH/loop"
APP="$LOOP/textbook-scan"
BRANCH="${TSCAN_LOOP_BRANCH:-improve/loop}"
MAX_REJECTS="${TSCAN_MAX_REJECTS:-5}"   # 連続でこの回数不合格なら打ち止め
LEDGER="$BENCH/LEDGER.md"

# 見張りなしで動かすため、許可するツールを絞る。
# 書き換えは worktree と採点フォルダの中だけ(acceptEdits)、実行できるのは Python・関所・git の閲覧に限る。
ALLOWED_TOOLS="Read,Edit,Write,Glob,Grep,Agent,Task,TodoWrite,\
Bash(tools/gate.sh:*),Bash(./tools/gate.sh:*),Bash(.venv/bin/python:*),Bash(.venv/bin/tscan:*),\
Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git checkout:*),\
Bash(ls:*),Bash(wc:*),Bash(mkdir:*)"
# holdout(未知の写真)の中身と正解はAIに見せない。見て直すと holdout の意味がなくなる
DENIED_TOOLS="Read(/$BENCH/manifest.json),Read(/$BENCH/baseline_holdout.json),Read(/$BENCH/holdout_runs/**),\
Edit(/$BENCH/manifest.json),Edit(/$BENCH/baseline_*.json),Write(/$BENCH/baseline_*.json),\
Bash(git commit:*),Bash(git reset:*),Bash(git push:*)"
CLAUDE_FLAGS="${TSCAN_CLAUDE_FLAGS:---permission-mode acceptEdits}"

die() { echo "エラー: $*" >&2; exit 1; }

cmd_setup() {
    [ -f "$BENCH/manifest.json" ] || die "$BENCH/manifest.json がありません(docs/improve-loop.md の「準備」を参照)"
    command -v uv > /dev/null || die "uv がありません(brew install uv)"
    REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel) || die "git リポジトリの中で実行してください"

    if [ ! -d "$LOOP" ]; then
        if git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
            git -C "$REPO" worktree add "$LOOP" "$BRANCH" || die "worktree を作れませんでした"
        else
            git -C "$REPO" worktree add -b "$BRANCH" "$LOOP" HEAD || die "worktree を作れませんでした"
        fi
        git -C "$LOOP" rev-parse HEAD > "$BENCH/loop_base"  # status で「ループが足した commit」を出すための起点
    fi
    echo "作業場所: $LOOP(ブランチ $BRANCH)"

    cd "$APP" || die "$APP がありません"
    EXTRAS="dev"
    [ "$(uname)" = "Darwin" ] && EXTRAS="dev,apple-vision"
    [ -x .venv/bin/python ] || uv venv --python 3.11 .venv || die "venv を作れませんでした"
    uv pip install --python .venv/bin/python -e ".[$EXTRAS]" || die "インストールに失敗しました"

    echo "基準を測ります(train / holdout)…"
    .venv/bin/python -m tscan bench --split train --save-baseline || die "train の採点に失敗しました"
    .venv/bin/python -m tscan bench --split holdout --save-baseline > /dev/null || die "holdout の採点に失敗しました"
    if [ ! -f "$LEDGER" ]; then
        printf '# 改善ループの成績表\n\n| 日時 | 結果 | train誤り率 | holdout誤り率 | 仮説 |\n|---|---|---|---|---|\n' > "$LEDGER"
    fi
    printf '| %s | 基準 %s | %s | %s | setup |\n' "$(date '+%Y-%m-%d %H:%M')" "$(git rev-parse --short HEAD)" \
        "$(.venv/bin/python -c 'import json;print(json.load(open("'"$BENCH"'/baseline_train.json"))["overall"]["cer"])')" \
        "$(.venv/bin/python -c 'import json;print(json.load(open("'"$BENCH"'/baseline_holdout.json"))["overall"]["cer"])')" >> "$LEDGER"
    echo "準備完了。tools/improve_loop.sh run で開始します"
}

# LEDGER の末尾から数えて、何回連続で不合格か
consecutive_rejects() {
    [ -f "$LEDGER" ] || { echo 0; return; }
    grep '^| 20' "$LEDGER" | awk -F'|' '
        { r[NR] = $3 }
        END { n = 0; for (i = NR; i >= 1; i--) { if (r[i] ~ /REJECT|ABORT/) n++; else break }; print n }'
}

cmd_run() {
    N="${1:-20}"
    command -v claude > /dev/null || die "claude コマンドがありません(Claude Code をインストールしてください)"
    [ -d "$APP" ] || die "先に tools/improve_loop.sh setup を実行してください"
    [ -f "$BENCH/baseline_train.json" ] || die "基準がありません。setup をやり直してください"
    rm -f "$BENCH/STOP"
    mkdir -p "$BENCH/sessions"
    cd "$APP" || exit 1

    i=1
    while [ "$i" -le "$N" ]; do
        if [ -f "$BENCH/STOP" ]; then
            echo "STOP ファイルがあるので終了します"; rm -f "$BENCH/STOP"; break
        fi
        R=$(consecutive_rejects)
        if [ "$R" -ge "$MAX_REJECTS" ]; then
            echo "$R 回連続で不合格なので打ち止めにします(今の方針で取れる改善は出尽くした可能性)"; break
        fi

        LOGF="$BENCH/sessions/$(date +%Y%m%d_%H%M%S)_iter$i.log"
        echo "=== $i / $N 回目($(date '+%H:%M'))ログ: $LOGF"
        # $CLAUDE_FLAGS は複数の引数に分けたいので引用符で囲まない
        # shellcheck disable=SC2086
        claude -p "/improve" $CLAUDE_FLAGS --add-dir "$BENCH" \
            --allowedTools "$ALLOWED_TOOLS" --disallowedTools "$DENIED_TOOLS" > "$LOGF" 2>&1
        tail -5 "$LOGF"

        # 関所を通らずに終わった変更は捨てる(次の回に持ち越さない)
        if [ -n "$(git status --porcelain -- src tests config)" ]; then
            git checkout -q -- src tests config; git clean -fdq -- src tests config
            printf '| %s | ABORT | - | - | 関所を通らずに終了(ログ: %s) |\n' "$(date '+%Y-%m-%d %H:%M')" "$LOGF" >> "$LEDGER"
        fi
        grep '^| 20' "$LEDGER" | tail -1
        i=$((i + 1))
    done
    cmd_status
}

cmd_status() {
    [ -f "$LEDGER" ] && tail -25 "$LEDGER"
    if [ -d "$LOOP" ]; then
        echo
        echo "ループが積んだ commit(見て良ければ: git merge $BRANCH):"
        if [ -f "$BENCH/loop_base" ]; then
            git -C "$LOOP" log --oneline "$(cat "$BENCH/loop_base")..HEAD"
        else
            git -C "$LOOP" log --oneline -10
        fi
    fi
}

cmd_stop() {
    mkdir -p "$BENCH" && touch "$BENCH/STOP"
    echo "今の1回が終わったところで止まります"
}

case "${1:-}" in
    setup) cmd_setup ;;
    run) shift; cmd_run "$@" ;;
    status) cmd_status ;;
    stop) cmd_stop ;;
    *) sed -n '2,12p' "$0"; exit 2 ;;
esac
