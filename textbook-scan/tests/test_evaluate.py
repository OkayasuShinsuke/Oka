from tscan.evaluate import cer, evaluate_book, normalize_for_eval
from tscan.models import Block, BlockKind, Book, Page


def test_cer_identical():
    assert cer("磁場の中を運動する", "磁場の中を運動する") == 0.0


def test_cer_one_substitution():
    # 6文字中1文字違い
    assert abs(cer("ローレンツ力", "ローレンツカ") - 1 / 6) < 1e-9


def test_cer_ignores_whitespace_and_width():
    assert cer("F = ma", "Ｆ＝ｍａ") == 0.0


def test_normalize_for_eval():
    assert normalize_for_eval("F = ma") == "F=ma"


def test_cer_empty_reference():
    assert cer("", "") == 0.0
    assert cer("", "余計な文字") == 1.0


def _book_with(text: str, latex: str = "") -> Book:
    page = Page.new("p1.png", order_key=1000.0)
    page.blocks = [
        Block(block_id="b1", kind=BlockKind.BODY_TEXT, bbox=(0, 0, 10, 10), reading_order=1, text=text,
              confidence=0.99, review_status="confirmed")
    ]
    if latex:
        page.blocks.append(
            Block(block_id="b2", kind=BlockKind.MATH_BLOCK, bbox=(0, 20, 10, 10), reading_order=2,
                  latex=latex, confidence=0.9, review_status="pending")
        )
    return Book(book_id="t", pages=[page])


def test_evaluate_book_measures_cer_and_review_rate():
    book = _book_with("磁場の中を運動する電荷", latex="F = ma")
    result = evaluate_book(book, references={1: "磁場の中を運動する電荷F = ma"})
    assert result.cer == 0.0
    assert result.blocks == 2
    assert abs(result.review_rate - 0.5) < 1e-9  # 2ブロック中1つがpending


def test_math_accuracy_is_none_when_nothing_measured():
    """測定対象がないのに「100%」と報告してはいけない。"""
    result = evaluate_book(_book_with("本文"), references={1: "本文"})
    assert result.math_accuracy is None
    passed, label = result.verdict()["数式行正解率 ≧ 95%"]
    assert "測定対象なし" in label


def test_math_accuracy_counts_matching_formula():
    book = _book_with("本文", latex="F = ma")
    result = evaluate_book(book, references={1: "本文"}, math_references={1: ["F = ma"]})
    assert result.math_accuracy == 1.0


def test_math_accuracy_counts_mismatch():
    book = _book_with("本文", latex="F = rna")
    result = evaluate_book(book, references={1: "本文"}, math_references={1: ["F = ma"]})
    assert result.math_accuracy == 0.0
