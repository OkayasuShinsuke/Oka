"""CLI設計(仕様書 §14.4)。`tscan` コマンドのエントリポイント。"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2
import typer
from rich.console import Console
from rich.progress import track

from tscan.config import load_config
from tscan.ingest import detect_page_gaps, rename_by_shot_time
from tscan.models import Book, Page, compute_display_page_index, insert_between
from tscan.preprocess import (
    calibrate_session,
    check_aspect_ratio,
    denoise_and_sharpen,
    deskew,
    detect_with_hint,
    flatten_illumination,
    load_as_bgr,
    correct_perspective,
    save_stage,
)

app = typer.Typer(help="教科書スキャン・日本語/数式OCRシステム(docs/textbook-scan-spec.md 実装)")
pages_app = typer.Typer(help="ページ管理(§7.7)")
app.add_typer(pages_app, name="pages")
console = Console()


def _book_dir(book_id: str, output_dir: str | None) -> Path:
    config = load_config()
    root = Path(output_dir).expanduser() if output_dir else config.resolved_output_root()
    return root / book_id


def _load_book(book_id: str, output_dir: str | None) -> Book:
    book_json = _book_dir(book_id, output_dir) / "book.json"
    if not book_json.exists():
        console.print(f"[red]本 '{book_id}' が見つかりません: {book_json}[/red]")
        raise typer.Exit(code=1)
    return Book.load(book_json)


@app.command()
def ingest(
    src_dir: Path = typer.Argument(..., help="撮影済み写真のフォルダ"),
    book_id: str = typer.Option(..., "--book-id", help="§7.3の命名規則に従うID"),
    title: str = typer.Option("", "--title", help="本のタイトル"),
    output_dir: str | None = typer.Option(None, "--output-dir", help="§14.5 保存先。省略時は既定値"),
) -> None:
    """撮影済み写真をリネームして取り込む(REQ-IMPL-01)。"""
    book_root = _book_dir(book_id, output_dir)
    raw_dir = book_root / "work" / "raw"

    created = rename_by_shot_time(src_dir, book_id, raw_dir)
    if not created:
        console.print(f"[yellow]対応する画像が見つかりませんでした: {src_dir}[/yellow]")
        raise typer.Exit(code=1)

    order_key = 1000.0
    pages = []
    for path in created:
        pages.append(Page.new(image_path=str(path), order_key=order_key))
        order_key += 1000.0

    book = Book(
        book_id=book_id,
        title=title,
        created_at=datetime.now().isoformat(),
        pages=pages,
    )
    book_root.mkdir(parents=True, exist_ok=True)
    book.save(book_root / "book.json")

    console.print(f"[green]✓[/green] {len(created)}ページを取り込みました → {book_root}")


@app.command()
def calibrate(
    book_id: str = typer.Argument(...),
    frame: str = typer.Option("p0001", "--frame", help="校正に使うページ(例: p0001)"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """セッション校正(§8.3.1 REQ-PRE-03)。最初の1枚から4隅を検出する。"""
    book = _load_book(book_id, output_dir)
    target = next((p for p in book.pages if frame in p.image_path), None)
    if target is None:
        console.print(f"[red]ページが見つかりません: {frame}[/red]")
        raise typer.Exit(code=1)

    image = load_as_bgr(Path(target.image_path))
    corners = calibrate_session(image)
    if corners is None:
        console.print("[red]4隅を検出できませんでした。照明・背景マットを確認してください(§6.3)。[/red]")
        raise typer.Exit(code=1)

    calib_path = _book_dir(book_id, output_dir) / "work" / "calibration.txt"
    calib_path.parent.mkdir(parents=True, exist_ok=True)
    calib_path.write_text("\n".join(f"{x},{y}" for x, y in corners), encoding="utf-8")
    console.print(f"[green]✓[/green] 校正結果を保存しました: {calib_path}")
    console.print(f"検出した4隅: {corners.tolist()}")
    console.print("[yellow]ズレていれば§11.7の編集画面(未実装)で手動修正してから再実行してください。[/yellow]")


@app.command()
def run(
    book_id: str = typer.Argument(...),
    output_dir: str | None = typer.Option(None, "--output-dir"),
    expected_ratio: float = typer.Option(182 / 257, "--expected-ratio", help="§8.3.2 想定縦横比(既定B5)"),
) -> None:
    """前処理パイプライン(§8)を実行する。

    現時点で実装済み: P1(読込)・P3-P4(校正+台形補正)・P5(デスキュー)・
    P7(照明ムラ補正)・P8(ノイズ除去)・P10(保存)・§8.3.2(自動検査)。
    未実装(要macOS実機/APIキー): §9のOCR、§10のレイアウト解析、§11の検証。
    """
    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)

    calib_path = book_root / "work" / "calibration.txt"
    hint_corners = None
    if calib_path.exists():
        import numpy as np

        lines = calib_path.read_text(encoding="utf-8").splitlines()
        hint_corners = np.array([[float(v) for v in ln.split(",")] for ln in lines], dtype="float32")
    else:
        console.print("[yellow]セッション校正がまだ行われていません。`tscan calibrate` の実行を推奨します(§8.3.1)。[/yellow]")

    visible = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    display_index = compute_display_page_index(book.pages)

    failed: list[str] = []
    for page in track(visible, description="前処理中(P1-P10相当)"):
        try:
            image = load_as_bgr(Path(page.image_path))
            corners = detect_with_hint(image, hint_corners) if hint_corners is not None else None
            if corners is not None:
                warped = correct_perspective(image, corners)
                if not check_aspect_ratio(warped, expected_ratio):
                    page.warnings.append("ASPECT_RATIO_ANOMALY")  # §8.3.2 REQ-PRE-04
            else:
                warped = image

            gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
            gray = deskew(gray)
            gray = flatten_illumination(gray)
            gray = denoise_and_sharpen(gray)

            save_stage(gray, book_root / "work", book_id, display_index[page.page_id], "preprocessed")
        except Exception as e:  # noqa: BLE001 — 1ページの失敗で全体を止めない(REQ-NFR-04)
            failed.append(f"{Path(page.image_path).name}: {e}")

    book.save(book_root / "book.json")

    if failed:
        console.print(f"[yellow]前処理に失敗したページ: {len(failed)}件[/yellow]")
        for msg in failed:
            console.print(f"  - {msg}")
    console.print(f"[green]✓[/green] 前処理完了。OCR以降(§9-§11)は別途エンジン設定が必要です。")


@app.command()
def check(book_id: str = typer.Argument(...), output_dir: str | None = typer.Option(None, "--output-dir")) -> None:
    """ページ抜け・重複のチェックのみ(§7.5.1 REQ-PAGECHK-02)。"""
    book = _load_book(book_id, output_dir)
    visible = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    nombres = [p.printed_number for p in visible]

    if all(n is None for n in nombres):
        console.print(
            "[yellow]ノンブルが未設定です。ノンブルOCR(§9)が未実装のため、"
            "現時点ではPage.printed_numberを手動設定するか、実装後に再実行してください。[/yellow]"
        )
        return

    issues = detect_page_gaps(nombres)
    if not issues:
        console.print("[green]✓ ページ抜け・重複は検出されませんでした[/green]")
    else:
        console.print(f"[yellow]{len(issues)}件の警告があります:[/yellow]")
        for msg in issues:
            console.print(f"  - {msg}")


@app.command()
def doctor() -> None:
    """依存関係・APIキー・保存先の空き容量を診断する(§14.4)。"""
    import importlib
    import os
    import platform

    console.print("[bold]tscan doctor[/bold]")
    console.print(f"OS: {platform.system()} {platform.release()}")

    for mod in ["cv2", "numpy", "PIL", "pillow_heif", "rawpy", "sympy", "fastapi", "typer"]:
        try:
            importlib.import_module(mod)
            console.print(f"  [green]✓[/green] {mod}")
        except ImportError:
            console.print(f"  [red]✗[/red] {mod} 未インストール")

    console.print(f"  {'[green]✓[/green]' if platform.system() == 'Darwin' else '[yellow]-[/yellow]'} "
                  f"Apple Vision (macOS実機が必要, §9.2)")
    console.print(f"  {'[green]✓[/green]' if os.environ.get('MATHPIX_APP_KEY') else '[yellow]-[/yellow]'} "
                  f"MATHPIX_APP_KEY (§16.2 REQ-SEC-02)")

    config = load_config()
    root = config.resolved_output_root()
    usage = shutil.disk_usage(root if root.exists() else Path.home())
    free_gb = usage.free / (1024**3)
    console.print(f"  保存先 {root}: 空き容量 約{free_gb:.1f}GB (§16.1: 1冊あたり目安5.1GB)")


@app.command()
def review(book_id: str = typer.Argument(None)) -> None:
    """レビュー・編集UIを起動する(§11.7)。"""
    import uvicorn

    console.print("[bold]レビューUIを起動します[/bold] → http://localhost:8000")
    uvicorn.run("tscan.review.server:app", host="127.0.0.1", port=8000)


@app.command()
def export(
    book_id: str = typer.Argument(...),
    format: str = typer.Option("md,report", "--format", help="pdf,md,json,report をカンマ区切りで指定"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """検索可能PDF / Markdown+LaTeX / JSON / 品質レポートを出力する(§13)。"""
    from tscan.export.markdown import book_to_markdown
    from tscan.export.report import generate_quality_report

    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    out_dir = book_root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    formats = {f.strip() for f in format.split(",")}

    if "md" in formats:
        md_path = out_dir / f"{book_id}.md"
        md_path.write_text(book_to_markdown(book), encoding="utf-8")
        console.print(f"[green]✓[/green] Markdown出力: {md_path}")

    if "json" in formats:
        json_path = out_dir / f"{book_id}.json"
        book.save(json_path)
        console.print(f"[green]✓[/green] JSON出力: {json_path}")

    if "report" in formats:
        report_path = out_dir / f"{book_id}_report.html"
        report_path.write_text(generate_quality_report(book), encoding="utf-8")
        console.print(f"[green]✓[/green] 品質レポート出力: {report_path}")

    if "pdf" in formats:
        console.print(
            "[yellow]PDF出力にはOCR結果とテキストレイヤーの統合(§13.1)が必要です。"
            "現状は tscan.export.pdf.build_image_pdf() で画像のみのPDFが作成できます。[/yellow]"
        )


@pages_app.command("list")
def pages_list(book_id: str = typer.Argument(...), output_dir: str | None = typer.Option(None, "--output-dir")) -> None:
    """ページ一覧をターミナルで確認する(§7.7.3)。"""
    book = _load_book(book_id, output_dir)
    display_index = compute_display_page_index(book.pages)
    for page in sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key):
        status = "⚠ 要確認" if page.needs_review else "✅"
        console.print(f"  p{display_index[page.page_id]:04d}  {Path(page.image_path).name}  {status}")


@pages_app.command("insert")
def pages_insert(
    book_id: str = typer.Argument(...),
    after: int = typer.Option(..., "--after", help="このページ番号の後に挿入"),
    image: Path = typer.Option(..., "--image", help="挿入する画像ファイル"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """ページを挿入する(§7.7.3 REQ-PAGEMGMT-03)。既存ページのorder_keyは一切変更しない。"""
    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    display_index = compute_display_page_index(book.pages)
    index_to_id = {v: k for k, v in display_index.items()}

    if after not in index_to_id:
        console.print(f"[red]ページ番号{after}が見つかりません[/red]")
        raise typer.Exit(code=1)

    visible_sorted = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    before_page = next(p for p in visible_sorted if p.page_id == index_to_id[after])
    after_idx = visible_sorted.index(before_page)
    after_page = visible_sorted[after_idx + 1] if after_idx + 1 < len(visible_sorted) else None

    new_key = insert_between(before_page.order_key, after_page.order_key if after_page else before_page.order_key + 2000.0)

    dst_dir = book_root / "work" / "raw"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_path = dst_dir / f"{book_id}_inserted_{image.stem}{image.suffix}"
    dst_path.write_bytes(image.read_bytes())

    book.pages.append(Page.new(image_path=str(dst_path), order_key=new_key))
    book.save(book_root / "book.json")
    console.print(f"[green]✓[/green] p{after}の後に挿入しました(order_key={new_key})。このページのみ`tscan run`で再処理してください。")


@pages_app.command("delete")
def pages_delete(
    book_id: str = typer.Argument(...),
    page: int = typer.Option(..., "--page"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """ページを論理削除する(§7.7.3)。物理削除は行わない(REQ-PAGEMGMT-04)。"""
    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    display_index = compute_display_page_index(book.pages)
    index_to_id = {v: k for k, v in display_index.items()}

    if page not in index_to_id:
        console.print(f"[red]ページ番号{page}が見つかりません[/red]")
        raise typer.Exit(code=1)

    target = next(p for p in book.pages if p.page_id == index_to_id[page])
    target.deleted = True
    book.save(book_root / "book.json")
    console.print(f"[green]✓[/green] p{page}を削除しました(論理削除。復元は book.json を編集)")


@pages_app.command("move")
def pages_move(
    book_id: str = typer.Argument(...),
    page: int = typer.Option(..., "--page"),
    after: int = typer.Option(..., "--after"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """ページを並べ替える(§7.7.3)。移動対象のorder_keyのみ更新する。"""
    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    display_index = compute_display_page_index(book.pages)
    index_to_id = {v: k for k, v in display_index.items()}

    if page not in index_to_id or after not in index_to_id:
        console.print("[red]指定したページ番号が見つかりません[/red]")
        raise typer.Exit(code=1)

    visible_sorted = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    target = next(p for p in book.pages if p.page_id == index_to_id[page])
    before_page = next(p for p in visible_sorted if p.page_id == index_to_id[after])
    before_idx = visible_sorted.index(before_page)
    after_page = visible_sorted[before_idx + 1] if before_idx + 1 < len(visible_sorted) else None

    target.order_key = insert_between(
        before_page.order_key, after_page.order_key if after_page else before_page.order_key + 2000.0
    )
    book.save(book_root / "book.json")
    console.print(f"[green]✓[/green] p{page}をp{after}の後に移動しました(order_key={target.order_key})")


@app.command("config-set-output-dir")
def config_set_output_dir(path: str = typer.Argument(...)) -> None:
    """既定保存先を変更する(§14.5)。ユーザー設定(~/.config/tscan/)に保存し、
    リポジトリ同梱の config/default.yaml は変更しない。"""
    from tscan.config import USER_CONFIG_PATH, set_user_output_dir

    set_user_output_dir(path)
    console.print(f"[green]✓[/green] 既定保存先を変更しました: {path} ({USER_CONFIG_PATH}に保存)")


if __name__ == "__main__":
    app()
