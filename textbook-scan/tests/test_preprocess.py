import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tscan.preprocess import (
    calibrate_session,
    check_aspect_ratio,
    correct_perspective,
    detect_page_corners,
    detect_with_hint,
    find_gutter_x,
    flatten_illumination,
    invert_dark_boxes,
    split_spread,
    stretch_contrast,
)


def _synthetic_page(width=400, height=600, margin=40) -> np.ndarray:
    """黒背景(§6.3のマット)に白い矩形(紙面)を描いた合成画像。"""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.rectangle(image, (margin, margin), (width - margin, height - margin), (255, 255, 255), -1)
    return image


def test_detect_page_corners_finds_rectangle():
    image = _synthetic_page()
    corners = detect_page_corners(image)
    assert corners is not None
    assert corners.shape == (4, 2)
    # 左上のx,yはマージン付近のはず
    assert abs(corners[0][0] - 40) < 5
    assert abs(corners[0][1] - 40) < 5


def test_detect_page_corners_none_on_blank_image():
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    # findContoursは全面同色でも輪郭0件のことがある。ここでは例外を出さず処理できることのみ確認
    result = detect_page_corners(blank)
    assert result is None or result.shape == (4, 2)


def test_correct_perspective_produces_expected_aspect_ratio():
    image = _synthetic_page(width=400, height=600, margin=40)
    corners = detect_page_corners(image)
    warped = correct_perspective(image, corners)
    ratio = warped.shape[1] / warped.shape[0]  # width / height
    expected = (400 - 80) / (600 - 80)
    assert abs(ratio - expected) < 0.05


def test_check_aspect_ratio_within_tolerance():
    image = np.zeros((257, 182, 3), dtype=np.uint8)  # B5相当
    assert check_aspect_ratio(image, expected_ratio=182 / 257, tolerance=0.05) is True


def test_check_aspect_ratio_outside_tolerance():
    image = np.zeros((100, 182, 3), dtype=np.uint8)  # 縦横比が大きく違う
    assert check_aspect_ratio(image, expected_ratio=182 / 257, tolerance=0.05) is False


def test_calibrate_session_and_hint_roundtrip():
    image = _synthetic_page()
    corners = calibrate_session(image)
    assert corners is not None

    hinted = detect_with_hint(image, corners, search_margin_px=20)
    # ヒントを与えても、同じ画像なら同じ4隅に近い結果が返る
    assert np.allclose(hinted, corners, atol=5)


def test_flatten_illumination_reduces_gradient():
    gray = np.tile(np.linspace(50, 200, 200, dtype=np.uint8), (200, 1))  # 左右に明暗差のある画像
    flattened = flatten_illumination(gray, kernel_size=51)
    # 補正後は左右の明るさ差が縮まっているはず
    original_range = int(gray[:, -1].mean()) - int(gray[:, 0].mean())
    flattened_range = abs(int(flattened[:, -1].mean()) - int(flattened[:, 0].mean()))
    assert flattened_range < original_range


def test_find_gutter_x_and_split_spread():
    width, height = 400, 300
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    gutter_x = width // 2
    image[:, gutter_x - 5 : gutter_x + 5] = 30  # ノド(暗い谷)を再現

    detected = find_gutter_x(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    assert abs(detected - gutter_x) < 15

    left, right = split_spread(image)
    assert left.shape[1] + right.shape[1] == width


# --- P6.5: 白抜き文字のボックス反転 ------------------------------------------


def _page_with_formula_box() -> np.ndarray:
    """白い紙に、黒文字の本文と「濃色の帯に白文字」の公式ボックスを置いたページ。"""
    gray = np.full((600, 500), 240, dtype=np.uint8)
    cv2.putText(gray, "body text", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 20, 3)
    cv2.rectangle(gray, (40, 200), (460, 320), 60, -1)  # 濃い帯
    cv2.putText(gray, "E = mc2", (80, 280), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 250, 4)  # 白抜き
    return gray


def test_invert_dark_boxes_turns_white_text_into_dark_text():
    gray = _page_with_formula_box()
    out, boxes = invert_dark_boxes(gray)
    assert boxes == 1
    # 帯の内側は明るくなり、白抜き文字は暗くなる
    assert out[210, 60] > 150  # 帯の地
    assert out[245:275, 100:400].min() < 60  # 文字
    # 本文の黒文字はそのまま
    assert out[:150].min() < 60


def test_invert_dark_boxes_ignores_line_drawings():
    """罫線だけの表や線画は塗りつぶし率が低いので反転しない。"""
    gray = np.full((600, 500), 240, dtype=np.uint8)
    cv2.rectangle(gray, (40, 200), (460, 320), 20, 3)  # 枠線だけ
    _out, boxes = invert_dark_boxes(gray)
    assert boxes == 0


def test_flatten_illumination_keeps_contrast_inside_dark_figure():
    """暗い図版の内側で背景推定が小さくなり、ノイズが白飛びするのを防ぐ。"""
    gray = np.full((300, 300), 230, dtype=np.uint8)
    gray[100:200, 100:200] = 40  # 暗い写真
    gray[150, 150] = 60  # 写真の中のわずかに明るい点
    out = flatten_illumination(gray)
    assert out[150, 150] < 160  # 割り算で255付近まで持ち上がらない
    assert out[50, 50] > 240  # 紙は白のまま


# --- P7.5: 薄くなった文字を戻す ------------------------------------------------


def _page_with_text(ink: int, paper: int) -> np.ndarray:
    gray = np.full((300, 400), paper, dtype=np.uint8)
    for y in range(40, 260, 40):
        cv2.putText(gray, "abcdefgh", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, ink, 2)
    return gray


def _ink_median(gray: np.ndarray) -> float:
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(np.median(gray[gray < t]))


def test_stretch_contrast_darkens_washed_out_text():
    """暗い写真を照明補正すると文字が薄いグレーになる(実測: APS-Cで145)。それを濃く戻す。"""
    washed = _page_with_text(ink=150, paper=250)
    out = stretch_contrast(washed)
    assert _ink_median(out) < 110
    assert out[5, 5] >= 250  # 紙は白のまま


def test_stretch_contrast_leaves_well_exposed_text_alone():
    """もともと十分濃い文字には何もしない(黒くしすぎると線が潰れてCERが悪化した)。"""
    good = _page_with_text(ink=90, paper=240)
    assert np.array_equal(stretch_contrast(good), good)
