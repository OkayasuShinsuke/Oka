"""品質レポート出力(仕様書 §13.4)。"""
from __future__ import annotations

from collections import Counter

from tscan.models import Book


def collect_stats(book: Book) -> dict:
    """レポート用の集計を行う(§13.4 / §11.8)。"""
    visible_pages = [p for p in book.pages if not p.deleted]
    blocks = [b for p in visible_pages for b in p.blocks]
    total = len(blocks)

    status = Counter(b.review_status for b in blocks)
    kinds = Counter(b.kind.value for b in blocks)
    issue_codes = Counter(i.code for b in blocks for i in b.issues)

    needs_review = sum(1 for b in blocks if b.needs_review)
    confidences = [b.confidence for b in blocks]

    nombre_warnings = sum(1 for p in visible_pages if "nombre_mismatch" in p.warnings)
    aspect_warnings = sum(1 for p in visible_pages if "ASPECT_RATIO_ANOMALY" in p.warnings)

    return {
        "pages": len(visible_pages),
        "blocks": total,
        "status": status,
        "kinds": kinds,
        "issue_codes": issue_codes,
        "needs_review": needs_review,
        "review_rate": (needs_review / total) if total else 0.0,
        "mean_confidence": (sum(confidences) / len(confidences)) if confidences else 0.0,
        "vertical_pages": sum(1 for p in visible_pages if p.layout == "vertical"),
        "nombre_warnings": nombre_warnings,
        "aspect_warnings": aspect_warnings,
    }


def generate_quality_report(book: Book) -> str:
    """§13.4のHTML品質レポートを生成する。"""
    s = collect_stats(book)
    total = s["blocks"]

    def pct(n: int) -> str:
        return f"{(n / total * 100):.1f}%" if total else "—"

    # §11.8 の受け入れ基準(レビュー率5%以下)に対する判定
    review_ok = "✅ 基準内" if s["review_rate"] <= 0.05 else "⚠️ 基準超過(§11.8: 5%以下)"

    summary_rows = "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>"
        for label, value in [
            ("総ページ数", s["pages"]),
            ("うち縦書きページ", s["vertical_pages"]),
            ("総ブロック数", total),
            ("自動採用(confirmed)", f"{s['status'].get('confirmed', 0)} ({pct(s['status'].get('confirmed', 0))})"),
            ("要確認(pending)", f"{s['status'].get('pending', 0)} ({pct(s['status'].get('pending', 0))})"),
            ("人手修正済み(edited)", f"{s['status'].get('edited', 0)} ({pct(s['status'].get('edited', 0))})"),
            ("スキップ(skipped)", f"{s['status'].get('skipped', 0)} ({pct(s['status'].get('skipped', 0))})"),
            ("平均信頼度", f"{s['mean_confidence']:.3f}"),
            ("レビュー率", f"{s['review_rate'] * 100:.1f}% — {review_ok}"),
            ("縦横比の異常検出(§8.3.2)", s["aspect_warnings"]),
            ("ノンブル不整合(§7.5)", s["nombre_warnings"]),
        ]
    )

    kind_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(s["kinds"].items()))
    issue_rows = (
        "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in s["issue_codes"].most_common())
        or "<tr><td colspan='2'>検出された問題はありません</td></tr>"
    )

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>{book.title or book.book_id} スキャン品質レポート</title>
<style>
 body {{ font-family: -apple-system, "Hiragino Sans", sans-serif; margin: 2rem; line-height: 1.7; color: #222; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 table {{ border-collapse: collapse; margin: 0.5rem 0 1.5rem; }}
 td, th {{ border: 1px solid #ccc; padding: 6px 12px; }}
 tr:nth-child(even) {{ background: #fafafa; }}
 .note {{ color: #666; font-size: 0.9rem; }}
</style></head>
<body>
<h1>{book.title or book.book_id} スキャン品質レポート</h1>

<h2>サマリー</h2>
<table>{summary_rows}</table>

<h2>ブロック種別の内訳(§10.1)</h2>
<table><tr><th>種別</th><th>件数</th></tr>{kind_rows}</table>

<h2>検出された問題の内訳(§11)</h2>
<table><tr><th>コード</th><th>件数</th></tr>{issue_rows}</table>

<p class="note">⚠️ 本ファイルは所有者の私的使用目的で作成されたものです(§16.2 REQ-SEC-04)。</p>
</body></html>
"""
