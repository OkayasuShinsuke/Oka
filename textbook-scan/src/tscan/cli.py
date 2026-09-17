"""CLI設計(仕様書 §14.4)。`tscan` コマンドのエントリポイント。"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from tscan.config import load_config
from tscan.ingest import detect_page_gaps, rename_by_shot_time
from tscan.models import Book, Page, compute_display_page_index, insert_between
from tscan.pipeline import PipelineContext, Stage, default_workers, expand_spreads, load_calibration, run_book

app = typer.Typer(help="教科書スキャン・日本語/数式OCRシステム(docs/textbook-scan-spec.md 実装)")
pages_app = typer.Typer(help="ページ管理(§7.7)")
app.add_typer(pages_app, name="pages")
console = Console()


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _book_dir(book_id: str, output_dir: str | None) -> Path:
    root = Path(output_dir).expanduser() if output_dir else load_config().resolved_output_root()
    return root / book_id


def _load_book(book_id: str, output_dir: str | None) -> Book:
    book_json = _book_dir(book_id, output_dir) / "book.json"
    if not book_json.exists():
        console.print(f"[red]本 '{book_id}' が見つかりません: {book_json}[/red]")
        raise typer.Exit(code=1)
    return Book.load(book_json)


def _resolve_page(book: Book, number: int) -> Page:
    pages = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    if not 1 <= number <= len(pages):
        console.print(f"[red]ページ番号{number}が見つかりません(1〜{len(pages)})[/red]")
        raise typer.Exit(code=1)
    return pages[number - 1]


# ---------------------------------------------------------------------------
# ① 取り込み
# ---------------------------------------------------------------------------


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

    pages = [Page.new(image_path=str(p), order_key=1000.0 * (i + 1)) for i, p in enumerate(created)]
    book = Book(book_id=book_id, title=title, created_at=datetime.now().isoformat(), pages=pages)
    book_root.mkdir(parents=True, exist_ok=True)
    book.save(book_root / "book.json")

    console.print(f"[green]✓[/green] {len(created)}ページを取り込みました → {book_root}")
    console.print("  次: [bold]tscan calibrate[/bold] で4隅を確認 → [bold]tscan run[/bold] で処理")


# ---------------------------------------------------------------------------
# ①' セッション校正(§8.3.1)
# ---------------------------------------------------------------------------


@app.command()
def calibrate(
    book_id: str = typer.Argument(...),
    frame: int = typer.Option(1, "--frame", help="校正に使うページ番号(既定: 1ページ目)"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """セッション校正(§8.3.1 REQ-PRE-03)。最初の1枚から4隅を検出する。"""
    from tscan.preprocess import calibrate_session, load_as_bgr

    book = _load_book(book_id, output_dir)
    page = _resolve_page(book, frame)

    image = load_as_bgr(Path(page.image_path))
    corners = calibrate_session(image)
    if corners is None:
        console.print("[red]4隅を検出できませんでした。照明・背景マットを確認してください(§6.3)。[/red]")
        raise typer.Exit(code=1)

    calib_path = _book_dir(book_id, output_dir) / "work" / "calibration.txt"
    calib_path.parent.mkdir(parents=True, exist_ok=True)
    calib_path.write_text("\n".join(f"{x},{y}" for x, y in corners), encoding="utf-8")

    console.print(f"[green]✓[/green] 校正結果を保存しました: {calib_path}")
    console.print(f"  検出した4隅: {[[round(v, 1) for v in c] for c in corners.tolist()]}")
    console.print("  [dim]ズレていればレビューUI(§11.7)で確認するか、このファイルを直接編集してください[/dim]")


# ---------------------------------------------------------------------------
# ② パイプライン実行
# ---------------------------------------------------------------------------


@app.command()
def run(
    book_id: str = typer.Argument(...),
    output_dir: str | None = typer.Option(None, "--output-dir"),
    from_stage: str = typer.Option("preprocess", "--from", help="再開する工程: preprocess | ocr | verify"),
    workers: int | None = typer.Option(None, "--workers", help="並列数(既定はCPUに応じて自動)"),
    subject: str = typer.Option("physics", "--subject", help="文脈辞書の分野: physics | math | kokugo(§11.6)"),
    expected_ratio: float = typer.Option(182 / 257, "--expected-ratio", help="§8.3.2 想定縦横比(既定B5)"),
    offline: bool = typer.Option(False, "--offline", help="外部APIを使わない(§16.2 REQ-SEC-03)"),
    enhance: bool = typer.Option(True, "--enhance/--no-enhance",
                                 help="ブック型スキャナ相当の補正(指の除去・湾曲補正・背景消去)"),
) -> None:
    """前処理→OCR→レイアウト解析→検証を実行する(§8〜§11)。"""
    try:
        stage = Stage(from_stage)
    except ValueError:
        console.print("[red]--from には preprocess / ocr / verify のいずれかを指定してください[/red]")
        raise typer.Exit(code=1)

    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    config = load_config()

    ctx = PipelineContext(
        book_id=book_id,
        book_root=book_root,
        expected_ratio=expected_ratio,
        aspect_tolerance=config.preprocess.aspect_ratio_tolerance,
        calibration=load_calibration(book_root),
        subject=subject,
        offline=offline,
        search_margin_px=config.preprocess.calibration_search_margin_px,
        enhance=enhance,
    )
    if ctx.calibration is None:
        console.print("[yellow]セッション校正が未実行です。`tscan calibrate` を推奨します(§8.3.1)。[/yellow]")

    n_workers = workers or default_workers()
    if stage == Stage.PREPROCESS and ctx.enhance and ctx.real_photo:
        # 見開き写真を左右2ページに展開してから枚数を数える(§8.4)
        added = expand_spreads(book, workers=n_workers)
        if added:
            console.print(f"見開き写真 {added}枚 を左右のページに分けました")
    visible = [p for p in book.pages if not p.deleted]
    console.print(f"[bold]{len(visible)}ページ[/bold] を処理します(並列数 {n_workers}、工程 {stage.value} から)")

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("処理中", total=len(visible))
        results, failures = run_book(
            book, ctx, from_stage=stage, workers=n_workers, progress=lambda: progress.advance(task)
        )

    book.save(book_root / "book.json")

    blocks = [b for p in book.pages for b in p.blocks]
    if blocks:
        pending = sum(1 for b in blocks if b.review_status == "pending")
        rate = pending / len(blocks) * 100
        console.print(f"[green]✓[/green] 完了: {len(blocks)}ブロック抽出 / 要確認 {pending}件 ({rate:.1f}%)")
    else:
        console.print("[yellow]ブロックが抽出されませんでした(画像・エンジン設定を確認してください)[/yellow]")
    if failures:
        console.print(f"[yellow]失敗したページ: {len(failures)}件[/yellow]")
        for msg in failures[:10]:
            console.print(f"  - {msg}")
    console.print("  次: [bold]tscan review[/bold] で確認 → [bold]tscan export[/bold] で出力")


# ---------------------------------------------------------------------------
# ページ抜けチェック(§7.5)
# ---------------------------------------------------------------------------


@app.command()
def check(book_id: str = typer.Argument(...), output_dir: str | None = typer.Option(None, "--output-dir")) -> None:
    """ページ抜け・重複のチェック(§7.5.1 REQ-PAGECHK-02: 機材を片付ける前に実行する)。"""
    book = _load_book(book_id, output_dir)
    visible = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    nombres = [p.printed_number for p in visible]

    if all(n is None for n in nombres):
        console.print("[yellow]ノンブルが1つも読めていません。`tscan run` を先に実行してください。[/yellow]")
        raise typer.Exit(code=1)

    issues = detect_page_gaps(nombres)
    if not issues:
        console.print(f"[green]✓ {len(visible)}ページ: ページ抜け・重複は検出されませんでした[/green]")
        return

    console.print(f"[yellow]{len(issues)}件の警告があります:[/yellow]")
    for msg in issues:
        console.print(f"  - {msg}")
    console.print("  [dim]機材がまだセットされているうちに撮り直すことを推奨します(§7.5.1)[/dim]")


# ---------------------------------------------------------------------------
# 診断
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """依存関係・OCRエンジン・保存先の空き容量を診断する(§14.4)。"""
    import importlib
    import platform

    from tscan.ocr.registry import describe_availability

    console.print("[bold]tscan doctor[/bold]")
    console.print(f"OS: {platform.system()} {platform.release()} / Python {platform.python_version()}")

    lib_table = Table("ライブラリ", "状態", title="依存ライブラリ(§14.2)")
    for mod in ["cv2", "numpy", "PIL", "pillow_heif", "rawpy", "sympy", "fastapi", "typer", "pymupdf", "pytesseract"]:
        try:
            importlib.import_module(mod)
            lib_table.add_row(mod, "[green]✓ 利用可能[/green]")
        except ImportError:
            lib_table.add_row(mod, "[yellow]— 未インストール[/yellow]")
    console.print(lib_table)

    engine_table = Table("OCRエンジン", "状態", title="OCRエンジン(§9.2)")
    for name, status in describe_availability().items():
        mark = "[green]✓[/green]" if status == "利用可能" else "[yellow]—[/yellow]"
        engine_table.add_row(name, f"{mark} {status}")
    console.print(engine_table)

    config = load_config()
    root = config.resolved_output_root()
    usage = shutil.disk_usage(root if root.exists() else Path.home())
    free_gb = usage.free / (1024**3)
    verdict = "[green]十分[/green]" if free_gb > 10 else "[yellow]不足の可能性[/yellow]"
    console.print(f"保存先 {root}: 空き容量 {free_gb:.1f}GB {verdict}(§16.1: 1冊あたり目安5.1GB)")
    console.print(f"既定の並列数: {default_workers()}(§15.1)")


# ---------------------------------------------------------------------------
# レビューUI / 出力
# ---------------------------------------------------------------------------


@app.command()
def review(
    book_id: str = typer.Argument(None, help="省略可(ホーム画面から選べます)"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """レビュー・編集UIを起動する(§11.7)。"""
    import uvicorn

    target = f"/book/{book_id}" if book_id else "/"
    console.print(f"[bold]レビューUIを起動します[/bold] → http://127.0.0.1:{port}{target}")
    uvicorn.run("tscan.review.server:app", host="127.0.0.1", port=port, log_level="warning")


@app.command()
def export(
    book_id: str = typer.Argument(...),
    format: str = typer.Option("md,json,report,pdf", "--format", help="pdf,md,json,report をカンマ区切りで"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """検索可能PDF / Markdown+LaTeX / JSON / 品質レポートを出力する(§13)。"""
    from tscan.export.markdown import book_to_markdown
    from tscan.export.pdf import build_searchable_pdf
    from tscan.export.report import generate_quality_report

    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    out_dir = book_root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = {f.strip() for f in format.split(",")}

    if "md" in formats:
        path = out_dir / f"{book_id}.md"
        path.write_text(book_to_markdown(book), encoding="utf-8")
        console.print(f"[green]✓[/green] Markdown: {path}")

    if "json" in formats:
        path = out_dir / f"{book_id}.json"
        book.save(path)
        console.print(f"[green]✓[/green] JSON: {path}")

    if "report" in formats:
        path = out_dir / f"{book_id}_report.html"
        path.write_text(generate_quality_report(book), encoding="utf-8")
        console.print(f"[green]✓[/green] 品質レポート: {path}")

    if "pdf" in formats:
        stage_dir = book_root / "work" / "stages" / "preprocessed"
        display = compute_display_page_index(book.pages)
        images = {}
        for page in book.pages:
            if page.deleted:
                continue
            candidate = stage_dir / f"{book_id}_p{display[page.page_id]:04d}.png"
            images[page.page_id] = candidate if candidate.exists() else Path(page.image_path)

        path = build_searchable_pdf(book, images, out_dir / f"{book_id}.pdf")
        size_mb = path.stat().st_size / (1024**2)
        console.print(f"[green]✓[/green] 検索可能PDF: {path} ({size_mb:.1f}MB)")


# ---------------------------------------------------------------------------
# ページ管理(§7.7)
# ---------------------------------------------------------------------------


@pages_app.command("list")
def pages_list(book_id: str = typer.Argument(...), output_dir: str | None = typer.Option(None, "--output-dir")) -> None:
    """ページ一覧を表示する(§7.7.3)。"""
    book = _load_book(book_id, output_dir)
    display = compute_display_page_index(book.pages)

    table = Table("#", "ファイル", "ノンブル", "レイアウト", "ブロック", "状態")
    for page in sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key):
        pending = sum(1 for b in page.blocks if b.review_status == "pending")
        status = f"[yellow]⚠ 要確認 {pending}[/yellow]" if pending else ("[green]✅[/green]" if page.blocks else "[dim]🔲 未処理[/dim]")
        table.add_row(
            f"{display[page.page_id]:04d}",
            Path(page.image_path).name,
            str(page.printed_number or "—"),
            "縦書き" if page.layout == "vertical" else "横書き",
            str(len(page.blocks)),
            status,
        )
    console.print(table)


@pages_app.command("insert")
def pages_insert(
    book_id: str = typer.Argument(...),
    after: int = typer.Option(..., "--after", help="このページ番号の後に挿入"),
    image: Path = typer.Option(..., "--image", help="挿入する画像ファイル"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """ページを挿入する(REQ-PAGEMGMT-03)。既存ページのorder_keyは変更しない。"""
    book = _load_book(book_id, output_dir)
    book_root = _book_dir(book_id, output_dir)
    pages = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    before = _resolve_page(book, after)
    idx = pages.index(before)
    following = pages[idx + 1] if idx + 1 < len(pages) else None

    new_key = insert_between(before.order_key, following.order_key if following else before.order_key + 2000.0)

    dst_dir = book_root / "work" / "raw"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{book_id}_inserted_{image.stem}{image.suffix}"
    dst.write_bytes(image.read_bytes())

    book.pages.append(Page.new(image_path=str(dst), order_key=new_key))
    book.save(book_root / "book.json")
    console.print(f"[green]✓[/green] p{after}の後に挿入(order_key={new_key})。`tscan run --from preprocess`で該当ページのみ処理されます。")


@pages_app.command("delete")
def pages_delete(
    book_id: str = typer.Argument(...),
    page: int = typer.Option(..., "--page"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """ページを論理削除する(REQ-PAGEMGMT-04)。"""
    book = _load_book(book_id, output_dir)
    target = _resolve_page(book, page)
    target.deleted = True
    book.save(_book_dir(book_id, output_dir) / "book.json")
    console.print(f"[green]✓[/green] p{page}を削除しました(論理削除)")


@pages_app.command("move")
def pages_move(
    book_id: str = typer.Argument(...),
    page: int = typer.Option(..., "--page"),
    after: int = typer.Option(..., "--after"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """ページを並べ替える(REQ-PAGEMGMT-03)。移動対象のorder_keyのみ更新する。"""
    book = _load_book(book_id, output_dir)
    pages = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    target = _resolve_page(book, page)
    before = _resolve_page(book, after)
    idx = pages.index(before)
    following = pages[idx + 1] if idx + 1 < len(pages) else None

    target.order_key = insert_between(before.order_key, following.order_key if following else before.order_key + 2000.0)
    book.save(_book_dir(book_id, output_dir) / "book.json")
    console.print(f"[green]✓[/green] p{page}をp{after}の後に移動(order_key={target.order_key})")


@app.command()
def evaluate(
    book_id: str = typer.Argument(...),
    ground_truth: Path = typer.Option(..., "--ground-truth", help="ページ番号->正解テキストのJSON"),
    math_ground_truth: Path | None = typer.Option(
        None, "--math-ground-truth", help="ページ番号->数式行リストのJSON(§11.8の数式行正解率用)"
    ),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """正解データと突き合わせて精度を測る(§11.8 / §18)。"""
    import json

    from tscan.evaluate import evaluate_book

    book = _load_book(book_id, output_dir)
    raw = json.loads(ground_truth.read_text(encoding="utf-8"))
    references = {int(k): v for k, v in raw.items()}

    math_references = None
    if math_ground_truth and math_ground_truth.exists():
        raw_math = json.loads(math_ground_truth.read_text(encoding="utf-8"))
        math_references = {int(k): v for k, v in raw_math.items()}

    result = evaluate_book(book, references, math_references)

    table = Table("指標", "実測値", "判定", title=f"{book_id} 精度評価(§11.8)")
    for label, (passed, value) in result.verdict().items():
        table.add_row(label, value, "[green]✓ 合格[/green]" if passed else "[red]✗ 未達[/red]")
    console.print(table)
    console.print(
        f"正解 {result.reference_chars}文字 / 認識 {result.hypothesis_chars}文字 / "
        f"{result.blocks}ブロック"
    )


@app.command("config-set-output-dir")
def config_set_output_dir(path: str = typer.Argument(...)) -> None:
    """既定保存先を変更する(§14.5)。ユーザー設定に保存し、リポジトリ同梱の設定は変更しない。"""
    from tscan.config import USER_CONFIG_PATH, set_user_output_dir

    set_user_output_dir(path)
    console.print(f"[green]✓[/green] 既定保存先を変更: {path} ({USER_CONFIG_PATH})")


if __name__ == "__main__":
    app()
