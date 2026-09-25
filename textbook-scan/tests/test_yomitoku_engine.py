"""yomitokuエンジンの実モデル結合テスト(§9.2)。

モックではなく実際にyomitokuのモデルを読み込み、縦書き日本語の合成画像を
認識させて動作確認する。初回実行時はモデルのダウンロードが発生するため、
実行に数十秒〜数分かかることがある。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

yomitoku = pytest.importorskip("yomitoku")
torch = pytest.importorskip("torch")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from tscan.evaluate import cer  # noqa: E402
from tscan.ocr.yomitoku_engine import YomitokuEngine  # noqa: E402

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

SAMPLE_TEXT = "吾輩は猫である。名前はまだ無い。"


def _find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _render_vertical_text(text: str, font_size: int = 48) -> Image.Image:
    """縦書き日本語テキストを1文字ずつ縦に並べて描画する。"""
    font_path = _find_font()
    if font_path is None:
        pytest.skip("日本語フォントが見つかりません")
    font = ImageFont.truetype(font_path, font_size)

    margin = font_size
    width = font_size * 2 + margin * 2
    height = font_size * len(text) + margin * 2

    image = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(image)
    x = margin
    for i, ch in enumerate(text):
        y = margin + i * font_size
        draw.text((x, y), ch, font=font, fill="black")
    return image


def test_yomitoku_recognizes_vertical_text() -> None:
    engine = YomitokuEngine()
    if not engine._available:
        pytest.skip("yomitokuが利用できません")

    pil_image = _render_vertical_text(SAMPLE_TEXT)
    image = np.array(pil_image)[:, :, ::-1]  # RGB -> BGR

    lines = engine.recognize(image, vertical=True)

    assert len(lines) > 0

    recognized = "".join(line.text for line in lines)
    cer_value = cer(SAMPLE_TEXT, recognized)
    assert cer_value < 0.5, f"CERが高すぎます: {cer_value} (認識結果: {recognized!r})"
