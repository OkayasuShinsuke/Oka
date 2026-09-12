import pytest

from tscan.verify import (
    check_brace_balance,
    combine_confidence,
    cross_check,
    majority_vote,
    semantic_check,
    suggest_lexicon_correction,
)


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


def test_cross_check_identical():
    text, sim, diffs = cross_check("F = ma", "F = ma")
    assert sim == 1.0
    assert diffs == []


def test_cross_check_disagreement():
    text, sim, diffs = cross_check("F = ma", "F = rna")
    assert sim < 1.0
    assert len(diffs) > 0


def test_majority_vote_unanimous():
    text, conf, needs_review = majority_vote("F=ma", "F=ma", "F=ma")
    assert (text, conf, needs_review) == ("F=ma", 0.995, False)


def test_majority_vote_two_to_one():
    text, conf, needs_review = majority_vote("F=ma", "F=ma", "F=rna")
    assert text == "F=ma"
    assert needs_review is False


def test_majority_vote_all_disagree():
    text, conf, needs_review = majority_vote("A", "B", "C")
    assert needs_review is True
    assert conf < 0.5


def test_combine_confidence_low_signal_dominates():
    high_all = combine_confidence(0.99, 0.99, 0.99, 0.99)
    one_low = combine_confidence(0.99, 0.99, 0.99, 0.05)
    assert one_low < high_all
    # 幾何平均は算術平均よりも低い値に強く引っ張られる
    arithmetic = (0.99 * 3 + 0.05) / 4
    assert one_low < arithmetic


def test_semantic_check_valid_expression():
    ok, warnings = semantic_check(r"\frac{1}{2}mv^2")
    assert ok is True


def test_semantic_check_suspicious_symbol():
    ok, warnings = semantic_check("l + 1")
    assert ok is True
    assert any("l" in w for w in warnings)


def test_suggest_lexicon_correction_within_distance():
    lexicon = ["ローレンツ力", "運動量", "微分"]
    assert suggest_lexicon_correction("ローレンッ力", lexicon) == "ローレンツ力"


def test_suggest_lexicon_correction_too_far():
    lexicon = ["ローレンツ力"]
    assert suggest_lexicon_correction("全く違う単語です", lexicon) is None
