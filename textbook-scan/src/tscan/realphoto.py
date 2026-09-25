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


# 最大の紙の塊に対して、この割合以上の大きさの塊も紙面として採用する(見開きの両ページ)
_MIN_PAGE_PIECE = 0.25


def paper_mask_hsv(image_bgr: np.ndarray, max_saturation: int = 70, min_value: int = 110) -> np.ndarray | None:
    """「明るくて彩度が低い」領域のうち、画像中心を含む連結成分を紙面として返す。"""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    paper = (s < max_saturation) & (v > min_value)
    # 印刷物の色付きの帯・ボックス(青〜緑、OpenCVの色相 35〜150)も紙面の一部として扱う。
    # 机(茶)・手(肌色)・籠(ピンク)の色相はこの範囲に入らないので背景と混ざらない。
    # ページの縁に接した公式ボックスは輪郭の穴埋めでは救えない(外側の切れ込みになる)ため、
    # 色相で最初から紙面に含める。赤・橙の帯は机や肌と色相が重なるので対象外(既知の制約)
    paper |= (s >= max_saturation) & (v > 60) & (h >= 35) & (h <= 150)
    paper = paper.astype(np.uint8) * 255

    paper = cv2.morphologyEx(paper, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))

    count, labels = cv2.connectedComponents(paper)
    if count <= 1:
        return None
    # 十分大きい紙の塊をすべて採用する。以前は「画像の中心を含む塊」だけを使っていたが、
    # 折り目の影で左右のページが分断されると片方のページだけが残り、もう片方が
    # 丸ごと白く塗りつぶされた(実測: APS-Cミラーレスの見開き)。
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    largest = int(sizes.max())
    keep = [i for i in range(1, count) if sizes[i] >= largest * _MIN_PAGE_PIECE]
    mask = np.isin(labels, keep).astype(np.uint8) * 255
    if (mask > 0).mean() < 0.10:
        return None

    mask = _grow_into_shaded_paper(s, v, mask, max_saturation=max_saturation)

    # 内側の穴を全て埋める。青い数式ボックス・図版・見出し帯は彩度が高く
    # 「紙ではない」と判定されるが、それらは紙面の**内側**にある。
    # 外側の輪郭だけを紙面の境界として使い、内部はすべて紙面とみなす
    # (これをしないと、一番大事な公式のボックスが白く塗り潰される)。
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest_area = max(cv2.contourArea(c) for c in contours)
    pages = [c for c in contours if cv2.contourArea(c) >= largest_area * _MIN_PAGE_PIECE]
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, pages, -1, 255, thickness=cv2.FILLED)

    # 閉処理でマスクは実際の紙より最大20px外側に広がる。その分を内側に寄せて、
    # 紙面の縁に机の暗い線が残らないようにする(残ると後段でノイズの塊として読まれる)
    filled = cv2.erode(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
    return filled


def _grow_into_shaded_paper(
    s: np.ndarray, v: np.ndarray, core: np.ndarray, max_saturation: int = 70, relative_value: float = 0.40
) -> np.ndarray:
    """確実な紙(core)から、影になって暗くなった紙へ紙面を広げる。

    紙の判定を「明るさ110以上」の固定値だけで行うと、影に入った紙が背景扱いされ、
    白で塗りつぶされて本文が消えた。実測(APS-Cミラーレス、片側からの照明):
        明るい紙: 明るさ172・彩度4 / 折り目の影の紙: 明るさ95・彩度0 / 手: 彩度128
    左ページの左半分と、右ページの折り目側が丸ごと塗りつぶされていた。

    影になっても紙は「彩度が低い」ままなので、次の条件で広げる。
        - 彩度が max_saturation 未満(手・机・籠と区別できる)
        - 明るさが「この写真の紙の明るさ」の relative_value 倍より上
          (固定値ではなく割合にして、暗く写った写真にも合わせる。
           真っ暗な影や黒いマットは入らない)
        - 確実な紙につながっている、またはページ並みに大きい
          (離れた場所の小さな灰色の物は入らない)
    """
    paper_value = float(np.median(v[core > 0]))
    candidate = ((s < max_saturation) & (v > paper_value * relative_value)).astype(np.uint8) * 255
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    candidate = cv2.bitwise_or(candidate, core)

    _count, labels = cv2.connectedComponents(candidate)
    connected = np.unique(labels[core > 0])
    connected = connected[connected > 0]
    # 確実な紙につながっていなくても、ページ並みに大きい塊は採用する。
    # 片方のページが丸ごと影に入り、しかも暗い折り目で明るいページと分断されると、
    # 影のページには「確実な紙」の種が1つも無いため(テストで確認)。
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    reference = int(sizes[connected].max()) if connected.size else 0
    large = np.flatnonzero(sizes >= reference * _MIN_PAGE_PIECE) if reference else np.array([], dtype=int)
    keep = np.union1d(connected, large[large > 0])
    grown = np.isin(labels, keep).astype(np.uint8) * 255
    return cv2.morphologyEx(grown, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))


def text_line_mask(image_bgr: np.ndarray, paper: np.ndarray | None = None) -> np.ndarray:
    """文字行のマスク。暗い小さな成分を横方向に繋げて「行」の帯にする。"""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    # 「周りより暗い画素」を文字とする(適応的二値化)。画像全体の暗さで判定すると、
    # 折り目の影のような、なだらかに暗い領域まで文字扱いになり、ノドの谷が埋まって
    # 見開きを検出できなかった(実測: APS-Cミラーレスの写真)。
    block = max((min(gray.shape[:2]) // 30) | 1, 15)
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 15)
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


def _text_row_fraction(lines: np.ndarray, min_fraction: float = 0.05) -> float:
    """文字行マスクで、幅の min_fraction 以上に文字がある行の割合(0〜1)。

    見開きの両側に本文があるかの判定に使う。以前は「行の帯が3つ以上あるか」で判定したが、
    湾曲したページでは行が斜めになって行間の隙間が消え、本文が1つの帯に数えられて
    見開きを検出できなかった。割合なら行が斜めでも影響を受けない。
    1行だけの画像(高さの1割強)は、この割合が小さいので除外できる。
    """
    if lines.size == 0:
        return 0.0
    width = max(lines.shape[1], 1)
    rows = (lines > 0).sum(axis=1) >= width * min_fraction
    return float(rows.mean())


def _count_line_bands(lines: np.ndarray, min_fraction: float = 0.15) -> int:
    """文字行マスクの行方向プロファイルから、行の帯(連続して文字がある行の塊)の数を数える。

    その行に幅の min_fraction 以上のインクがあるときだけ「文字がある行」とみなす。本文の行は
    幅のほとんどを占めるが、図の線や罫線は1行あたりの画素が少ない。低い閾値だと図の縦線が
    行間を埋めて1ページが1つの帯に繋がり、見開きなのに単ページと判定された。
    """
    width = max(lines.shape[1], 1)
    rows = (lines > 0).sum(axis=1) >= width * min_fraction
    return int(np.count_nonzero(rows[1:] & ~rows[:-1]) + (1 if rows.size and rows[0] else 0))


def _osd_rotation(pytesseract_module, image_bgr: np.ndarray) -> int | None:
    """Tesseract OSD を1回実行して回転角を返す。判定できなければ None。"""
    try:
        osd = pytesseract_module.image_to_osd(image_bgr, config="--psm 0")
    except Exception:  # noqa: BLE001 — osd.traineddata が無い、文字が少なすぎる等
        return None
    for line in osd.splitlines():
        if line.startswith("Rotate:"):
            try:
                return int(line.split(":")[1].strip()) % 360
            except ValueError:
                return None
    return None


def detect_rotation(image_bgr: np.ndarray) -> int:
    """写真を正立させるのに必要な時計回りの回転角(0/90/180/270)を返す。

    本を横向きに構えて撮ると(見開きを画面いっぱいに入れるための自然な持ち方)、
    写真の中で文字が縦に流れる。Tesseract の OSD(向きと文字体系の判定)を
    半分の解像度で走らせて向きを決める。半解像度では判定がぎりぎりで失敗することが
    あるため、失敗した場合だけフル解像度でもう一度だけ試す。それでも判定できなければ
    0(回転しない)。
    """
    try:
        import pytesseract
    except ImportError:
        return 0
    small = cv2.resize(image_bgr, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    rotation = _osd_rotation(pytesseract, small)
    if rotation is not None:
        return rotation
    rotation = _osd_rotation(pytesseract, image_bgr)
    return rotation if rotation is not None else 0


_ROTATIONS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rotate_upright(image_bgr: np.ndarray, rotation: int) -> np.ndarray:
    """detect_rotation の結果で画像を正立させる。"""
    code = _ROTATIONS.get(rotation % 360)
    return cv2.rotate(image_bgr, code) if code is not None else image_bgr


@dataclass
class SpreadLayout:
    """見開き写真の解析結果。"""

    content_box: tuple[int, int, int, int]  # (x0, y0, x1, y1) 本文の範囲
    gutter_x: int | None  # ノドのx座標(単ページなら None)
    paper: np.ndarray  # 紙面マスク


def _valley(profile: np.ndarray) -> tuple[int, float, float] | None:
    """密度プロファイルの中央40%で最も低い位置を返す: (位置, 谷の値, 判定に使う閾値)。"""
    lo, hi = int(len(profile) * 0.30), int(len(profile) * 0.70)
    if hi - lo < 10:
        return None
    window = profile[lo:hi]
    v = int(np.argmin(window))
    left_peak = profile[:lo].max() if lo > 0 else 0
    right_peak = profile[hi:].max() if hi < len(profile) else 0
    if left_peak <= 0 or right_peak <= 0:
        return None
    return lo + v, float(window[v]), float(min(left_peak, right_peak)) * 0.25


def find_gutter(lines: np.ndarray, x0: int, x1: int, max_tilt_deg: float = 8.0) -> int | None:
    """文字行マスクからノド(見開きの中央の谷)のx座標を求める。単ページなら None。

    本を手で持って撮ると、ノドは写真の中で少し傾く。まっすぐ縦に足し合わせるだけだと、
    傾いた谷は隣の行の文字で埋まって浅くなり、見開きなのに単ページと判定される
    (実測: 谷の値125に対し閾値92で不成立)。そこで画像を少しずつ横にずらして
    (シアー変換)傾きを打ち消しながら探し、最も深い谷を採用する。

    戻り値のx座標は画像の高さの中央での位置。切り出しは矩形なので、そこで左右に分ける。
    """
    band = lines[:, x0:x1]
    if band.size == 0:
        return None
    h, w = band.shape
    best: tuple[float, int] | None = None  # (谷の深さの比, x)
    for tilt in np.arange(-max_tilt_deg, max_tilt_deg + 0.01, 2.0):
        if tilt == 0:
            warped = band
        else:
            t = float(np.tan(np.radians(tilt)))
            matrix = np.float32([[1, -t, t * h / 2], [0, 1, 0]])
            warped = cv2.warpAffine(band, matrix, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        profile = (warped > 0).sum(axis=0).astype(np.float64)
        found = _valley(profile)
        if found is None:
            continue
        pos, value, threshold = found
        if value >= threshold:
            continue
        ratio = value / max(threshold, 1e-6)
        if best is None or ratio < best[0]:
            best = (ratio, pos)
    if best is None:
        return None

    candidate = x0 + best[1]
    # 谷の両側に本文があること(文字のある行が高さの25%以上)。1行だけの画像を見開きと誤認しない
    if _text_row_fraction(lines[:, x0:candidate]) < 0.25 or _text_row_fraction(lines[:, candidate:x1]) < 0.25:
        return None
    return candidate


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
    # 実測で 0.89〜1.18 だったので、境界を 0.80 に置く
    ys, xs = np.where(paper > 0)
    paper_aspect = (xs.max() - xs.min() + 1) / max(ys.max() - ys.min() + 1, 1)
    if paper_aspect < 0.80:
        gutter = None
    else:
        # 「文字のある行の割合」の分母は紙面の高さにする(画像全体だと、本の上下に写った
        # 机まで分母に入り、背景が多く写った写真ほど見開きと判定されにくくなる)
        rows_with_paper = np.flatnonzero(paper.any(axis=1))
        top, bottom = int(rows_with_paper[0]), int(rows_with_paper[-1]) + 1
        gutter = find_gutter(lines[top:bottom], x0, x1)

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
    image_bgr: np.ndarray,
    paper: np.ndarray,
    x_range: tuple[int, int],
    y_range: tuple[int, int],
    margin: float = 0.03,
    bottom_margin: float = 0.08,
) -> tuple[np.ndarray, np.ndarray]:
    """指定範囲の紙面を、周囲に少し余白を付けて切り出す。紙面外は紙の地色で塗る。

    下端(y1側)だけ margin より広い bottom_margin を使う。ノンブルは紙面下端近くに
    印字されるが、紙面マスクは湾曲・影・レンズ歪みで下端をわずかに過小推定しやすく、
    そのままだとノンブルが切り出し画像から欠落してしまうため。

    戻り値: (切り出した画像, 対応する紙面マスク)。マスクは後段の湾曲補正が輪郭を辿るのに使う。
    """
    h, w = image_bgr.shape[:2]
    x0, x1 = x_range
    y0, y1 = y_range
    mx, my = int((x1 - x0) * margin), int((y1 - y0) * margin)
    my_bottom = int((y1 - y0) * bottom_margin)
    x0, x1 = max(x0 - mx, 0), min(x1 + mx, w)
    y0, y1 = max(y0 - my, 0), min(y1 + my_bottom, h)

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
