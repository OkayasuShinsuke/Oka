"""「開いた本を手で押さえて撮った写真」の再現(仕様書 §8 の補正機能の検証用)。

tools/make_sample_page.py が作る平らな紙面に対して、
ハイエンドのブック型スキャナが補正対象としている劣化を加える:

    1. ページの湾曲(ノド側が丸まり、文字が縦にずれ・横に圧縮される)
    2. 指の写り込み(ページを押さえる手)
    3. 暗い背景・机の写り込み
    4. 台形歪み・照明ムラ・ノイズ

これにより、tscan.enhance の補正が実際に効いているかを
「補正前後のCER」で定量比較できる。

使い方:
    python tools/simulate_book_photo.py --out-dir /tmp/booksample --pages 3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from make_sample_page import SAMPLE_PAGES, render_page


def curl_page(paper: np.ndarray, strength: float = 0.16, gutter: str = "left") -> np.ndarray:
    """ページの湾曲を再現する。

    開いた本のノド(綴じ側)付近は円筒状に丸まっており、真上から撮ると
        - 縦方向: 紙面がカメラから遠ざかるぶん、上下の端が内側に寄る(edgeが曲線になる)
        - 横方向: 斜めになった面が圧縮されて写る
    という2つの歪みが同時に出る。ここではそれを円筒モデルで近似する。

    strength: 湾曲の強さ(0で平ら)
    gutter  : ノドの位置("left" か "right")
    """
    h, w = paper.shape[:2]
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")

    # ノドからの正規化距離 u(0がノド、1が小口)
    u = (xx / (w - 1)) if gutter == "left" else (1.0 - xx / (w - 1))

    # 円筒面の傾き。ノド付近ほど急に立ち上がる
    lam = 0.30
    tilt = np.exp(-u / lam)  # 1(ノド) → 0(小口)

    # 縦方向: 面が遠ざかるぶん上下が内側に寄る(中心からの距離が縮む)
    center_y = (h - 1) / 2.0
    vertical_scale = 1.0 - strength * tilt
    src_y = center_y + (yy - center_y) / np.maximum(vertical_scale, 0.5)

    # 横方向: ノド側が圧縮されて写る = 元画像ではより広い範囲が同じ幅に詰まる
    #         累積積分で「見かけの x」→「紙面上の x」の対応を作る
    profile = 1.0 / np.maximum(1.0 - strength * np.exp(-np.linspace(0, 1, w) / lam), 0.5)
    if gutter == "right":
        profile = profile[::-1]
    cumulative = np.cumsum(profile)
    cumulative = (cumulative - cumulative[0]) / (cumulative[-1] - cumulative[0]) * (w - 1)
    src_x = np.interp(xx.ravel(), cumulative, np.arange(w, dtype=np.float32)).reshape(xx.shape).astype(np.float32)

    return cv2.remap(
        paper, src_x, src_y.astype(np.float32), interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def add_fingers(canvas: np.ndarray, page_box: tuple[int, int, int, int], seed: int = 0) -> np.ndarray:
    """ページを押さえる指を描き込む(カラー画像に対して行う)。

    指は肌色で、ページの左右の端から内側に少し入り込む。
    高機能スキャナが「指消し」の対象にしているのと同じ状況を作る。
    """
    rng = np.random.default_rng(seed)
    x, y, w, h = page_box
    out = canvas.copy()

    skin = (int(rng.integers(120, 145)), int(rng.integers(150, 172)), int(rng.integers(196, 220)))  # BGR
    finger_w, finger_h = int(w * 0.055), int(h * 0.11)

    for side in ("left", "right"):
        cy = y + h // 2 + int(rng.integers(-h // 8, h // 8))
        cx = x + finger_w // 2 if side == "left" else x + w - finger_w // 2
        cv2.ellipse(out, (cx, cy), (finger_w, finger_h), 0, 0, 360, skin, -1)
        # 爪(わずかに明るい部分)
        nail_cy = cy - finger_h // 2
        cv2.ellipse(out, (cx, nail_cy), (finger_w // 2, finger_h // 5), 0, 0, 360,
                    tuple(min(c + 28, 255) for c in skin), -1)
    return out


def simulate_book_photo(
    paper: np.ndarray, seed: int = 0, curl: float = 0.16, with_fingers: bool = True
) -> np.ndarray:
    """平らな紙面から「本を撮った写真」を作る(カラーBGRで返す)。

    重要: 湾曲は「紙を暗い背景の上に置いてから」かける。
    先に紙だけを湾曲させて白で埋めると、紙の端が直線のまま残ってしまい、
    実際の本の写真(端が曲がり、その外に背景が覗く)と違うものになる。
    """
    h, w = paper.shape[:2]

    # 机(暗い背景マット)の上に平らな紙を置く
    pad = int(min(h, w) * 0.10)
    canvas_gray = np.full((h + pad * 2, w + pad * 2), 22, dtype=np.uint8)
    canvas_gray[pad : pad + h, pad : pad + w] = paper

    # 背景ごと湾曲させる → 紙の上端・下端が曲線になり、その外側に背景が現れる
    canvas_gray = curl_page(canvas_gray, strength=curl)
    canvas = cv2.cvtColor(canvas_gray, cv2.COLOR_GRAY2BGR)
    h, w = paper.shape[:2]

    if with_fingers:
        canvas = add_fingers(canvas, (pad, pad, w, h), seed=seed)

    ch, cw = canvas.shape[:2]

    # 台形歪み(カメラのわずかな傾き)
    rng = np.random.default_rng(seed + 100)
    d = cw * 0.010
    src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    dst = np.float32([
        [pad + rng.uniform(0, d), pad + rng.uniform(0, d)],
        [pad + w - rng.uniform(0, d), pad + rng.uniform(0, d)],
        [pad + w - rng.uniform(0, d), pad + h - rng.uniform(0, d)],
        [pad + rng.uniform(0, d), pad + h - rng.uniform(0, d)],
    ])
    canvas = cv2.warpPerspective(canvas, cv2.getPerspectiveTransform(src, dst), (cw, ch),
                                 borderValue=(22, 22, 22))

    # 照明ムラ + ノイズ
    gradient = np.linspace(0.80, 1.06, cw, dtype=np.float32)[None, :, None]
    lit = np.clip(canvas.astype(np.float32) * gradient, 0, 255)
    lit = cv2.GaussianBlur(lit, (0, 0), sigmaX=0.8)
    noise = np.random.default_rng(seed).normal(0, 3.0, lit.shape)
    return np.clip(lit + noise, 0, 255).astype(np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser(description="本を撮った写真を再現する")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--curl", type=float, default=0.16, help="湾曲の強さ(0で平ら)")
    parser.add_argument("--no-fingers", action="store_true")
    args = parser.parse_args()

    photo_dir = args.out_dir / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)

    truths: dict[int, str] = {}
    math_truths: dict[int, list[str]] = {}

    for i in range(args.pages):
        spec = SAMPLE_PAGES[i % len(SAMPLE_PAGES)]
        page, truth = render_page(spec)
        truths[i + 1] = truth
        math_truths[i + 1] = [spec["formula"]]

        photo = simulate_book_photo(
            np.array(page), seed=i, curl=args.curl, with_fingers=not args.no_fingers
        )
        out_path = photo_dir / f"book_{i + 1:04d}.png"
        cv2.imwrite(str(out_path), photo)
        print(f"generated {out_path} ({photo.shape[1]}x{photo.shape[0]})")

    (args.out_dir / "ground_truth.json").write_text(
        json.dumps(truths, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out_dir / "ground_truth_math.json").write_text(
        json.dumps(math_truths, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"ground truth -> {args.out_dir}")


if __name__ == "__main__":
    main()
