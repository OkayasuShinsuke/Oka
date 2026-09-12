"""Mathpix API連携(仕様書 §9.2〜§9.3)。数式のLaTeX化。

環境変数 MATHPIX_APP_ID / MATHPIX_APP_KEY が必要(§16.2 REQ-SEC-02)。
未設定の場合はエラーを出す(texifyへのフォールバックは呼び出し側=verify/pipeline層で行う, REQ-OCR-02)。
"""
from __future__ import annotations

import base64
import os

import cv2
import numpy as np
import requests

from tscan.ocr.base import OcrLine

MATHPIX_ENDPOINT = "https://api.mathpix.com/v3/text"


class MathpixEngine:
    name = "mathpix"

    def __init__(self, app_id: str | None = None, app_key: str | None = None, timeout: float = 15.0) -> None:
        self.app_id = app_id or os.environ.get("MATHPIX_APP_ID")
        self.app_key = app_key or os.environ.get("MATHPIX_APP_KEY")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_key)

    def recognize(self, image: np.ndarray, vertical: bool = False) -> list[OcrLine]:
        """切り出した数式領域の画像を送信し、LaTeXを取得する(REQ-OCR-03: 数式領域のみ送信)。"""
        if not self.is_configured:
            raise RuntimeError(
                "MATHPIX_APP_ID / MATHPIX_APP_KEY が未設定です。環境変数で設定してください(§16.2 REQ-SEC-02)。"
            )

        ok, buffer = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("画像のエンコードに失敗しました")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")

        response = requests.post(
            MATHPIX_ENDPOINT,
            json={"src": data_uri, "formats": ["latex_styled"]},
            headers={"app_id": self.app_id, "app_key": self.app_key, "Content-type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()

        latex = payload.get("latex_styled", "")
        confidence = float(payload.get("latex_confidence", payload.get("confidence", 0.0)))
        height, width = image.shape[:2]
        return [OcrLine(text=latex, bbox=(0, 0, width, height), confidence=confidence, engine=self.name, kind="math")]
