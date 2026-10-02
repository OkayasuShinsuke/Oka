#!/usr/bin/env python3
"""LiDAR深度マップ(TIFF)に、ページの湾曲が写っているかを調べる。

    python tools/view_depth.py depth.tiff            # 数字を表示し、depth_view.png を作る
    python tools/view_depth.py depth.tiff --out /tmp/look.png

見るのは次の2つ。
    湾曲の高さ  … 紙面の中央付近で、傾き(カメラの向き)を除いた凹凸の幅
    ノイズ      … 隣り合う画素どうしの細かいばらつき
    比 = 湾曲の高さ ÷ ノイズ。比が大きいほど、深度から湾曲を読み取れる(目安は下の判定に表示)

深度の単位はメートルを想定(中央値が20より大きければミリメートルとみなす)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def load_depth(path: Path) -> np.ndarray:
    """深度TIFFを float32 の配列(メートル)として読む。無効な画素は NaN にする。"""
    image = Image.open(path)
    depth = np.asarray(image).astype(np.float32)
    if depth.ndim == 3:  # 念のため。深度は1チャンネルのはず
        depth = depth[:, :, 0]
    depth[~np.isfinite(depth) | (depth <= 0)] = np.nan
    if np.nanmedian(depth) > 20:  # ミリメートルで保存されている
        depth = depth / 1000.0
    return depth


def fill_nan(depth: np.ndarray) -> np.ndarray:
    """欠けた画素を中央値で埋める(フィルタ計算のため)。"""
    return np.where(np.isnan(depth), np.nanmedian(depth), depth).astype(np.float32)


def analyze(depth: np.ndarray, center: float = 0.6) -> dict:
    """湾曲の高さ(mm)とノイズ(mm)を推定する。"""
    h, w = depth.shape
    filled = fill_nan(depth)

    # ノイズ: 少しぼかした画像との差のばらつき(MADから標準偏差へ換算)
    residual = filled - cv2.GaussianBlur(filled, (0, 0), 2.0)
    noise = 1.4826 * float(np.nanmedian(np.abs(residual - np.nanmedian(residual))))

    # 湾曲: 中央部に平面(カメラの傾き)を当てはめて引いた残りの幅
    y0, y1 = int(h * (1 - center) / 2), int(h * (1 + center) / 2)
    x0, x1 = int(w * (1 - center) / 2), int(w * (1 + center) / 2)
    smooth = cv2.GaussianBlur(filled, (0, 0), 3.0)[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    design = np.column_stack([xx.ravel(), yy.ravel(), np.ones(xx.size)])
    coef, *_ = np.linalg.lstsq(design, smooth.ravel(), rcond=None)
    flat = smooth.ravel() - design @ coef
    lo, hi = np.percentile(flat, [5, 95])

    return {
        "size": (w, h),
        "valid": float(np.mean(~np.isnan(depth))),
        "median_cm": float(np.nanmedian(depth) * 100),
        "relief_mm": float((hi - lo) * 1000),
        "noise_mm": noise * 1000,
        "ratio": float((hi - lo) / noise) if noise > 0 else float("inf"),
    }


def verdict(ratio: float) -> str:
    # 目安。湾曲の高さがノイズの何倍あれば形が追えるか、という経験則であって厳密な基準ではない
    if ratio >= 6:
        return "湾曲がはっきり写っています。補正に使える見込みがあります。"
    if ratio >= 3:
        return "湾曲は写っていますが、ノイズが大きめです。文字行と併用すれば使えるかもしれません。"
    return "湾曲がノイズに埋もれています。深度だけでの補正は難しそうです。"


def remove_plane(depth: np.ndarray) -> np.ndarray:
    """全体に当てはめた平面(カメラの傾き)を引く。凹凸だけを見るため。"""
    h, w = depth.shape
    yy, xx = np.mgrid[0:h, 0:w]
    valid = ~np.isnan(depth)
    design = np.column_stack([xx[valid], yy[valid], np.ones(valid.sum())])
    coef, *_ = np.linalg.lstsq(design, depth[valid], rcond=None)
    plane = coef[0] * xx + coef[1] * yy + coef[2]
    return depth - plane.astype(np.float32)


def render(depth: np.ndarray, out: Path) -> None:
    """凹凸(傾きを引いた深度)を色で表した画像を保存する。近い=赤、遠い=青。

    生の距離で塗ると、カメラの傾きによるグラデーションが目立って、
    ページの谷のような小さな凹凸が見えなくなる。
    """
    relief = remove_plane(depth) * 1000  # mm
    lo, hi = np.nanpercentile(relief, [2, 98])
    scaled = np.clip((relief - lo) / max(hi - lo, 1e-6), 0, 1)
    colored = cv2.applyColorMap((255 * (1 - scaled)).astype(np.uint8), cv2.COLORMAP_TURBO)
    colored[np.isnan(depth)] = 0
    if colored.shape[1] < 800:  # LiDARの深度マップは小さいので、見やすく拡大する
        scale = 800 / colored.shape[1]
        colored = cv2.resize(colored, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    cv2.putText(colored, f"red = near ({lo:.0f}mm)   blue = far ({hi:.0f}mm)   tilt removed", (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.imwrite(str(out), colored)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("depth", type=Path)
    parser.add_argument("--out", type=Path, default=Path("depth_view.png"))
    args = parser.parse_args()

    depth = load_depth(args.depth)
    result = analyze(depth)
    render(depth, args.out)
    print(f"サイズ {result['size'][0]}x{result['size'][1]} / 有効な画素 {result['valid'] * 100:.0f}% / "
          f"中央値の距離 {result['median_cm']:.1f}cm")
    print(f"湾曲の高さ {result['relief_mm']:.1f}mm / ノイズ {result['noise_mm']:.1f}mm / 比 {result['ratio']:.1f}")
    print(verdict(result["ratio"]))
    print(f"色で表した画像: {args.out}")


if __name__ == "__main__":
    main()
