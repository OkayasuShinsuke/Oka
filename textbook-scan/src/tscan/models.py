"""データモデル(仕様書 §12.2)。

OCR結果・ページ情報を表すデータクラス群。§12.1のJSONスキーマと1対1で対応する。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class BlockKind(str, Enum):
    """領域の種別(§10.1)。文字列としても扱えるEnum。"""

    BODY_TEXT = "body_text"
    MATH_BLOCK = "math_block"
    MATH_INLINE = "math_inline"
    FIGURE = "figure"
    CAPTION = "caption"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"
    RUBY = "ruby"
    NOTE = "note"


@dataclass
class Issue:
    """検証で見つかった問題1件(§11)。"""

    code: str  # "ENGINE_DISAGREE" 等の機械可読コード
    detail: str
    severity: str  # "low" | "medium" | "high"


@dataclass
class Block:
    """ページ内の1領域(§12.1)。"""

    block_id: str
    kind: BlockKind
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    reading_order: int
    text: str = ""
    latex: str = ""
    equation_number: str = ""  # 「(3.14)」等。数式ブロックのみ(§12.1)
    confidence: float = 0.0
    issues: list[Issue] = field(default_factory=list)
    review_status: str = "pending"  # pending | confirmed | edited | skipped(§11.7.4)
    edited_by: str | None = None  # None=自動採用 / "user"=手動修正(REQ-UI-09)
    edited_at: str | None = None

    @property
    def needs_review(self) -> bool:
        """レビューが必要かを判定する(§11.5の閾値)。"""
        return self.confidence < 0.80 or any(i.severity == "high" for i in self.issues)


@dataclass
class Page:
    """1ページ分のデータ(§7.7.2/§12.1)。"""

    page_id: str  # UUID。挿入・削除・並べ替えでも不変(REQ-PAGEMGMT-01)
    order_key: float  # 並び順キー(REQ-PAGEMGMT-02)
    image_path: str
    layout: str = "horizontal"  # horizontal | vertical(§10.3)
    printed_number: int | None = None
    deleted: bool = False  # 論理削除フラグ(§7.7.3)
    warnings: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    # 見開き写真のどちら側か: "left" | "right" | "single"。None は未判定(§8.4 見開き分割)。
    # 見開き1枚から左右2つの Page が作られ、両方が同じ image_path を指す
    spread_side: str | None = None

    @property
    def needs_review(self) -> bool:
        return any(b.needs_review for b in self.blocks)

    @staticmethod
    def new(image_path: str, order_key: float) -> "Page":
        return Page(page_id=str(uuid.uuid4()), order_key=order_key, image_path=image_path)


@dataclass
class Book:
    """1冊分のデータ(§12.1トップレベル)。"""

    book_id: str
    title: str = ""
    created_at: str = ""
    source_device: str = ""
    capture_mode: str = ""
    pages: list[Page] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "Book":
        raw = json.loads(path.read_text(encoding="utf-8"))
        pages = []
        for p in raw.get("pages", []):
            blocks = [
                Block(
                    **{**b, "kind": BlockKind(b["kind"]), "issues": [Issue(**i) for i in b.get("issues", [])]}
                )
                for b in p.get("blocks", [])
            ]
            pages.append(Page(**{**{k: v for k, v in p.items() if k != "blocks"}, "blocks": blocks}))
        return Book(
            book_id=raw["book_id"],
            title=raw.get("title", ""),
            created_at=raw.get("created_at", ""),
            source_device=raw.get("source_device", ""),
            capture_mode=raw.get("capture_mode", ""),
            pages=pages,
        )


def compute_display_page_index(pages: list[Page]) -> dict[str, int]:
    """order_keyを昇順ソートし、削除されていないページに表示用の連番を振る。

    重要: この結果はJSONに保存しない(REQ-PAGEMGMT-05)。表示のたびに計算する。
    """
    visible = sorted((p for p in pages if not p.deleted), key=lambda p: p.order_key)
    return {p.page_id: index for index, p in enumerate(visible, start=1)}


def insert_between(before_key: float, after_key: float) -> float:
    """2つのorder_keyの中間値を返す(§7.7.2 フラクショナル・インデックス方式)。

    他のページのorder_keyには一切触れない。
    """
    return (before_key + after_key) / 2
