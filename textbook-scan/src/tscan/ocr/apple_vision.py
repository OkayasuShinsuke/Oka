"""Apple Vision連携(仕様書 §9.2)。

macOS実機 + `pip install tscan[apple-vision]` (pyobjc-framework-Vision) が必須。
このリポジトリの開発・テストはLinux上でも行えるようにするため、
非macOS環境ではインポート時に例外を投げず、実際に recognize() を呼んだ時点で
分かりやすいエラーを出す設計にしている。
"""
from __future__ import annotations

import platform

import numpy as np

from tscan.ocr.base import OcrLine


class AppleVisionEngine:
    name = "apple_vision"

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            self._available = False
        else:
            try:
                import Vision  # noqa: F401
                import Quartz  # noqa: F401

                self._available = True
            except ImportError:
                self._available = False

    def recognize(self, image: np.ndarray, vertical: bool = False) -> list[OcrLine]:
        if not self._available:
            raise RuntimeError(
                "Apple Visionはこの環境では利用できません。macOS実機で "
                "`pip install pyobjc-framework-Vision` を実行してから使用してください(§9.2)。"
            )
        # macOS実機での実装: VNRecognizeTextRequest を呼び出す。
        # PyObjCブリッジの都合上、ここでは骨格のみを示す(実機での動作確認が必要)。
        raise NotImplementedError(
            "VNRecognizeTextRequestの呼び出しはmacOS実機での実装・検証が必要です。"
            "docs/textbook-scan-spec.md §9.2を参照してください。"
        )
