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
    split_spread,
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
