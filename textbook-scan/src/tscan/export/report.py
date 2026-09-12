"""品質レポート出力(仕様書 §13.4)。"""
from __future__ import annotations

from tscan.models import Book


def generate_quality_report(book: Book) -> str:
    """§13.4のHTML品質レポートを生成する。"""
    visible_pages = [p for p in book.pages if not p.deleted]
    all_blocks = [b for p in visible_pages for b in p.blocks]

    total = len(all_blocks)
    auto_accepted = sum(1 for b in all_blocks if b.review_status == "confirmed")
    edited = sum(1 for b in all_blocks if b.review_status == "edited")
    skipped = sum(1 for b in all_blocks if b.review_status == "skipped")

    def pct(n: int) -> str:
        return f"{(n / total * 100):.1f}%" if total else "—"

    rows = f"""
    <tr><td>総ページ数</td><td>{len(visible_pages)}</td></tr>
    <tr><td>総ブロック数</td><td>{total}</td></tr>
    <tr><td>自動採用</td><td>{auto_accepted} ({pct(auto_accepted)})</td></tr>
    <tr><td>要確認 → 修正済み</td><td>{edited} ({pct(edited)})</td></tr>
    <tr><td>スキップ</td><td>{skipped} ({pct(skipped)})</td></tr>
    """

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>{book.title or book.book_id} スキャン品質レポート</title></head>
<body>
<h1>{book.title or book.book_id} スキャン品質レポート</h1>
<table border="1" cellpadding="6">{rows}</table>
<p>⚠️ 注記: 本ファイルは所有者の私的使用目的で作成されたものです(§16.2 REQ-SEC-04)。</p>
</body></html>
"""
