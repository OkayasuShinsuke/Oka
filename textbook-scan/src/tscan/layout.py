"""レイアウト解析と読み順(仕様書 §10)。"""
from __future__ import annotations

from tscan.models import Block


# ---------------------------------------------------------------------------
# §10.2 数式領域の判定ロジック
# ---------------------------------------------------------------------------

MATH_SYMBOLS = set("∫∑√±≦≧∞→∈≠≒×÷")
HIRAGANA_RANGE = range(0x3041, 0x3097)


def score_math_block(
    text: str,
    bbox: tuple[int, int, int, int],
    body_line_height: float,
    has_equation_number: bool = False,
    centered: bool = False,
) -> int:
    """行領域が数式かどうかをルールベースでスコアリングする(§10.2 REQ-LAYOUT-02)。

    スコア >= 5 で math_block、<= 0 で body_text、それ以外は要・学習モデル(未実装)。
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

    hiragana_run = 0
    max_hiragana_run = 0
    for ch in text:
        if ord(ch) in HIRAGANA_RANGE:
            hiragana_run += 1
            max_hiragana_run = max(max_hiragana_run, hiragana_run)
        else:
            hiragana_run = 0

    if "、" in text or "。" in text:
        score -= 3
    if max_hiragana_run >= 3:
        score -= 2

    return score


def classify_math_block(score: int) -> str:
    """スコアから種別を決める。境界ケースは要判定として"ambiguous"を返す。"""
    if score >= 5:
        return "math_block"
    if score <= 0:
        return "body_text"
    return "ambiguous"  # §10.2: 学習モデルでの判定+信頼度を下げる対象(未実装)


# ---------------------------------------------------------------------------
# §10.3 読み順の決定
# ---------------------------------------------------------------------------


def is_vertical_layout(lines: list[Block]) -> bool:
    """行領域の形状から、縦書きページかどうかを判定する(REQ-LAYOUT-03)。"""
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
