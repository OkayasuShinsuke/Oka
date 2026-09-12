"""取り込み・リネーム・ページ抜け検出(仕様書 §7)。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import exifread

# iPhone(HEIC)・ミラーレス一眼RAW(ARW等)の両方に対応(§6.4.6, §7.3)
SUPPORTED_EXTENSIONS = ["heic", "arw", "cr2", "cr3", "nef", "raf", "jpg", "jpeg"]


def _iter_source_files(src_dir: Path) -> list[Path]:
    """対応拡張子のファイルを、大小文字を問わず全て集める。"""
    paths: list[Path] = []
    for ext in SUPPORTED_EXTENSIONS:
        paths.extend(src_dir.glob(f"*.{ext}"))
        paths.extend(src_dir.glob(f"*.{ext.upper()}"))
    return paths


def _read_shot_time(path: Path) -> datetime | None:
    """EXIFのDateTimeOriginalを読む。読めない場合はファイルの更新日時にフォールバックする。"""
    try:
        with path.open("rb") as f:
            tags = exifread.process_file(f, stop_tag="EXIF DateTimeOriginal", details=False)
        raw = str(tags.get("EXIF DateTimeOriginal", ""))
        if raw:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def rename_by_shot_time(src_dir: Path, book_id: str, dst_dir: Path) -> list[Path]:
    """撮影時刻の昇順にソートし、{book_id}_p0001.<ext> 形式にリネームして複製する(REQ-IMPL-01)。

    ポイント: ファイル名(IMG_xxxx)ではなくEXIFの撮影日時で並べる。
             連写でファイル番号が飛ぶことがあるため。
    戻り値: 生成したファイルパスのリスト(撮影順)。
    """
    dst_dir.mkdir(parents=True, exist_ok=True)

    entries = [(_read_shot_time(p), p) for p in _iter_source_files(src_dir)]
    entries.sort(key=lambda pair: pair[0])  # 撮影日時で昇順ソート

    created: list[Path] = []
    for page_number, (_, path) in enumerate(entries, start=1):
        new_name = f"{book_id}_p{page_number:04d}{path.suffix.lower()}"
        dst_path = dst_dir / new_name
        dst_path.write_bytes(path.read_bytes())  # 元は残す(非破壊)
        created.append(dst_path)
    return created


def detect_page_gaps(nombres: list[int | None]) -> list[str]:
    """OCRしたノンブル列から、抜け・重複・逆順を検出する(§7.5 REQ-PAGECHK-01)。

    引数 nombres : ファイル名順に並んだノンブル。読めなければ None。
    戻り値       : 人間向けの警告メッセージのリスト。
    """
    issues: list[str] = []
    prev_value: int | None = None
    prev_index: int | None = None

    for index, value in enumerate(nombres):
        if value is None:
            issues.append(f"p{index + 1:04d}: ノンブルを読めませんでした(目視確認)")
            continue
        if prev_value is not None:
            diff = value - prev_value
            if diff == 0:
                issues.append(f"p{prev_index + 1:04d}-p{index + 1:04d}: 同じページを2回撮った可能性")
            elif diff < 0:
                issues.append(f"p{index + 1:04d}: ページが逆行({prev_value}→{value})")
            elif diff > 1:
                missing = diff - 1
                issues.append(f"p{index + 1:04d}: {missing}ページ抜けの可能性({prev_value}→{value})")
        prev_value = value
        prev_index = index

    return issues


@dataclass
class RetakeResult:
    """撮り直し(§7.4)の結果。"""

    trashed_path: Path
    new_path: Path


def retake(target_path: Path, new_image_bytes: bytes, trash_dir: Path) -> RetakeResult:
    """直前のページを撮り直す。counterは進めず、同名ファイルを上書きする(REQ-RETAKE-01/02)。"""
    trash_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    trashed_path = trash_dir / f"{target_path.stem}_{timestamp}{target_path.suffix}"
    if target_path.exists():
        trashed_path.write_bytes(target_path.read_bytes())
    target_path.write_bytes(new_image_bytes)
    return RetakeResult(trashed_path=trashed_path, new_path=target_path)
