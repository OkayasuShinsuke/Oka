"""Apple Vision連携(仕様書 §9.2)。macOS実機の日本語OCRを主エンジンとして使う。

なぜApple Visionが主エンジンなのか:
    Tesseractの日本語モデルは、写真から撮った教科書で「特定の漢字が別の漢字に化ける」
    誤りを多く出す(実測: 誘電体→計電体、磁性体→彼性体、絶縁体→多緑体)。
    Apple Visionは日本語の認識精度がこれより大きく上で、しかもNeural Engineで速い。
    仕様書§9.3はApple Vision + yomitokuの2エンジン照合を前提にしている。

必要なもの:
    - macOS 13 (Ventura) 以降。日本語は Vision の revision 3 で追加されたため
    - pip install "tscan[apple-vision]"(pyobjc-framework-Vision / -Quartz が入る)

Linux上でも開発・テストできるよう、インポート時には失敗させない。
実際に recognize() を呼んだ時点で、何が足りないかを日本語で説明する。
"""
from __future__ import annotations

import io
import platform

import numpy as np

from tscan.ocr.base import OcrLine

# Visionの認識レベル(Vision.VNRequestTextRecognitionLevelAccurate と同じ値)。
# 定数をインポートせず数値で持つのは、pyobjcの版によって公開名が変わるため。
_LEVEL_ACCURATE = 0
# 日本語が使えるようになった版。これ未満だと recognitionLanguages に ja-JP を渡せない
_REVISION_JAPANESE = 3


class AppleVisionEngine:
    """VNRecognizeTextRequest で1ページを読み、行単位の OcrLine にして返す。"""

    name = "apple_vision"

    def __init__(self, languages: tuple[str, ...] = ("ja-JP", "en-US")) -> None:
        self.languages = list(languages)
        self._unavailable_reason = self._probe()
        self._available = self._unavailable_reason == ""

    @staticmethod
    def _probe() -> str:
        """使える状態かを調べ、使えない理由(空文字なら使える)を返す。"""
        if platform.system() != "Darwin":
            return "macOS実機ではありません(Apple VisionはmacOSのフレームワークです)"
        try:
            import Quartz  # noqa: F401
            import Vision  # noqa: F401
        except ImportError:
            return 'pip install "tscan[apple-vision]" が必要です(pyobjc)'
        try:
            release = int(platform.mac_ver()[0].split(".")[0])
        except (ValueError, IndexError):
            release = 0
        if release and release < 13:
            return f"macOS 13以降が必要です(日本語対応はVisionのrevision {_REVISION_JAPANESE}から。現在 {release})"
        return ""

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    # -- 本体 -------------------------------------------------------------

    def recognize(self, image: np.ndarray, vertical: bool = False) -> list[OcrLine]:
        """1枚の画像を読み、行単位の結果を返す。

        vertical は縦書きページかどうか。Visionには縦書き専用の指定が無く、
        revision 3 以降が縦組みを自動で扱う。ここでは読み順の並べ替えにだけ使う
        (縦書きは右の列から左へ読む)。
        """
        if not self._available:
            raise RuntimeError(f"Apple Visionは利用できません: {self._unavailable_reason}(§9.2)")

        import Vision

        height, width = image.shape[:2]
        handler = self._make_handler(image)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(_LEVEL_ACCURATE)
        request.setRecognitionLanguages_(self.languages)
        request.setUsesLanguageCorrection_(True)
        # 教科書の本文は十分な大きさなので、極端に小さい文字を拾わせて誤検出を増やさない
        request.setMinimumTextHeight_(0.008)
        self._request_japanese_revision(request)

        success, error = handler.performRequests_error_([request], None)
        if not success:
            raise RuntimeError(f"Apple Visionの認識に失敗しました: {error}")

        lines = [
            line
            for observation in (request.results() or [])
            if (line := self._to_ocr_line(observation, width, height)) is not None
        ]
        return self._sort_reading_order(lines, vertical=vertical)

    # -- 内部処理 ---------------------------------------------------------

    @staticmethod
    def _request_japanese_revision(request) -> None:
        """日本語が使えるrevisionを明示する。古いpyobjcには無いので失敗は無視する。"""
        try:
            supported = list(type(request).supportedRevisions() or [])
        except Exception:  # noqa: BLE001 — pyobjcの版差
            return
        if _REVISION_JAPANESE in supported:
            try:
                request.setRevision_(_REVISION_JAPANESE)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _make_handler(image: np.ndarray):
        """numpy配列を CGImage にして VNImageRequestHandler を作る。

        numpy → PNG → CGImage と経由する。生のバッファから CGImage を組むより
        行の詰め物(stride)やカラースペースの扱いを間違えにくい。
        """
        import Quartz
        from Foundation import NSData
        from PIL import Image

        if image.ndim == 3:
            rgb = image[:, :, ::-1]  # OpenCVのBGR → RGB
            pil = Image.fromarray(rgb.astype(np.uint8), mode="RGB")
        else:
            pil = Image.fromarray(image.astype(np.uint8), mode="L")

        buffer = io.BytesIO()
        pil.save(buffer, format="PNG")
        data = NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))

        source = Quartz.CGImageSourceCreateWithData(data, None)
        if source is None:
            raise RuntimeError("画像をCGImageに変換できませんでした")
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
        if cg_image is None:
            raise RuntimeError("画像をCGImageに変換できませんでした")

        import Vision

        return Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)

    @staticmethod
    def _to_ocr_line(observation, width: int, height: int) -> OcrLine | None:
        """VNRecognizedTextObservation を OcrLine に変換する。

        Visionの座標は「左下が原点、0〜1に正規化」。画像の画素座標(左上が原点)に直す。
        """
        candidates = observation.topCandidates_(1)
        if not candidates:
            return None
        best = candidates[0]
        text = str(best.string()).strip()
        if not text:
            return None

        box = observation.boundingBox()
        x = int(box.origin.x * width)
        w = max(int(box.size.width * width), 1)
        h = max(int(box.size.height * height), 1)
        # 左下原点 → 左上原点
        y = int((1.0 - box.origin.y - box.size.height) * height)

        return OcrLine(
            text=text,
            bbox=(max(x, 0), max(y, 0), w, h),
            confidence=float(best.confidence()),
            engine="apple_vision",
            kind="text",
        )

    @staticmethod
    def _sort_reading_order(lines: list[OcrLine], vertical: bool) -> list[OcrLine]:
        """横書きは上から下・左から右、縦書きは右の列から左へ並べる(§10.4)。"""
        if vertical:
            return sorted(lines, key=lambda line: (-line.bbox[0], line.bbox[1]))
        return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))

    def recognize_text(
        self, image: np.ndarray, vertical: bool = False, psm: int = 0, whitelist: str | None = None
    ) -> str:
        """テキストだけを返す簡易版(ノンブル読み取りなどで使う, §7.5)。

        psm は Tesseract 固有の指定なので無視する。whitelist が指定された場合は
        その文字だけを残す(Visionには文字種の制限が無いため後処理で行う)。
        """
        lines = self.recognize(image, vertical=vertical)
        text = "".join(line.text for line in lines)
        if whitelist:
            allowed = set(whitelist)
            text = "".join(ch for ch in text if ch in allowed)
        return text
