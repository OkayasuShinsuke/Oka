from tscan.layout import (
    _merge_stray_equation_numbers,
    assign_reading_order,
    classify_header_footer,
    classify_math_block,
    detect_ruby,
    estimate_body_line_height,
    format_ruby,
    has_equation_number,
    is_centered,
    is_vertical_layout,
    lines_to_blocks,
    parse_nombre,
    score_math_block,
    sort_reading_order,
    split_equation_number,
)
from tscan.models import Block, BlockKind
from tscan.ocr.base import OcrLine


def _block(bbox, kind=BlockKind.BODY_TEXT) -> Block:
    return Block(block_id="b", kind=kind, bbox=bbox, reading_order=0)


def _line(text: str, bbox, conf: float = 0.9) -> OcrLine:
    return OcrLine(text=text, bbox=bbox, confidence=conf, engine="test", kind="text")


# --- 縦書き判定・読み順(§10.3) ----------------------------------------------


def test_is_vertical_layout_true_when_most_lines_tall():
    lines = [_block((0, 0, 20, 200)), _block((30, 0, 20, 180)), _block((60, 0, 20, 190))]
    assert is_vertical_layout(lines) is True


def test_is_vertical_layout_false_when_most_lines_wide():
    assert is_vertical_layout([_block((0, 0, 300, 20)), _block((0, 30, 280, 20))]) is False


def test_is_vertical_layout_empty():
    assert is_vertical_layout([]) is False


def test_sort_reading_order_horizontal():
    blocks = [_block((100, 50, 10, 10)), _block((10, 50, 10, 10)), _block((10, 0, 10, 10))]
    assert [b.bbox for b in sort_reading_order(blocks, vertical=False)] == [
        (10, 0, 10, 10), (10, 50, 10, 10), (100, 50, 10, 10)
    ]


def test_sort_reading_order_vertical_right_to_left():
    blocks = [_block((10, 0, 10, 10)), _block((100, 0, 10, 10)), _block((100, 50, 10, 10))]
    assert [b.bbox for b in sort_reading_order(blocks, vertical=True)] == [
        (100, 0, 10, 10), (100, 50, 10, 10), (10, 0, 10, 10)
    ]


def test_assign_reading_order_updates_field():
    ordered = assign_reading_order([_block((100, 0, 10, 10)), _block((10, 0, 10, 10))], vertical=False)
    assert [b.reading_order for b in ordered] == [1, 2]


# --- 数式判定(§10.2) --------------------------------------------------------


def test_score_math_block_detects_formula_with_equation_number():
    score = score_math_block(
        "F = qvB sin θ (3.14)", bbox=(0, 0, 100, 60), body_line_height=30,
        has_equation_number=True, centered=True,
    )
    assert classify_math_block(score) == "math_block"


def test_score_math_block_detects_kana_free_formula():
    """かなが1つもなく演算子を含む行は数式(実測で見つかった判定漏れへの対策)。"""
    score = score_math_block("F=qvBsin9(3.14)", bbox=(0, 0, 700, 40), body_line_height=48,
                             has_equation_number=True, centered=False)
    assert classify_math_block(score) == "math_block"


def test_score_math_block_detects_body_text():
    score = score_math_block("したがって、次のようになる。", bbox=(0, 0, 500, 30), body_line_height=30)
    assert classify_math_block(score) == "body_text"


def test_has_equation_number():
    assert has_equation_number("F = ma (3.15)")
    assert not has_equation_number("これは本文です。")


def test_split_equation_number():
    assert split_equation_number("F = ma (3.15)") == ("F = ma", "(3.15)")
    assert split_equation_number("F = ma") == ("F = ma", "")


def test_is_centered():
    assert is_centered((400, 0, 200, 40), page_width=1000) is True
    assert is_centered((20, 0, 900, 40), page_width=1000) is False


# --- ノンブル(§7.5) ---------------------------------------------------------


def test_parse_nombre():
    assert parse_nombre("87") == 87
    assert parse_nombre("— 128 —") == 128
    assert parse_nombre("ページ") is None
    assert parse_nombre("99999") is None  # 4桁までを想定


# --- ルビ(§10.4) ------------------------------------------------------------


def test_detect_ruby_horizontal():
    base = _line("蜃気楼", (100, 100, 150, 50))
    ruby = _line("しんきろう", (100, 70, 150, 22))
    mapping = detect_ruby([ruby, base], body_line_height=50, vertical=False)
    assert mapping.get(0) == 1


def test_detect_ruby_ignores_kanji_line():
    base = _line("蜃気楼", (100, 100, 150, 50))
    other = _line("気温", (100, 70, 150, 22))  # 漢字を含むのでルビではない
    assert detect_ruby([other, base], body_line_height=50, vertical=False) == {}


def test_format_ruby():
    assert format_ruby("蜃気楼", "しんきろう") == "｜蜃気楼《しんきろう》"


def test_estimate_body_line_height():
    lines = [_line("あ", (0, 0, 100, 40)), _line("い", (0, 50, 100, 44)), _line("う", (0, 100, 100, 42))]
    assert estimate_body_line_height(lines) == 42


# --- 分裂した式番号の結合(Apple Vision対策) ----------------------------------


def test_merge_stray_equation_numbers_same_row():
    formula = _line("F＝ qvB sin θ", (558, 483, 302, 52), conf=0.5)
    eq_number = _line("（3.14）", (1159, 487, 113, 41), conf=1.0)
    merged = _merge_stray_equation_numbers([formula, eq_number])
    assert len(merged) == 1
    assert "（3.14）" in merged[0].text


def test_merge_stray_equation_numbers_different_row():
    formula = _line("F＝ qvB sin θ", (558, 483, 302, 52), conf=0.5)
    eq_number = _line("（3.14）", (1159, 900, 113, 41), conf=1.0)  # y位置が大きく離れている
    merged = _merge_stray_equation_numbers([formula, eq_number])
    assert len(merged) == 2


# --- ヘッダ/フッタ(§10.1) ----------------------------------------------------


def test_classify_header_footer_wider_band():
    """実測: ノンブルがband_ratio=0.07の帯からわずかに外れてbody_text扱いになっていた回帰テスト。"""
    assert classify_header_footer((687, 1898, 47, 33), 2057) == BlockKind.FOOTER


# --- 行 -> Block 変換 --------------------------------------------------------


def test_lines_to_blocks_classifies_and_orders():
    lines = [
        _line("3.4 ローレンツ力", (100, 100, 400, 50)),
        _line("磁場の中を運動する電荷には力がはたらく。", (100, 200, 700, 45)),
        _line("F = qvB (3.14)", (400, 300, 300, 45)),
        _line("87", (480, 1900, 40, 25)),
    ]
    blocks, vertical, _ruby = lines_to_blocks(lines, page_size=(1000, 2000), page_prefix="p0001")

    assert vertical is False
    assert len(blocks) == 4
    kinds = {b.text or b.latex: b.kind for b in blocks}
    assert kinds["F = qvB"] == BlockKind.MATH_BLOCK
    assert kinds["87"] == BlockKind.FOOTER
    # 式番号は分離して保持される
    math_block = next(b for b in blocks if b.kind == BlockKind.MATH_BLOCK)
    assert math_block.equation_number == "(3.14)"
    # 読み順が1から振られている
    assert sorted(b.reading_order for b in blocks) == [1, 2, 3, 4]
