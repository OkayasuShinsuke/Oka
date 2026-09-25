"""yomitoku連携(仕様書 §9.2)。日本語文書特化OCR。縦書き・レイアウトに強い。

`pip install tscan[yomitoku]` が必要(torch等の重い依存を含むため任意インストール)。

実モデル(yomitoku 0.15.0)による疎通確認済み: `tests/test_yomitoku_engine.py` で
PILで描画した縦書き日本語合成画像を認識させ、CERが妥当な範囲であることを確認している。
ただし実際の教科書写真での検証はまだ行っていない(§9.2参照)。
"""
from __future__ import annotations

import cv2
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
        self._ocr = None

    def _ensure_loaded(self) -> None:
        """OCRモデルを遅延ロードする(初回のみ。重いダウンロードを伴うため)。"""
        if self._ocr is not None:
            return
        import torch
        from yomitoku import OCR

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._ocr = OCR(device=device)

    def recognize(self, image: np.ndarray, vertical: bool = False) -> list[OcrLine]:
        """1枚の画像を読み、行(語)単位の結果を返す。

        vertical はyomitokuでは使わない(word.directionで自動判定されるため)。
        他エンジンと同じ関数シグネチャに合わせるためだけに残している。
        """
        if not self._available:
            raise RuntimeError(
                "yomitokuがインストールされていません。`pip install yomitoku` を実行してください(§9.2)。"
            )
        self._ensure_loaded()

        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        results, _ = self._ocr(image)

        lines: list[OcrLine] = []
        for word in results.words:
            xs = [p[0] for p in word.points]
            ys = [p[1] for p in word.points]
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
            confidence = min(word.rec_score, word.det_score)
            lines.append(
                OcrLine(
                    text=word.content,
                    bbox=(x, y, w, h),
                    confidence=confidence,
                    engine=self.name,
                    kind="text",
                )
            )
        return lines
