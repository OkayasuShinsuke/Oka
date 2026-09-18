"""誤読を防ぐ品質保証ロジック(仕様書 §11)。

5層の検出ネット:
    第1層 §11.2 アンサンブル照合(2エンジン)/ §11.2.1 3エンジン多数決
    第2層 §11.3 信頼度スコアによる振り分け
    第3層 §11.4 LaTeX構文検証
    第4層 §11.5 数式の意味的サニティチェック(SymPy)
    第5層 §11.6 文脈辞書による補正候補提示(自動修正はしない)
"""
from __future__ import annotations

import functools
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from tscan.models import Block, BlockKind, Issue

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# ---------------------------------------------------------------------------
# §11.4 REQ-QA-06: LaTeX構文検証(第3層)
# ---------------------------------------------------------------------------

_BRACE_PAIRS = {"}": "{", "]": "[", ")": "("}

# 引数の個数が決まっている代表的なコマンド(§11.4 の③引数個数チェック)
_COMMAND_ARITY = {"frac": 2, "sqrt": 1, "vec": 1, "hat": 1, "bar": 1, "overline": 1, "underline": 1, "text": 1}


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


def check_command_arity(latex: str) -> list[str]:
    """\\frac のように引数の個数が決まったコマンドの引数不足を検出する。"""
    problems: list[str] = []
    i = 0
    while i < len(latex):
        if latex[i] != "\\":
            i += 1
            continue
        j = i + 1
        while j < len(latex) and latex[j].isalpha():
            j += 1
        name = latex[i + 1 : j]
        arity = _COMMAND_ARITY.get(name)
        if arity:
            found = 0
            k = j
            while found < arity and k < len(latex):
                while k < len(latex) and latex[k] == " ":
                    k += 1
                if k < len(latex) and latex[k] == "{":
                    depth = 0
                    while k < len(latex):
                        if latex[k] == "{":
                            depth += 1
                        elif latex[k] == "}":
                            depth -= 1
                            if depth == 0:
                                k += 1
                                break
                        k += 1
                    found += 1
                else:
                    break
            if found < arity:
                problems.append(f"\\{name} は引数が{arity}個必要ですが{found}個しかありません")
        i = j if j > i else i + 1
    return problems


# ---------------------------------------------------------------------------
# §11.2 REQ-QA-02/03: アンサンブル照合(第1層)
# ---------------------------------------------------------------------------


def cross_check(text_a: str, text_b: str) -> tuple[str, float, list[str]]:
    """2エンジンの結果を突き合わせ、(採用テキスト, 一致度, 不一致箇所) を返す。"""
    matcher = SequenceMatcher(None, text_a, text_b)
    similarity = matcher.ratio()

    disagreements = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            disagreements.append(f"[{tag}] A='{text_a[i1:i2]}' / B='{text_b[j1:j2]}'")

    return text_a, similarity, disagreements


def majority_vote(text_a: str, text_b: str, text_c: str) -> tuple[str, float, bool]:
    """3エンジンの結果を多数決する(§11.2.1 REQ-QA-03b)。"""
    counts = Counter([text_a, text_b, text_c])
    winner, votes = counts.most_common(1)[0]

    if votes == 3:
        return winner, 0.995, False
    if votes == 2:
        return winner, 0.95, False
    return text_a, 0.40, True


def normalize_latex(latex: str) -> str:
    """表記ゆれを吸収してから比較する(§11.2.1 REQ-QA-03c)。

    `\\frac{1}{2}` と `\\frac{1} {2}` のような無意味な差で多数決が割れないようにする。
    """
    return "".join(latex.split())


# ---------------------------------------------------------------------------
# §11.3 REQ-QA-04: 信頼度スコアの統合(第2層)
# ---------------------------------------------------------------------------


def combine_confidence(engine_conf: float, agree: float, penalty: float, context: float) -> float:
    """4つの指標を重み付き幾何平均で1つのスコアにまとめる。

    算術平均と違い、1つでも致命的に低ければ全体が下がる(チェーンは最も弱い輪で決まる)。
    """
    weights = {"engine": 3, "agree": 4, "penalty": 2, "context": 1}
    values = {"engine": engine_conf, "agree": agree, "penalty": penalty, "context": context}

    total_weight = sum(weights.values())
    product = 1.0
    for key, weight in weights.items():
        product *= max(values[key], 1e-6) ** weight

    return product ** (1 / total_weight)


# ---------------------------------------------------------------------------
# §11.1 REQ-QA-01: 誤読ペア台帳によるペナルティ
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_confusion_pairs(path: str | None = None) -> list[tuple[str, str, str]]:
    """config/confusion_pairs.yaml を読み込み、(a, b, note) のリストにする。"""
    config_path = Path(path) if path else CONFIG_DIR / "confusion_pairs.yaml"
    if not config_path.exists():
        return []
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    pairs: list[tuple[str, str, str]] = []
    for group in raw.values():
        for entry in group or []:
            pairs.append((entry.get("a", ""), entry.get("b", ""), entry.get("note", "")))
    return pairs


@functools.lru_cache(maxsize=1)
def _confusion_index() -> dict[str, set[str]]:
    """文字 -> 混同されうる相手の集合。双方向に引けるようにする。"""
    index: dict[str, set[str]] = {}
    for a, b, _note in load_confusion_pairs():
        if not a or not b:
            continue
        index.setdefault(a, set()).add(b)
        index.setdefault(b, set()).add(a)
    return index


def is_confusable(char_a: str, char_b: str) -> bool:
    """2文字が§11.1の誤読ペア台帳に載っているか。"""
    return char_b in _confusion_index().get(char_a, set())


def confusion_penalty(
    text: str, evidence_chars: set[str] | None = None, pairs: list[tuple[str, str, str]] | None = None
) -> tuple[float, list[str]]:
    """混同されやすい文字が「実際に疑わしい箇所に出ている」場合だけ減点する。

    重要: 台帳に載っている文字が含まれるだけで減点してはならない。
    「力」「一」「ロ」などは日本語の教科書に当然のように出てくるため、
    無条件に減点すると全ブロックが要確認になり、§11.8のレビュー率5%以下を満たせなくなる
    (実測で100%要確認になることを確認済み)。

    evidence_chars: 他の検査(エンジン不一致の差分・辞書の惜しい一致)が疑わしいと
                    指摘した文字の集合。ここに含まれる文字だけを減点対象にする。
    """
    if not evidence_chars:
        return 1.0, []

    pairs = load_confusion_pairs() if pairs is None else pairs
    index = _confusion_index()
    hits: list[str] = []
    seen: set[str] = set()

    for char in evidence_chars:
        if char in seen or char not in index or char not in text:
            continue
        seen.add(char)
        partners = "/".join(sorted(index[char]))
        hits.append(f"'{char}' は '{partners}' と混同されやすい(§11.1の台帳)")

    penalty = max(1.0 - 0.10 * len(hits), 0.6)
    return penalty, hits


# ---------------------------------------------------------------------------
# §11.5 REQ-QA-07: 数式の意味的サニティチェック(第4層)
# ---------------------------------------------------------------------------

SUSPICIOUS_SYMBOLS = {"l", "O", "I"}  # 1, 0, 1 と誤読されやすい


_BARE_FUNCTIONS = ("sin", "cos", "tan", "log", "ln", "exp", "lim", "max", "min", "sqrt")


def check_bare_functions(latex: str) -> list[str]:
    r"""`\sin` と書くべきところが `sin` になっていないかを調べる。

    SymPyは `sin9` を s*i*n*9 という積として黙って受理してしまうため、
    構文チェックだけでは通り抜けてしまう。教科書の数式で関数名が裸で出ることはまずない。

    判定方法: まず `\sin` のようなLaTeXコマンドの範囲を全て特定し、**その外側**にある
    関数名だけを対象にする。こうすることで
        - `\sin` `\arcsin` は正しい記法として見逃す
        - `qvBsin9` のように変数と繋がってしまったものも検出できる
    の両方を満たせる(単純な単語境界の正規表現では後者を取りこぼす)。
    """
    command_spans = [m.span() for m in re.finditer(r"\\[A-Za-z]+", latex)]

    def inside_command(start: int, end: int) -> bool:
        return any(cs <= start and end <= ce for cs, ce in command_spans)

    problems = []
    for name in _BARE_FUNCTIONS:
        for match in re.finditer(name, latex):
            if inside_command(match.start(), match.end()):
                continue
            problems.append(f"'{name}' がバックスラッシュなしで書かれています(正しくは \\{name})")
            break
    return problems


def semantic_check(latex: str) -> tuple[bool, list[str]]:
    """LaTeXを数式として解釈できるか、怪しい記号がないかを調べる。

    SymPyは壊れた式を黙って受理することがある(例: `x^{2` は `x` と解釈される)ため、
    構文検査(括弧の対応・引数個数)を必ず先に通してから意味検証に進む。
    """
    warnings: list[str] = []

    if not check_brace_balance(latex):
        return False, ["括弧の対応が取れていません"]
    arity_problems = check_command_arity(latex)
    if arity_problems:
        return False, arity_problems

    warnings.extend(check_bare_functions(latex))

    try:
        from sympy.parsing.latex import parse_latex
    except ImportError:
        return True, warnings + ["sympy未インストールのため意味的検証はスキップされました"]

    try:
        expression = parse_latex(latex)
    except Exception as error:
        return False, warnings + [f"数式として解釈できません: {error}"]

    used = {str(s) for s in expression.free_symbols}
    for name in sorted(used & SUSPICIOUS_SYMBOLS):
        warnings.append(f"変数 '{name}' は数字の誤読かもしれません(l↔1, O↔0)")

    return True, warnings


# ---------------------------------------------------------------------------
# §11.6 REQ-QA-08/09: 文脈辞書(第5層)。自動修正はせず候補提示のみ
# ---------------------------------------------------------------------------


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


@functools.lru_cache(maxsize=8)
def load_lexicon(subject: str) -> tuple[str, ...]:
    """config/lexicon/{subject}.txt を読み込む(コメント行と空行は無視)。"""
    path = CONFIG_DIR / "lexicon" / f"{subject}.txt"
    if not path.exists():
        return ()
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return tuple(words)


def single_substitution(fragment: str, word: str) -> tuple[str, str] | None:
    """同じ長さの2語が「1文字の置換だけ」で違う場合、その(誤り側, 正しい側)を返す。"""
    if len(fragment) != len(word):
        return None
    diffs = [(a, b) for a, b in zip(fragment, word) if a != b]
    return diffs[0] if len(diffs) == 1 else None


# 「漢字1文字違い」だけで誤読候補にしてよい専門用語の最小の長さ。
# 短い語(2〜3文字)で許すと、偶然1文字違いの別の語を大量に拾う
LONG_TERM_LENGTH = 3


def _is_kanji(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def context_score(text: str, lexicon: tuple[str, ...]) -> tuple[float, list[str], set[str]]:
    """辞書との整合性から (文脈スコア, 修正候補, 疑わしい文字の集合) を返す。

    候補として出すのは、辞書語との違いが**1文字の置換**で、かつ
    その1文字が§11.1の誤読ペア台帳に載っている場合だけに限る。

    単純な編集距離1だけで判定すると、
        「運動する」の一部 '運動す' → '運動量' の誤読?
        「vは速度」の一部 'は速度' → '加速度' の誤読?
    のような偶然の一致を大量に拾ってしまう(実測で確認済み)。
    「す↔量」「は↔加」は誤読ペアではないため、この条件で正しく除外できる。
    """
    if not lexicon or not text.strip():
        return 1.0, [], set()

    suggestions: list[str] = []
    suspicious: set[str] = set()
    near_miss = 0
    exact = 0

    # 辞書語がそのまま現れている位置を先に押さえる。そこに重なる「1文字違い」は
    # 正しい語の一部を切り取っただけなので候補にしない
    # (「運動方程式」の中の「運動方」を「運動量」の誤読だと言い出すのを防ぐ)。
    covered: set[int] = set()
    for word in lexicon:
        start = text.find(word)
        while start != -1:
            covered.update(range(start, start + len(word)))
            start = text.find(word, start + 1)

    for word in lexicon:
        if word in text:
            exact += 1
            continue
        window = len(word)
        for i in range(0, max(len(text) - window + 1, 0)):
            fragment = text[i : i + window]
            if not fragment or covered & set(range(i, i + window)):
                continue
            substitution = single_substitution(fragment, word)
            if substitution is None:
                continue
            wrong, correct = substitution
            if is_confusable(wrong, correct):
                reason = "§11.1の混同ペア"
            elif (
                len(word) >= LONG_TERM_LENGTH
                and _is_kanji(wrong)
                and _is_kanji(correct)
                and fragment not in lexicon
            ):
                # 実写真では、辞書にない任意の漢字同士の取り違えが起きる
                # (磁性体→彼性体、絶縁体→多緑体、電気双極子→電気台板子)。
                # 混同ペア台帳だけでは拾えないので、専門用語に限って「漢字1文字だけ違う」も
                # 候補にする。誤りと判定する側(fragment)が辞書にある正しい語のときは対象外
                # (「磁束密度」を「電束密度」の誤読だと言い出すのを防ぐ)。
                # 違う1文字が両方とも漢字であることも条件にする(「運動す」→「運動量」を防ぐ)
                reason = f"{LONG_TERM_LENGTH}文字以上の専門用語と漢字1文字違い"
            else:
                continue  # 偶然の1文字違い。短い語や仮名の違いは候補にしない
            suggestions.append(f"'{fragment}' は '{word}' の誤読かもしれません('{wrong}'→'{correct}', {reason})")
            suspicious.add(wrong)
            near_miss += 1
            break

    if near_miss:
        return max(1.0 - 0.15 * near_miss, 0.5), suggestions, suspicious
    return (1.0 if exact else 0.95), suggestions, suspicious


# ---------------------------------------------------------------------------
# 5層をまとめて適用する(パイプラインから呼ばれる入口)
# ---------------------------------------------------------------------------


@dataclass
class VerifyThresholds:
    auto_accept: float = 0.95
    review_required: float = 0.80


def verify_block(
    block: Block,
    engine_texts: list[str] | None = None,
    lexicon: tuple[str, ...] = (),
    thresholds: VerifyThresholds | None = None,
    math_engine_is_specialist: bool = True,
) -> Block:
    """1ブロックに5層の検証を適用し、confidence/issues/review_statusを更新して返す。

    engine_texts: 同じ領域を別エンジンが読んだ結果(§11.2/§11.2.1のアンサンブル用)。
    """
    thresholds = thresholds or VerifyThresholds()
    issues: list[Issue] = []
    content = block.latex if block.kind == BlockKind.MATH_BLOCK else block.text

    # --- 第1層: アンサンブル照合 -----------------------------------------
    agree = 1.0
    suspicious: set[str] = set()
    others = [t for t in (engine_texts or []) if t is not None]
    if len(others) >= 2:
        is_math = block.kind == BlockKind.MATH_BLOCK
        norm = normalize_latex if is_math else (lambda s: s)
        winner, agree, disagreed = majority_vote(norm(content), norm(others[0]), norm(others[1]))
        if disagreed:
            issues.append(Issue(code="ENGINE_DISAGREE", detail="3エンジンの結果が全て不一致", severity="high"))
            suspicious.update(content)
    elif len(others) == 1:
        _, agree, disagreements = cross_check(content, others[0])
        if agree < 0.95:
            issues.append(
                Issue(
                    code="ENGINE_DISAGREE",
                    detail="; ".join(disagreements[:3]) or f"一致度 {agree:.2f}",
                    severity="high" if agree < 0.90 else "medium",
                )
            )
            # 不一致だった箇所の文字だけを、誤読ペア照合の対象にする
            for d in disagreements:
                suspicious.update(d)
    else:
        # 照合相手がいない = §11.2のアンサンブルによる裏付けがない。
        # 減点はするが、これ単独でレビュー行きにはしない(全ブロックが対象になってしまうため)
        agree = 0.93
        issues.append(
            Issue(code="NO_ENSEMBLE", detail="照合できるエンジンが1つしかありません", severity="low")
        )

    # --- 第3層/第4層: 数式の構文・意味検証 --------------------------------
    if block.kind == BlockKind.MATH_BLOCK and content.strip():
        if not check_brace_balance(content):
            issues.append(Issue(code="LATEX_BRACE", detail="括弧の対応が取れていません", severity="high"))
        for problem in check_command_arity(content):
            issues.append(Issue(code="LATEX_ARITY", detail=problem, severity="high"))

        ok, warnings = semantic_check(content)
        if not ok:
            issues.append(Issue(code="LATEX_PARSE", detail=warnings[0] if warnings else "解釈不能", severity="high"))
        for w in warnings:
            if "誤読かもしれません" in w:
                issues.append(Issue(code="SUSPICIOUS_SYMBOL", detail=w, severity="medium"))

    # --- 第5層: 文脈辞書 + 誤読ペア台帳 -----------------------------------
    ctx, suggestions, lexicon_suspicious = context_score(content, lexicon)
    for s in suggestions[:3]:
        issues.append(Issue(code="LEXICON_SUGGEST", detail=s, severity="high"))
    suspicious.update(lexicon_suspicious)

    penalty, confusion_hits = confusion_penalty(content, evidence_chars=suspicious)
    for hit in confusion_hits[:3]:
        issues.append(Issue(code="CONFUSION_PAIR", detail=hit, severity="low"))

    # --- 第2層: 信頼度の統合と振り分け(§11.3 REQ-QA-05) --------------------
    block.confidence = combine_confidence(
        engine_conf=max(block.confidence, 1e-6), agree=agree, penalty=penalty, context=ctx
    )

    # REQ-OCR-02: 数式専用エンジン(Mathpix等)が使えず汎用OCRで代用した場合は
    # 信頼度を0.7倍する。数式は2次元配置に意味があり(§9.1)、汎用OCRでは
    # 上下付き・分数構造が壊れても表面上それらしく見えるため、必ず人間の確認に回す。
    if block.kind == BlockKind.MATH_BLOCK and not math_engine_is_specialist:
        block.confidence *= 0.7
        issues.append(
            Issue(
                code="MATH_ENGINE_FALLBACK",
                detail="数式専用エンジンが使えないため汎用OCRで読み取りました(REQ-OCR-02)",
                severity="medium",
            )
        )

    block.issues = issues

    if block.edited_by == "user" or block.review_status == "edited":
        return block  # 人間が修正済みのものは上書きしない(§11.7.4 REQ-UI-09)

    # 閾値の意味(§11.3):
    #   >= auto_accept(0.95)        : 自動採用。マーカーもなし
    #   review_required(0.80)〜0.95 : 自動採用するが、出力に⚠️マーカーを付ける
    #   < review_required(0.80)     : レビューキューへ
    # high severity の問題があるものは、信頼度によらずレビューキューへ送る。
    has_blocking_issue = any(i.severity == "high" for i in issues)
    if has_blocking_issue or block.confidence < thresholds.review_required:
        block.review_status = "pending"
    else:
        block.review_status = "confirmed"
    return block
