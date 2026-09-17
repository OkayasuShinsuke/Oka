"""評価用サンプルページの生成(仕様書 §18 のテスト計画・合成画像による検証)。

2段階で作る:
    1. 「印刷された紙面」を描画する(正解テキストが既知)
    2. それを「§6.3のセットアップで撮影した写真」に見せかけて劣化させる
       (台形歪み・照明ムラ・ノイズ・軽いボケ・暗い背景マット)

これにより、実際の教科書を撮影しなくても、
    - 前処理(§8)が歪み・ムラを戻せているか
    - OCR(§9)とレイアウト解析(§10)が正しく読めているか
    - 検証(§11)が誤読を拾えているか
を、正解と突き合わせて定量評価できる(CERは tscan.evaluate)。

使い方:
    python tools/make_sample_page.py --out-dir /tmp/sample --pages 3
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

# 物理教科書を模した本文(横書き)。正解テキストとしてそのまま使う。
SAMPLE_PAGES: list[dict] = [
    {
        "heading": "3.4 ローレンツ力",
        "body": [
            "磁場の中を運動する電荷には力がはたらく。",
            "この力をローレンツ力とよび、次式で表される。",
        ],
        "formula": "F = qvB sin θ",
        "equation_number": "(3.14)",
        "after": [
            "ここで q は電荷、v は速度、B は磁束密度である。",
            "力の向きはフレミングの左手の法則で決まる。",
        ],
        "nombre": "87",
    },
    {
        "heading": "3.5 運動方程式",
        "body": [
            "物体に力がはたらくとき、加速度は力に比例する。",
            "比例定数の逆数が質量であり、次のように書ける。",
        ],
        "formula": "F = ma",
        "equation_number": "(3.15)",
        "after": [
            "この関係を運動方程式とよぶ。",
            "単位はニュートンを用いる。",
        ],
        "nombre": "88",
    },
    {
        "heading": "3.6 力学的エネルギー",
        "body": [
            "運動エネルギーと位置エネルギーの和を考える。",
            "保存力のみがはたらく場合、その和は一定に保たれる。",
        ],
        "formula": "E = K + U",
        "equation_number": "(3.16)",
        "after": [
            "これを力学的エネルギー保存の法則という。",
            "摩擦がある場合は成り立たない。",
        ],
        "nombre": "89",
    },
]


def _find_font() -> str:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    raise RuntimeError("日本語フォントが見つかりません(IPAGothic等をインストールしてください)")


def render_page(spec: dict, width: int = 1400, height: int = 1980) -> tuple[Image.Image, str]:
    """紙面を描画し、(画像, 正解テキスト) を返す。

    サイズはB5の比率(182:257 ≒ 0.708)に合わせてある。
    """
    font_path = _find_font()
    page = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(page)

    f_heading = ImageFont.truetype(font_path, 52)
    f_body = ImageFont.truetype(font_path, 38)
    f_formula = ImageFont.truetype(font_path, 46)
    f_nombre = ImageFont.truetype(font_path, 30)

    margin = 130
    y = margin
    truth: list[str] = []

    draw.text((margin, y), spec["heading"], font=f_heading, fill=20)
    truth.append(spec["heading"])
    y += 110

    for line in spec["body"]:
        draw.text((margin, y), line, font=f_body, fill=25)
        truth.append(line)
        y += 72

    # 中央寄せの数式 + 右端の式番号(§10.2のスコアリングが数式と判定できる形)
    y += 50
    formula = spec["formula"]
    fw = draw.textlength(formula, font=f_formula)
    draw.text(((width - fw) / 2, y), formula, font=f_formula, fill=20)
    eq_no = spec["equation_number"]
    draw.text((width - margin - draw.textlength(eq_no, font=f_body), y + 8), eq_no, font=f_body, fill=20)
    truth.append(formula)
    y += 130

    for line in spec["after"]:
        draw.text((margin, y), line, font=f_body, fill=25)
        truth.append(line)
        y += 72

    # ノンブル(§7.5のページ抜け検出が読み取る対象)
    nombre = spec["nombre"]
    nw = draw.textlength(nombre, font=f_nombre)
    draw.text(((width - nw) / 2, height - margin + 20), nombre, font=f_nombre, fill=40)

    return page, "".join(truth)


def simulate_photo(page: Image.Image, seed: int = 0, tilt: float = 0.012) -> np.ndarray:
    """描画した紙面を「§6.3のセットアップで撮影した写真」らしく劣化させる。"""
    rng = random.Random(seed)
    paper = np.array(page)
    h, w = paper.shape

    # 1) 黒い背景マット(§6.3)の上に紙を置く
    pad = int(min(h, w) * 0.10)
    canvas = np.full((h + pad * 2, w + pad * 2), 18, dtype=np.uint8)
    canvas[pad : pad + h, pad : pad + w] = paper
    ch, cw = canvas.shape

    # 2) カメラのわずかな傾きによる台形歪み(§8.3のP3-P4が直す対象)
    dx, dy = cw * tilt, ch * tilt
    src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    dst = np.float32(
        [
            [pad + rng.uniform(0, dx), pad + rng.uniform(0, dy)],
            [pad + w - rng.uniform(0, dx), pad + rng.uniform(0, dy)],
            [pad + w - rng.uniform(0, dx), pad + h - rng.uniform(0, dy)],
            [pad + rng.uniform(0, dx), pad + h - rng.uniform(0, dy)],
        ]
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(canvas, matrix, (cw, ch), borderValue=18)

    # 3) 照明ムラ(左右のLEDの当たり方の差, §8.5のP7が直す対象)
    gradient = np.linspace(0.82, 1.06, cw, dtype=np.float32)[None, :]
    gradient = np.repeat(gradient, ch, axis=0)
    lit = np.clip(warped.astype(np.float32) * gradient, 0, 255)

    # 4) レンズのわずかなボケと撮像ノイズ(P8が対象)
    blurred = cv2.GaussianBlur(lit, (0, 0), sigmaX=0.8)
    noise = np.random.default_rng(seed).normal(0, 3.2, blurred.shape)
    noisy = np.clip(blurred + noise, 0, 255).astype(np.uint8)

    return noisy


def main() -> None:
    parser = argparse.ArgumentParser(description="評価用サンプルページを生成する")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=len(SAMPLE_PAGES))
    parser.add_argument("--clean", action="store_true", help="劣化させず、印刷紙面そのものを出力する")
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

        if args.clean:
            image = np.array(page)
        else:
            image = simulate_photo(page, seed=i)

        out_path = photo_dir / f"sample_{i + 1:04d}.png"
        cv2.imwrite(str(out_path), image)
        print(f"generated {out_path} ({image.shape[1]}x{image.shape[0]})")

    truth_path = args.out_dir / "ground_truth.json"
    truth_path.write_text(json.dumps(truths, ensure_ascii=False, indent=2), encoding="utf-8")
    math_path = args.out_dir / "ground_truth_math.json"
    math_path.write_text(json.dumps(math_truths, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ground truth -> {truth_path}")
    print(f"math ground truth -> {math_path}")


if __name__ == "__main__":
    main()
