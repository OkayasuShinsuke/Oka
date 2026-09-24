"""実写真ベンチマーク: 自動改善ループの「採点係」(仕様書 §11.8 の実写真版)。

自動改善ループ(docs/improve-loop.md)では、エージェントが前処理やOCRの修正を提案し、
このベンチマークの点数で採否を決める。採点が甘いとエージェントは誤差を「改善」と
取り違えて空回りするので、ここで次の3点を守る。

    1. 1ページ・1回の数値で判断しない
       同じページでも切り出しが数px変わるだけで誤り率が32%〜55%揺れた(実測)。
       多くのページの抜粋を文字数で重み付けして合算し、揺れを平均でならす
    2. 写真のセット(撮影条件)ごとに悪化を見張る
       APS-Cで効いた修正がiPhoneで10%→16%に悪化したことがある(実測)。
       全体が良くなっても、どれか1セットが一定以上悪化したら不採用にする
    3. 調整に使う写真(train)と、採否の最終判断に使う写真(holdout)を分ける
       同じ写真だけで改善を繰り返すと、その写真にだけ効く修正が積み上がる

正解データは「写真ごとに本文から数行を書き写した抜粋」でよい(ページ全文は不要)。
抜粋は、認識結果全体のうち最も近い部分と比べて採点する(読み順の違いに左右されない)。

manifest.json の形式(パスは manifest からの相対パス):

    {
      "photos": [
        {"file": "apsc/DSC00016.jpg", "set": "apsc", "split": "train",
         "snippets": ["物理学の問題を考えるとき，ヒントになるのは「保存する量」です。", "..."]},
        {"file": "iphone/IMG_8630.HEIC", "set": "iphone", "split": "holdout",
         "snippets": ["..."]}
      ]
    }
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from tscan.evaluate import normalize_for_eval

# 採否判定の既定値(ポイント = 誤り率の%差)。
# 全体でこれ以上良くならなければ「改善」と認めない(揺れの範囲の変化を拾わない)
DEFAULT_MIN_GAIN = 0.5
# どれか1セットがこれ以上悪化したら、全体が良くなっていても不採用
DEFAULT_MAX_SET_LOSS = 1.0


@dataclass
class PhotoCase:
    file: Path
    set: str
    split: str
    snippets: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> list[PhotoCase]:
    """manifest.json を読み、写真ごとの採点対象を返す。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).parent
    cases = []
    for entry in raw.get("photos", []):
        snippets = [s for s in entry.get("snippets", []) if normalize_for_eval(s)]
        if not snippets:
            continue
        cases.append(
            PhotoCase(
                file=(base / entry["file"]).resolve(),
                set=entry.get("set", "default"),
                split=entry.get("split", "train"),
                snippets=snippets,
            )
        )
    return cases


# ---------------------------------------------------------------------------
# 採点: 抜粋を認識結果のどこかと照合する
# ---------------------------------------------------------------------------


def best_substring_distance(ref: str, hyp: str) -> tuple[int, str]:
    """ref と、hyp の中で最も近い部分文字列との編集距離を返す: (距離, その部分文字列)。

    Sellers のアルゴリズム(近似文字列照合)。普通の編集距離の表で、
        - 1行目(ref が空)を全部0にする → hyp のどこから照合を始めてもよい
        - 最終行の最小値を取る         → hyp のどこで照合を終えてもよい
    とするだけで、「hyp のどこかにある ref」を ref の長さ×hyp の長さの計算で探せる。
    窓をずらしながら毎回編集距離を計算する方法より桁違いに速い。
    """
    n, m = len(ref), len(hyp)
    if n == 0:
        return 0, ""
    if m == 0:
        return n, ""

    prev = [0] * (m + 1)  # 1行目: どこから始めても距離0
    prev_start = list(range(m + 1))  # その位置の照合が hyp のどこから始まったか
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        cur_start = [0] * (m + 1)
        rc = ref[i - 1]
        for j in range(1, m + 1):
            sub = prev[j - 1] + (0 if rc == hyp[j - 1] else 1)
            dele = prev[j] + 1  # ref の文字が認識結果に無い(脱落)
            ins = cur[j - 1] + 1  # 認識結果に余計な文字がある(挿入)
            if sub <= dele and sub <= ins:
                cur[j], cur_start[j] = sub, prev_start[j - 1]
            elif dele <= ins:
                cur[j], cur_start[j] = dele, prev_start[j]
            else:
                cur[j], cur_start[j] = ins, cur_start[j - 1]
        prev, prev_start = cur, cur_start

    end = min(range(m + 1), key=lambda j: prev[j])
    return prev[end], hyp[prev_start[end] : end]


def score_snippets(snippets: list[str], hyp_text: str) -> list[dict]:
    """抜粋ごとに (正解の文字数, 誤り文字数, 最も近かった認識結果) を返す。"""
    hyp = normalize_for_eval(hyp_text)
    rows = []
    for snippet in snippets:
        ref = normalize_for_eval(snippet)
        dist, matched = best_substring_distance(ref, hyp)
        rows.append({"ref": ref, "matched": matched, "ref_chars": len(ref), "errors": min(dist, len(ref))})
    return rows


def _cer(rows: list[dict]) -> float:
    chars = sum(r["ref_chars"] for r in rows)
    return round(100.0 * sum(r["errors"] for r in rows) / chars, 2) if chars else 0.0


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except Exception:  # noqa: BLE001 — git が無くても採点はできる
        return "unknown"


def _photo_text(book, file: Path) -> str:
    """1枚の写真から作られたページ(見開きなら左右)の本文を、読み順につなげる。"""
    pages = sorted(
        (p for p in book.pages if not p.deleted and Path(p.image_path) == file),
        key=lambda p: p.order_key,
    )
    parts = []
    for page in pages:
        for block in sorted(page.blocks, key=lambda b: b.reading_order):
            parts.append(block.text or block.latex or "")
    return "".join(parts)


def run_bench(
    cases: list[PhotoCase],
    split: str = "all",
    offline: bool = False,
    workers: int | None = None,
    work_dir: Path | None = None,
) -> dict:
    """写真を実際のパイプライン(tscan run と同じ処理)に通して採点する。"""
    from tscan.models import Book, Page
    from tscan.ocr.registry import build_text_engines
    from tscan.pipeline import PipelineContext, Stage, run_book

    selected = [c for c in cases if split == "all" or c.split == split]
    if not selected:
        raise ValueError(f"split='{split}' に該当する写真が manifest にありません")

    started = time.time()
    with tempfile.TemporaryDirectory(prefix="tscan_bench_") as tmp:
        root = Path(work_dir) if work_dir else Path(tmp)
        book = Book(
            book_id="bench",
            pages=[Page.new(str(c.file), order_key=(i + 1) * 1000.0) for i, c in enumerate(selected)],
        )
        ctx = PipelineContext(book_id="bench", book_root=root, offline=offline)
        results, failures = run_book(book, ctx, from_stage=Stage.PREPROCESS, workers=workers)
        # 前処理後の画像の場所(work_dir を指定したときだけ残る。失敗分析で画像を見るため)
        images: dict[str, list[str]] = {}
        for r in results:
            if r.preprocessed_path:
                images.setdefault(str(Path(r.page.image_path)), []).append(r.preprocessed_path)

        photos = []
        for case in selected:
            rows = score_snippets(case.snippets, _photo_text(book, case.file))
            pages = [p for p in book.pages if not p.deleted and Path(p.image_path) == case.file]
            photos.append(
                {
                    "file": str(case.file),
                    "set": case.set,
                    "split": case.split,
                    "cer": _cer(rows),
                    "pages": len(pages),
                    "warnings": sorted({w for p in pages for w in p.warnings}),
                    "snippets": rows,
                    "images": images.get(str(case.file), []) if work_dir else [],
                }
            )

        blocks = [b for p in book.pages if not p.deleted for b in p.blocks]

    sets: dict[str, dict] = {}
    for name in sorted({p["set"] for p in photos}):
        rows = [r for p in photos if p["set"] == name for r in p["snippets"]]
        sets[name] = {"cer": _cer(rows), "photos": sum(1 for p in photos if p["set"] == name)}

    all_rows = [r for p in photos for r in p["snippets"]]
    engines = build_text_engines(vertical=False, offline=offline)
    return {
        "overall": {
            "cer": _cer(all_rows),
            "ref_chars": sum(r["ref_chars"] for r in all_rows),
            "review_rate": round(100.0 * sum(b.review_status == "pending" for b in blocks) / len(blocks), 1)
            if blocks
            else 0.0,
            "failures": failures,
        },
        "sets": sets,
        "photos": photos,
        "meta": {
            "split": split,
            "commit": _git_commit(),
            "engines": [getattr(e, "name", type(e).__name__) for e in engines.engines],
            "seconds": round(time.time() - started, 1),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }


# ---------------------------------------------------------------------------
# 採否の判定
# ---------------------------------------------------------------------------


def compare(
    result: dict,
    baseline: dict,
    min_gain: float = DEFAULT_MIN_GAIN,
    max_set_loss: float = DEFAULT_MAX_SET_LOSS,
) -> tuple[str, list[str]]:
    """基準(baseline)と比べて、判定と説明の行を返す。

    判定:
        accept       全体が min_gain 以上改善し、どのセットも max_set_loss を超えて悪化していない
        reject       どれかのセットが max_set_loss を超えて悪化した、または全体が min_gain 以上悪化した
        neutral      上のどちらでもない(揺れの範囲の変化。採用しない)
        incomparable 使ったOCRエンジンや写真の範囲が違い、比べられない
    """
    lines: list[str] = []
    if result["meta"]["engines"] != baseline["meta"]["engines"]:
        return "incomparable", [f"OCRエンジンが違います: 基準 {baseline['meta']['engines']} / 今回 {result['meta']['engines']}"]
    if result["meta"]["split"] != baseline["meta"]["split"] or result["overall"]["ref_chars"] != baseline["overall"]["ref_chars"]:
        return "incomparable", ["採点に使った写真・抜粋が基準と違います(manifest か --split を確認)"]

    delta = result["overall"]["cer"] - baseline["overall"]["cer"]
    lines.append(f"全体: {baseline['overall']['cer']:.2f}% → {result['overall']['cer']:.2f}% ({delta:+.2f})")

    worst_loss = 0.0
    for name, now in result["sets"].items():
        before = baseline["sets"].get(name)
        if before is None:
            continue
        d = now["cer"] - before["cer"]
        worst_loss = max(worst_loss, d)
        lines.append(f"  {name}: {before['cer']:.2f}% → {now['cer']:.2f}% ({d:+.2f})")

    if worst_loss > max_set_loss or delta >= min_gain:
        verdict = "reject"
    elif delta <= -min_gain:
        verdict = "accept"
    else:
        verdict = "neutral"
    lines.append(f"判定: {verdict}(改善の最小幅 {min_gain} / セットごとの許容悪化 {max_set_loss})")
    return verdict, lines
