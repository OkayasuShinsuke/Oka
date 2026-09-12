"""Markdown+LaTeX出力(仕様書 §13.2)。"""
from __future__ import annotations

from tscan.models import Block, BlockKind, Book, Page


def _block_to_markdown(block: Block) -> str:
    if block.kind in (BlockKind.MATH_BLOCK,):
        body = f"$$\n{block.latex}\n$$"
    elif block.kind == BlockKind.MATH_INLINE:
        body = f"${block.latex}$"
    elif block.kind == BlockKind.FIGURE:
        body = f"![{block.text or '図'}](figures/{block.block_id}.png)"
    else:
        body = block.text

    if block.confidence < 0.80:
        body += f"\n\n> ⚠️ 信頼度 {block.confidence:.2f} — 要確認 (block {block.block_id})"

    return body


def page_to_markdown(page: Page) -> str:
    """1ページ分のブロックを、reading_order順にMarkdownへ変換する。"""
    ordered = sorted(page.blocks, key=lambda b: b.reading_order)
    return "\n\n".join(_block_to_markdown(b) for b in ordered if not b.kind == BlockKind.HEADER)


def book_to_markdown(book: Book) -> str:
    """本全体をMarkdownへ変換する(§13.2 REQ-OUT-01)。"""
    visible_pages = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    sections = [f"# {book.title or book.book_id}\n"]
    for page in visible_pages:
        content = page_to_markdown(page)
        if content:
            sections.append(content)
    return "\n\n".join(sections) + "\n"
