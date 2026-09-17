"""実写真(手持ち・見開き・雑多な背景)向けの紙面検出と分割(仕様書 §8 の拡張)。

tscan.enhance の湾曲補正は「暗い一様な背景に1ページ」という前提で作り、合成画像で
検証した。しかし実際にiPhoneで撮った写真(木の机・ピンクの籠・マット・手・見開き)では
    - 明るさだけの紙面検出(Otsu)が籠やマットまで拾う
    - 肌色検出(YCrCb)が暖色照明下の白い紙を肌と誤認する
という2つの前提崩れが起きた。本モジュールは背景の色に依存しない手がかりで紙面を捉える。

考え方:
    1. 紙は「明るくて彩度が低い」。籠(ピンク)・木目(茶)は彩度が高いので除外できる
    2. 本文の文字がある範囲が紙面の本体。マットや机には文字行がない
    3. 見開きなら、左右の文字ブロックの間(ノド)に文字密度の谷がある
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# 紙面の検出(背景の色に依存しない)
# ---------------------------------------------------------------------------


def paper_mask_hsv(image_bgr: np.ndarray, max_saturation: int = 70, min_value: int = 110) -> np.ndarray | None:
    """「明るくて彩度が低い」領域のうち、画像中心を含む連結成分を紙面として返す。"""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    _h, s, v = cv2.split(hsv)
    paper = ((s < max_saturation) & (v > min_value)).astype(np.uint8) * 255

    paper = cv2.morphologyEx(paper, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))

    count, labels = cv2.connectedComponents(paper)
    if count <= 1:
        return None
    cy, cx = image_bgr.shape[0] // 2, image_bgr.shape[1] // 2
    label = labels[cy, cx]
    if label == 0:
        # 中心が紙でなければ最大の成分を使う
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        label = int(sizes.argmax())
    mask = (labels == label).astype(np.uint8) * 255
    if (mask > 0).mean() < 0.10:
        return None

    # 内側の穴を全て埋める。青い数式ボックス・図版・見出し帯は彩度が高く
    # 「紙ではない」と判定されるが、それらは紙面の**内側**にある。
    # 外側の輪郭だけを紙面の境界として使い、内部はすべて紙面とみなす
    # (これをしないと、一番大事な公式のボックスが白く塗り潰される)。
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, [max(contours, key=cv2.contourArea)], -1, 255, thickness=cv2.FILLED)

    # 閉処理でマスクは実際の紙より最大20px外側に広がる。その分を内側に寄せて、
    # 紙面の縁に机の暗い線が残らないようにする(残ると後段でノイズの塊として読まれる)
    filled = cv2.erode(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
    return filled


def text_line_mask(image_bgr: np.ndarray, paper: np.ndarray | None = None) -> np.ndarray:
    """文字行のマスク。暗い小さな成分を横方向に繋げて「行」の帯にする。"""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if paper is not None:
        ink = cv2.bitwise_and(ink, paper)  # 紙面内のインクだけ

    scale = max(image_bgr.shape[1] // 80, 8)
    lines = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (scale, 3)))
    lines = cv2.morphologyEx(lines, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (scale // 2, 1)))
    return lines


def dense_span(profile: np.ndarray, fraction: float = 0.12, min_gap: int = 40) -> tuple[int, int] | None:
    """密度プロファイルから「本文がある連続区間」を求める。

    最大値の fraction 倍を超える位置を本文とみなし、min_gap 以下の途切れは繋げる。
    最も長い区間を返す(縁のノイズは短い区間になるので落ちる)。
    """
    if profile.size == 0 or profile.max() <= 0:
        return None
    active = profile > profile.max() * fraction

    # 短い途切れを埋める
    kernel = np.ones(min_gap, dtype=np.uint8)
    filled = cv2.dilate(active.astype(np.uint8)[None, :], kernel[None, :]).ravel() > 0
    filled = cv2.erode(filled.astype(np.uint8)[None, :], kernel[None, :]).ravel() > 0

    best: tuple[int, int] | None = None
    start = None
    for i, flag in enumerate(np.concatenate([filled, [False]])):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if best is None or (i - start) > (best[1] - best[0]):
                best = (start, i)
            start = None
    return best


def _count_line_bands(lines: np.ndarray) -> int:
    """文字行マスクの行方向プロファイルから、行の帯(連続して文字がある行の塊)の数を数える。"""
    rows = (lines > 0).sum(axis=1) > 0
    return int(np.count_nonzero(rows[1:] & ~rows[:-1]) + (1 if rows.size and rows[0] else 0))


@dataclass
class SpreadLayout:
    """見開き写真の解析結果。"""

    content_box: tuple[int, int, int, int]  # (x0, y0, x1, y1) 本文の範囲
    gutter_x: int | None  # ノドのx座標(単ページなら None)
    paper: np.ndarray  # 紙面マスク


def analyze_spread(image_bgr: np.ndarray) -> SpreadLayout | None:
    """紙面・本文範囲・ノドの位置を求める。"""
    paper = paper_mask_hsv(image_bgr)
    if paper is None:
        return None
    lines = text_line_mask(image_bgr, paper)

    row_profile = (lines > 0).sum(axis=1).astype(np.float64)
    col_profile = (lines > 0).sum(axis=0).astype(np.float64)
    rows = dense_span(row_profile, fraction=0.10, min_gap=max(image_bgr.shape[0] // 12, 40))
    cols = dense_span(col_profile, fraction=0.08, min_gap=max(image_bgr.shape[1] // 10, 40))
    if rows is None or cols is None:
        return None

    y0, y1 = rows
    x0, x1 = cols

    # ノド: 本文範囲の中央40%で、文字密度が最も低い列。谷が浅ければ単ページ。
    # ただし紙面の形が縦長なら物理的に1ページなので、谷があっても見開きとしない
    # (1ページ内の見出しの隙間や段組の間を「ノド」と誤認するのを防ぐ)。
    # B5/A4/A5 の1ページは幅/高さ ≒ 0.71。見開きは本の厚み(上下に写るページの束)を含めても
    # 実測で 0.89〜1.06 だったので、境界を 0.80 に置く
    ys, xs = np.where(paper > 0)
    paper_aspect = (xs.max() - xs.min() + 1) / max(ys.max() - ys.min() + 1, 1)
    inner = col_profile[x0:x1]
    lo, hi = int(len(inner) * 0.30), int(len(inner) * 0.70)
    if hi - lo < 10 or paper_aspect < 0.80:
        gutter = None
    else:
        window = inner[lo:hi]
        valley = int(np.argmin(window))
        # 左右にしっかり本文があり、谷が両側の1/4以下なら見開き
        left_peak = inner[:lo].max() if lo > 0 else 0
        right_peak = inner[hi:].max() if hi < len(inner) else 0
        candidate = x0 + lo + valley
        if (
            left_peak > 0
            and right_peak > 0
            and window[valley] < min(left_peak, right_peak) * 0.25
            and _count_line_bands(lines[:, x0:candidate]) >= 3
            and _count_line_bands(lines[:, candidate:x1]) >= 3
        ):
            # 谷の両側にそれぞれ3行以上の本文がある → 見開き。
            # (1行だけの画像の語間の隙間や、図の余白を「ノド」と誤認しない)
            gutter = candidate
        else:
            gutter = None

    return SpreadLayout(content_box=(x0, y0, x1, y1), gutter_x=gutter, paper=paper)


# ---------------------------------------------------------------------------
# 見開きの分割と各ページの切り出し
# ---------------------------------------------------------------------------


def fill_outside_paper(image_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """紙面外(机・手など)を、周囲の紙の明るさに合わせて塗る。

    一様な色で塗ると、照明ムラのある紙との境目が薄い線として残り、照明補正と鮮鋭化で
    強調されてOCRが文字として拾う(合成画像で「^、」という余計な文字が出た)。
    そこで「文字を消した紙の明るさ」を大きな核の閉処理+ぼかしで推定し、その値で塗る。
    塗った領域の推定値は周囲の紙から滲み込んだものになるので、境目が目立たない。
    2回繰り返して、塗った領域の内側まで周囲の明るさを行き渡らせる。
    """
    h, w = image_bgr.shape[:2]
    outside = mask == 0
    if not outside.any():
        return image_bgr.copy()
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    inside = gray[~outside]
    bright = inside > np.percentile(inside, 60) if inside.size else np.zeros(0, bool)
    base = np.median(image_bgr[~outside][bright], axis=0).astype(np.uint8) if bright.any() else np.array([245, 245, 245], np.uint8)

    filled = image_bgr.copy()
    filled[outside] = base
    kernel = max(31, (min(h, w) // 30) | 1)
    for _ in range(2):
        background = cv2.morphologyEx(filled, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))
        background = cv2.GaussianBlur(background, (0, 0), sigmaX=kernel / 2)
        filled[outside] = background[outside]
    return filled


def _page_crop(
    image_bgr: np.ndarray, paper: np.ndarray, x_range: tuple[int, int], y_range: tuple[int, int], margin: float = 0.03
) -> tuple[np.ndarray, np.ndarray]:
    """指定範囲の紙面を、周囲に少し余白を付けて切り出す。紙面外は紙の地色で塗る。

    戻り値: (切り出した画像, 対応する紙面マスク)。マスクは後段の湾曲補正が輪郭を辿るのに使う。
    """
    h, w = image_bgr.shape[:2]
    x0, x1 = x_range
    y0, y1 = y_range
    mx, my = int((x1 - x0) * margin), int((y1 - y0) * margin)
    x0, x1 = max(x0 - mx, 0), min(x1 + mx, w)
    y0, y1 = max(y0 - my, 0), min(y1 + my, h)

    crop = image_bgr[y0:y1, x0:x1].copy()
    mask_crop = paper[y0:y1, x0:x1]

    crop = fill_outside_paper(crop, mask_crop)
    return crop, mask_crop.copy()


def split_spread_photo(image_bgr: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]] | None:
    """見開き写真を左右のページに分ける。各要素は (画像, 紙面マスク)。単ページなら1要素。

    切り出す範囲は**紙面マスクの物理的な広がり**(ノドで左右に分ける)。本文の範囲で切ると、
    文字の少ないページ(章の扉など)で余白ごと切り落としてしまう。本文範囲はノドの位置を
    決めるためだけに使う。
    """
    layout = analyze_spread(image_bgr)
    if layout is None:
        return None

    def _extent(x_lo: int, x_hi: int) -> tuple[tuple[int, int], tuple[int, int]] | None:
        region = layout.paper[:, x_lo:x_hi]
        rows = np.where(region.any(axis=1))[0]
        cols = np.where(region.any(axis=0))[0]
        if rows.size == 0 or cols.size == 0:
            return None
        return (x_lo + int(cols[0]), x_lo + int(cols[-1]) + 1), (int(rows[0]), int(rows[-1]) + 1)

    w = image_bgr.shape[1]
    if layout.gutter_x is None:
        extent = _extent(0, w)
        return [_page_crop(image_bgr, layout.paper, *extent)] if extent else None

    g = layout.gutter_x
    pages = []
    for x_lo, x_hi in ((0, g), (g, w)):
        extent = _extent(x_lo, x_hi)
        if extent is None:
            return None
        pages.append(_page_crop(image_bgr, layout.paper, *extent))
    return pages


# ---------------------------------------------------------------------------
# パイプラインからの利用(見開きの判定と、片側ページの取り出し)
# ---------------------------------------------------------------------------


def detect_spread(image_bgr: np.ndarray) -> str | None:
    """見開き写真なら "spread"、1ページなら "single"、紙面を検出できなければ None。"""
    layout = analyze_spread(image_bgr)
    if layout is None:
        return None
    return "spread" if layout.gutter_x is not None else "single"


def extract_page(image_bgr: np.ndarray, side: str | None) -> tuple[np.ndarray, np.ndarray] | None:
    """写真から指定した側のページを取り出す。side は "left" / "right" / "single" / None。

    見開きなのに side が None なら左ページを返す(呼び出し側が右ページ用の Page を用意する前提)。
    """
    pages = split_spread_photo(image_bgr)
    if not pages:
        return None
    if side == "right":
        return pages[-1]
    return pages[0]
