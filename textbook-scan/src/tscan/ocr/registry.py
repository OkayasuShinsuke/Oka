"""OCRエンジンの検出と選択(仕様書 §9.3 REQ-OCR-01 / §9.6)。

仕様書は「Apple Vision + yomitoku(+DeepSeek-OCR)」を本文用、
「Mathpix + texify(+ローカルVLM)」を数式用と定めているが、
実際に使える顔ぶれは環境によって変わる(macOSか否か、APIキーの有無、
重い依存をインストールしたか)。このモジュールは**実行時に使えるものだけを集めて**
§9.3の構成を組み立てる。

エンジンが1つしか使えない場合はアンサンブル照合(§11.2)が成立しないため、
その旨をcapability情報として返し、呼び出し側が信頼度を保守的に扱えるようにする。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tscan.ocr.base import OcrEngine


@dataclass
class EngineSet:
    """あるページ種別に対して実際に使えるエンジン群。"""

    primary: OcrEngine | None = None
    secondary: OcrEngine | None = None
    tertiary: OcrEngine | None = None
    unavailable: dict[str, str] = field(default_factory=dict)  # エンジン名 -> 使えない理由

    @property
    def engines(self) -> list[OcrEngine]:
        return [e for e in (self.primary, self.secondary, self.tertiary) if e is not None]

    @property
    def count(self) -> int:
        return len(self.engines)

    @property
    def ensemble_available(self) -> bool:
        """2つ以上あればアンサンブル照合(§11.2)が成立する。"""
        return self.count >= 2


def _try_apple_vision() -> tuple[OcrEngine | None, str]:
    from tscan.ocr.apple_vision import AppleVisionEngine

    engine = AppleVisionEngine()
    if getattr(engine, "_available", False):
        return engine, ""
    return None, "macOS実機 + pyobjc-framework-Vision が必要(§9.2)"


def _try_yomitoku() -> tuple[OcrEngine | None, str]:
    from tscan.ocr.yomitoku_engine import YomitokuEngine

    engine = YomitokuEngine()
    if getattr(engine, "_available", False):
        return engine, ""
    return None, "pip install yomitoku が必要(§9.2)"


def _try_tesseract() -> tuple[OcrEngine | None, str]:
    from tscan.ocr.tesseract_engine import TesseractEngine

    engine = TesseractEngine()
    if engine.is_available:
        return engine, ""
    return None, "tesseract本体 + pip install pytesseract が必要"


def _try_mathpix() -> tuple[OcrEngine | None, str]:
    from tscan.ocr.mathpix import MathpixEngine

    engine = MathpixEngine()
    if engine.is_configured:
        return engine, ""
    return None, "MATHPIX_APP_ID / MATHPIX_APP_KEY の設定が必要(§16.2 REQ-SEC-02)"


def build_text_engines(vertical: bool = False, offline: bool = False) -> EngineSet:
    """本文OCRのエンジン構成を組み立てる(§9.3)。

    仕様書の優先順位:
        横書き: Apple Vision(主) + yomitoku(副)
        縦書き: yomitoku(主) + Apple Vision(副)  ※DeepSeek-OCR系は縦書きでは不採用(REQ-OCR-04)
    どちらも使えない環境では、実際に動くTesseractを主エンジンとして採用する。
    """
    candidates = [("yomitoku", _try_yomitoku), ("apple_vision", _try_apple_vision)]
    if not vertical:
        candidates.reverse()  # 横書きはApple Visionが主
    candidates.append(("tesseract", _try_tesseract))

    result = EngineSet()
    for name, probe in candidates:
        engine, reason = probe()
        if engine is None:
            result.unavailable[name] = reason
            continue
        if result.primary is None:
            result.primary = engine
        elif result.secondary is None:
            result.secondary = engine
        elif result.tertiary is None:
            result.tertiary = engine
    return result


def build_math_engines(offline: bool = False) -> EngineSet:
    """数式OCRのエンジン構成を組み立てる(§9.3)。

    Mathpixが使えない場合はREQ-OCR-02のフォールバック方針に従い、
    本文エンジン(Tesseract)で暫定的に読み取る。その場合は信頼度を下げて扱う。
    """
    result = EngineSet()

    if not offline:
        engine, reason = _try_mathpix()
        if engine is not None:
            result.primary = engine
        else:
            result.unavailable["mathpix"] = reason
    else:
        result.unavailable["mathpix"] = "--offline指定のため使用しない(§16.2 REQ-SEC-03)"

    # texify は未実装。現時点のフォールバックはTesseract(数式としての精度は限定的)
    result.unavailable["texify"] = "未実装(§9.2のOSS数式OCR。将来の実装対象)"

    fallback, reason = _try_tesseract()
    if fallback is not None:
        if result.primary is None:
            result.primary = fallback
        else:
            result.secondary = fallback
    else:
        result.unavailable["tesseract"] = reason

    return result


def describe_availability() -> dict[str, str]:
    """`tscan doctor` 用: 各エンジンの利用可否を1回で調べる。"""
    status: dict[str, str] = {}
    for name, probe in [
        ("apple_vision", _try_apple_vision),
        ("yomitoku", _try_yomitoku),
        ("tesseract", _try_tesseract),
        ("mathpix", _try_mathpix),
    ]:
        engine, reason = probe()
        status[name] = "利用可能" if engine is not None else reason
    return status
