"""パイプライン統合とOCRエンジンのテスト(仕様書 §8〜§11)。

実際のOCRを伴うテストは、tesseractが入っていない環境では自動的にスキップする。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tscan.models import Book, Page
from tscan.ocr.registry import build_math_engines, build_text_engines, describe_availability
from tscan.ocr.tesseract_engine import TesseractEngine
from tscan.ocr.base import OcrLine
from tscan.ocr.registry import EngineSet
from tscan.pipeline import PipelineContext, Stage, default_workers, ocr_page, run_book

TESSERACT = shutil.which("tesseract") is not None
requires_tesseract = pytest.mark.skipif(not TESSERACT, reason="tesseractが未インストール")


def _render_text_image(lines: list[str], width: int = 900, height: int = 500) -> np.ndarray:
    """日本語フォントでテキストを描画した画像を作る。フォントが無ければスキップ。"""
    from PIL import Image, ImageDraw, ImageFont

    candidates = [
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    font_path = next((p for p in candidates if Path(p).exists()), None)
    if font_path is None:
        pytest.skip("日本語フォントが見つかりません")

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 40)
    y = 40
    for line in lines:
        draw.text((40, y), line, font=font, fill=20)
        y += 70
    return np.array(image)


# --- エンジンレジストリ(§9.3) -----------------------------------------------


def test_describe_availability_lists_all_engines():
    status = describe_availability()
    assert set(status) == {"apple_vision", "yomitoku", "tesseract", "mathpix"}


def test_build_text_engines_reports_unavailable_reasons():
    engines = build_text_engines(vertical=False)
    # 使えないエンジンには必ず理由が付く(利用者が対処できるように)
    for name, reason in engines.unavailable.items():
        assert reason, f"{name} の理由が空です"


@requires_tesseract
def test_build_text_engines_finds_tesseract():
    engines = build_text_engines(vertical=False)
    assert engines.primary is not None
    assert engines.count >= 1


def test_build_math_engines_falls_back_without_mathpix(monkeypatch):
    monkeypatch.delenv("MATHPIX_APP_KEY", raising=False)
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)
    engines = build_math_engines()
    assert "mathpix" in engines.unavailable


def test_offline_mode_excludes_mathpix():
    engines = build_math_engines(offline=True)
    assert "mathpix" in engines.unavailable
    assert "--offline" in engines.unavailable["mathpix"]


# --- Tesseractエンジン(§9.2) -------------------------------------------------


@requires_tesseract
def test_tesseract_reads_japanese_text():
    image = _render_text_image(["磁場の中を運動する電荷", "力がはたらく"])
    engine = TesseractEngine()
    lines = engine.recognize(image, vertical=False)

    assert lines, "1行も認識されませんでした"
    joined = "".join(ln.text for ln in lines)
    assert "磁場" in joined
    assert all(0.0 <= ln.confidence <= 1.0 for ln in lines)
    assert all(len(ln.bbox) == 4 for ln in lines)


@requires_tesseract
def test_tesseract_digit_whitelist():
    image = _render_text_image(["87"], width=300, height=140)
    engine = TesseractEngine()
    text = engine.recognize_text(image, psm=7, whitelist="0123456789")
    assert "87" in text


# --- パイプライン ------------------------------------------------------------


def test_default_workers_is_sane():
    assert 1 <= default_workers() <= 64


def test_stage_ordering():
    assert Stage.VERIFY.at_or_after(Stage.PREPROCESS)
    assert not Stage.PREPROCESS.at_or_after(Stage.OCR)


@requires_tesseract
def test_run_book_end_to_end(tmp_path: Path):
    """取り込み済みの1ページを前処理→OCR→検証まで通す。"""
    image = _render_text_image(["磁場の中を運動する電荷には力がはたらく。"], width=1000, height=300)
    raw_dir = tmp_path / "work" / "raw"
    raw_dir.mkdir(parents=True)
    image_path = raw_dir / "t_p0001.png"
    cv2.imwrite(str(image_path), image)

    book = Book(book_id="t", title="テスト", pages=[Page.new(str(image_path), order_key=1000.0)])
    ctx = PipelineContext(book_id="t", book_root=tmp_path, offline=True)

    results, failures = run_book(book, ctx, from_stage=Stage.PREPROCESS, workers=1)

    assert failures == [], f"失敗: {failures}"
    assert len(results) == 1
    page = book.pages[0]
    assert page.blocks, "ブロックが抽出されませんでした"
    assert "磁場" in "".join(b.text for b in page.blocks)
    # 前処理済み画像が保存され、再開(--from ocr)に使えること(REQ-PRE-01)
    assert (ctx.stage_dir("preprocessed") / "t_p0001.png").exists()


@requires_tesseract
def test_run_book_survives_broken_page(tmp_path: Path):
    """1ページの失敗でバッチ全体を止めない(REQ-NFR-04)。"""
    raw_dir = tmp_path / "work" / "raw"
    raw_dir.mkdir(parents=True)

    good = raw_dir / "t_p0001.png"
    cv2.imwrite(str(good), _render_text_image(["運動方程式"], width=800, height=200))
    broken = raw_dir / "t_p0002.png"
    broken.write_bytes(b"this is not an image")

    book = Book(
        book_id="t",
        pages=[Page.new(str(good), order_key=1000.0), Page.new(str(broken), order_key=2000.0)],
    )
    ctx = PipelineContext(book_id="t", book_root=tmp_path, offline=True)
    results, failures = run_book(book, ctx, workers=1)

    assert len(results) == 2
    assert len(failures) == 1  # 壊れた1ページだけが失敗として報告される
    assert book.pages[0].blocks  # 正常なページは処理されている


# --- Apple Vision(§9.2) -----------------------------------------------------


def test_apple_vision_reports_why_it_is_unavailable():
    """macOS以外では、使えない理由を日本語で説明して例外を投げる(黙って落ちない)。"""
    import platform

    from tscan.ocr.apple_vision import AppleVisionEngine

    engine = AppleVisionEngine()
    if platform.system() == "Darwin":
        pytest.skip("macOS実機では利用可否が環境に依存するため、この確認は行わない")

    assert engine.is_available is False
    assert "macOS" in engine.unavailable_reason
    with pytest.raises(RuntimeError, match="Apple Vision"):
        engine.recognize(np.zeros((10, 10, 3), dtype=np.uint8))


def test_apple_vision_is_listed_in_engine_availability():
    """`tscan doctor` の一覧にApple Visionが出ること。"""
    from tscan.ocr.registry import describe_availability

    assert any("apple" in name.lower() for name in describe_availability())


# --- 副エンジンの信頼度によるアンサンブル除外(§11.2) -------------------------


class _FakeEngine:
    """テスト用の最小限のOCRエンジン。固定の行を返すだけ。"""

    def __init__(self, name: str, lines: list[OcrLine]):
        self.name = name
        self._lines = lines

    def recognize(self, image, vertical: bool = False) -> list[OcrLine]:
        return self._lines


_BBOX = (10, 10, 200, 30)


def test_ocr_page_ignores_low_confidence_alternate():
    """副エンジンが自信なく読んだ行は、主エンジンとの突き合わせに使わない。

    実写真(APS-Cミラーレス)で、Apple Vision(主)が信頼度0.9台で正しく読めているのに、
    Tesseract(副)が低解像度でほぼ読めず(自己申告の信頼度も低い)、その乱れた文字列との
    「不一致」だけを理由に大半のブロックが要確認になった(実測)。副エンジンが読めなかった
    ことは主エンジンを疑う根拠にならない、という判断をここで検証する。
    """
    primary = _FakeEngine(
        "primary", [OcrLine(text="正しい文章です", bbox=_BBOX, confidence=0.94, engine="primary", kind="text")]
    )
    garbled_secondary = _FakeEngine(
        "secondary",
        [OcrLine(text="乱れたXY12", bbox=_BBOX, confidence=0.10, engine="secondary", kind="text")],
    )
    engines = EngineSet(primary=primary, secondary=garbled_secondary)

    image = np.full((60, 260), 255, dtype=np.uint8)
    blocks, _vertical, alternates = ocr_page(image, "p0001", engines, EngineSet())

    assert len(blocks) == 1
    assert alternates[blocks[0].block_id] == []  # 低信頼度なので突き合わせから除外される


def test_ocr_page_keeps_confident_disagreeing_alternate():
    """副エンジンが自信を持って違う読みを出したときは、これまでどおり突き合わせに使う。"""
    primary = _FakeEngine(
        "primary", [OcrLine(text="正しい文章です", bbox=_BBOX, confidence=0.94, engine="primary", kind="text")]
    )
    confident_secondary = _FakeEngine(
        "secondary",
        [OcrLine(text="違う読み方です", bbox=_BBOX, confidence=0.80, engine="secondary", kind="text")],
    )
    engines = EngineSet(primary=primary, secondary=confident_secondary)

    image = np.full((60, 260), 255, dtype=np.uint8)
    blocks, _vertical, alternates = ocr_page(image, "p0001", engines, EngineSet())

    assert alternates[blocks[0].block_id] == ["違う読み方です"]
