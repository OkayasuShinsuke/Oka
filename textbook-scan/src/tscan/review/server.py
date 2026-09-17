"""レビュー・編集UI(仕様書 §11.7)。

REQ-UI-02: ローカルWebアプリ(FastAPI + 素のHTML/JS)。追加インストールを最小化する。

画面構成(§11.7.1):
    /                      ホーム画面(本棚)            §11.7.2
    /book/{book_id}        ページ一覧(サムネイル)       §11.7.3
    /book/{book_id}/page/N 個別ページ編集              §11.7.4b
    /book/{book_id}/queue  要確認キュー(キーボード駆動) §11.7.4a
    /settings              設定(保存先・エンジン)       §11.7.5
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from tscan.config import load_config, set_user_output_dir
from tscan.models import Block, BlockKind, Book, insert_between
from tscan.models import Page as PageModel

app = FastAPI(title="教科書スキャン レビューUI")

# --- §11.7.6 REQ-UI-05: 色とアイコンを全画面で統一する -----------------------
STATUS_STYLE = {
    "confirmed": ("✅", "#1b873f", "確認済み"),
    "edited": ("✏️", "#1b6ec2", "修正済み"),
    "pending": ("⚠️", "#c88a00", "要確認"),
    "skipped": ("⏭", "#888888", "保留"),
}

_BASE_CSS = """
:root { --ok:#1b873f; --warn:#c88a00; --edit:#1b6ec2; --muted:#888; --bg:#fafafa; --line:#ddd; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif;
       margin:0; background:var(--bg); color:#222; line-height:1.6; }
header { background:#fff; border-bottom:1px solid var(--line); padding:12px 20px;
         display:flex; align-items:center; gap:16px; position:sticky; top:0; z-index:10; }
header h1 { font-size:1.05rem; margin:0; }
.crumb { color:var(--muted); font-size:.9rem; }
.crumb a { color:var(--edit); text-decoration:none; }
main { padding:20px; max-width:1200px; margin:0 auto; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:14px; }
.card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:10px;
        text-decoration:none; color:inherit; display:block; }
.card:hover { border-color:var(--edit); }
.card img { width:100%; height:180px; object-fit:contain; background:#f0f0f0; border-radius:4px; }
.badge { font-size:.85rem; }
.split { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:20px; align-items:start; }
.panel { background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; }
.panel img { width:100%; border-radius:4px; }
.block { border:1px solid var(--line); border-radius:6px; padding:10px; margin-bottom:10px; background:#fff; }
.block.sel { border-color:var(--edit); box-shadow:0 0 0 2px rgba(27,110,194,.15); }
.block textarea { width:100%; font-family:ui-monospace,monospace; font-size:.9rem;
                  border:1px solid var(--line); border-radius:4px; padding:6px; min-height:56px; }
.row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:6px; }
button, select { font:inherit; padding:5px 10px; border:1px solid var(--line);
                 background:#fff; border-radius:5px; cursor:pointer; }
button.primary { background:var(--edit); color:#fff; border-color:var(--edit); }
.issue { font-size:.85rem; color:var(--warn); }
.kbd { background:#eee; border:1px solid #ccc; border-radius:3px; padding:1px 5px;
       font-family:ui-monospace,monospace; font-size:.85rem; }
.hint { color:var(--muted); font-size:.9rem; }
"""


def _header(title: str, crumb_html: str = "") -> str:
    return f"""<header>
      <h1>📚 {title}</h1>
      <span class="crumb">{crumb_html}</span>
      <span style="margin-left:auto"><a class="crumb" href="/settings">⚙ 設定</a></span>
    </header>"""


def _page(title: str, crumb: str, body: str, script: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_BASE_CSS}</style></head>
<body>{_header(title, crumb)}<main>{body}</main><script>{script}</script></body></html>"""
    )


# ---------------------------------------------------------------------------
# データアクセス
# ---------------------------------------------------------------------------


def _output_root() -> Path:
    return load_config().resolved_output_root()


def _book_path(book_id: str) -> Path:
    return _output_root() / book_id / "book.json"


def _load(book_id: str) -> Book:
    path = _book_path(book_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"本が見つかりません: {book_id}")
    return Book.load(path)


def _save(book: Book) -> None:
    Book.save(book, _book_path(book.book_id))


def _ordered_pages(book: Book) -> list[PageModel]:
    return sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)


def _page_by_number(book: Book, number: int) -> PageModel:
    pages = _ordered_pages(book)
    if not 1 <= number <= len(pages):
        raise HTTPException(status_code=404, detail=f"ページ{number}は存在しません")
    return pages[number - 1]


def _preprocessed_path(book_id: str, number: int) -> Path:
    return _output_root() / book_id / "work" / "stages" / "preprocessed" / f"{book_id}_p{number:04d}.png"


# ---------------------------------------------------------------------------
# §11.7.2 ホーム画面(本棚)
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    root = _output_root()
    cards = []
    if root.exists():
        for book_dir in sorted(root.iterdir()):
            book_json = book_dir / "book.json"
            if not book_json.exists():
                continue
            data = json.loads(book_json.read_text(encoding="utf-8"))
            pages = [p for p in data.get("pages", []) if not p.get("deleted")]
            pending = sum(
                1
                for p in pages
                for b in p.get("blocks", [])
                if b.get("review_status") == "pending"
            )
            state = (
                f'<span class="badge" style="color:var(--warn)">⚠️ 要確認 {pending}件</span>'
                if pending
                else '<span class="badge" style="color:var(--ok)">✅ 全て確認済</span>'
            )
            cards.append(
                f"""<a class="card" href="/book/{data.get('book_id')}">
                     <strong>{data.get('title') or data.get('book_id')}</strong><br>
                     <span class="hint">{len(pages)}ページ</span><br>{state}</a>"""
            )

    body = f"""<p class="hint">保存先: {root}</p>
      <div class="grid">{''.join(cards) or '<p>まだ本がありません。<code>tscan ingest</code> で取り込んでください。</p>'}</div>"""
    return _page("教科書スキャン", "", body)


# ---------------------------------------------------------------------------
# §11.7.3 ページ一覧(サムネイルグリッド)
# ---------------------------------------------------------------------------


@app.get("/book/{book_id}", response_class=HTMLResponse)
def page_grid(book_id: str, filter: str = "all") -> HTMLResponse:
    book = _load(book_id)
    pages = _ordered_pages(book)

    cards = []
    for i, page in enumerate(pages, start=1):
        pending = sum(1 for b in page.blocks if b.review_status == "pending")
        if filter == "pending" and not pending:
            continue
        if not page.blocks:
            icon, color, label = "🔲", "var(--muted)", "未処理"
        elif pending:
            icon, color, label = "⚠️", "var(--warn)", f"要確認 {pending}件"
        else:
            icon, color, label = "✅", "var(--ok)", "確認済み"

        nombre = f"p.{page.printed_number}" if page.printed_number else ""
        side = {"left": "見開き・左", "right": "見開き・右"}.get(page.spread_side or "", "")
        if side:
            nombre = f"{nombre} {side}".strip()
        cards.append(
            f"""<a class="card" href="/book/{book_id}/page/{i}">
                  <img src="/api/{book_id}/image/{i}" loading="lazy" alt="page {i}">
                  <div class="row"><strong>#{i}</strong> <span class="hint">{nombre}</span></div>
                  <span class="badge" style="color:{color}">{icon} {label}</span>
                </a>"""
        )

    total_pending = sum(1 for p in pages for b in p.blocks if b.review_status == "pending")
    body = f"""
      <div class="row" style="margin-bottom:14px">
        <a href="/book/{book_id}?filter=all"><button>すべて表示</button></a>
        <a href="/book/{book_id}?filter=pending"><button>要確認のみ表示</button></a>
        <a href="/book/{book_id}/queue"><button class="primary">要確認キューを開く({total_pending}件)</button></a>
        <span class="hint">✅確認済み ⚠️要確認 🔲未処理</span>
      </div>
      <div class="grid">{''.join(cards) or '<p>該当するページがありません。</p>'}</div>"""
    crumb = f'<a href="/">ホーム</a> ＞ {book.title or book_id}'
    return _page(book.title or book_id, crumb, body)


@app.get("/api/{book_id}/image/{number}")
def page_image(book_id: str, number: int) -> Response:
    """前処理済み画像を返す。未処理なら元画像にフォールバックする。"""
    path = _preprocessed_path(book_id, number)
    if not path.exists():
        book = _load(book_id)
        page = _page_by_number(book, number)
        path = Path(page.image_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="画像が見つかりません")

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise HTTPException(status_code=404, detail="画像を読み込めません")

    scale = min(1.0, 1400 / max(image.shape[:2]))
    if scale < 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        raise HTTPException(status_code=500, detail="画像のエンコードに失敗しました")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# §11.7.4b 個別ページ編集(どのページ・どのブロックでも編集できる)
# ---------------------------------------------------------------------------


def _block_html(book_id: str, number: int, block: Block) -> str:
    icon, color, label = STATUS_STYLE.get(block.review_status, ("・", "#666", block.review_status))
    content = block.latex if block.kind == BlockKind.MATH_BLOCK else block.text
    issues = "".join(f'<div class="issue">• {i.detail}</div>' for i in block.issues[:4])
    kinds = "".join(
        f'<option value="{k.value}" {"selected" if k == block.kind else ""}>{k.value}</option>' for k in BlockKind
    )
    return f"""
    <div class="block" id="blk-{block.block_id}" data-id="{block.block_id}">
      <div class="row">
        <span class="badge" style="color:{color}">{icon} {label}</span>
        <span class="hint">信頼度 {block.confidence:.2f}</span>
        <span class="hint">#{block.reading_order}</span>
        {f'<span class="hint">{block.edited_by}が修正</span>' if block.edited_by else ''}
      </div>
      <textarea id="txt-{block.block_id}">{content}</textarea>
      {issues}
      <div class="row">
        <select id="kind-{block.block_id}">{kinds}</select>
        <button class="primary" onclick="saveBlock('{block.block_id}')">保存</button>
        <button onclick="reocr('{block.block_id}')">🔄 このブロックだけOCR再実行</button>
        <button onclick="setStatus('{block.block_id}','skipped')">⏭ 保留</button>
      </div>
    </div>"""


@app.get("/book/{book_id}/page/{number}", response_class=HTMLResponse)
def page_editor(book_id: str, number: int) -> HTMLResponse:
    book = _load(book_id)
    page = _page_by_number(book, number)
    total = len(_ordered_pages(book))

    blocks_html = "".join(
        _block_html(book_id, number, b) for b in sorted(page.blocks, key=lambda b: b.reading_order)
    ) or '<p class="hint">このページにはまだブロックがありません(<code>tscan run</code>が未実行)。</p>'

    prev_link = f'<a href="/book/{book_id}/page/{number - 1}"><button>◀ 前ページ</button></a>' if number > 1 else ""
    next_link = f'<a href="/book/{book_id}/page/{number + 1}"><button>次ページ ▶</button></a>' if number < total else ""

    body = f"""
      <div class="row" style="margin-bottom:12px">
        {prev_link}{next_link}
        <span class="hint">{number} / {total} ページ{' ・ 縦書き' if page.layout == 'vertical' else ''}</span>
        <span style="margin-left:auto">
          <button onclick="insertPage()">＋ このページの後に挿入</button>
          <button onclick="deletePage()">🗑 このページを削除</button>
        </span>
      </div>
      <div class="split">
        <div class="panel"><img src="/api/{book_id}/image/{number}" alt="page {number}"></div>
        <div>{blocks_html}</div>
      </div>
      <p class="hint">変更は保存ボタンで確定します。信頼度の高いブロックも自由に編集できます(§11.7.4b REQ-UI-08)。</p>"""

    script = f"""
      const BOOK = "{book_id}", NUM = {number};
      async function saveBlock(id) {{
        const text = document.getElementById('txt-'+id).value;
        const kind = document.getElementById('kind-'+id).value;
        const r = await fetch(`/api/${{BOOK}}/block/${{id}}`, {{
          method:'PATCH', headers:{{'Content-Type':'application/json'}},
          body: JSON.stringify({{text, kind, review_status:'edited'}})}});
        if (r.ok) location.reload(); else alert('保存に失敗しました');
      }}
      async function setStatus(id, status) {{
        const r = await fetch(`/api/${{BOOK}}/block/${{id}}`, {{
          method:'PATCH', headers:{{'Content-Type':'application/json'}},
          body: JSON.stringify({{review_status:status}})}});
        if (r.ok) location.reload();
      }}
      async function reocr(id) {{
        const r = await fetch(`/api/${{BOOK}}/page/${{NUM}}/reocr/${{id}}`, {{method:'POST'}});
        const data = await r.json();
        if (r.ok) {{ document.getElementById('txt-'+id).value = data.text; }}
        else alert(data.detail || 'OCR再実行に失敗しました');
      }}
      async function insertPage() {{
        const path = prompt('挿入する画像ファイルのパスを入力してください');
        if (!path) return;
        const r = await fetch(`/api/${{BOOK}}/pages/insert`, {{
          method:'POST', headers:{{'Content-Type':'application/json'}},
          body: JSON.stringify({{after:NUM, image_path:path}})}});
        if (r.ok) location.href = `/book/${{BOOK}}`; else alert('挿入に失敗しました');
      }}
      async function deletePage() {{
        if (!confirm('このページを削除しますか?(論理削除。book.jsonから復元可能)')) return;
        const r = await fetch(`/api/${{BOOK}}/pages/${{NUM}}`, {{method:'DELETE'}});
        if (r.ok) location.href = `/book/${{BOOK}}`;
      }}"""

    crumb = f'<a href="/">ホーム</a> ＞ <a href="/book/{book_id}">{book.title or book_id}</a> ＞ p{number}'
    return _page(book.title or book_id, crumb, body, script)


# ---------------------------------------------------------------------------
# §11.7.4a 要確認キュー(キーボード駆動)
# ---------------------------------------------------------------------------


@app.get("/book/{book_id}/queue", response_class=HTMLResponse)
def review_queue(book_id: str) -> HTMLResponse:
    book = _load(book_id)
    pages = _ordered_pages(book)

    items = []
    for i, page in enumerate(pages, start=1):
        for block in sorted(page.blocks, key=lambda b: b.reading_order):
            if block.review_status == "pending":
                items.append(
                    {
                        "page": i,
                        "block_id": block.block_id,
                        "text": block.latex if block.kind == BlockKind.MATH_BLOCK else block.text,
                        "kind": block.kind.value,
                        "confidence": round(block.confidence, 3),
                        "issues": [issue.detail for issue in block.issues],
                    }
                )

    body = f"""
      <div id="empty" style="display:none"><p>✅ 要確認の項目はありません。</p>
        <a href="/book/{book_id}"><button>ページ一覧へ戻る</button></a></div>
      <div id="app" class="split">
        <div class="panel"><img id="img" alt="対象ページ"><div class="hint" id="pos"></div></div>
        <div class="panel">
          <div class="row"><span id="meta" class="hint"></span></div>
          <div id="issues"></div>
          <textarea id="editor" style="width:100%;min-height:120px;font-family:ui-monospace,monospace"></textarea>
          <div class="row">
            <button class="primary" onclick="commit('edited')">保存して次へ <span class="kbd">Ctrl+S</span></button>
            <button onclick="commit('confirmed')">このままでOK <span class="kbd">Enter</span></button>
            <button onclick="commit('skipped')">保留 <span class="kbd">Esc</span></button>
          </div>
          <p class="hint">残り <span id="left"></span> 件 — キーボードだけで処理できます(§11.7.4a)。</p>
        </div>
      </div>"""

    script = f"""
      const BOOK = "{book_id}";
      let items = {json.dumps(items, ensure_ascii=False)};
      let idx = 0;
      function render() {{
        if (idx >= items.length) {{
          document.getElementById('app').style.display='none';
          document.getElementById('empty').style.display='block'; return;
        }}
        const it = items[idx];
        document.getElementById('img').src = `/api/${{BOOK}}/image/${{it.page}}`;
        document.getElementById('editor').value = it.text;
        document.getElementById('meta').textContent =
          `p${{it.page}} / ${{it.kind}} / 信頼度 ${{it.confidence}}`;
        document.getElementById('issues').innerHTML =
          it.issues.map(s => `<div class="issue">• ${{s}}</div>`).join('');
        document.getElementById('pos').textContent = `${{idx+1}} / ${{items.length}} 件目`;
        document.getElementById('left').textContent = items.length - idx;
        document.getElementById('editor').focus();
      }}
      async function commit(status) {{
        const it = items[idx];
        await fetch(`/api/${{BOOK}}/block/${{it.block_id}}`, {{
          method:'PATCH', headers:{{'Content-Type':'application/json'}},
          body: JSON.stringify({{text: document.getElementById('editor').value, review_status: status}})}});
        idx++; render();
      }}
      document.addEventListener('keydown', e => {{
        if (e.key === 'Enter' && !e.shiftKey && e.target.id !== 'editor') {{ e.preventDefault(); commit('confirmed'); }}
        else if (e.key === 's' && (e.ctrlKey || e.metaKey)) {{ e.preventDefault(); commit('edited'); }}
        else if (e.key === 'Escape') {{ e.preventDefault(); commit('skipped'); }}
      }});
      render();"""

    crumb = f'<a href="/">ホーム</a> ＞ <a href="/book/{book_id}">{book.title or book_id}</a> ＞ 要確認キュー'
    return _page(book.title or book_id, crumb, body, script)


# ---------------------------------------------------------------------------
# 編集API
# ---------------------------------------------------------------------------


class BlockPatch(BaseModel):
    text: str | None = None
    kind: str | None = None
    review_status: str | None = None


@app.patch("/api/{book_id}/block/{block_id}")
def patch_block(book_id: str, block_id: str, patch: BlockPatch) -> JSONResponse:
    """ブロックを手動修正する(§11.7.4b REQ-UI-09: edited_by/edited_atを記録)。"""
    book = _load(book_id)
    for page in book.pages:
        for block in page.blocks:
            if block.block_id != block_id:
                continue
            if patch.kind:
                block.kind = BlockKind(patch.kind)
            if patch.text is not None:
                if block.kind == BlockKind.MATH_BLOCK:
                    block.latex = patch.text
                else:
                    block.text = patch.text
            if patch.review_status:
                block.review_status = patch.review_status
            if patch.text is not None or patch.kind:
                block.edited_by = "user"
                block.edited_at = datetime.now().isoformat()
                block.confidence = max(block.confidence, 0.99)  # 人間の確認は最も信頼できる
                block.issues = []
            _save(book)
            return JSONResponse({"ok": True, "block_id": block_id})
    raise HTTPException(status_code=404, detail=f"ブロックが見つかりません: {block_id}")


@app.post("/api/{book_id}/page/{number}/reocr/{block_id}")
def reocr_block(book_id: str, number: int, block_id: str) -> JSONResponse:
    """1ブロックだけOCRを再実行する(§11.7.4b)。"""
    from tscan.ocr.registry import build_text_engines

    book = _load(book_id)
    page = _page_by_number(book, number)
    block = next((b for b in page.blocks if b.block_id == block_id), None)
    if block is None:
        raise HTTPException(status_code=404, detail="ブロックが見つかりません")

    path = _preprocessed_path(book_id, number)
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path.exists() else None
    if image is None:
        raise HTTPException(status_code=404, detail="前処理済み画像がありません(tscan runを先に実行してください)")

    x, y, w, h = block.bbox
    crop = image[max(y, 0) : y + h, max(x, 0) : x + w]
    if crop.size == 0:
        raise HTTPException(status_code=400, detail="ブロックの範囲が不正です")

    engines = build_text_engines(vertical=(page.layout == "vertical"))
    if engines.primary is None:
        raise HTTPException(status_code=503, detail="利用可能なOCRエンジンがありません")

    lines = engines.primary.recognize(crop, vertical=(page.layout == "vertical"))
    text = "".join(ln.text for ln in lines)
    return JSONResponse({"ok": True, "text": text})


class InsertRequest(BaseModel):
    after: int
    image_path: str


@app.post("/api/{book_id}/pages/insert")
def api_insert_page(book_id: str, req: InsertRequest) -> JSONResponse:
    """ページを挿入する(§7.7.3)。既存ページのorder_keyは変更しない。"""
    book = _load(book_id)
    pages = _ordered_pages(book)
    if not 1 <= req.after <= len(pages):
        raise HTTPException(status_code=400, detail="挿入位置が不正です")

    src = Path(req.image_path).expanduser()
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"画像が見つかりません: {src}")

    before = pages[req.after - 1]
    after = pages[req.after] if req.after < len(pages) else None
    new_key = insert_between(before.order_key, after.order_key if after else before.order_key + 2000.0)

    dst_dir = _output_root() / book_id / "work" / "raw"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{book_id}_inserted_{src.stem}{src.suffix}"
    dst.write_bytes(src.read_bytes())

    book.pages.append(PageModel.new(image_path=str(dst), order_key=new_key))
    _save(book)
    return JSONResponse({"ok": True, "order_key": new_key})


@app.delete("/api/{book_id}/pages/{number}")
def api_delete_page(book_id: str, number: int) -> JSONResponse:
    """ページを論理削除する(§7.7.3 REQ-PAGEMGMT-04)。"""
    book = _load(book_id)
    page = _page_by_number(book, number)
    page.deleted = True
    _save(book)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# §11.7.5 設定画面
# ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> HTMLResponse:
    from tscan.ocr.registry import describe_availability

    config = load_config()
    root = config.resolved_output_root()
    engines = describe_availability()
    rows = "".join(
        f"<tr><td>{name}</td><td>{'✅ ' if status == '利用可能' else '— '}{status}</td></tr>"
        for name, status in engines.items()
    )

    import shutil

    usage = shutil.disk_usage(root if root.exists() else Path.home())

    body = f"""
      <div class="panel">
        <h3>保存先フォルダ(§14.5)</h3>
        <p>現在の場所: <code>{root}</code><br>
           <span class="hint">空き容量 {usage.free / (1024**3):.1f}GB(§16.1: 1冊あたり目安5.1GB)</span></p>
        <div class="row">
          <input id="path" value="{root}" style="flex:1;padding:6px;border:1px solid var(--line);border-radius:4px">
          <button class="primary" onclick="saveDir()">変更</button>
        </div>
        <p class="hint">ユーザー設定(~/.config/tscan/config.yaml)に保存されます。</p>
      </div>
      <div class="panel" style="margin-top:16px">
        <h3>OCRエンジンの利用可否(§9.2)</h3>
        <table style="border-collapse:collapse"><tr><th style="text-align:left;padding:4px 12px">エンジン</th>
        <th style="text-align:left;padding:4px 12px">状態</th></tr>{rows}</table>
      </div>"""
    script = """
      async function saveDir() {
        const path = document.getElementById('path').value;
        const r = await fetch('/api/settings/output-dir', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({path})});
        if (r.ok) { alert('保存しました'); location.reload(); } else alert('保存に失敗しました');
      }"""
    return _page("設定", '<a href="/">ホーム</a> ＞ 設定', body, script)


class OutputDirRequest(BaseModel):
    path: str


@app.post("/api/settings/output-dir")
def api_set_output_dir(req: OutputDirRequest) -> JSONResponse:
    set_user_output_dir(req.path)
    return JSONResponse({"ok": True})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
