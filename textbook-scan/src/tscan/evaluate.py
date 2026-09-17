"""精度評価(仕様書 §11.8 / §18)。

受け入れ基準の測定に使う指標:
    CER(文字誤り率)       = 編集距離 ÷ 正解文字数     … 合格ライン 0.5%以下
    数式行正解率           = 完全一致した数式行の割合   … 合格ライン 95%以上
    誤読の検出率(Recall)   = 検出できた誤読 ÷ 実際の誤読 … 合格ライン 95%以上(最重要)
    レビュー率             = 要確認行 ÷ 全行           … 上限 5%
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from tscan.models import Block, BlockKind, Book


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1))
        prev = cur
    return prev[-1]


def normalize_for_eval(text: str) -> str:
    """評価用の正規化。

    全角/半角、空白の有無といった「意味が変わらない差」は誤りとして数えない。
    OCR評価では一般的な前処理で、これをしないと本質的でない差で数値が悪化する。
    """
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", "", text)
    return text


def cer(reference: str, hypothesis: str, normalize: bool = True) -> float:
    """文字誤り率(Character Error Rate)。0.0が完全一致。"""
    if normalize:
        reference, hypothesis = normalize_for_eval(reference), normalize_for_eval(hypothesis)
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _levenshtein(reference, hypothesis) / len(reference)


@dataclass
class EvalResult:
    cer: float
    reference_chars: int
    hypothesis_chars: int
    math_lines_total: int = 0
    math_lines_correct: int = 0
    review_rate: float = 0.0
    blocks: int = 0

    @property
    def math_accuracy(self) -> float | None:
        """測定対象の数式行が1つもなければ None(「100%」と誤解させない)。"""
        if not self.math_lines_total:
            return None
        return self.math_lines_correct / self.math_lines_total

    def verdict(self) -> dict[str, tuple[bool, str]]:
        """§11.8の合格ラインに対する判定。"""
        math = self.math_accuracy
        math_row = (
            (math >= 0.95, f"{math * 100:.1f}% ({self.math_lines_correct}/{self.math_lines_total}行)")
            if math is not None
            else (True, "測定対象なし(正解データに数式行がありません)")
        )
        return {
            "本文CER ≦ 0.5%": (self.cer <= 0.005, f"{self.cer * 100:.2f}%"),
            "数式行正解率 ≧ 95%": math_row,
            "レビュー率 ≦ 5%": (self.review_rate <= 0.05, f"{self.review_rate * 100:.1f}%"),
        }


def _page_text(blocks: list[Block]) -> str:
    """ヘッダ・フッタ・ルビを除いた本文テキストを読み順に連結する。"""
    skip = {BlockKind.HEADER, BlockKind.FOOTER, BlockKind.RUBY, BlockKind.FIGURE}
    parts = []
    for block in sorted(blocks, key=lambda b: b.reading_order):
        if block.kind in skip:
            continue
        parts.append(block.latex if block.kind == BlockKind.MATH_BLOCK else block.text)
    return "".join(parts)


def evaluate_book(
    book: Book, references: dict[int, str], math_references: dict[int, list[str]] | None = None
) -> EvalResult:
    """本の認識結果を正解データと突き合わせる。

    references      : ページ番号 -> 本文の正解テキスト(連結したもの)
    math_references : ページ番号 -> そのページの数式行の正解リスト(§11.8の数式行正解率用)
    """
    pages = sorted((p for p in book.pages if not p.deleted), key=lambda p: p.order_key)
    math_references = math_references or {}

    ref_all, hyp_all = [], []
    blocks_total = 0
    pending = 0
    math_total = 0
    math_correct = 0

    for i, page in enumerate(pages, start=1):
        blocks_total += len(page.blocks)
        pending += sum(1 for b in page.blocks if b.review_status == "pending")

        if i in references:
            ref_all.append(references[i])
            hyp_all.append(_page_text(page.blocks))

        expected_math = math_references.get(i)
        if expected_math:
            recognized = [
                normalize_for_eval(b.latex)
                for b in sorted(page.blocks, key=lambda b: b.reading_order)
                if b.kind == BlockKind.MATH_BLOCK
            ]
            for expected in expected_math:
                math_total += 1
                if normalize_for_eval(expected) in recognized:
                    math_correct += 1

    reference = "".join(ref_all)
    hypothesis = "".join(hyp_all)

    return EvalResult(
        cer=cer(reference, hypothesis),
        reference_chars=len(normalize_for_eval(reference)),
        hypothesis_chars=len(normalize_for_eval(hypothesis)),
        math_lines_total=math_total,
        math_lines_correct=math_correct,
        review_rate=(pending / blocks_total) if blocks_total else 0.0,
        blocks=blocks_total,
    )
