from tscan.models import Block, BlockKind, Page, compute_display_page_index, insert_between


def test_insert_between_midpoint():
    assert insert_between(149000, 150000) == 149500


def test_compute_display_page_index_orders_by_order_key():
    pages = [
        Page.new("p3.png", order_key=3000),
        Page.new("p1.png", order_key=1000),
        Page.new("p2.png", order_key=2000),
    ]
    index = compute_display_page_index(pages)
    assert index[pages[1].page_id] == 1
    assert index[pages[2].page_id] == 2
    assert index[pages[0].page_id] == 3


def test_compute_display_page_index_skips_deleted():
    pages = [
        Page.new("p1.png", order_key=1000),
        Page.new("p2.png", order_key=2000),
    ]
    pages[0].deleted = True
    index = compute_display_page_index(pages)
    assert pages[0].page_id not in index
    assert index[pages[1].page_id] == 1


def test_page_needs_review_reflects_blocks():
    page = Page.new("p1.png", order_key=1000)
    page.blocks.append(
        Block(block_id="b1", kind=BlockKind.BODY_TEXT, bbox=(0, 0, 10, 10), reading_order=1, confidence=0.99)
    )
    assert page.needs_review is False

    page.blocks.append(
        Block(block_id="b2", kind=BlockKind.MATH_BLOCK, bbox=(0, 0, 10, 10), reading_order=2, confidence=0.5)
    )
    assert page.needs_review is True
