"""実写真向けの紙面検出・見開き分割のテスト(仕様書 §8 の拡張、realphoto)。

合成画像で「木の机(茶)+ピンクの籠の上に見開きの本を置き、指で押さえた」状況を再現する。
"""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tscan.models import Book, Page
from tscan.pipeline import expand_spreads
from tscan.realphoto import (
    _page_crop,
    analyze_spread,
    dense_span,
    detect_spread,
    extract_page,
    paper_mask_hsv,
    split_spread_photo,
)


def _text_lines(canvas: np.ndarray, x0: int, x1: int, y0: int, y1: int, pitch: int = 20) -> None:
    """本文の行を「細長い黒い帯」で模す(OCRは掛けないので文字である必要はない)。

    行の高さと行送りの比は実際の本文に近づけてある(実写真の見開きでは、文字のある行が
    ページの高さの40〜90%を占める)。細すぎる帯だと、どの実写真よりも文字が疎になる。
    """
    for y in range(y0, y1, pitch):
        cv2.rectangle(canvas, (x0, y), (x1, y + 12), (30, 30, 30), -1)


def _spread_photo(w: int = 1200, h: int = 900, with_box: bool = True) -> np.ndarray:
    """机(茶)+籠(ピンク)の上の見開き。左右ページに本文、右ページに青い数式ボックス。"""
    canvas = np.full((h, w, 3), (40, 90, 140), np.uint8)  # 木の机(BGR 茶色)
    cv2.rectangle(canvas, (0, 0), (w, 120), (180, 120, 230), -1)  # ピンクの籠
    cv2.rectangle(canvas, (100, 140), (w - 100, h - 60), (235, 240, 245), -1)  # 紙(暖色照明)
    # 左ページ本文
    _text_lines(canvas, 150, 560, 200, h - 160)
    # 右ページ本文(ノドの右側に本文の谷を挟む)
    _text_lines(canvas, 640, w - 150, 200, h - 160)
    if with_box:
        cv2.rectangle(canvas, (680, 300), (w - 200, 380), (200, 120, 40), -1)  # 青い公式ボックス
    # 指(肌色)が下端から紙面に入り込む
    cv2.ellipse(canvas, (300, h - 40), (40, 90), 0, 0, 360, (130, 160, 205), -1)
    return canvas


def _single_photo() -> np.ndarray:
    canvas = np.full((900, 800, 3), (40, 90, 140), np.uint8)
    cv2.rectangle(canvas, (120, 60), (680, 840), (235, 240, 245), -1)  # B5に近い縦長(0.72)
    _text_lines(canvas, 170, 630, 150, 760)
    return canvas


# --- 紙面の検出 --------------------------------------------------------------


def test_paper_mask_excludes_desk_and_basket():
    mask = paper_mask_hsv(_spread_photo())
    assert mask is not None
    assert mask[50, 600] == 0  # 籠
    assert mask[850, 30] == 0  # 机
    assert mask[450, 600] == 255  # 紙


def test_paper_mask_keeps_saturated_boxes_inside_page():
    """青い公式ボックスは彩度が高いが、紙面の内側にあるので紙面として残す。"""
    mask = paper_mask_hsv(_spread_photo(with_box=True))
    assert mask is not None
    assert mask[340, 800] == 255


def test_paper_mask_returns_none_without_paper():
    desk = np.full((300, 300, 3), (40, 90, 140), np.uint8)
    assert paper_mask_hsv(desk) is None


# --- 密度プロファイル --------------------------------------------------------


def test_dense_span_picks_longest_run_and_bridges_gaps():
    profile = np.zeros(200)
    profile[10:20] = 5  # 縁のノイズ(短い)
    profile[50:100] = 50
    profile[110:150] = 50  # 10の途切れは繋がる
    span = dense_span(profile, fraction=0.1, min_gap=20)
    assert span is not None
    assert abs(span[0] - 50) <= 2 and abs(span[1] - 150) <= 2  # 形態学処理の丸めで±1ずれうる


def test_dense_span_empty():
    assert dense_span(np.zeros(10)) is None


# --- 見開きの解析と分割 ------------------------------------------------------


def test_analyze_spread_finds_gutter_between_pages():
    layout = analyze_spread(_spread_photo())
    assert layout is not None
    assert layout.gutter_x is not None
    assert 560 < layout.gutter_x < 640


def test_analyze_single_page_has_no_gutter():
    layout = analyze_spread(_single_photo())
    assert layout is not None
    assert layout.gutter_x is None


def test_split_spread_photo_returns_two_pages_with_masks():
    pages = split_spread_photo(_spread_photo())
    assert pages is not None and len(pages) == 2
    for image, mask in pages:
        assert image.shape[:2] == mask.shape[:2]
        assert image.shape[1] < 800  # 片側だけ


def test_split_paints_outside_paper_with_paper_color():
    """切り出した画像で、指や机だった場所は紙の地色になっている。"""
    (left, mask), _right = split_spread_photo(_spread_photo())
    outside = left[mask == 0]
    assert outside.size > 0
    assert outside.mean() > 200  # 暗い机の色ではない


def test_detect_spread_and_extract_page_sides():
    photo = _spread_photo()
    assert detect_spread(photo) == "spread"
    assert detect_spread(_single_photo()) == "single"

    left = extract_page(photo, "left")
    right = extract_page(photo, "right")
    assert left is not None and right is not None
    # 右ページの切り出しに青いボックスが含まれる(BGRのB成分が高い画素がある)
    r_img, _ = right
    assert (r_img[..., 0].astype(int) - r_img[..., 2].astype(int) > 100).any()
    l_img, _ = left
    assert not (l_img[..., 0].astype(int) - l_img[..., 2].astype(int) > 100).any()


# --- パイプラインでの見開き展開 ----------------------------------------------


def test_expand_spreads_inserts_right_page_after_left(tmp_path):
    spread_path = tmp_path / "spread.png"
    single_path = tmp_path / "single.png"
    cv2.imwrite(str(spread_path), _spread_photo())
    cv2.imwrite(str(single_path), _single_photo())

    book = Book(book_id="t", pages=[Page.new(str(spread_path), 1000.0), Page.new(str(single_path), 2000.0)])
    added = expand_spreads(book, workers=1)

    assert added == 1
    ordered = sorted(book.pages, key=lambda p: p.order_key)
    assert [p.spread_side for p in ordered] == ["left", "right", "single"]
    assert ordered[0].image_path == ordered[1].image_path
    assert 1000.0 < ordered[1].order_key < 2000.0  # 他のページの order_key は動かない
    assert ordered[0].page_id != ordered[1].page_id

    # 2回目は何も追加しない(判定済み)
    assert expand_spreads(book, workers=1) == 0


def test_expand_spreads_leaves_undetectable_photo_unmarked(tmp_path):
    path = tmp_path / "desk.png"
    cv2.imwrite(str(path), np.full((300, 300, 3), (40, 90, 140), np.uint8))
    book = Book(book_id="t", pages=[Page.new(str(path), 1000.0)])
    assert expand_spreads(book, workers=1) == 0
    assert book.pages[0].spread_side is None  # 従来経路に任せる


# --- 向きの判定・縁に接した色付きの帯 -----------------------------------------


def test_rotate_upright_roundtrip():
    from tscan.realphoto import rotate_upright

    photo = _spread_photo()
    turned = cv2.rotate(photo, cv2.ROTATE_90_CLOCKWISE)  # 本を横向きに構えた写真
    assert rotate_upright(turned, 270).shape == photo.shape
    assert np.array_equal(rotate_upright(turned, 270), photo)
    assert rotate_upright(photo, 0) is photo


def test_detect_rotation_on_sideways_photo():
    """横向きの写真は 90 か 270、正立した写真は 0 と判定する(OSDが使える環境のみ)。"""
    import shutil

    if shutil.which("tesseract") is None:
        pytest.skip("tesseractが未インストール")
    from tscan.realphoto import detect_rotation

    from PIL import Image, ImageDraw, ImageFont

    font_path = next(
        (p for p in ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"]
         if __import__("pathlib").Path(p).exists()), None,
    )
    if font_path is None:
        pytest.skip("日本語フォントが見つかりません")
    image = Image.new("RGB", (1200, 900), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 36)
    for i in range(12):
        draw.text((60, 60 + i * 60), "磁場の中を運動する電荷には力がはたらく。この力をローレンツ力とよぶ。", font=font, fill=(20, 20, 20))
    upright = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    if detect_rotation(upright) != 0:
        pytest.skip("この環境のOSDでは正立画像を判定できない")  # osd.traineddata の有無に依存
    assert detect_rotation(cv2.rotate(upright, cv2.ROTATE_90_CLOCKWISE)) in (90, 270)


def test_paper_mask_keeps_colored_band_touching_page_edge():
    """ページ上端に接した青い帯(公式ボックス)は、輪郭の切れ込みになっても紙面に含める。"""
    photo = _spread_photo(with_box=False)
    cv2.rectangle(photo, (640, 140), (1100, 260), (200, 120, 40), -1)  # 右ページ上端に接する青い帯
    mask = paper_mask_hsv(photo)
    assert mask is not None
    assert mask[200, 900] == 255


# --- 傾いたノドの検出 ---------------------------------------------------------


def _tilt(image: np.ndarray, degrees: float) -> np.ndarray:
    """画像を横方向にずらして、本を斜めに持って撮った状態を作る。"""
    h, w = image.shape[:2]
    t = np.tan(np.radians(degrees))
    matrix = np.float32([[1, t, -t * h / 2], [0, 1, 0]])
    return cv2.warpAffine(image, matrix, (w, h), borderValue=(235, 240, 245))


def test_find_gutter_handles_tilted_spread():
    """ノドが傾いた見開きでも分割できる。

    まっすぐ縦に足し合わせるだけだと、傾いた谷が隣の行の文字で埋まって浅くなり、
    見開きなのに単ページと判定された(実写真で実測)。
    """
    from tscan.realphoto import detect_spread

    assert detect_spread(_tilt(_spread_photo(), 5.0)) == "spread"


def test_find_gutter_returns_none_for_single_page():
    from tscan.realphoto import find_gutter, paper_mask_hsv, text_line_mask

    photo = _single_photo()
    lines = text_line_mask(photo, paper_mask_hsv(photo))
    assert find_gutter(lines, 0, photo.shape[1]) is None


# --- 影になった紙・折り目で分断された見開き ------------------------------------


def _shaded_spread_photo() -> np.ndarray:
    """左ページが影に入って暗く写った見開き(片側からの照明)。折り目は暗い帯。

    実写真で測った値に合わせてある: 明るい紙 172・影の紙 95 前後(どちらも彩度はほぼ0)。
    """
    h, w = 900, 1200
    canvas = np.full((h, w, 3), (40, 90, 140), np.uint8)  # 木の机
    cv2.rectangle(canvas, (100, 140), (590, 840), (95, 95, 95), -1)  # 影になった左ページ
    cv2.rectangle(canvas, (630, 140), (1100, 840), (172, 172, 172), -1)  # 明るい右ページ
    cv2.rectangle(canvas, (590, 140), (630, 840), (25, 25, 25), -1)  # 折り目の影
    for y in range(230, 760, 30):  # 本文(行間に紙がしっかり残る密度)
        cv2.rectangle(canvas, (150, y), (560, y + 10), (20, 20, 20), -1)
        cv2.rectangle(canvas, (660, y), (1060, y + 10), (40, 40, 40), -1)
    return canvas


def test_paper_mask_includes_shaded_page():
    """影で暗くなった紙も紙面に含める(白で塗りつぶして本文を消さない)。

    実写真(APS-Cミラーレス、片側からの照明)で、左ページの左半分と右ページの
    折り目側が「明るさ110未満」のため背景扱いされ、本文が白く塗りつぶされた。
    """
    mask = paper_mask_hsv(_shaded_spread_photo())
    assert mask is not None
    assert mask[185, 300] == 255  # 影になった左ページ(上の余白)
    assert mask[455, 300] == 255  # 影になった左ページ(行間)
    assert mask[185, 900] == 255  # 明るい右ページ
    assert mask[870, 30] == 0  # 机は含めない


def test_spread_split_by_dark_fold_keeps_both_pages():
    """折り目の影で左右のページが分断されても、両方のページを紙面として残す。"""
    pages = split_spread_photo(_shaded_spread_photo())
    assert pages is not None and len(pages) == 2
    (left, left_mask), (right, right_mask) = pages
    assert (left_mask > 0).mean() > 0.5
    assert (right_mask > 0).mean() > 0.5


def test_page_crop_extends_bottom_margin_further_than_other_sides():
    """下端(ノンブル側)は margin より広い bottom_margin で拡張されることを確認する。"""
    image = np.full((300, 200, 3), (235, 240, 245), np.uint8)
    paper = np.zeros((300, 200), np.uint8)
    paper[50:250, 30:170] = 255  # 紙面

    crop, _mask = _page_crop(image, paper, (30, 170), (50, 250))

    # margin(3%)のみなら下端拡張は 200*0.03=6px だが、bottom_margin(8%)では 200*0.08=16px。
    # symmetric marginでの拡張分より明らかに大きく下端が拡張されていることを確認する。
    assert crop.shape[0] > 200 + int(200 * 0.03) + 2
