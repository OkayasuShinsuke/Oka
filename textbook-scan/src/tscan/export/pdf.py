"""検索可能PDF出力(仕様書 §13.1)。

現時点では画像レイヤーのみを実装している(img2pdfで結合)。
透明テキストレイヤーの重ね合わせ(§13.1: 不可視テキスト, `Tr 3`)は
`ocrmypdf --sidecar` 等との連携が必要で、実際のOCR結果(§9)が
本実装で有効化されてから追加する(§9.2のApple Vision/yomitoku/Mathpixは
macOS実機・API키・重い依存が必要なため、現時点ではスタブ)。
"""
from __future__ import annotations

from pathlib import Path


def build_image_pdf(page_image_paths: list[Path], out_path: Path) -> Path:
    """ページ画像を結合し、画像のみのPDFを作る(テキストレイヤーは未実装)。"""
    try:
        import img2pdf
    except ImportError as e:
        raise RuntimeError("img2pdfが必要です。`pip install img2pdf` を実行してください(§14.2)。") from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(img2pdf.convert([str(p) for p in page_image_paths]))
    return out_path
