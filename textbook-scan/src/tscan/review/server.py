"""レビュー・編集UI(仕様書 §11.7)。

現時点ではホーム画面(§11.7.2: 本棚)相当の最小実装のみ。
ページ一覧(§11.7.3)・編集画面(§11.7.4)・設定画面(§11.7.5)は今後の実装対象。
REQ-UI-02の通りFastAPI+素のHTML/JSで、追加インストールを最小化する方針を継続する。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from tscan.config import load_config

app = FastAPI(title="教科書スキャン レビューUI")


def _list_books(output_root: Path) -> list[dict]:
    books = []
    if not output_root.exists():
        return books
    for book_dir in sorted(output_root.iterdir()):
        book_json = book_dir / "book.json"
        if book_json.exists():
            data = json.loads(book_json.read_text(encoding="utf-8"))
            page_count = len(data.get("pages", []))
            books.append({"book_id": data.get("book_id", book_dir.name), "title": data.get("title", ""), "pages": page_count})
    return books


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """ホーム画面(§11.7.2)。保存先フォルダ配下の本を一覧表示する。"""
    config = load_config()
    books = _list_books(config.resolved_output_root())

    rows = "".join(
        f"<li><strong>{b['title'] or b['book_id']}</strong> — {b['pages']}ページ</li>" for b in books
    ) or "<li>まだ本がありません。`tscan ingest` で取り込んでください。</li>"

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>教科書スキャン</title></head>
<body>
<h1>📚 教科書スキャン</h1>
<p>保存先: {config.resolved_output_root()}</p>
<ul>{rows}</ul>
</body></html>
"""


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
