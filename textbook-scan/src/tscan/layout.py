"""レイアウト解析と読み順(仕様書 §10)。

OCRエンジンが返した行(OcrLine)を入力に、
    - ページが縦書きか横書きか(§10.3 REQ-LAYOUT-03)
    - 各行が本文か数式かルビか(§10.1 / §10.2 REQ-LAYOUT-02 / §10.4 REQ-LAYOUT-05)
    - 読み順(§10.3 REQ-LAYOUT-04)
    - ノンブル(印刷されたページ番号, §7.5 REQ-PAGECHK-01)
を決める。
"""
from __future__ import annotations

import re
import statistics

import numpy as np

from tscan.models import Block, BlockKind
from tscan.ocr.base import OcrLine

# ---------------------------------------------------------------------------
# §10.2 数式領域の判定ロジック
# ---------------------------------------------------------------------------

MATH_SYMBOLS = set("∫∑√±≦≧∞→∈≠≒×÷∂∇αβγθλμπσωΔΩ")
_HIRAGANA = re.compile(r"[ぁ-ゖ]")
_KATAKANA = re.compile(r"[ァ-ヺー]")
_KANJI = re.compile(r"[一-龥]")
_EQUATION_NUMBER = re.compile(r"[(（]\s*\d+(?:[.\-‐―]\d+)*\s*[)）]\s*$")


def score_math_block(
    text: str,
    bbox: tuple[int, int, int, int],
    body_line_height: float,
    has_equation_number: bool = False,
    centered: bool = False,
) -> int:
    """行領域が数式かどうかをルールベースでスコアリングする(§10.2 REQ-LAYOUT-02)。

    スコア >= 5 で math_block、<= 0 で body_text、それ以外は判定保留(ambiguous)。
    """
    score = 0
    _, _, _, height = bbox

    if centered:
        score += 3
    if has_equation_number:
        score += 3
    if body_line_height > 0 and height >= body_line_height * 1.5:
        score += 2
    if any(ch in MATH_SYMBOLS for ch in text):
        score += 2

    # イタリック体の単独アルファベットが多い(変数として使われている)
    single_letters = len(re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", text))
    if single_letters >= 2:
        score += 2

    # 日本語の本文には必ずかなが混ざる。かなが1文字もなく、等号や演算子を含む行は
    # ほぼ確実に数式(実測: 「F=qvBsinθ (3.14)」がこの規則で正しく数式と判定される)
    stripped = text.strip()
    has_kana = bool(_HIRAGANA.search(stripped) or _KATAKANA.search(stripped))
    has_operator = bool(re.search(r"[=＝+\-−×÷/^_<>≤≥]", stripped))
    if stripped and not has_kana and has_operator:
        score += 4

    if "、" in text or "。" in text:
        score -= 3

    longest_kana_run = 0
    run = 0
    for ch in text:
        if _HIRAGANA.match(ch):
            run += 1
            longest_kana_run = max(longest_kana_run, run)
        else:
            run = 0
    if longest_kana_run >= 3:
        score -= 2

    return score


def classify_math_block(score: int) -> str:
    """スコアから種別を決める。境界ケースは"ambiguous"(§10.2: 信頼度を下げる対象)。"""
    if score >= 5:
        return "math_block"
    if score <= 0:
        return "body_text"
    return "ambiguous"


def has_equation_number(text: str) -> bool:
    """行末に「(3.14)」形式の式番号があるか。"""
    return bool(_EQUATION_NUMBER.search(text.strip()))


def split_equation_number(text: str) -> tuple[str, str]:
    """「F = ma (3.15)」を ("F = ma", "(3.15)") に分ける。式番号がなければ第2要素は空。"""
    stripped = text.strip()
    match = _EQUATION_NUMBER.search(stripped)
    if not match:
        return stripped, ""
    return stripped[: match.start()].strip(), match.group(0).strip()


def is_centered(bbox: tuple[int, int, int, int], page_width: int, tolerance: float = 0.12) -> bool:
    """行がページ中央に寄っているか(左右の余白がほぼ等しく、かつ十分に大きいか)。"""
    x, _, w, _ = bbox
    if page_width <= 0 or w <= 0:
        return False
    left_margin = x
    right_margin = page_width - (x + w)
    if left_margin < page_width * 0.08 or right_margin < page_width * 0.08:
        return False  # どちらかの余白が小さい = 本文の行
    return abs(left_margin - right_margin) <= page_width * tolerance


# ---------------------------------------------------------------------------
# §10.3 縦書き判定と読み順
# ---------------------------------------------------------------------------


def is_vertical_layout(lines: list) -> bool:
    """行領域の形状から、縦書きページかどうかを判定する(REQ-LAYOUT-03)。

    lines は Block でも OcrLine でもよい(どちらも .bbox を持つ)。
    """
    if not lines:
        return False

    vertical_count = 0
    for line in lines:
        _, _, w, h = line.bbox
        if h > w * 1.5:  # 縦長 = 縦書きの行
            vertical_count += 1

    return vertical_count / len(lines) >= 0.6


def sort_reading_order(blocks: list[Block], vertical: bool) -> list[Block]:
    """読み順に並べ替える(REQ-LAYOUT-04)。

    横書き: (y, x) 昇順(上から下、同じ高さなら左から右)。
    縦書き: (-x, y) 昇順(右から左、各列は上から下)。
    """
    if vertical:
        return sorted(blocks, key=lambda b: (-b.bbox[0], b.bbox[1]))
    return sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))


def assign_reading_order(blocks: list[Block], vertical: bool) -> list[Block]:
    """読み順ソート後、各ブロックの reading_order フィールドを更新して返す。"""
    ordered = sort_reading_order(blocks, vertical)
    for i, block in enumerate(ordered, start=1):
        block.reading_order = i
    return ordered


# ---------------------------------------------------------------------------
# §10.4 ルビ(振り仮名)の扱い
# ---------------------------------------------------------------------------


def _is_kana_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return all(_HIRAGANA.match(c) or _KATAKANA.match(c) for c in stripped)


def detect_ruby(lines: list[OcrLine], body_line_height: float, vertical: bool) -> dict[int, int]:
    """ルビ行を検出し、{ルビ行のindex: 親文字行のindex} を返す(REQ-LAYOUT-05)。

    判定条件(§10.4): 文字高が本文の40〜60% / 本文行のすぐ上(縦書きなら右)に隣接 /
    全文字がひらがな・カタカナ。
    """
    mapping: dict[int, int] = {}
    if body_line_height <= 0:
        return mapping

    for i, line in enumerate(lines):
        _, _, w, h = line.bbox
        size = w if vertical else h
        ratio = size / body_line_height
        if not (0.3 <= ratio <= 0.65):
            continue
        if not _is_kana_only(line.text):
            continue

        parent = _find_ruby_parent(lines, i, vertical, body_line_height)
        if parent is not None:
            mapping[i] = parent
    return mapping


def _find_ruby_parent(lines: list[OcrLine], ruby_index: int, vertical: bool, body_line_height: float) -> int | None:
    """ルビ行に隣接する親文字行を探す。"""
    rx, ry, rw, rh = lines[ruby_index].bbox
    best: int | None = None
    best_gap = body_line_height * 1.2  # これ以上離れていたら別物とみなす

    for j, other in enumerate(lines):
        if j == ruby_index:
            continue
        ox, oy, ow, oh = other.bbox
        other_size = ow if vertical else oh
        if other_size < body_line_height * 0.8:
            continue  # 親文字は本文サイズであるはず

        if vertical:
            # 縦書き: ルビは親文字の列の右側に付く。縦方向(y)に重なりがあるかを見る
            gap = abs(rx - (ox + ow))
            overlaps = not (ry + rh < oy or oy + oh < ry)
        else:
            # 横書き: ルビは親文字の上に付く
            gap = abs(oy - (ry + rh))
            overlaps = not (rx + rw < ox or ox + ow < rx)

        if overlaps and gap < best_gap:
            best, best_gap = j, gap
    return best


def format_ruby(base: str, ruby: str) -> str:
    """青空文庫形式(｜漢字《かんじ》)に整形する(§10.4 / §13.2)。"""
    return f"｜{base}《{ruby}》"


# ---------------------------------------------------------------------------
# §7.5 ノンブル(印刷されたページ番号)の抽出
# ---------------------------------------------------------------------------

_NOMBRE_PATTERN = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def extract_nombre(gray: np.ndarray, engine, band_ratio: float = 0.10, min_digits: int = 2) -> int | None:
    """ページ上下の帯を切り出してOCRし、ノンブルを読み取る(REQ-PAGECHK-01)。

    engine は recognize_text(image, vertical, psm, whitelist) を持つもの(TesseractEngine等)。
    上下どちらの帯からも読めなければ None。

    工夫している点:
        - 数字だけを認識対象にする(whitelist)。ノンブルは数字しか来ないので誤読が大きく減る
        - 帯をさらに左・中央・右に分けて試す。ページ番号の位置は本によって違うため
        - 小さい文字は拡大してから読ませる(OCRは極端に小さい文字が苦手)
        - min_digits 桁未満の数字は採用しない。実写真では「197」の一部だけを拾って
          "7" を返し、存在しないページ抜けを報告してしまった。誤った番号は
          「読めなかった」より害が大きいので、確信が持てない結果は捨てる(§7.5.1)
    """
    height, width = gray.shape[:2]
    band = max(int(height * band_ratio), 24)

    # 帯は「画像の端」ではなく「印字されている範囲の端」から取る。
    # 実写真では切り出しに紙の地色で塗った余白が付き、画像の下端10%が
    # 真っ白になってノンブルを1件も読めなかった(実測13ページ中9ページ)。
    top_ink, bottom_ink = _ink_extent(gray)
    if top_ink is None or bottom_ink is None:
        bands = [gray[height - band :, :], gray[:band, :]]
    else:
        pad = max(band // 5, 4)
        bands = [
            gray[max(bottom_ink - band, 0) : min(bottom_ink + pad, height), :],
            gray[max(top_ink - pad, 0) : min(top_ink + band, height), :],
        ]
    for region in bands:
        if region.size == 0:
            continue
        if _contains_japanese_text(region, engine):
            # 「3.5 運動方程式」のような柱(ランニングヘッド)を、
            # 数字だけ拾って "35" と誤認しないようにする(実測で発生した誤りへの対策)
            continue
        w = region.shape[1]
        # 全体 → 中央 → 右下 → 左下 の順に試す
        candidates = [
            region,
            region[:, int(w * 0.35) : int(w * 0.65)],
            region[:, int(w * 0.6) :],
            region[:, : int(w * 0.4)],
        ]
        for crop in candidates:
            if crop.size == 0 or crop.shape[1] < 10:
                continue
            number = _read_digits(crop, engine, min_digits=min_digits)
            if number is not None:
                return number
    return None


def _ink_extent(gray: np.ndarray, min_fraction: float = 0.002) -> tuple[int | None, int | None]:
    """印字されている範囲の上端・下端の行番号を返す。何も無ければ (None, None)。

    紙の地色で塗った余白は真っ白なのでインクが無く、この範囲から外れる。
    ごく少数の画素しかない行はノイズとみなして無視する。
    """
    import cv2

    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    counts = (ink > 0).sum(axis=1)
    rows = np.where(counts >= max(gray.shape[1] * min_fraction, 3))[0]
    if rows.size == 0:
        return None, None
    return int(rows[0]), int(rows[-1])


def _contains_japanese_text(region: np.ndarray, engine) -> bool:
    """帯にかな・漢字が含まれるか(= ノンブルではなく柱・見出しであるか)を調べる。"""
    try:
        text = engine.recognize_text(region, vertical=False, psm=7)
    except Exception:
        return False
    return bool(_HIRAGANA.search(text) or _KATAKANA.search(text) or _KANJI.search(text))


def _read_digits(crop: np.ndarray, engine, min_digits: int = 1) -> int | None:
    """小さな領域から数字を読む。拡大・二値化してからpsm 7/8/13を順に試す。

    min_digits 桁未満の結果は捨てる(数式番号や本文の数字の一部を拾わないため)。
    """
    import cv2

    scale = max(1.0, 80.0 / max(crop.shape[0], 1))
    if scale > 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # ノンブルは小さく淡いことが多い。二値化してコントラストを最大化すると読み取りが安定する
    _, crop = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    crop = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)

    for psm in (7, 8, 13):
        try:
            text = engine.recognize_text(crop, vertical=False, psm=psm, whitelist="0123456789")
        except TypeError:
            # whitelistに対応していないエンジン(将来の別実装)へのフォールバック
            try:
                text = engine.recognize_text(crop, vertical=False, psm=psm)
            except Exception:
                continue
        except Exception:
            continue
        number = parse_nombre(text, min_digits=min_digits)
        if number is not None:
            return number
    return None


def parse_nombre(text: str, min_digits: int = 1) -> int | None:
    """OCRしたテキストからページ番号らしき数値を1つ取り出す。

    min_digits 桁未満の数字列は無視する。
    """
    candidates = [c for c in _NOMBRE_PATTERN.findall(text or "") if len(c) >= min_digits]
    if not candidates:
        return None
    # 行内に複数あるときは最も長い数字列(章番号より本文ページ番号が長いことが多い)
    best = max(candidates, key=len)
    value = int(best)
    return value if 1 <= value <= 9999 else None


# ---------------------------------------------------------------------------
# ヘッダ/フッタ・図領域の判定
# ---------------------------------------------------------------------------


def estimate_body_line_height(lines: list[OcrLine], vertical: bool = False) -> float:
    """本文1行の代表的な高さ(縦書きなら幅)を中央値で推定する。"""
    sizes = [(ln.bbox[2] if vertical else ln.bbox[3]) for ln in lines if ln.text.strip()]
    if not sizes:
        return 0.0
    return float(statistics.median(sizes))


def classify_header_footer(bbox: tuple[int, int, int, int], page_height: int, band_ratio: float = 0.07) -> BlockKind | None:
    """ページ上下の帯にある短い行をヘッダ/フッタとみなす(§10.1)。"""
    _, y, _, h = bbox
    band = page_height * band_ratio
    if y + h <= band:
        return BlockKind.HEADER
    if y >= page_height - band:
        return BlockKind.FOOTER
    return None


def detect_figure_regions(
    gray: np.ndarray, text_boxes: list[tuple[int, int, int, int]], min_area_ratio: float = 0.02
) -> list[tuple[int, int, int, int]]:
    """テキストとして認識されなかった大きな塊を図領域の候補として返す(§10.1)。

    仕様書では図はOCRせず画像として切り出す方針(REQ-LAYOUT-01)。
    """
    import cv2

    height, width = gray.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask[binary > 0] = 255

    # テキスト行の領域を塗り潰して除外する
    for x, y, w, h in text_boxes:
        pad = 3
        x0, y0 = max(x - pad, 0), max(y - pad, 0)
        x1, y1 = min(x + w + pad, width), min(y + h + pad, height)
        mask[y0:y1, x0:x1] = 0

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    page_area = height * width
    figures = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h >= page_area * min_area_ratio and w > 40 and h > 40:
            figures.append((x, y, w, h))
    return figures


# ---------------------------------------------------------------------------
# 行 -> Block への変換(パイプラインの中核)
# ---------------------------------------------------------------------------


def lines_to_blocks(
    lines: list[OcrLine],
    page_size: tuple[int, int],
    page_prefix: str,
    vertical: bool | None = None,
) -> tuple[list[Block], bool, dict[int, int]]:
    """OCR結果の行を分類し、Blockのリストに変換する。

    戻り値: (blocks, vertical, ruby_map)
    """
    width, height = page_size
    if vertical is None:
        vertical = is_vertical_layout(lines)

    body_height = estimate_body_line_height(lines, vertical=vertical)
    ruby_map = detect_ruby(lines, body_height, vertical)

    blocks: list[Block] = []
    for i, line in enumerate(lines):
        block_id = f"{page_prefix}_b{i + 1:03d}"

        if i in ruby_map:
            kind = BlockKind.RUBY
        else:
            header_footer = classify_header_footer(line.bbox, height)
            if header_footer is not None and len(line.text.strip()) <= 20:
                kind = header_footer
            else:
                score = score_math_block(
                    line.text,
                    line.bbox,
                    body_height,
                    has_equation_number=has_equation_number(line.text),
                    centered=is_centered(line.bbox, width),
                )
                label = classify_math_block(score)
                kind = {
                    "math_block": BlockKind.MATH_BLOCK,
                    "body_text": BlockKind.BODY_TEXT,
                    "ambiguous": BlockKind.BODY_TEXT,  # 保留は本文扱いにし、信頼度で拾う
                }[label]

        if kind == BlockKind.MATH_BLOCK:
            formula, eq_number = split_equation_number(line.text)
            block = Block(
                block_id=block_id,
                kind=kind,
                bbox=line.bbox,
                reading_order=0,
                latex=formula,
                equation_number=eq_number,
                confidence=line.confidence,
            )
        else:
            block = Block(
                block_id=block_id,
                kind=kind,
                bbox=line.bbox,
                reading_order=0,
                text=line.text,
                confidence=line.confidence,
            )
        blocks.append(block)

    assign_reading_order(blocks, vertical)
    return blocks, vertical, ruby_map
