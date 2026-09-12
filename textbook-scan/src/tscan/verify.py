"""誤読を防ぐ品質保証ロジック(仕様書 §11)。"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

# --- §11.4 REQ-QA-06: LaTeX構文検証 -----------------------------------------

_BRACE_PAIRS = {"}": "{", "]": "[", ")": "("}


def check_brace_balance(latex: str) -> bool:
    """LaTeXの括弧の対応が取れているかを、スタックで検査する。"""
    stack: list[str] = []
    index = 0
    while index < len(latex):
        char = latex[index]
        if char == "\\":  # \{ や \} はエスケープなので括弧として数えない
            index += 2
            continue
        if char in "{[(":
            stack.append(char)
        elif char in _BRACE_PAIRS:
            if not stack or stack.pop() != _BRACE_PAIRS[char]:
                return False
        index += 1
    return len(stack) == 0


# --- §11.2 REQ-QA-02/03: アンサンブル照合(2エンジン) -------------------------


def cross_check(text_a: str, text_b: str) -> tuple[str, float, list[str]]:
    """2エンジンの結果を突き合わせ、(採用テキスト, 信頼度, 不一致箇所) を返す。"""
    matcher = SequenceMatcher(None, text_a, text_b)
    similarity = matcher.ratio()

    disagreements = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            disagreements.append(f"[{tag}] A='{text_a[i1:i2]}' / B='{text_b[j1:j2]}'")

    return text_a, similarity, disagreements


# --- §11.2.1 REQ-QA-03b: 3エンジン多数決 ------------------------------------


def majority_vote(text_a: str, text_b: str, text_c: str) -> tuple[str, float, bool]:
    """3エンジンの結果を多数決する。(採用テキスト, 信頼度, 要確認フラグ) を返す。"""
    counts = Counter([text_a, text_b, text_c])
    winner, votes = counts.most_common(1)[0]

    if votes == 3:
        return winner, 0.995, False
    if votes == 2:
        return winner, 0.95, False
    return text_a, 0.40, True


# --- §11.3 REQ-QA-04: 信頼度スコアの統合 ------------------------------------


def combine_confidence(engine_conf: float, agree: float, penalty: float, context: float) -> float:
    """4つの指標を重み付き幾何平均で1つのスコアにまとめる。"""
    weights = {"engine": 3, "agree": 4, "penalty": 2, "context": 1}
    values = {"engine": engine_conf, "agree": agree, "penalty": penalty, "context": context}

    total_weight = sum(weights.values())
    product = 1.0
    for key, weight in weights.items():
        product *= max(values[key], 1e-6) ** weight

    return product ** (1 / total_weight)


# --- §11.5 REQ-QA-07: 数式の意味的サニティチェック --------------------------

SUSPICIOUS_SYMBOLS = {"l", "O", "I"}  # 1, 0, 1 と誤読されやすい


def semantic_check(latex: str) -> tuple[bool, list[str]]:
    """LaTeXを数式として解釈できるか、怪しい記号がないかを調べる。

    sympy未インストール環境では ImportError を捕まえ、構文チェックのみの結果を返す。
    """
    warnings: list[str] = []
    try:
        from sympy.parsing.latex import parse_latex
    except ImportError:
        return check_brace_balance(latex), ["sympy未インストールのため意味的検証はスキップされました"]

    try:
        expression = parse_latex(latex)
    except Exception as error:
        return False, [f"数式として解釈できません: {error}"]

    used = {str(s) for s in expression.free_symbols}
    for name in used & SUSPICIOUS_SYMBOLS:
        warnings.append(f"変数 '{name}' は数字の誤読かもしれません(l↔1, O↔0)")

    return True, warnings


# --- §11.6 REQ-QA-08/09: 文脈辞書による補正候補提示(自動修正はしない) -------


def _levenshtein(a: str, b: str) -> int:
    """編集距離(レーベンシュタイン距離)。標準ライブラリのみで実装。"""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def suggest_lexicon_correction(word: str, lexicon: list[str], max_distance: int = 1) -> str | None:
    """辞書内の語で編集距離が近いものを候補として返す(自動修正はしない, REQ-QA-09)。"""
    best: str | None = None
    best_distance = max_distance + 1
    for entry in lexicon:
        d = _levenshtein(word, entry)
        if d < best_distance:
            best, best_distance = entry, d
    return best if best_distance <= max_distance else None
