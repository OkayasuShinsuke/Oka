"""yomitoku連携(仕様書 §9.2)。日本語文書特化OCR。縦書き・レイアウトに強い。

`pip install tscan[yomitoku]` が必要(torch等の重い依存を含むため任意インストール)。
"""
from __future__ import annotations

import numpy as np

from tscan.ocr.base import OcrLine


class YomitokuEngine:
    name = "yomitoku"

    def __init__(self) -> None:
        try:
            import yomitoku  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False

    def recognize(self, image: np.ndarray, vertical: bool = False) -> list[OcrLine]:
        if not self._available:
            raise RuntimeError(
                "yomitokuがインストールされていません。`pip install yomitoku` を実行してください(§9.2)。"
            )
        raise NotImplementedError(
            "yomitokuのAPI呼び出しは、実際のモデルダウンロード・実画像での検証が必要です。"
            "docs/textbook-scan-spec.md §9.2を参照してください。"
        )
