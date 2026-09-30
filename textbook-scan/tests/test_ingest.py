import os
import time
from pathlib import Path

from tscan.ingest import detect_page_gaps, rename_by_shot_time


def test_detect_page_gaps_no_issues():
    assert detect_page_gaps([148, 149, 150, 151, 152]) == []


def test_detect_page_gaps_missing():
    issues = detect_page_gaps([148, 149, 152, 153])
    assert len(issues) == 1
    assert "抜け" in issues[0]


def test_detect_page_gaps_duplicate():
    issues = detect_page_gaps([148, 149, 149, 150])
    assert any("同じページ" in i for i in issues)


def test_detect_page_gaps_reversed():
    issues = detect_page_gaps([148, 150, 149])
    assert any("逆行" in i for i in issues)


def test_detect_page_gaps_unreadable():
    issues = detect_page_gaps([148, None, 150])
    assert any("読めません" in i for i in issues)


def test_rename_by_shot_time_orders_by_mtime(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()

    # EXIFなしのJPEGを3枚、更新時刻の順にmtime差をつけて作る(EXIF読めない場合はmtimeにフォールバック)
    paths = []
    for i, name in enumerate(["c.jpg", "a.jpg", "b.jpg"]):
        p = src / name
        p.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-content")
        os.utime(p, (time.time() + i, time.time() + i))
        paths.append(p)

    created = rename_by_shot_time(src, "testbook", dst)
    assert len(created) == 3
    assert created[0].name == "testbook_p0001.jpg"
    assert created[0].read_bytes() == paths[0].read_bytes()


def test_detect_page_gaps_suspicious_repeat_flagged_as_misread():
    issues = detect_page_gaps([163, 165, 165, 165, 170])
    assert len(issues) == 2
    assert any("誤読" in i for i in issues)
    assert any("抜け" in i for i in issues)


def test_detect_page_gaps_two_repeats_still_duplicate():
    issues = detect_page_gaps([148, 149, 149, 150])
    assert any("同じページ" in i for i in issues)
    assert not any("誤読" in i for i in issues)
