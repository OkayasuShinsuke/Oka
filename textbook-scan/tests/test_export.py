from pathlib import Path

import numpy as np
import pytest

from tscan.export.markdown import book_to_markdown
from tscan.export.report import collect_stats, generate_quality_report
from tscan.models import Block, BlockKind, Book, Issue, Page


def _sample_book() -> Book:
    page = Page.new("p0001.png", order_key=1000.0)
    page.printed_number = 87
    page.blocks = [
        Block(block_id="b1", kind=BlockKind.BODY_TEXT, bbox=(0, 0, 100, 20), reading_order=1,
              text="したがって、力は次式で表される。", confidence=0.99, review_status="confirmed"),
        Block(block_id="b2", kind=BlockKind.MATH_BLOCK, bbox=(0, 30, 100, 20), reading_order=2,
              latex=r"\vec{F} = q\vec{v} \times \vec{B}", equation_number="(3.14)",
              confidence=0.62, review_status="pending",
              issues=[Issue(code="ENGINE_DISAGREE", detail="times vs x", severity="high")]),
        Block(block_id="b3", kind=BlockKind.FOOTER, bbox=(0, 900, 20, 10), reading_order=3,
              text="87", confidence=0.95, review_status="confirmed"),
    ]
    return Book(book_id="physics_2026", title="物理基礎", pages=[page])


def test_book_to_markdown_includes_text_and_math():
    md = book_to_markdown(_sample_book())
    assert "物理基礎" in md and "したがって" in md
    assert r"\vec{F}" in md and "$$" in md


def test_book_to_markdown_renders_equation_number_as_tag():
    assert r"\tag{3.14}" in book_to_markdown(_sample_book())


def test_book_to_markdown_flags_low_confidence():
    md = book_to_markdown(_sample_book())
    assert "要確認" in md and "0.62" in md


def test_book_to_markdown_skips_footer():
    """ヘッダ・フッタは本文に混ぜない(§13.2)。"""
    md = book_to_markdown(_sample_book(), include_page_markers=False)
    assert "\n87\n" not in md


def test_book_to_markdown_includes_page_marker():
    assert "p.87" in book_to_markdown(_sample_book())


def test_collect_stats_counts_status():
    stats = collect_stats(_sample_book())
    assert stats["blocks"] == 3
    assert stats["status"]["confirmed"] == 2
    assert stats["needs_review"] == 1
    assert abs(stats["review_rate"] - 1 / 3) < 1e-9


def test_generate_quality_report_contains_verdict():
    html = generate_quality_report(_sample_book())
    assert "物理基礎" in html and "<table" in html
    assert "レビュー率" in html


def test_searchable_pdf_has_invisible_text_layer(tmp_path: Path):
    """§13.1: 画像の上に不可視テキストを重ね、検索できること。"""
    pymupdf = pytest.importorskip("pymupdf")
    cv2 = pytest.importorskip("cv2")

    image = np.full((400, 300), 255, dtype=np.uint8)
    image_path = tmp_path / "page.png"
    cv2.imwrite(str(image_path), image)

    book = Book(book_id="t", title="テスト", pages=[Page.new(str(image_path), order_key=1000.0)])
    book.pages[0].blocks = [
        Block(block_id="b1", kind=BlockKind.BODY_TEXT, bbox=(20, 40, 200, 30), reading_order=1,
              text="磁場の中を運動する電荷", confidence=0.99)
    ]

    from tscan.export.pdf import build_searchable_pdf

    out = build_searchable_pdf(book, {book.pages[0].page_id: image_path}, tmp_path / "out.pdf")
    assert out.exists()

    doc = pymupdf.open(str(out))
    assert doc.page_count == 1
    assert "磁場" in doc[0].get_text()
    assert len(doc[0].search_for("磁場")) >= 1
    doc.close()
