#!/usr/bin/env bash
# 単元をPDFにする。
#
#   ./build.sh notes/2-6_分岐公式     その単元(main.tex のあるフォルダ)を作る
#   ./build.sh --all                  notes/ の全単元を作る
#   ./build.sh --clean notes/2-6_分岐公式   作りかけの中間ファイルを消す
#
# 成功すると notes/<単元>/<単元>.pdf ができる。失敗すると、原因の行だけを短く表示する
# (LaTeXのログは数千行あるので、エラーを探す手間を省くため)。
# macOS 標準の bash 3.2 でも動く。
set -u

ROOT=$(cd "$(dirname "$0")" && pwd)
# template/(共通設定)と figures/(図)を、フォルダの深さに関係なく見つけられるようにする
export TEXINPUTS=".:$ROOT/template:$ROOT/figures:"

if ! command -v latexmk > /dev/null; then
    echo "latexmk が見つかりません。MacTeX を入れて、ターミナルを開き直してください:" >&2
    echo "  brew install --cask mactex-no-gui" >&2
    exit 2
fi

build_one() {
    dir="${1%/}"
    name=$(basename "$dir")
    if [ ! -f "$dir/main.tex" ]; then
        echo "main.tex がありません: $dir" >&2
        return 2
    fi
    # 途中のファイルは build/ にまとめる(-outdir)。エラーで止まったら即終了(-halt-on-error)
    mkdir -p "$dir/build"
    if (cd "$dir" && latexmk -lualatex -interaction=nonstopmode -halt-on-error -file-line-error \
            -outdir=build main.tex > build/latexmk.out 2>&1); then
        cp "$dir/build/main.pdf" "$dir/$name.pdf"
        undefined=$(grep -c "LaTeX Warning: .*undefined" "$dir/build/main.log" 2> /dev/null || true)
        overfull=$(grep -c "Overfull \\\\hbox" "$dir/build/main.log" 2> /dev/null || true)
        echo "OK: $dir/$name.pdf(未定義の参照 ${undefined:-0} / 行のはみ出し ${overfull:-0})"
        return 0
    fi
    echo "FAIL: $dir"
    # 「ファイル:行番号: 原因」の行と、その直後の数行(どの命令で起きたか)だけを出す
    grep -E -A4 '^(\./)?[^ ]+\.(tex|sty|cls):[0-9]+:|^! ' "$dir/build/main.log" 2> /dev/null | head -40
    echo "(全文: $dir/build/main.log)"
    return 1
}

case "${1:-}" in
    "" | -h | --help)
        sed -n 2,9p "$0"
        exit 2
        ;;
    --all)
        status=0
        for main in "$ROOT"/notes/*/main.tex; do
            [ -f "$main" ] || continue
            build_one "$(dirname "$main")" || status=1
        done
        exit $status
        ;;
    --clean)
        shift
        rm -rf "${1%/}/build"
        echo "消しました: ${1%/}/build"
        ;;
    *)
        build_one "$1"
        ;;
esac
