from tscan.layout import (
    assign_reading_order,
    classify_math_block,
    is_vertical_layout,
    score_math_block,
    sort_reading_order,
)
from tscan.models import Block, BlockKind


def _block(bbox, kind=BlockKind.BODY_TEXT) -> Block:
    return Block(block_id="b", kind=kind, bbox=bbox, reading_order=0)


def test_is_vertical_layout_true_when_most_lines_tall():
    lines = [_block((0, 0, 20, 200)), _block((30, 0, 20, 180)), _block((60, 0, 20, 190))]
    assert is_vertical_layout(lines) is True


def test_is_vertical_layout_false_when_most_lines_wide():
    lines = [_block((0, 0, 300, 20)), _block((0, 30, 280, 20))]
    assert is_vertical_layout(lines) is False


def test_is_vertical_layout_empty():
    assert is_vertical_layout([]) is False


def test_sort_reading_order_horizontal():
    blocks = [_block((100, 50, 10, 10)), _block((10, 50, 10, 10)), _block((10, 0, 10, 10))]
    ordered = sort_reading_order(blocks, vertical=False)
    assert [b.bbox for b in ordered] == [(10, 0, 10, 10), (10, 50, 10, 10), (100, 50, 10, 10)]


def test_sort_reading_order_vertical_right_to_left():
    blocks = [_block((10, 0, 10, 10)), _block((100, 0, 10, 10)), _block((100, 50, 10, 10))]
    ordered = sort_reading_order(blocks, vertical=True)
    assert [b.bbox for b in ordered] == [(100, 0, 10, 10), (100, 50, 10, 10), (10, 0, 10, 10)]


def test_assign_reading_order_updates_field():
    blocks = [_block((100, 0, 10, 10)), _block((10, 0, 10, 10))]
    ordered = assign_reading_order(blocks, vertical=False)
    assert [b.reading_order for b in ordered] == [1, 2]


def test_score_math_block_detects_formula():
    score = score_math_block("F = qv \\times B", bbox=(0, 0, 100, 60), body_line_height=30, centered=True, has_equation_number=True)
    assert classify_math_block(score) == "math_block"


def test_score_math_block_detects_body_text():
    score = score_math_block("したがって、次のようになる。", bbox=(0, 0, 500, 30), body_line_height=30)
    assert classify_math_block(score) == "body_text"
