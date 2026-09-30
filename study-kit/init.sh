#!/usr/bin/env bash
# 参考書づくりの作業場(~/study など)を作る。
#
#   study-kit/init.sh ~/study
#
# scaffold/ の中身をコピーするだけ。すでにあるファイルは上書きしない
# (自分で育てた CLAUDE.md や辞書を、うっかり消さないため)。何度実行しても安全。
set -eu

DEST="${1:-$HOME/study}"
KIT=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$DEST"
copied=0
skipped=0
# 隠しファイル(.claude/ .gitignore)も含めて、上書きなしでコピーする
while IFS= read -r file; do
    if [ -e "$DEST/$file" ]; then
        skipped=$((skipped + 1))
    else
        mkdir -p "$(dirname "$DEST/$file")"
        cp -p "$KIT/scaffold/$file" "$DEST/$file"
        copied=$((copied + 1))
    fi
done < <(cd "$KIT/scaffold" && find . -type f ! -name '.DS_Store' | sed 's|^\./||' | sort)

mkdir -p "$DEST/sources" "$DEST/ocr" "$DEST/figures" "$DEST/notes"
chmod +x "$DEST/build.sh" "$DEST"/tools/*.py

if [ ! -d "$DEST/.git" ]; then
    git -C "$DEST" init -q
    echo "git リポジトリを作りました(sources/ と ocr/ は .gitignore で除外済み)"
fi

echo "作業場: $DEST(新規 $copied ファイル / 既存のため据え置き $skipped)"
echo "次の手順: cd $DEST && ./build.sh notes/_example   # LaTeX の動作確認"
