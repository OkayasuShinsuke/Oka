#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow", "pillow-heif"]
# ///
"""ページ画像から図の部分を切り抜いて figures/ に保存する。

    uv run tools/crop.py sources/young_em/IMG_0001.jpg 0.10 0.40 0.90 0.70 figures/em-2-6a.png

座標は「画像の幅・高さに対する割合(0〜1)」で、左上 x0 y0、右下 x1 y1 の順。
ピクセルではなく割合にしているのは、Claude が見ている縮小後の画像と、
元の高解像度の画像とで、同じ位置を指せるようにするため。
図は描き直さず、元の写真から切り抜いて貼る(誤りが入らないため)。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


def crop(src: Path, box: tuple[float, float, float, float], out: Path) -> tuple[int, int]:
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("座標は 0 <= x0 < x1 <= 1, 0 <= y0 < y1 <= 1 の割合で指定してください")
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    image = Image.open(src).convert("RGB")
    w, h = image.size
    cropped = image.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
    out.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out)
    return cropped.size


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    size = crop(Path(sys.argv[1]), tuple(float(v) for v in sys.argv[2:6]), Path(sys.argv[6]))
    print(f"保存しました: {sys.argv[6]} ({size[0]}x{size[1]}px)")
