"""ブック型スキャナ相当の補正のテスト(湾曲補正・指の除去・背景消去)。"""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tscan.enhance import (
    clean_background,
    dewarp_page,
    enhance_book_photo,
    estimate_curl,
    estimate_paper_color,
    find_finger_regions,
    page_mask,
    remove_fingers,
    skin_mask,
    trace_page_edges,
)


def _flat_page(w: int = 600, h: int = 800, margin: int = 60) -> np.ndarray:
    """暗い背景の上に置いた平らな紙(カラー)。"""
    canvas = np.full((h, w, 3), 20, dtype=np.uint8)
    canvas[margin : h - margin, margin : w - margin] = 245
    return canvas


def _curved_page(w: int = 600, h: int = 800, margin: int = 60, strength: float = 0.18) -> np.ndarray:
    """ノド側が丸まった紙(上下の端が曲線になる)。"""
    canvas = _flat_page(w, h, margin)
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
    u = xx / (w - 1)
    scale = 1.0 - strength * np.exp(-u / 0.30)
    src_y = (h - 1) / 2 + (yy - (h - 1) / 2) / np.maximum(scale, 0.5)
    return cv2.remap(canvas, xx, src_y.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _add_finger(image: np.ndarray, cx: int, cy: int) -> np.ndarray:
    out = image.copy()
    cv2.ellipse(out, (cx, cy), (28, 80), 0, 0, 360, (130, 160, 205), -1)  # BGR: 肌色
    return out


# --- 紙面の検出 --------------------------------------------------------------


def test_page_mask_finds_paper():
    mask = page_mask(_flat_page())
    assert mask is not None
    assert 0.3 < (mask > 0).mean() < 0.9


def test_page_mask_returns_none_for_dark_image():
    assert page_mask(np.full((200, 200, 3), 10, dtype=np.uint8)) is None


def test_trace_page_edges_on_flat_page():
    traced = trace_page_edges(page_mask(_flat_page()))
    assert traced is not None
    top, bottom, _x0, _x1 = traced
    # 平らな紙なら上端はほぼ一定
    assert float(np.std(top)) < 3.0


def test_trace_page_edges_detects_curve():
    traced = trace_page_edges(page_mask(_curved_page()))
    assert traced is not None
    top, _bottom, _x0, _x1 = traced
    # 湾曲した紙は上端が単調に変化する(端と中央で差が出る)
    assert abs(top[0] - top[-1]) > 10


# --- 湾曲の推定と補正 --------------------------------------------------------


def test_estimate_curl_is_small_for_flat_page():
    assert estimate_curl(_flat_page()) < 0.05


def test_estimate_curl_is_larger_for_curved_page():
    """湾曲度は「弓なりの度合い」を測る指標。単調に変わる湾曲では値自体は小さいが、
    平らな紙より大きくなることを確認する(補正するかの判断は安全弁側が行う)。"""
    assert estimate_curl(_curved_page()) > estimate_curl(_flat_page())


def test_dewarp_straightens_page_edges():
    """湾曲補正後は、紙の上端が直線に近づくこと。"""
    curved = _curved_page()
    before = trace_page_edges(page_mask(curved))
    result = dewarp_page(curved)
    assert result is not None

    after = trace_page_edges(page_mask(result))
    assert after is not None
    # 補正後のほうが上端のばらつきが小さい
    assert float(np.std(after[0])) < float(np.std(before[0]))


def test_dewarp_gives_up_on_unreliable_input():
    """輪郭が信用できない画像では、補正せず None を返す(壊すより何もしない)。"""
    noise = np.random.default_rng(0).integers(0, 255, (300, 300, 3), dtype=np.uint8)
    assert dewarp_page(noise) is None


# --- 指の除去 ----------------------------------------------------------------


def test_skin_mask_detects_skin_color():
    image = _add_finger(_flat_page(), cx=75, cy=400)
    assert (skin_mask(image) > 0).any()


def test_skin_mask_ignores_grayscale_page():
    """文字や紙(無彩色)を肌と誤認しないこと。"""
    assert (skin_mask(_flat_page()) > 0).mean() < 0.01


def test_find_finger_regions_at_border():
    image = _add_finger(_flat_page(), cx=75, cy=400)
    assert len(find_finger_regions(image)) >= 1


def test_find_finger_regions_ignores_center():
    """紙面中央の肌色(図版など)は指として扱わない。"""
    image = _add_finger(_flat_page(), cx=300, cy=400)
    assert find_finger_regions(image) == []


def test_remove_fingers_fills_with_paper_color():
    image = _add_finger(_flat_page(), cx=75, cy=400)
    repaired, count = remove_fingers(image)
    assert count >= 1
    assert (skin_mask(repaired) > 0).sum() < (skin_mask(image) > 0).sum() * 0.1


def test_estimate_paper_color_is_bright():
    color = estimate_paper_color(_flat_page())
    assert int(color.mean()) > 200


# --- 背景消去 ----------------------------------------------------------------


def test_clean_background_whitens_border_but_keeps_text():
    """机(縁に接する暗い領域)は消し、本文(接していない暗い領域)は残す。"""
    image = _flat_page()
    cv2.putText(image, "TEXT", (200, 400), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 8)

    dark_before = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 90).sum()
    cleaned = clean_background(image)
    dark_after = (cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY) < 90).sum()

    assert dark_after < dark_before * 0.5  # 背景は消えた
    assert dark_after > 0  # 文字は残っている


# --- 統合 --------------------------------------------------------------------


def test_enhance_book_photo_reports_what_it_did():
    image = _add_finger(_curved_page(), cx=75, cy=400)
    result, info = enhance_book_photo(image)
    assert info["fingers_removed"] >= 1
    assert result.shape[2] == 3


def test_dewarp_always_pairs_with_background_cleanup():
    """湾曲補正をかけたら背景消去も必ず行う(片方だけだと補正しないより悪化する)。"""
    result, info = enhance_book_photo(_curved_page(), do_background=False)
    if info["dewarped"]:
        assert info["background_cleaned"] is True
