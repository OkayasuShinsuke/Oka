"""ブック型スキャナ相当の補正(仕様書 §8 の拡張)。

ハイエンドのブック型スキャナ(ScanSnap SV600、CZUR等)が備えている
3つの補正をソフトウェアで実現する。

    1. 湾曲補正(dewarp)   : 開いた本のページの丸まりを平らに戻す
    2. 指の除去           : ページを押さえる指を消す
    3. 背景の消去         : 紙面の外側(机・マット)を白で塗り潰す

仕様書§6.3では「アクリル板で物理的に平らにするのが確実で、ソフトのdewarpingは
それより難しい」と書いた。それは今も正しいが、本モジュールがあれば
**アクリル板を使わずに撮ったページも救済できる**(300ページで板を上げ下げする
手間が省ける)。物理対策と排他ではなく、併用できる保険として位置づける。

湾曲補正の考え方:
    開いた本のページは、ノド(綴じ側)を軸とした円筒面に近い。
    真上から撮ると、
        - ページの上端・下端が「曲線」になる(平らなら直線のはず)
        - ノド付近の文字が横方向に圧縮される(斜めの面を正面から見るため)
    という2つの歪みが同時に出る。
    そこで**上端と下端の曲線を実測し、それを直線に戻す変換**を作れば、
    縦方向のズレと横方向の圧縮を同時に補正できる。
"""
from __future__ import annotations

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 紙面の輪郭(上端・下端の曲線)を求める
# ---------------------------------------------------------------------------


def page_mask(image: np.ndarray, min_area_ratio: float = 0.15) -> np.ndarray | None:
    """紙面(明るい大きな塊)のマスクを返す。見つからなければ None。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 文字や指による穴を埋めて、紙面を1つの塊にする
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < gray.size * min_area_ratio:
        return None

    mask = np.zeros_like(gray)
    cv2.drawContours(mask, [largest], -1, 255, thickness=cv2.FILLED)

    # 紙は「周囲より明るい」はず。一様に暗い画像でOtsuが全面を前景と判定しても、
    # それを紙面として返さないようにする
    inside = gray[mask > 0]
    outside = gray[mask == 0]
    if inside.size == 0 or float(inside.mean()) < 100:
        return None
    if outside.size > 0 and float(inside.mean()) - float(outside.mean()) < 20:
        return None
    return mask


def trace_page_edges(
    mask: np.ndarray, poly_order: int = 3, max_residual_ratio: float = 0.06
) -> tuple[np.ndarray, np.ndarray, int, int] | None:
    """紙面マスクから、各列の上端・下端のy座標を滑らかな曲線として取り出す。

    戻り値: (上端y[列], 下端y[列], 左端x, 右端x)。推定が信用できなければ None。

    ここは湾曲補正の土台になるため、**信用できない推定を返さない**ことを最優先にしている。
    指や影がマスクに混ざると輪郭が乱れ、それに多項式が過剰適合すると
    波打った変換になって画像を破壊してしまう(実測でCERが3.8%→338%に悪化した)。
    そこで、
        1. 低次(3次)の多項式を、外れ値を繰り返し除きながら当てる
        2. 当てはまりが悪ければ None を返し、呼び出し側に補正を諦めさせる
    という2段構えにしている。
    """
    columns = np.where(mask.any(axis=0))[0]
    if columns.size < 50:
        return None
    x0, x1 = int(columns[0]), int(columns[-1])

    width = x1 - x0 + 1
    top = np.full(width, np.nan, dtype=np.float64)
    bottom = np.full(width, np.nan, dtype=np.float64)

    for i, x in enumerate(range(x0, x1 + 1)):
        rows = np.where(mask[:, x] > 0)[0]
        if rows.size:
            top[i] = rows[0]
            bottom[i] = rows[-1]

    valid = ~np.isnan(top) & ~np.isnan(bottom)
    if valid.sum() < width * 0.5:
        return None

    median_height = float(np.nanmedian(bottom - top))
    if median_height <= 10:
        return None

    # 注意: 「ページの高さが列ごとに変わること」自体は外れ値ではない。
    # 湾曲しているページは端ほど高さが縮むので、それこそが検出したい信号である。
    # 指のような局所的な乱れだけを外れ値として落とすため、
    # 絶対値ではなく「滑らかな曲線からのズレ」で判定する(_robust_polyfitが担当)。
    index = np.arange(width, dtype=np.float64)
    fitted = []
    for values in (top, bottom):
        curve = _robust_polyfit(index[valid], values[valid], index, poly_order)
        if curve is None:
            return None
        # 当てはまりの評価は、外れ値を除いた中央付近の残差で行う
        residual = np.abs(values[valid] - curve[valid])
        if float(np.percentile(residual, 90)) > median_height * max_residual_ratio:
            return None  # 当てはまりが悪い = 信用できない。補正を諦める
        fitted.append(curve)

    return fitted[0], fitted[1], x0, x1


def _robust_polyfit(
    x: np.ndarray, y: np.ndarray, x_out: np.ndarray, order: int, iterations: int = 2
) -> np.ndarray | None:
    """外れ値を繰り返し除きながら多項式を当てる。"""
    if x.size < order + 2:
        return None
    keep = np.ones(x.size, dtype=bool)
    coefficients = None
    for _ in range(iterations):
        if keep.sum() < order + 2:
            break
        coefficients = np.polyfit(x[keep], y[keep], order)
        residual = np.abs(y - np.polyval(coefficients, x))
        threshold = max(float(np.median(residual)) * 3.0, 1.0)
        keep = residual <= threshold
    if coefficients is None:
        return None
    return np.polyval(coefficients, x_out)


# ---------------------------------------------------------------------------
# 湾曲補正(円筒展開)
# ---------------------------------------------------------------------------


def estimate_curl(image: np.ndarray) -> float:
    """ページの湾曲の強さを 0〜1 で推定する(0なら平ら)。

    上端の曲線が、両端を結んだ直線からどれだけ外れているかで測る。
    """
    mask = page_mask(image)
    if mask is None:
        return 0.0
    traced = trace_page_edges(mask)
    if traced is None:
        return 0.0

    top, bottom, _x0, _x1 = traced
    height = float(np.median(bottom - top))
    if height <= 1:
        return 0.0

    straight = np.linspace(top[0], top[-1], top.size)
    deviation = float(np.max(np.abs(top - straight)))
    return min(deviation / height, 1.0)


def dewarp_page(
    image: np.ndarray, output_size: tuple[int, int] | None = None, inset: float = 0.015
) -> np.ndarray | None:
    """ページの湾曲を平らに戻す(円筒展開)。補正できなければ None。

    手順:
        1. 各列の上端 top(x)・下端 bottom(x) を実測する
        2. 縦方向: 各列の [top(x), bottom(x)] を出力の [0, H] に引き伸ばす
           → 曲がった上下の端が直線になる
        3. 横方向: 上端曲線の「弧の長さ」を出力のx座標にする
           → 円筒を転がして広げたことになり、ノド付近の横圧縮が戻る
    """
    mask = page_mask(image)
    if mask is None:
        return None
    traced = trace_page_edges(mask)
    if traced is None:
        return None

    top, bottom, x0, x1 = traced
    heights = bottom - top
    if np.median(heights) <= 10:
        return None

    out_h = int(np.median(heights)) if output_size is None else output_size[1]
    out_w = int(x1 - x0 + 1) if output_size is None else output_size[0]
    out_h, out_w = max(out_h, 10), max(out_w, 10)

    # --- 横方向: 上端曲線の弧長を使って「転がした長さ」を求める ---------------
    dx = np.gradient(np.arange(top.size, dtype=np.float64))
    dy = np.gradient(top)
    arc = np.concatenate([[0.0], np.cumsum(np.sqrt(dx[1:] ** 2 + dy[1:] ** 2))])
    if arc[-1] <= 0:
        return None
    arc_normalized = arc / arc[-1]  # 0〜1

    # 出力の各列が、入力のどの列に対応するかを逆引きする。
    # 端は輪郭推定が甘くなり、背景を巻き込んで黒帯になるため、わずかに内側を使う
    # (実測では、この黒帯をOCRがノイズとして拾いCERが大きく悪化した)。
    target = np.linspace(inset, 1.0 - inset, out_w)
    src_columns = np.interp(target, arc_normalized, np.arange(top.size, dtype=np.float64))

    # --- 縦方向: 各列の上端〜下端を出力の高さいっぱいに割り当てる -------------
    col_top = np.interp(src_columns, np.arange(top.size, dtype=np.float64), top)
    col_bottom = np.interp(src_columns, np.arange(bottom.size, dtype=np.float64), bottom)

    ratios = np.linspace(inset, 1.0 - inset, out_h)[:, None]  # (out_h, 1)
    map_y = (col_top[None, :] + ratios * (col_bottom - col_top)[None, :]).astype(np.float32)
    map_x = np.repeat((src_columns + x0)[None, :], out_h, axis=0).astype(np.float32)

    result = cv2.remap(
        image, map_x, map_y, interpolation=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_REPLICATE,
    )

    # 安全弁: 補正後がまともな紙面になっているかを確認する。
    # 変換が破綻すると背景や指を引き伸ばした画像ができ、OCRがノイズを文字として
    # 大量に拾ってしまう。「補正しない」ほうが「壊す」より常に良い。
    if not _looks_like_page(result):
        return None
    return result


def _looks_like_page(image: np.ndarray, min_bright_ratio: float = 0.55) -> bool:
    """紙面らしい(明るい画素が過半を占める)画像かどうか。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if gray.size == 0:
        return False
    return float((gray > 128).mean()) >= min_bright_ratio


# ---------------------------------------------------------------------------
# 指の除去
# ---------------------------------------------------------------------------


def skin_mask(image_bgr: np.ndarray) -> np.ndarray:
    """肌色領域のマスクを作る。

    YCrCb色空間を使う。肌の色は明るさ(Y)が変わってもCr/Crの範囲が比較的安定しており、
    照明条件の違いに強いため、肌色検出では定番の手法。
    """
    ycrcb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2YCrCb)
    lower = np.array([0, 133, 77], dtype=np.uint8)
    upper = np.array([255, 180, 127], dtype=np.uint8)
    mask = cv2.inRange(ycrcb, lower, upper)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def find_finger_regions(
    image_bgr: np.ndarray, border_ratio: float = 0.18, min_area_ratio: float = 0.0008
) -> list[tuple[int, int, int, int]]:
    """紙面の縁に接している肌色の塊を「指」として検出する。

    紙面の中央にある肌色(肌色に近い図版など)を誤検出しないよう、
    **画像の縁に近い領域に限る**。指は必ず外から入ってくるという事実を利用している。
    """
    mask = skin_mask(image_bgr)
    height, width = mask.shape[:2]
    margin_x, margin_y = int(width * border_ratio), int(height * border_ratio)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < mask.size * min_area_ratio:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        touches_border = x <= margin_x or y <= margin_y or (x + w) >= width - margin_x or (y + h) >= height - margin_y
        if touches_border:
            regions.append((x, y, w, h))
    return regions


def estimate_paper_color(image_bgr: np.ndarray) -> np.ndarray:
    """紙の地色を推定する。明るい側の分位点を使う(文字や影に引っ張られないように)。"""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    bright = gray > np.percentile(gray, 75)
    if not bright.any():
        return np.array([255, 255, 255], dtype=np.uint8)
    if image_bgr.ndim == 3:
        return np.median(image_bgr[bright], axis=0).astype(np.uint8)
    return np.array([np.median(gray[bright])], dtype=np.uint8)


def remove_fingers(image_bgr: np.ndarray, fill: str = "paper") -> tuple[np.ndarray, int]:
    """指を検出して消す。(処理後の画像, 消した個数) を返す。

    消し方は**紙の地色でそのまま塗る**。

    周囲のテクスチャを伝播させるinpaint(cv2.inpaint)も試したが、指の跡に滲みが残り、
    OCRがそれを文字として拾ってしまった(実測で存在しない「剛」という文字が出た)。
    指が乗っているのは紙の余白で、そこは一様な地色なので、
    テクスチャを作る必要はなく、単色で塗るほうが安全で結果も良い。
    """
    regions = find_finger_regions(image_bgr)
    if not regions:
        return image_bgr, 0

    mask = np.zeros(image_bgr.shape[:2], dtype=np.uint8)
    skin = skin_mask(image_bgr)
    for x, y, w, h in regions:
        mask[y : y + h, x : x + w] = skin[y : y + h, x : x + w]

    # 指の輪郭に残る肌色の縁まで消すため、少し太らせる
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))

    out = image_bgr.copy()
    if fill == "inpaint":
        return cv2.inpaint(out, mask, 5, cv2.INPAINT_TELEA), len(regions)

    color = estimate_paper_color(image_bgr)
    out[mask > 0] = color
    return out, len(regions)


# ---------------------------------------------------------------------------
# 背景の消去
# ---------------------------------------------------------------------------


def clean_background(image: np.ndarray, dark_threshold: int = 90) -> np.ndarray:
    """紙面の外側(机・マット)を紙の地色で塗り潰す。

    台形補正や湾曲補正で切り出しても、ページが湾曲していると四隅の外側に背景が残る。
    OCRはこの暗い縁を文字と誤認することがあるため消しておく。

    消す対象は「**画像の縁から繋がっている暗い領域**」に限る。
    以前は「最大の明るい塊の外側」を全て塗っていたが、切り出し済みの画像では
    本文まで塗り潰してしまった(実測でCERが3.8%→57%に悪化した)。
    机やマットは必ず画像の縁に接しているのに対し、本文は接していない。
    この違いを使えば、本文を巻き込まずに背景だけを消せる。
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    dark = (gray < dark_threshold).astype(np.uint8)
    if not dark.any():
        return image

    # 縁から暗い領域を塗り広げる(flood fill)。届いた範囲だけが「背景」
    h, w = dark.shape
    flood_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    filled = dark.copy()
    for x in range(0, w, max(w // 64, 1)):
        for y in (0, h - 1):
            if filled[y, x]:
                cv2.floodFill(filled, flood_mask, (x, y), 2)
    for y in range(0, h, max(h // 64, 1)):
        for x in (0, w - 1):
            if filled[y, x]:
                cv2.floodFill(filled, flood_mask, (x, y), 2)

    background = filled == 2
    if not background.any():
        return image

    # 境界に残る暗い縁も巻き込む
    background = cv2.dilate(background.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0

    out = image.copy()
    color = estimate_paper_color(image)
    out[background] = color
    return out


# ---------------------------------------------------------------------------
# まとめて適用
# ---------------------------------------------------------------------------


def enhance_book_photo(
    image_bgr: np.ndarray,
    do_finger_removal: bool = True,
    do_dewarp: bool = True,
    do_background: bool = True,
    curl_threshold: float = 0.0,
) -> tuple[np.ndarray, dict]:
    """本の写真に対して、指の除去 → 湾曲補正 → 背景消去 を順に適用する。

    順番には理由がある:
        指の除去を最初に行う。指はページの輪郭を隠してしまうため、
        先に消しておかないと湾曲補正が輪郭を読み違える。

    戻り値: (処理後の画像, 何をしたかの記録)
    """
    info: dict = {"fingers_removed": 0, "curl": 0.0, "dewarped": False, "background_cleaned": False}
    result = image_bgr

    if do_finger_removal:
        result, count = remove_fingers(result)
        info["fingers_removed"] = count

    if do_dewarp:
        # 湾曲の強さは記録するが、既定では**閾値で止めない**。
        # 実測では湾曲が無い(curl≒0)ページでも、輪郭に沿った展開のほうが
        # 4隅のホモグラフィより正確で、CERが 1.74% → 1.05% に下がった。
        # 補正が信用できない場合は dewarp_page が None を返して自動的に見送られる。
        curl = estimate_curl(result)
        info["curl"] = round(curl, 4)
        if curl >= curl_threshold:
            dewarped = dewarp_page(result)
            if dewarped is not None:
                result = dewarped
                info["dewarped"] = True

    # 湾曲補正をかけたら、背景消去は必須(任意ではない)。
    # 展開後は紙面の縁に背景が薄く残り、OCRがそれをノイズとして拾う。
    # 実測: 湾曲補正のみ CER 6.27% / 背景消去も行うと 1.05%(補正なしは 3.83%)。
    # 片方だけ有効にすると補正しないより悪くなるため、ここで強制的に組にする。
    if do_background or info["dewarped"]:
        cleaned = clean_background(result)
        info["background_cleaned"] = True
        result = cleaned

    return result, info
