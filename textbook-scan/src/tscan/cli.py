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
def doctor(
    test_ocr: bool = typer.Option(False, "--test-ocr", help="使えるエンジンで実際に日本語を読ませて結果を見せる"),
) -> None:
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

    if test_ocr:
        _run_ocr_selftest()


_SELFTEST_SENTENCE = "磁場の中を運動する電荷には力がはたらく。"


def _render_selftest_image():
    """自己診断用に、日本語フォントで1行だけ描いた画像を作る。フォントが無ければ None。"""
    from pathlib import Path as _Path

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    ]
    font_path = next((p for p in candidates if _Path(p).exists()), None)
    if font_path is None:
        return None
    image = Image.new("L", (1000, 140), 250)
    ImageDraw.Draw(image).text((30, 40), _SELFTEST_SENTENCE, font=ImageFont.truetype(font_path, 48), fill=20)
    return np.array(image)


def _run_ocr_selftest() -> None:
    """使えるエンジンすべてに同じ1行を読ませ、読めたかどうかを表示する(§9.2)。

    インストール直後に「エンジンは見えているが実際には読めない」状態を切り分けるためのもの。
    """
    from tscan.evaluate import cer
    from tscan.ocr.registry import build_text_engines

    console.print("\n[bold]OCRの実動作確認[/bold]")
    image = _render_selftest_image()
    if image is None:
        console.print("[yellow]日本語フォントが見つからないため実動作確認を省略しました[/yellow]")
        return

    console.print(f"読ませる文: [cyan]{_SELFTEST_SENTENCE}[/cyan]")
    engines = build_text_engines(vertical=False, offline=True).engines
    if not engines:
        console.print("[red]利用可能な本文OCRエンジンがありません[/red]")
        return

    table = Table("エンジン", "結果", "文字誤り率", title="同じ1行を各エンジンに読ませた結果")
    for engine in engines:
        try:
            text = "".join(line.text for line in engine.recognize(image, vertical=False))
        except Exception as exc:  # noqa: BLE001 — 何が起きたかをそのまま見せる
            table.add_row(engine.name, f"[red]失敗: {type(exc).__name__}: {exc}[/red]", "—")
            continue
        rate = cer(_SELFTEST_SENTENCE, text)
        color = "green" if rate <= 0.05 else ("yellow" if rate <= 0.30 else "red")
        table.add_row(engine.name, f"[{color}]{text or '(何も読めませんでした)'}[/{color}]", f"{rate * 100:.1f}%")
    console.print(table)
    console.print("  誤り率が [green]5%以下[/green] なら正常です。失敗と出た場合はそのメッセージが原因です。")
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


# ---------------------------------------------------------------------------
# 実写真ベンチマーク(自動改善ループの採点係, docs/improve-loop.md)
# ---------------------------------------------------------------------------


def _default_bench_dir() -> Path:
    import os

    return Path(os.environ.get("TSCAN_BENCH_DIR", str(Path.home() / "tscan_bench"))).expanduser()


@app.command()
def bench(
    manifest: Path = typer.Option(None, "--manifest", help="採点対象の manifest.json(既定: $TSCAN_BENCH_DIR/manifest.json)"),
    split: str = typer.Option("train", "--split", help="train | holdout | all"),
    out: Path = typer.Option(None, "--out", help="結果JSONの保存先(既定: <bench>/runs/<日時>_<split>.json)"),
    baseline: Path = typer.Option(None, "--baseline", help="比較する基準の結果JSON"),
    save_baseline: bool = typer.Option(False, "--save-baseline", help="今回の結果を <bench>/baseline_<split>.json に保存"),
    gate: str = typer.Option(
        None, "--gate", help="accept: 判定が accept 以外なら終了コード1 / no-worse: reject / incomparable のとき終了コード1"
    ),
    min_gain: float = typer.Option(0.5, "--min-gain", help="改善と認める最小幅(ポイント)"),
    max_set_loss: float = typer.Option(1.0, "--max-set-loss", help="セットごとの許容悪化(ポイント)"),
    offline: bool = typer.Option(False, "--offline", help="外部APIを使わない"),
    workers: int | None = typer.Option(None, "--workers"),
    keep_images: Path = typer.Option(None, "--keep-images", help="前処理後の画像をこのフォルダに残す(失敗分析用)"),
) -> None:
    """実写真で前処理+OCRを採点し、基準と比べて採否を判定する(自動改善ループ用)。"""
    import json as _json

    from tscan.bench import compare, load_manifest, run_bench

    bench_dir = _default_bench_dir()
    manifest = manifest or bench_dir / "manifest.json"
    if not manifest.exists():
        console.print(f"[red]manifest が見つかりません: {manifest}[/red](docs/improve-loop.md の準備手順を参照)")
        raise typer.Exit(code=2)
    bench_dir = manifest.parent
    if gate and gate not in ("accept", "no-worse"):
        console.print("[red]--gate は accept か no-worse を指定してください[/red]")
        raise typer.Exit(code=2)
    if gate and not baseline:
        console.print("[red]--gate には --baseline が必要です[/red]")
        raise typer.Exit(code=2)

    result = run_bench(load_manifest(manifest), split=split, offline=offline, workers=workers, work_dir=keep_images)

    out = out or bench_dir / "runs" / f"{datetime.now():%Y%m%d_%H%M%S}_{split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    table = Table("セット", "写真", "誤り率", title=f"ベンチマーク({split}, エンジン: {', '.join(result['meta']['engines'])})")
    for name, s in result["sets"].items():
        table.add_row(name, str(s["photos"]), f"{s['cer']:.2f}%")
    table.add_row("[bold]全体[/bold]", str(len(result["photos"])), f"[bold]{result['overall']['cer']:.2f}%[/bold]")
    console.print(table)
    worst = sorted(result["photos"], key=lambda p: -p["cer"])[:5]
    console.print("悪い順: " + " / ".join(f"{Path(p['file']).name} {p['cer']:.1f}%" for p in worst))
    console.print(f"結果: {out}({result['meta']['seconds']}秒)")

    if save_baseline:
        path = bench_dir / f"baseline_{split}.json"
        path.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]基準として保存しました: {path}[/green]")

    if baseline:
        verdict, lines = compare(
            result, _json.loads(Path(baseline).read_text(encoding="utf-8")), min_gain=min_gain, max_set_loss=max_set_loss
        )
        for line in lines:
            console.print(line)
        # エージェントが機械的に読み取れるよう、最後に1行で判定を出す
        print(f"VERDICT={verdict}")
        if (gate == "accept" and verdict != "accept") or (gate == "no-worse" and verdict in ("reject", "incomparable")):
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
