"""OCRエンジンの抽象化インターフェース(仕様書 §9.6)。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class OcrLine:
    """OCR結果の1行分。全エンジンがこの形で返す。"""

    text: str  # 認識テキスト(本文なら日本語、数式ならLaTeX)
    bbox: tuple[int, int, int, int]  # 画像内の位置 (x, y, w, h)
    confidence: float  # 0.0〜1.0の自信度
    engine: str  # "apple_vision" / "yomitoku" / "mathpix" など
    kind: str  # "text" | "math" | "caption"


class OcrEngine(Protocol):
    """すべてのOCRエンジンが満たすべき『約束』。"""

    name: str

    def recognize(self, image: np.ndarray, vertical: bool = False) -> list[OcrLine]:
        ...
