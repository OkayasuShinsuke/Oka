"""採点用の manifest.json のひな形を作る(docs/improve-loop.md の「準備」)。

    python tools/make_manifest.py ~/tscan_bench

<採点フォルダ>/photos/<セット名>/ にある写真を集め、3枚に1枚を holdout(未知の写真)に振り分ける。
snippets(正解の抜粋)は空なので、写真を見ながら書き写して埋める。
すでに manifest.json があれば、書き写し済みの抜粋を残したまま、新しい写真だけを足す。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bench_dir", type=Path)
    parser.add_argument("--holdout-every", type=int, default=3, help="何枚に1枚を holdout にするか(既定3)")
    args = parser.parse_args()

    bench = args.bench_dir.expanduser()
    manifest_path = bench / "manifest.json"
    existing = {}
    if manifest_path.exists():
        existing = {p["file"]: p for p in json.loads(manifest_path.read_text(encoding="utf-8")).get("photos", [])}

    photos = []
    for set_dir in sorted(p for p in (bench / "photos").iterdir() if p.is_dir()):
        files = sorted(f for f in set_dir.iterdir() if f.suffix.lower() in PHOTO_SUFFIXES)
        for i, f in enumerate(files):
            rel = str(f.relative_to(bench))
            if rel in existing:
                photos.append(existing[rel])
                continue
            # セットごとに振り分けるので、どの撮影条件も train と holdout の両方に入る
            split = "holdout" if i % args.holdout_every == args.holdout_every - 1 else "train"
            photos.append({"file": rel, "set": set_dir.name, "split": split, "snippets": []})

    manifest_path.write_text(json.dumps({"photos": photos}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    empty = sum(1 for p in photos if not p["snippets"])
    print(f"{manifest_path} に {len(photos)} 枚を書きました(うち抜粋が空: {empty} 枚。空の写真は採点されません)")


if __name__ == "__main__":
    main()
