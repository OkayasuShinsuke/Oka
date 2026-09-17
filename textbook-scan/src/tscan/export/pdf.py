"""検索可能PDF出力(仕様書 §13.1)。

画像レイヤー(見た目)の上に、不可視のテキストレイヤー(検索・コピー用)を重ねる。

    ┌─────────────────────────────┐
    │ 【見た目のレイヤー】          │ ← 撮影画像(紙面そのまま)
    ├─────────────────────────────┤
    │ 【透明テキストレイヤー】      │ ← OCR結果を render_mode=3(不可視)で重ねる
    └─────────────────────────────┘

PyMuPDFのrender_mode=3は「描画しないが文字としては存在する」PDFのテキスト描画モードで、
Cmd+Fでの検索やコピーの対象になる。
"""
from __future__ import annotations

from pathlib import Path

from tscan.models import BlockKind, Book

# 日本語を埋め込めるフォントの候補(先に見つかったものを使う)
_CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",  # Linux (ipafont-gothic)
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def _find_cjk_font() -> str | None:
    for path in _CJK_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _text_of(block) -> str:
    if block.kind == BlockKind.MATH_BLOCK:
        return block.latex
    return block.text


def build_searchable_pdf(
    book: Book,
    page_images: dict[str, Path],
    out_path: Path,
    jpeg_quality: int = 85,
    max_long_side: int = 3000,
) -> Path:
    """本1冊を検索可能PDFにする。

    page_images: page_id -> 画像ファイルのパス(前処理済みPNG等)
    """
    try:
        import pymupdf
    except ImportError as e:  # pragma: no cover - 環境依存
        raise RuntimeError("PyMuPDFが必要です。`pip install pymupdf` を実行してください(§14.2)。") from e

    import cv2

    font_path = _find_cjk_font()
    doc = pymupdf.open()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    visible = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)

    for page in visible:
        image_path = page_images.get(page.page_id)
        if image_path is None or not Path(image_path).exists():
            continue

        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        src_h, src_w = image.shape[:2]

        # §13.1: JPEG品質85・長辺3000pxへダウンサンプルして容量を抑える
        scale = min(1.0, max_long_side / max(src_w, src_h))
        if scale < 1.0:
            image = cv2.resize(image, (int(src_w * scale), int(src_h * scale)), interpolation=cv2.INTER_AREA)
        img_h, img_w = image.shape[:2]

        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if not ok:
            continue

        pdf_page = doc.new_page(width=img_w, height=img_h)
        pdf_page.insert_image(pymupdf.Rect(0, 0, img_w, img_h), stream=buffer.tobytes())

        if font_path:
            pdf_page.insert_font(fontname="cjk", fontfile=font_path)

        # 透明テキストレイヤー(§13.1)
        for block in sorted(page.blocks, key=lambda b: b.reading_order):
            if block.kind in (BlockKind.FIGURE, BlockKind.RUBY):
                continue
            text = _text_of(block).strip()
            if not text:
                continue

            bx, by, _bw, bh = block.bbox
            # 前処理画像の座標をPDFのページ座標へスケールする
            x, y = bx * scale, by * scale
            h = max(bh * scale, 1)

            fontsize = max(min(h * 0.85, 72), 4)
            try:
                pdf_page.insert_text(
                    pymupdf.Point(x, y + h * 0.85),
                    text,
                    fontsize=fontsize,
                    fontname="cjk" if font_path else "helv",
                    render_mode=3,  # 3 = 不可視(検索・コピーは可能)
                )
            except Exception:
                # フォントに無い文字などで失敗しても、画像レイヤーは残す
                continue

    doc.set_metadata(
        {
            "title": book.title or book.book_id,
            "producer": "tscan (textbook-scan)",
            "subject": "所有者の私的使用目的で作成(§16.2 REQ-SEC-04)",
        }
    )
    doc.save(str(out_path), deflate=True)
    doc.close()
    return out_path


def build_image_pdf(page_image_paths: list[Path], out_path: Path) -> Path:
    """テキストレイヤーなしの、画像のみのPDFを作る(OCR未実行時のフォールバック)。"""
    try:
        import img2pdf
    except ImportError as e:  # pragma: no cover - 環境依存
        raise RuntimeError("img2pdfが必要です。`pip install img2pdf` を実行してください(§14.2)。") from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(img2pdf.convert([str(p) for p in page_image_paths]))
    return out_path
