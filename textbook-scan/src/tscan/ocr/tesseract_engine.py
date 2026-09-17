"""Tesseract OCRエンジン(仕様書 §9.2の比較表に記載のベースラインエンジン)。

Apple Vision(macOS実機必須)やyomitoku(重い依存)と違い、
**LinuxでもmacOSでも実際に動く**ため、本実装ではこれを既定の実働エンジンとして扱う。
仕様書§9.3の構成では「比較用のベースライン」と位置づけられているが、
Apple Vision/yomitokuが利用できない環境でもパイプライン全体を検証できるようにする役割も持つ。

必要なもの:
    macOS : brew install tesseract tesseract-lang
    Ubuntu: apt-get install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert
    共通  : pip install pytesseract
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

import numpy as np

from tscan.ocr.base import OcrLine


@dataclass
class _Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    line_key: tuple[int, int, int]


class TesseractEngine:
    """Tesseractで日本語(横書き/縦書き)を認識する。

    psm(Page Segmentation Mode)の使い分け:
        6 = 単一の均一なテキストブロック(横書きの本文向け)
        5 = 縦方向に並んだ単一の均一ブロック(縦書きの本文向け)
        7 = 単一行(数式領域など、切り出し済みの1行を読ませる場合)
    """

    name = "tesseract"

    def __init__(self, lang_horizontal: str = "jpn", lang_vertical: str = "jpn_vert") -> None:
        self.lang_horizontal = lang_horizontal
        self.lang_vertical = lang_vertical
        self._available = shutil.which("tesseract") is not None
        if self._available:
            try:
                import pytesseract  # noqa: F401
            except ImportError:
                self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def _require(self) -> None:
        if not self._available:
            raise RuntimeError(
                "tesseractが見つかりません。\n"
                "  macOS : brew install tesseract tesseract-lang\n"
                "  Ubuntu: apt-get install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert\n"
                "  共通  : pip install pytesseract"
            )

    @staticmethod
    def _limit_threads() -> None:
        """Tesseract内部のOpenMP並列を止める(§15 並列処理の前提)。

        tscan はページ単位で複数の tesseract プロセスを同時に走らせる。各プロセスが
        さらにCPUコア数分のスレッドを立てると、スレッドがコアを奪い合って
        待ち時間(スピンウェイト)だけが増える。実測では1ページ0.9秒の処理が
        4プロセス同時で13分以上(CPU時間)に膨らんだ。1プロセス1スレッドにすると
        ページ並列の効果がそのまま出る。利用者が明示的に設定していれば尊重する。
        """
        import os

        os.environ.setdefault("OMP_THREAD_LIMIT", "1")

    def recognize(self, image: np.ndarray, vertical: bool = False, psm: int = 0) -> list[OcrLine]:
        """画像を認識し、行単位のOcrLineのリストを返す。

        psm=0を渡すと、verticalフラグから自動で6(横書き)/5(縦書き)を選ぶ。
        """
        self._require()
        self._limit_threads()
        import pytesseract
        from pytesseract import Output

        lang = self.lang_vertical if vertical else self.lang_horizontal
        mode = psm if psm else (5 if vertical else 6)
        config = f"--psm {mode} --oem 1"

        data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=Output.DICT)
        words = self._collect_words(data)
        return self._group_into_lines(words, vertical=vertical)

    # -- 内部処理 ---------------------------------------------------------

    @staticmethod
    def _collect_words(data: dict) -> list[_Word]:
        """TesseractのTSV出力から、意味のある単語だけを取り出す。"""
        words: list[_Word] = []
        for i, text in enumerate(data["text"]):
            text = (text or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < 0:  # Tesseractは認識できなかった要素に-1を入れる
                continue
            words.append(
                _Word(
                    text=text,
                    left=int(data["left"][i]),
                    top=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                    conf=conf / 100.0,
                    line_key=(int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i])),
                )
            )
        return words

    def _group_into_lines(self, words: list[_Word], vertical: bool) -> list[OcrLine]:
        """単語を行単位にまとめる。Tesseractのblock/par/line番号をそのまま行の識別子に使う。"""
        lines: dict[tuple[int, int, int], list[_Word]] = {}
        for w in words:
            lines.setdefault(w.line_key, []).append(w)

        result: list[OcrLine] = []
        for key, group in lines.items():
            # 日本語は単語間の空白を持たないため、座標順に連結する
            group.sort(key=(lambda w: w.top) if vertical else (lambda w: w.left))
            text = "".join(w.text for w in group)

            left = min(w.left for w in group)
            top = min(w.top for w in group)
            right = max(w.left + w.width for w in group)
            bottom = max(w.top + w.height for w in group)
            confidence = sum(w.conf for w in group) / len(group)

            result.append(
                OcrLine(
                    text=text,
                    bbox=(left, top, right - left, bottom - top),
                    confidence=confidence,
                    engine=self.name,
                    kind="text",
                )
            )

        # 読み順は§10.3のsort_reading_orderで後段が決めるため、ここでは安定順序のみ保証する
        result.sort(key=(lambda ln: (-ln.bbox[0], ln.bbox[1])) if vertical else (lambda ln: (ln.bbox[1], ln.bbox[0])))
        return result

    def recognize_text(
        self, image: np.ndarray, vertical: bool = False, psm: int = 7, whitelist: str | None = None
    ) -> str:
        """1行分の画像からテキストだけを取り出す簡易版(ノンブル読み取り等に使う)。

        whitelist: 認識を特定の文字だけに制限する(例: "0123456789")。
                   ノンブルのように「数字しか来ない」場所では誤読が大きく減る。
        """
        self._require()
        import pytesseract

        # 数字だけを読ませる場合は、日本語モデルより英数字モデルのほうが安定する
        lang = "eng" if whitelist and whitelist.isdigit() else (self.lang_vertical if vertical else self.lang_horizontal)
        config = f"--psm {psm} --oem 1"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"
        return pytesseract.image_to_string(image, lang=lang, config=config).strip()
