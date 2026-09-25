"""実写真ベンチマーク(自動改善ループの採点係)のテスト。"""
import json

from tscan.bench import best_substring_distance, compare, load_manifest, score_snippets


def _result(overall: float, sets: dict[str, float], engines=("tesseract",), ref_chars=100, split="train") -> dict:
    return {
        "overall": {"cer": overall, "ref_chars": ref_chars, "review_rate": 0.0, "failures": 0},
        "sets": {name: {"cer": cer, "photos": 1} for name, cer in sets.items()},
        "photos": [],
        "meta": {"split": split, "engines": list(engines)},
    }


# -- 照合 -------------------------------------------------------------------


def test_substring_found_exactly_inside_longer_text():
    dist, matched = best_substring_distance("電磁誘導", "前の文。電磁誘導の法則。後の文。")
    assert dist == 0
    assert matched == "電磁誘導"


def test_substring_counts_one_kanji_confusion():
    dist, matched = best_substring_distance("誘電体", "…この計電体は…")
    assert dist == 1
    assert matched == "計電体"


def test_substring_empty_cases():
    assert best_substring_distance("", "何か") == (0, "")
    assert best_substring_distance("abc", "") == (3, "")


def test_score_snippets_ignores_width_and_spaces():
    rows = score_snippets(["Ｆ ＝ ｍａ"], "運動方程式はF=maである")
    assert rows[0]["errors"] == 0
    assert rows[0]["ref_chars"] == len("F=ma")


def test_score_snippets_errors_capped_at_length():
    rows = score_snippets(["あいう"], "")
    assert rows[0]["errors"] == 3


# -- 判定 -------------------------------------------------------------------


def test_compare_accepts_clear_improvement():
    verdict, _ = compare(_result(30.0, {"a": 30.0, "b": 30.0}), _result(32.0, {"a": 33.0, "b": 31.0}))
    assert verdict == "accept"


def test_compare_rejects_when_one_set_gets_worse():
    # 全体は良くなっても、セット b が2ポイント悪化したら不採用
    verdict, lines = compare(_result(28.0, {"a": 20.0, "b": 36.0}), _result(32.0, {"a": 30.0, "b": 34.0}))
    assert verdict == "reject"
    assert any("b:" in line for line in lines)


def test_compare_neutral_within_noise():
    verdict, _ = compare(_result(31.8, {"a": 31.8}), _result(32.0, {"a": 32.0}))
    assert verdict == "neutral"


def test_compare_rejects_overall_worsening():
    verdict, _ = compare(_result(33.0, {"a": 33.0}), _result(32.0, {"a": 32.0}), max_set_loss=5.0)
    assert verdict == "reject"


def test_compare_incomparable_when_engines_differ():
    verdict, _ = compare(
        _result(10.0, {"a": 10.0}, engines=("apple_vision", "tesseract")), _result(30.0, {"a": 30.0})
    )
    assert verdict == "incomparable"


def test_compare_incomparable_when_snippets_changed():
    verdict, _ = compare(_result(10.0, {"a": 10.0}, ref_chars=90), _result(30.0, {"a": 30.0}))
    assert verdict == "incomparable"


# -- manifest ---------------------------------------------------------------


def test_load_manifest_resolves_paths_and_skips_empty(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "photos": [
                    {"file": "iphone/a.jpg", "set": "iphone", "split": "holdout", "snippets": ["本文", " "]},
                    {"file": "iphone/b.jpg", "snippets": ["  "]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases = load_manifest(tmp_path / "manifest.json")
    assert len(cases) == 1
    assert cases[0].file == (tmp_path / "iphone" / "a.jpg").resolve()
    assert cases[0].split == "holdout"
    assert cases[0].snippets == ["本文"]
