"""パイプライン統合(仕様書 §5.1 の全体図、§8〜§11 の接続)。

前処理(§8) → レイアウト解析(§10) → OCR(§9) → 検証(§11) を1本につなぐ。

設計上の要点:
    - 工程ごとに中間成果物を保存し、任意の工程から再開できる(REQ-PRE-01)
    - 1ページの失敗でバッチ全体を止めない(REQ-NFR-04)
    - ページ単位で並列化する(REQ-PERF-02 / REQ-PERF-04)
"""
from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from tscan import enhance, layout, preprocess, realphoto
from tscan.models import Block, BlockKind, Book, Page, insert_between
from tscan.ocr.registry import EngineSet, build_math_engines, build_text_engines
from tscan.verify import VerifyThresholds, load_lexicon, verify_block


class Stage(str, Enum):
    """パイプラインの工程。`--from` で再開地点として指定できる。"""

    PREPROCESS = "preprocess"
    OCR = "ocr"
    VERIFY = "verify"

    @classmethod
    def order(cls) -> list["Stage"]:
        return [cls.PREPROCESS, cls.OCR, cls.VERIFY]

    def at_or_after(self, other: "Stage") -> bool:
        order = Stage.order()
        return order.index(self) >= order.index(other)


@dataclass
class PipelineContext:
    """1回の実行を通して共有する設定。"""

    book_id: str
    book_root: Path
    expected_ratio: float = 182 / 257  # B5(§8.3.2)
    aspect_tolerance: float = 0.05
    calibration: np.ndarray | None = None
    subject: str = "physics"  # 文脈辞書の分野(§11.6)
    offline: bool = False
    thresholds: VerifyThresholds = field(default_factory=VerifyThresholds)
    search_margin_px: int = 40
    # ブック型スキャナ相当の補正(§8 拡張: 指の除去・湾曲補正・背景消去)
    enhance: bool = True
    curl_threshold: float = 0.0
    # 実写真モード: 背景の色に依存しない紙面検出と見開きの自動分割(realphoto)。
    # 検出できなかった写真は従来の経路(enhance + 4隅検出)に落ちる
    real_photo: bool = True

    @property
    def work_dir(self) -> Path:
        return self.book_root / "work"

    def stage_dir(self, stage: str) -> Path:
        return self.work_dir / "stages" / stage


# 前処理が付ける警告。前処理をやり直すときに古いものを消すための接頭辞一覧
_PREPROCESS_WARNINGS = (
    "ASPECT_RATIO_ANOMALY", "PAGE_CORNERS_NOT_FOUND", "DEWARPED", "DEWARP_SKIPPED", "DEWARP_REJECTED", "FINGERS_REMOVED",
    "DARK_BOXES_INVERTED", "ROTATED",
)


@dataclass
class PageResult:
    page: Page
    error: str | None = None
    preprocessed_path: str | None = None


# ---------------------------------------------------------------------------
# 前処理(§8)
# ---------------------------------------------------------------------------


def preprocess_page(page: Page, ctx: PipelineContext, page_index: int) -> tuple[np.ndarray, list[str]]:
    """1ページ分の前処理(P1〜P10)を実行し、(グレースケール画像, 警告) を返す。"""
    warnings: list[str] = []

    image = preprocess.load_as_bgr(Path(page.image_path))  # P1

    # P2: 実写真モード(手持ち・見開き・雑多な背景)。紙面を「明るくて彩度が低い領域」と
    # 「文字行の密度」で捉え、見開きなら指定された側だけを切り出す。
    # 紙面の外(机・手)は紙の地色で塗られ、湾曲補正には紙面マスクをそのまま渡す。
    extracted = None
    if ctx.enhance and ctx.real_photo:
        if page.rotation:
            image = realphoto.rotate_upright(image, page.rotation)
            warnings.append(f"ROTATED:{page.rotation}")
        extracted = realphoto.extract_page(image, page.spread_side)

    dewarped = False
    if extracted is not None:
        crop, mask = extracted
        result = enhance.dewarp_page(crop, mask=mask) if ctx.enhance else None
        if result is None:
            warped = crop
            warnings.append("DEWARP_SKIPPED")
        elif enhance.text_line_sharpness(result) < enhance.text_line_sharpness(crop):
            # 補正で文字行がかえって崩れた(縁の曲線が本文の湾曲を表していない)→ 採用しない
            warped = crop
            warnings.append("DEWARP_REJECTED")
        else:
            warped = result
            dewarped = True
            warnings.append("DEWARPED:real")
        # 切り出しは本文範囲を基準にしているので、物理的なページの縦横比は検査しない
    else:
        # P2.5: ブック型スキャナ相当の補正(指の除去・湾曲補正・背景消去)
        # 指はページの輪郭を隠すため、4隅検出より先に消す必要がある。
        if ctx.enhance:
            image, info = enhance.enhance_book_photo(
                image, do_dewarp=True, do_finger_removal=True, do_background=False,
                curl_threshold=ctx.curl_threshold,
            )
            dewarped = bool(info["dewarped"])
            if info["fingers_removed"]:
                warnings.append(f"FINGERS_REMOVED:{info['fingers_removed']}")
            if dewarped:
                warnings.append(f"DEWARPED:{info['curl']}")

        if dewarped:
            # 湾曲補正は紙面の切り出しと矩形化まで済ませているため、台形補正は行わない
            warped = image
            if not preprocess.check_aspect_ratio(warped, ctx.expected_ratio, ctx.aspect_tolerance):
                warnings.append("ASPECT_RATIO_ANOMALY")
        else:
            # P3-P4: セッション校正のヒントを使った4隅検出 + 台形補正(§8.3.1)
            if ctx.calibration is not None:
                corners = preprocess.detect_with_hint(image, ctx.calibration, ctx.search_margin_px)
            else:
                corners = preprocess.detect_page_corners(image)

            if corners is not None:
                warped = preprocess.correct_perspective(image, corners)
                # §8.3.2 REQ-PRE-04: 補正結果の自動検査
                if not preprocess.check_aspect_ratio(warped, ctx.expected_ratio, ctx.aspect_tolerance):
                    warnings.append("ASPECT_RATIO_ANOMALY")
            else:
                warped = image
                warnings.append("PAGE_CORNERS_NOT_FOUND")

    if ctx.enhance:
        warped = enhance.clean_background(warped)  # 紙面の外に残った机・マットを白で消す

    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    gray = preprocess.deskew(gray)  # P5
    gray, boxes = preprocess.invert_dark_boxes(gray)  # P6.5: 白抜きの公式ボックスを黒文字に
    if boxes:
        warnings.append(f"DARK_BOXES_INVERTED:{boxes}")
    gray = preprocess.flatten_illumination(gray)  # P7
    gray = preprocess.denoise_and_sharpen(gray)  # P8
    return gray, warnings


# ---------------------------------------------------------------------------
# OCR(§9) + レイアウト解析(§10)
# ---------------------------------------------------------------------------


def _recognize_with(engine, image: np.ndarray, vertical: bool) -> list:
    try:
        return engine.recognize(image, vertical=vertical)
    except Exception:
        return []


def ocr_page(
    gray: np.ndarray,
    page_prefix: str,
    text_engines: EngineSet,
    math_engines: EngineSet,
) -> tuple[list[Block], bool, dict[str, list[str]]]:
    """OCRとレイアウト解析を行い、(blocks, vertical, ブロックID->他エンジンの読み) を返す。"""
    if text_engines.primary is None:
        raise RuntimeError(
            "利用可能な本文OCRエンジンがありません。`tscan doctor` で状況を確認してください。\n"
            f"未使用の理由: {text_engines.unavailable}"
        )

    # 縦横の判定は主エンジンの横書き結果の形状から行う(§10.3 REQ-LAYOUT-03)
    probe_lines = _recognize_with(text_engines.primary, gray, vertical=False)
    vertical = layout.is_vertical_layout(probe_lines)

    primary_lines = probe_lines if not vertical else _recognize_with(text_engines.primary, gray, vertical=True)
    blocks, vertical, _ruby_map = layout.lines_to_blocks(
        primary_lines, page_size=(gray.shape[1], gray.shape[0]), page_prefix=page_prefix, vertical=vertical
    )

    # 副・第3エンジンの結果を、重なりの大きいブロックへ突き合わせ用に割り当てる(§11.2)。
    #
    # 副エンジン自身の信頼度が低い行は突き合わせに使わない。実写真(APS-Cミラーレス)で
    # Apple Vision(主)が信頼度0.9台で正しく読めているのに、Tesseract(副)がその写真では
    # 低解像度・照明ムラでほぼ読めておらず(自己申告の信頼度も低い)、その乱れた文字列との
    # 「不一致」だけを理由に91%のブロックが要確認になった(実測)。
    # 副エンジンが「読めなかった」ことは、主エンジンを疑う根拠にはならない。
    # 逆に、副エンジンが高い信頼度で違う読みを出したときは、これまでどおり不一致として扱う
    # (両エンジンとも自信を持って食い違っている = 本当に疑わしい)。
    alternates: dict[str, list[str]] = {b.block_id: [] for b in blocks}
    for engine in text_engines.engines[1:]:
        other_lines = _recognize_with(engine, gray, vertical=vertical)
        for block in blocks:
            match = _best_overlap(block.bbox, other_lines)
            if match is not None and match.confidence >= MIN_ALTERNATE_CONFIDENCE:
                alternates[block.block_id].append(match.text)

    # 数式ブロックは数式特化エンジンで読み直す(§9.3)
    if math_engines.primary is not None and getattr(math_engines.primary, "name", "") == "mathpix":
        for block in blocks:
            if block.kind != BlockKind.MATH_BLOCK:
                continue
            x, y, w, h = block.bbox
            crop = gray[max(y, 0) : y + h, max(x, 0) : x + w]
            if crop.size == 0:
                continue
            try:
                results = math_engines.primary.recognize(crop)
            except Exception:
                continue
            if results:
                # Mathpixの結果を採用し、元のTesseract読みは照合用に回す(§11.2)
                alternates[block.block_id].append(block.latex)
                block.latex = results[0].text
                block.confidence = max(results[0].confidence, block.confidence)

    return blocks, vertical, alternates


# この信頼度未満の副エンジンの読みは、突き合わせの対象から外す(上記コメント参照)。
# Tesseractの単語信頼度・Apple Visionの行信頼度とも0〜1のスケールなので共通に使える。
MIN_ALTERNATE_CONFIDENCE = 0.35


def _best_overlap(bbox: tuple[int, int, int, int], lines: list):
    """bboxと最も重なる行を返す。重なりがなければNone。"""
    x, y, w, h = bbox
    best = None
    best_area = 0
    for line in lines:
        lx, ly, lw, lh = line.bbox
        ix = max(0, min(x + w, lx + lw) - max(x, lx))
        iy = max(0, min(y + h, ly + lh) - max(y, ly))
        area = ix * iy
        if area > best_area:
            best, best_area = line, area
    # 対象ブロック面積の30%以上重なっていなければ「対応する行なし」とみなす
    return best if best_area > w * h * 0.3 else None


# ---------------------------------------------------------------------------
# 1ページ分の全工程
# ---------------------------------------------------------------------------


def process_page(
    page: Page,
    ctx: PipelineContext,
    page_index: int,
    from_stage: Stage,
    text_engines: EngineSet,
    math_engines: EngineSet,
    lexicon: tuple[str, ...],
) -> PageResult:
    """1ページを前処理からOCR・検証まで通す。例外は握って PageResult.error に入れる。"""
    try:
        page_prefix = f"p{page_index:04d}"
        preprocessed_path = ctx.stage_dir("preprocessed") / f"{ctx.book_id}_{page_prefix}.png"

        # --- 前処理 -------------------------------------------------------
        if from_stage == Stage.PREPROCESS or not preprocessed_path.exists():
            gray, warnings = preprocess_page(page, ctx, page_index)
            preprocessed_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(preprocessed_path), gray)
            page.warnings = [w for w in page.warnings if not w.startswith(_PREPROCESS_WARNINGS)]
            page.warnings.extend(warnings)
        else:
            gray = cv2.imread(str(preprocessed_path), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise RuntimeError(f"前処理済み画像を読めません: {preprocessed_path}")

        result = PageResult(page=page, preprocessed_path=str(preprocessed_path))

        # 数式専用エンジン(Mathpix等)が使えているか。使えていなければREQ-OCR-02の減点対象
        math_specialist = getattr(math_engines.primary, "name", "") in {"mathpix", "texify"}

        if from_stage == Stage.VERIFY and page.blocks:
            # OCRをやり直さず、保存済みブロックに検証だけ掛け直す
            for block in page.blocks:
                verify_block(
                    block,
                    engine_texts=None,
                    lexicon=lexicon,
                    thresholds=ctx.thresholds,
                    math_engine_is_specialist=math_specialist,
                )
            return result

        # --- OCR + レイアウト ---------------------------------------------
        blocks, vertical, alternates = ocr_page(gray, page_prefix, text_engines, math_engines)
        page.blocks = blocks
        page.layout = "vertical" if vertical else "horizontal"

        # --- ノンブル(§7.5) ------------------------------------------------
        if text_engines.primary is not None and hasattr(text_engines.primary, "recognize_text"):
            page.printed_number = layout.extract_nombre(gray, text_engines.primary)

        # --- 検証(§11) ----------------------------------------------------
        for block in page.blocks:
            verify_block(
                block,
                engine_texts=alternates.get(block.block_id),
                lexicon=lexicon,
                thresholds=ctx.thresholds,
                math_engine_is_specialist=math_specialist,
            )

        return result
    except Exception as e:  # noqa: BLE001 — REQ-NFR-04: 1ページの失敗で全体を止めない
        return PageResult(page=page, error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")


# ---------------------------------------------------------------------------
# 本1冊分の実行
# ---------------------------------------------------------------------------


def default_workers() -> int:
    """既定のワーカー数(§15.1 / REQ-PERF-04)。

    重い処理はOpenCV(C実装)とtesseract(別プロセス)にあり、どちらもGILを手放すため、
    スレッド並列で実効的な並列度が得られる。プロセス並列と違いエンジンの再初期化や
    ピクル化が不要で、48MP画像をプロセス間でコピーする無駄も生じない。
    """
    cpu = os.cpu_count() or 4
    return max(1, min(cpu - 1, cpu // 2 + 1))


def expand_spreads(book: Book, workers: int = 1) -> int:
    """見開き写真を左右2つの Page に分ける(§8.4 見開き分割)。追加したページ数を返す。

    未判定(spread_side=None)の写真だけを調べる。見開きなら元の Page を左ページとし、
    同じ写真を指す右ページを直後に挿入する(order_key は間の値。他のページは触らない)。
    1ページ写真は "single" と記録し、次回以降は調べない。
    """
    targets = [p for p in book.pages if not p.deleted and p.spread_side is None]
    if not targets:
        return 0

    def _kind(page: Page) -> str | None:
        try:
            image = preprocess.load_as_bgr(Path(page.image_path))
            page.rotation = realphoto.detect_rotation(image)  # 本を横向きに構えた写真を正立させる
            return realphoto.detect_spread(realphoto.rotate_upright(image, page.rotation))
        except Exception:  # noqa: BLE001 — 読めない写真は後段の処理で失敗として報告される
            return None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        kinds = list(pool.map(_kind, targets))

    added = 0
    ordered = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    for page, kind in zip(targets, kinds):
        if kind == "spread":
            page.spread_side = "left"
            pos = ordered.index(page)
            next_key = ordered[pos + 1].order_key if pos + 1 < len(ordered) else page.order_key + 1000.0
            right = Page.new(page.image_path, insert_between(page.order_key, next_key))
            right.spread_side = "right"
            right.rotation = page.rotation
            book.pages.append(right)
            ordered.insert(pos + 1, right)
            added += 1
        elif kind == "single":
            page.spread_side = "single"
        # None: 紙面を検出できない写真。従来経路で処理するため未判定のまま残す
    return added


def run_book(
    book: Book,
    ctx: PipelineContext,
    from_stage: Stage = Stage.PREPROCESS,
    workers: int | None = None,
    progress=None,
) -> tuple[list[PageResult], list[str]]:
    """本1冊を処理する。(結果, 失敗メッセージ) を返す。"""
    text_engines_h = build_text_engines(vertical=False, offline=ctx.offline)
    text_engines_v = build_text_engines(vertical=True, offline=ctx.offline)
    math_engines = build_math_engines(offline=ctx.offline)
    lexicon = load_lexicon(ctx.subject)

    workers = workers or default_workers()
    if from_stage == Stage.PREPROCESS and ctx.enhance and ctx.real_photo:
        expand_spreads(book, workers=workers)

    visible = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    indexed = list(enumerate(visible, start=1))

    results: list[PageResult] = []
    failures: list[str] = []

    def _task(item):
        index, page = item
        # 縦書きかどうかはページごとに判定するため、まず横書き構成で走らせる
        return process_page(page, ctx, index, from_stage, text_engines_h, math_engines, lexicon)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_task, item): item for item in indexed}
        for future in as_completed(futures):
            index, page = futures[future]
            result = future.result()
            results.append(result)
            if result.error:
                failures.append(f"p{index:04d} ({Path(page.image_path).name}): {result.error.splitlines()[0]}")
            if progress is not None:
                progress()

    # 縦書きと判定されたページは、縦書き用エンジン構成で読み直す(§9.3 / REQ-OCR-04)
    vertical_pages = [(i, p) for i, p in indexed if p.layout == "vertical" and text_engines_v.primary is not None]
    if vertical_pages and text_engines_v.primary is not text_engines_h.primary:
        for index, page in vertical_pages:
            redo = process_page(page, ctx, index, Stage.OCR, text_engines_v, math_engines, lexicon)
            if redo.error:
                failures.append(f"p{index:04d} (縦書き再処理): {redo.error.splitlines()[0]}")

    return results, failures


def load_calibration(book_root: Path) -> np.ndarray | None:
    """`tscan calibrate` が保存した4隅を読み込む(§8.3.1)。"""
    path = book_root / "work" / "calibration.txt"
    if not path.exists():
        return None
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 4:
        return None
    return np.array([[float(v) for v in ln.split(",")] for ln in lines], dtype=np.float32)
