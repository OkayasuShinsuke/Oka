from tscan.export.markdown import book_to_markdown
from tscan.export.report import generate_quality_report
from tscan.models import Block, BlockKind, Book, Page


def _sample_book() -> Book:
    page = Page.new("p0001.png", order_key=1000)
    page.blocks = [
        Block(
            block_id="b1",
            kind=BlockKind.BODY_TEXT,
            bbox=(0, 0, 100, 20),
            reading_order=1,
            text="したがって、力は次式で表される。",
            confidence=0.99,
            review_status="confirmed",
        ),
        Block(
            block_id="b2",
            kind=BlockKind.MATH_BLOCK,
            bbox=(0, 30, 100, 20),
            reading_order=2,
            latex=r"\vec{F} = q\vec{v} \times \vec{B}",
            confidence=0.62,
            review_status="edited",
        ),
    ]
    return Book(book_id="physics_2026", title="物理基礎", pages=[page])


def test_book_to_markdown_includes_text_and_math():
    md = book_to_markdown(_sample_book())
    assert "物理基礎" in md
    assert "したがって" in md
    assert r"\vec{F}" in md
    assert "$$" in md


def test_book_to_markdown_flags_low_confidence():
    md = book_to_markdown(_sample_book())
    assert "要確認" in md
    assert "0.62" in md


def test_generate_quality_report_counts_blocks():
    html = generate_quality_report(_sample_book())
    assert "物理基礎" in html
    assert "<table" in html
