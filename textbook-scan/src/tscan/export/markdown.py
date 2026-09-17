"""Markdown+LaTeX出力(仕様書 §13.2)。"""
from __future__ import annotations

from tscan.models import Block, BlockKind, Book, Page

_SKIP_KINDS = {BlockKind.HEADER, BlockKind.FOOTER, BlockKind.RUBY}


def _block_to_markdown(block: Block) -> str:
    if block.kind == BlockKind.MATH_BLOCK:
        # 式番号は \tag{} として保持する(§13.2)
        tag = f" \\tag{{{block.equation_number.strip('()（）')}}}" if block.equation_number else ""
        body = f"$$\n{block.latex}{tag}\n$$"
    elif block.kind == BlockKind.MATH_INLINE:
        body = f"${block.latex}$"
    elif block.kind == BlockKind.FIGURE:
        body = f"![{block.text or '図'}](figures/{block.block_id}.png)"
    elif block.kind == BlockKind.TABLE:
        body = f"<!-- 表(未構造化, §20 Q4によりv2対応): {block.block_id} -->\n{block.text}"
    else:
        body = block.text

    # §11.3 REQ-QA-05: 0.80〜0.95 は自動採用するが⚠️マーカーを付ける
    if block.confidence < 0.95 and block.edited_by != "user":
        reasons = "; ".join(i.detail for i in block.issues[:2] if i.severity != "low")
        label = "要確認" if block.review_status == "pending" else "自動採用(要注意)"
        body += f"\n\n> ⚠️ 信頼度 {block.confidence:.2f} — {label} ({block.block_id})"
        if reasons:
            body += f"\n> {reasons}"

    return body


def page_to_markdown(page: Page, include_page_marker: bool = True, page_number: int | None = None) -> str:
    """1ページ分のブロックを、reading_order順にMarkdownへ変換する。"""
    ordered = sorted(page.blocks, key=lambda b: b.reading_order)
    parts: list[str] = []

    if include_page_marker:
        label = f"p.{page.printed_number}" if page.printed_number else (f"#{page_number}" if page_number else "")
        if label:
            parts.append(f"<!-- --- {label} --- -->")

    for block in ordered:
        if block.kind in _SKIP_KINDS:
            continue
        rendered = _block_to_markdown(block)
        if rendered.strip():
            parts.append(rendered)

    return "\n\n".join(parts)


def book_to_markdown(book: Book, include_page_markers: bool = True) -> str:
    """本全体をMarkdownへ変換する(§13.2 REQ-OUT-01)。"""
    visible_pages = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    sections = [f"# {book.title or book.book_id}\n"]

    for i, page in enumerate(visible_pages, start=1):
        content = page_to_markdown(page, include_page_marker=include_page_markers, page_number=i)
        if content.strip():
            sections.append(content)

    sections.append(
        "\n---\n\n> 本ファイルは所有者の私的使用目的で作成されたものです(§3.3 / §16.2 REQ-SEC-04)。"
    )
    return "\n\n".join(sections) + "\n"
