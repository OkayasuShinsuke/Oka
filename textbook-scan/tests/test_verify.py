import pytest

from tscan.models import Block, BlockKind
from tscan.verify import (
    check_bare_functions,
    check_brace_balance,
    check_command_arity,
    combine_confidence,
    confusion_penalty,
    context_score,
    cross_check,
    is_confusable,
    majority_vote,
    normalize_latex,
    semantic_check,
    single_substitution,
    suggest_lexicon_correction,
    verify_block,
)


# --- §11.4 LaTeX構文検証 ----------------------------------------------------


@pytest.mark.parametrize(
    "latex, expected",
    [
        (r"\frac{1}{2}", True),
        (r"\frac{1}{2", False),
        (r"\frac{1}}{2}", False),
        (r"\{ x \}", True),
        (r"x^{2}_{i}", True),
        ("", True),
    ],
)
def test_check_brace_balance(latex: str, expected: bool):
    assert check_brace_balance(latex) is expected


def test_check_command_arity_detects_missing_argument():
    assert check_command_arity(r"\frac{1}") != []
    assert check_command_arity(r"\frac{1}{2}") == []


def test_check_bare_functions():
    assert check_bare_functions("F=qvBsin9") != []
    assert check_bare_functions(r"F=qvB\sin\theta") == []


def test_semantic_check_rejects_broken_braces():
    """SymPyは `x^{2` を黙って `x` と解釈するため、構文検査を先に通す必要がある。"""
    ok, warnings = semantic_check("x^{2")
    assert ok is False


def test_semantic_check_valid_expression():
    ok, _ = semantic_check(r"\frac{1}{2}mv^2")
    assert ok is True


def test_semantic_check_flags_bare_function():
    ok, warnings = semantic_check("F=qvBsin9")
    assert any("sin" in w for w in warnings)


# --- §11.2 アンサンブル ------------------------------------------------------


def test_cross_check_identical():
    _, sim, diffs = cross_check("F = ma", "F = ma")
    assert sim == 1.0 and diffs == []


def test_cross_check_disagreement():
    _, sim, diffs = cross_check("F = ma", "F = rna")
    assert sim < 1.0 and diffs


def test_majority_vote_unanimous():
    assert majority_vote("F=ma", "F=ma", "F=ma") == ("F=ma", 0.995, False)


def test_majority_vote_two_to_one():
    text, _, needs_review = majority_vote("F=ma", "F=ma", "F=rna")
    assert text == "F=ma" and needs_review is False


def test_majority_vote_all_disagree():
    _, conf, needs_review = majority_vote("A", "B", "C")
    assert needs_review is True and conf < 0.5


def test_normalize_latex_ignores_whitespace():
    assert normalize_latex(r"\frac{1} {2}") == normalize_latex(r"\frac{1}{2}")


# --- §11.3 信頼度 ------------------------------------------------------------


def test_combine_confidence_low_signal_dominates():
    high = combine_confidence(0.99, 0.99, 0.99, 0.99)
    one_low = combine_confidence(0.99, 0.99, 0.99, 0.05)
    assert one_low < high < 1.0
    assert one_low < (0.99 * 3 + 0.05) / 4  # 算術平均より厳しい


# --- §11.1 誤読ペア台帳 ------------------------------------------------------


def test_is_confusable_is_bidirectional():
    assert is_confusable("カ", "力")
    assert is_confusable("力", "カ")
    assert not is_confusable("あ", "い")


def test_confusion_penalty_without_evidence_does_not_penalize():
    """台帳の文字が含まれるだけでは減点しない(全ブロックが要確認になるため)。"""
    penalty, hits = confusion_penalty("力の向きはフレミングの左手の法則で決まる。")
    assert penalty == 1.0 and hits == []


def test_confusion_penalty_with_evidence_penalizes():
    penalty, hits = confusion_penalty("ローレンツカ", evidence_chars={"カ"})
    assert penalty < 1.0 and hits


# --- §11.6 文脈辞書 ----------------------------------------------------------


def test_single_substitution():
    assert single_substitution("ローレンツカ", "ローレンツ力") == ("カ", "力")
    assert single_substitution("運動する", "運動量") is None  # 長さが違う
    assert single_substitution("あい", "うえ") is None  # 2文字違う


def test_context_score_flags_confusable_misread():
    score, suggestions, suspicious = context_score("この力をローレンツカとよぶ", ("ローレンツ力",))
    assert score < 1.0
    assert suggestions and "ローレンツ力" in suggestions[0]
    assert "カ" in suspicious


def test_context_score_ignores_coincidental_near_miss():
    """「運動す(る)」と「運動量」は編集距離1だが、す↔量は誤読ペアではないので候補にしない。"""
    score, suggestions, suspicious = context_score("磁場の中を運動する電荷", ("運動量",))
    assert suggestions == []
    assert suspicious == set()


def test_suggest_lexicon_correction_within_distance():
    assert suggest_lexicon_correction("ローレンッ力", ["ローレンツ力", "運動量"]) == "ローレンツ力"


def test_suggest_lexicon_correction_too_far():
    assert suggest_lexicon_correction("全く違う単語です", ["ローレンツ力"]) is None


# --- verify_block 全体 -------------------------------------------------------


def _block(text: str, kind: BlockKind = BlockKind.BODY_TEXT, conf: float = 0.95) -> Block:
    return Block(block_id="b1", kind=kind, bbox=(0, 0, 100, 20), reading_order=1,
                 text=text if kind != BlockKind.MATH_BLOCK else "",
                 latex=text if kind == BlockKind.MATH_BLOCK else "",
                 confidence=conf)


def test_verify_block_confirms_clean_text():
    """§11.3: 問題がなければ自動採用(pendingにしない)。"""
    block = verify_block(_block("力の向きはフレミングの左手の法則で決まる。"), engine_texts=None)
    assert block.review_status == "confirmed"


def test_verify_block_flags_lexicon_misread():
    block = verify_block(_block("この力をローレンツカとよぶ"), lexicon=("ローレンツ力",))
    assert block.review_status == "pending"
    assert any(i.code == "LEXICON_SUGGEST" for i in block.issues)


def test_verify_block_math_fallback_penalty():
    """REQ-OCR-02: 数式専用エンジンが無い場合は0.7倍し、必ず人の確認に回す。"""
    specialist = verify_block(_block("F = ma", BlockKind.MATH_BLOCK), math_engine_is_specialist=True)
    fallback = verify_block(_block("F = ma", BlockKind.MATH_BLOCK), math_engine_is_specialist=False)
    assert fallback.confidence < specialist.confidence
    assert fallback.review_status == "pending"
    assert any(i.code == "MATH_ENGINE_FALLBACK" for i in fallback.issues)


def test_verify_block_does_not_overwrite_user_edit():
    """§11.7.4 REQ-UI-09: 人間が修正したブロックは上書きしない。"""
    block = _block("人が直したテキスト")
    block.review_status = "edited"
    block.edited_by = "user"
    result = verify_block(block, lexicon=("ローレンツ力",))
    assert result.review_status == "edited"


def test_verify_block_ensemble_disagreement_is_flagged():
    block = verify_block(_block("運動方程式"), engine_texts=["運動方裎式"])
    assert any(i.code == "ENGINE_DISAGREE" for i in block.issues)
