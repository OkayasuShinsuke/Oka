"""画像処理パイプライン(仕様書 §8)。P1〜P10の各工程 + セッション校正(§8.3.1)。

各関数は仕様書のコードブロックをベースに、実際に動作する形へ実装したもの。
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:  # HEIC非対応環境でも他の形式は使えるようにする
    _HEIF_AVAILABLE = False

try:
    import rawpy

    _RAWPY_AVAILABLE = True
except ImportError:  # RAW非対応環境でもHEIC/JPEGは使えるようにする(§6.4.6)
    _RAWPY_AVAILABLE = False

RAW_EXTENSIONS = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng"}


# ---------------------------------------------------------------------------
# P1: フォーマット変換
# ---------------------------------------------------------------------------


def load_as_bgr(path: Path) -> np.ndarray:
    """HEIC/RAW/JPEG等、対応形式を読み込み、OpenCV形式(BGR, uint8)の配列にする(P1)。"""
    ext = path.suffix.lower()

    if ext in RAW_EXTENSIONS:
        if not _RAWPY_AVAILABLE:
            raise RuntimeError(
                f"{path.name}: RAW現像には rawpy が必要です。"
                "`pip install tscan[apple-vision]` 等ではなく `pip install rawpy` を実行してください(§14.2)。"
            )
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(no_auto_bright=True, output_bps=8)  # §6.4.2: 自動加工を避ける
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    # HEIC/JPEG/PNG等はPillow経由(pillow_heifが登録済みならHEICも同じ経路で開ける)
    with Image.open(path) as img:
        img = img.convert("RGB")
        rgb = np.array(img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# P2: EXIF向き正規化
# ---------------------------------------------------------------------------


def normalize_orientation(path: Path) -> Path:
    """EXIFのOrientationタグに従い、画像を正立させたコピーを返す(P2)。

    load_as_bgr はPillow/rawpy経由で読むため、事前にこの関数でファイル自体を
    正立させておくと、以降の工程(P3以降)がシンプルになる。
    """
    with Image.open(path) as img:
        fixed = ImageOps.exif_transpose(img)
        buffer = io.BytesIO()
        fixed.convert("RGB").save(buffer, format="PNG")
    out_path = path.with_suffix(".oriented.png")
    out_path.write_bytes(buffer.getvalue())
    return out_path


# ---------------------------------------------------------------------------
# P3: ページ矩形検出 / P4: 台形補正
# ---------------------------------------------------------------------------


def detect_page_corners(image: np.ndarray, search_region: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
    """紙面の4隅を検出する(P3)。

    search_region: (x, y, w, h)。指定すると、その範囲内だけを探索する(§8.3.1 ヒント付き探索)。
    戻り値: 形状(4, 2)の配列 [左上, 右上, 右下, 左下]。見つからなければ None。
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image

    offset = np.array([0, 0])
    if search_region is not None:
        x, y, w, h = search_region
        x, y = max(x, 0), max(y, 0)
        gray = gray[y : y + h, x : x + w]
        offset = np.array([x, y])

    # 背景(黒/濃紺マット, §6.3)に対して紙面(白)を二値化してから輪郭検出
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, 0.02 * peri, True)

    if len(approx) != 4:
        # 4角形として近似できない(反射・写り込み等) → 外接矩形にフォールバック
        x, y, w, h = cv2.boundingRect(largest)
        pts = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32)
    else:
        pts = approx.reshape(4, 2).astype(np.float32)

    pts = _order_corners(pts) + offset
    return pts.astype(np.float32)


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """4点を[左上, 右上, 右下, 左下]の順に並べ替える。"""
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    top_left = pts[np.argmin(s)]
    bottom_right = pts[np.argmax(s)]
    top_right = pts[np.argmin(diff)]
    bottom_left = pts[np.argmax(diff)]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def compute_perspective_matrix(corners: np.ndarray) -> tuple[np.ndarray, int, int]:
    """4隅から射影変換行列を求める。(行列, 出力幅, 出力高さ) を返す。"""
    (tl, tr, br, bl) = corners
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    width, height = max(width, 1), max(height, 1)

    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(corners.astype(np.float32), destination)
    return matrix, width, height


def correct_perspective(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """4隅の座標を与えて、紙面を正面から見た長方形に変換する(P4)。"""
    matrix, width, height = compute_perspective_matrix(corners)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_LANCZOS4)


# ---------------------------------------------------------------------------
# §8.3.1 セッション校正(REQ-PRE-03)
# ---------------------------------------------------------------------------


def calibrate_session(first_frame: np.ndarray) -> np.ndarray | None:
    """セッション最初の1枚から4隅を検出する。

    人間の確認ポイント: ここで検出した4隅をUIに表示し、OKならそのまま、
    ズレていれば手動でドラッグ修正してから確定する(§11.7の編集画面と連携する想定)。
    """
    return detect_page_corners(first_frame)


def detect_with_hint(
    frame: np.ndarray, expected_corners: np.ndarray, search_margin_px: int = 40
) -> np.ndarray:
    """校正済みの4隅の"近く"だけを探す(§8.3.1)。ゼロから探すより誤検出が起きにくい。"""
    x_min = int(expected_corners[:, 0].min()) - search_margin_px
    y_min = int(expected_corners[:, 1].min()) - search_margin_px
    x_max = int(expected_corners[:, 0].max()) + search_margin_px
    y_max = int(expected_corners[:, 1].max()) + search_margin_px
    region = (x_min, y_min, x_max - x_min, y_max - y_min)

    corners = detect_page_corners(frame, search_region=region)
    if corners is None:
        return expected_corners  # 見つからなければ校正値をそのまま使う(フォールバック)
    return corners


# ---------------------------------------------------------------------------
# §8.3.2 補正結果の自動検査(REQ-PRE-04)
# ---------------------------------------------------------------------------


def check_aspect_ratio(corrected: np.ndarray, expected_ratio: float, tolerance: float = 0.05) -> bool:
    """補正後の縦横比が、想定するページの縦横比から大きく外れていないかを検査する。

    B5サイズなら 182/257 ≒ 0.708 のような期待比率と比べる。
    """
    height, width = corrected.shape[:2]
    if height == 0:
        return False
    actual_ratio = width / height
    return abs(actual_ratio - expected_ratio) <= tolerance


# ---------------------------------------------------------------------------
# P5: デスキュー
# ---------------------------------------------------------------------------


def deskew(image: np.ndarray, max_angle: float = 15.0) -> np.ndarray:
    """残った微小な傾きを水平に補正する(P5)。輪郭の最小外接矩形の角度を使う簡易実装。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(binary > 0))
    if coords.size == 0:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) > max_angle or abs(angle) < 0.05:
        return image  # 想定外の角度、または補正不要なほど小さい

    height, width = image.shape[:2]
    center = (width // 2, height // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, rotation_matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


# ---------------------------------------------------------------------------
# P6: 見開き分割
# ---------------------------------------------------------------------------


def find_gutter_x(gray: np.ndarray) -> int:
    """見開き画像のノド(綴じ目)のx座標を返す(P6)。"""
    column_means = gray.mean(axis=0)
    width = gray.shape[1]
    lo, hi = int(width * 0.35), int(width * 0.65)
    return lo + int(np.argmin(column_means[lo:hi]))


def split_spread(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """見開き1枚を左右2ページに分割する(P6)。戻り値: (左ページ, 右ページ)。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gutter_x = find_gutter_x(gray)
    return image[:, :gutter_x], image[:, gutter_x:]


# ---------------------------------------------------------------------------
# P7: 照明ムラ補正
# ---------------------------------------------------------------------------


def invert_dark_boxes(
    gray: np.ndarray, min_area_frac: float = 0.003, min_height_frac: float = 0.04, min_fill: float = 0.6
) -> tuple[np.ndarray, int]:
    """白抜き文字の「暗い帯・ボックス」(公式の囲み、章見出しの帯)を反転して黒文字にする(P6.5)。

    教科書は重要な公式を色付きのボックスに白文字で載せることが多い。OCRは「明るい紙に暗い文字」
    を前提にしているので、そのままでは読めない。暗くて矩形に近い大きな塊を見つけ、その内側だけ
    明暗を反転する。

    ボックスと間違えやすいものの除外:
        - 本文の太い1行: 高さがページの min_height_frac 未満(本文1行は 2〜3%)
        - 線画・表の罫線: 外接矩形に対する暗い画素の割合が低い
        - 前処理に失敗した真っ暗な画像: 画像全体が1つの塊

    戻り値: (画像, 反転したボックスの数)
    """
    h, w = gray.shape[:2]
    _, dark = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
    out = gray.copy()
    boxes = 0
    for i in range(1, count):
        x, y, bw, bh, area = stats[i]
        if area < h * w * min_area_frac or bh < h * min_height_frac or bw < 12:
            continue
        if bw >= w - 2 and bh >= h - 2:
            continue  # 画像全体が暗い(前処理失敗)
        raw_fill = float((dark[y : y + bh, x : x + bw] > 0).mean())
        if raw_fill < min_fill:
            continue  # 線画・表の罫線・文字の集まり
        # 成分の外側の輪郭で塗りつぶす(白抜き文字の穴も含めて反転する。角丸にも追従)
        component = (labels[y : y + bh, x : x + bw] == i).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        region = np.zeros_like(component)
        cv2.drawContours(region, contours, -1, 1, thickness=cv2.FILLED)
        patch = out[y : y + bh, x : x + bw]
        patch[region > 0] = 255 - patch[region > 0]
        boxes += 1
    return out, boxes


def flatten_illumination(
    gray: np.ndarray, kernel_size: int | None = None, min_background_ratio: float = 0.6
) -> np.ndarray:
    """照明ムラを除去して、紙の白さを均一にする(P7)。

    背景(紙の明るさ)の推定は「文字より大きく、照明ムラより小さい」核で行う。核は画像の
    短辺の 1/16 を既定にする(固定51pxでは高解像度画像で文字の塊まで背景に取り込む)。
    暗い図版の内側で背景推定値が小さくなり、割り算でノイズが増幅されるのを防ぐため、
    背景は紙の明るさの 60% を下限にする。
    """
    if kernel_size is None:
        kernel_size = max(51, (min(gray.shape[:2]) // 16) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    # 閉処理だけだと、表の網掛けや裏写りの周りに核の形(楕円)の明るい斑が出る。
    # 照明ムラは滑らかなので、推定した背景をぼかして斑を消す
    background = cv2.GaussianBlur(background, (0, 0), sigmaX=kernel_size / 2)
    paper_level = float(np.percentile(background, 90))
    background = np.clip(background, max(1.0, paper_level * min_background_ratio), 255).astype(np.uint8)
    normalized = cv2.divide(gray, background, scale=255)
    return normalized


def stretch_contrast(
    gray: np.ndarray, ink_target: int = 90, apply_above: int = 120, min_gap: float = 15.0
) -> np.ndarray:
    """文字が薄すぎるときだけ、文字を適度な濃さに戻す(P7.5)。

    照明補正(P7)は「紙を白くする」割り算なので、紙と一緒に文字も同じ割合で明るくなる。
    暗く写った低コントラストの写真(実測: APS-Cミラーレスで文字119・紙159)では、
    補正後の文字が145の薄いグレーになり、PDFで見ると文章が白飛びして消えたように見えた。

    Otsuの二値化で文字と紙を分け、文字の中央値が apply_above より明るいときだけ
        文字の中央値 → ink_target(既定90)
        紙の中央値   → 255(白)
    に写す直線で引き伸ばす。

    文字を真っ黒(30)まで引き伸ばす版も試したが、もともと十分濃い写真(iPhone、文字103)で
    線が太って隣の画と潰れ、CERが10.0%→16.3%に悪化した。そこで、
      - 文字がすでに十分濃い画像には何もしない(apply_above で判定)
      - 薄い画像も、iPhoneで良く読めていた濃さ(約90〜100)までにとどめる
    という形にしている。合成画像のような真っ黒な文字にも影響しない。

    通常の文字ページでは文字(ink)が紙面の3割を超えることはまずない。実写真(影・照明ムラあり)
    ではOtsu二値化が汚染され、ink画素比率が37%まで膨張して文字の中央値が汚染された値になり、
    引き伸ばしで文字が破壊されることを実測で確認したため、この場合は引き伸ばしをスキップする。
    """
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = gray[binary == 0]
    paper = gray[binary > 0]
    if ink.size < gray.size * 0.002 or paper.size == 0 or ink.size > gray.size * 0.3:
        return gray
    lo = float(np.median(ink))
    hi = float(np.median(paper))
    if lo <= apply_above or hi - lo < min_gap:
        return gray
    scale = (255.0 - ink_target) / (hi - lo)
    out = (gray.astype(np.float32) - lo) * scale + ink_target
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# P8: ノイズ除去・シャープ化 / P9: リサイズ正規化
# ---------------------------------------------------------------------------


def estimate_char_height(gray: np.ndarray) -> float:
    """文字らしい連結成分の高さの中央値(px)。解像度が足りているかの目安(§6.1 の40px基準)。"""
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    if count <= 1:
        return 0.0
    h = stats[1:, cv2.CC_STAT_HEIGHT]
    w = stats[1:, cv2.CC_STAT_WIDTH]
    sel = (h >= 6) & (h <= 80) & (w <= 80)
    return float(np.median(h[sel])) if sel.any() else 0.0


def denoise_and_sharpen(gray: np.ndarray, min_char_height: float = 20.0) -> np.ndarray:
    """粒状ノイズを消し、文字の輪郭を立てる(P8)。

    文字が小さい(中央値 min_char_height px 未満)ときは何もしない。細い画線に
    ノイズ除去と鮮鋭化を掛けると画線が欠けて、実写真(文字高15px)ではCERが
    24%→37%に悪化した。十分な解像度(合成画像: 文字高40px)では効果がある。
    """
    if estimate_char_height(gray) < min_char_height:
        return gray
    denoised = cv2.fastNlMeansDenoising(gray, h=7)
    blurred = cv2.GaussianBlur(denoised, (0, 0), sigmaX=1.0)
    sharpened = cv2.addWeighted(denoised, 1.5, blurred, -0.5, 0)
    return sharpened


def resize_to_target_dpi(gray: np.ndarray, current_px_per_mm: float, target_dpi: int = 400) -> np.ndarray:
    """OCRが得意な解像度(既定400dpi相当)にリサイズする(P9)。"""
    target_px_per_mm = target_dpi / 25.4
    scale = target_px_per_mm / current_px_per_mm
    if abs(scale - 1.0) < 0.02:
        return gray  # ほぼ同じ解像度なら何もしない
    height, width = gray.shape[:2]
    new_size = (max(int(width * scale), 1), max(int(height * scale), 1))
    interpolation = cv2.INTER_LANCZOS4 if scale > 1.0 else cv2.INTER_AREA
    return cv2.resize(gray, new_size, interpolation=interpolation)


# ---------------------------------------------------------------------------
# P10: 保存
# ---------------------------------------------------------------------------


def save_stage(image: np.ndarray, out_dir: Path, book_id: str, page_index: int, stage: str) -> Path:
    """中間成果物を `work/{book_id}/{stage}/` に保存する(P10, REQ-PRE-01)。"""
    stage_dir = out_dir / book_id / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    out_path = stage_dir / f"{book_id}_p{page_index:04d}.png"
    cv2.imwrite(str(out_path), image)
    return out_path
