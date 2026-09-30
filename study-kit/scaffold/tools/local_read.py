#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow", "pymupdf", "pillow-heif"]
# ///
"""教科書のページ画像・PDFを、ローカルの画像認識モデル(既定: Qwen2.5-VL 72B)で読む。

読み取り結果は Markdown(数式は LaTeX)で ocr/<本の名前>/ に保存する。
自信のない文字は推測せず `[?]` と書かせる。あとで Claude が、その位置だけ元画像で確認する。

    uv run tools/local_read.py --check                                  # サーバーとモデルの確認
    uv run tools/local_read.py sources/young_em/IMG_0001.jpg --book young_em
    uv run tools/local_read.py sources/young_em/ --book young_em        # フォルダ内の全画像
    uv run tools/local_read.py sources/young.pdf --book young --pages 63-66

なぜ標準入出力とHTTPだけで書いてあるか:
    モデルの動かし方(Ollama / LM Studio / mlx-vlm)は変わりやすい。どれもHTTPで話せるので、
    このスクリプトは Python 標準ライブラリだけでサーバーに話しかけ、動かし方に依存しない。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = os.environ.get("STUDY_VLM_MODEL", "qwen2.5vl:72b")
DEFAULT_API = os.environ.get("STUDY_VLM_API", "ollama")
DEFAULT_URLS = {"ollama": "http://localhost:11434", "openai": "http://localhost:1234/v1"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".webp", ".bmp"}

# 「読むだけ」を徹底させる指示。要約・訂正・補完をさせると、誤読が本物らしく見えてしまう
PROMPT = """You are transcribing ONE page of a Japanese physics/math textbook (photo or scan).
Transcribe exactly what is printed, in natural reading order, as Markdown.

Rules:
- Copy what you see. Do NOT summarize, translate, explain, complete, or correct anything.
- Math: inline $...$ and display $$...$$ in LaTeX. Keep subscripts, superscripts, vector arrows/bold, units, and equation numbers like (2.58).
- Headings: use # / ## / ### according to visual hierarchy. Footnotes and side notes: use "> " quotes.
- Figures, photos, graphs: write one line "[図: short description in Japanese]" at their position. Never invent numbers that are not printed.
- Text in a dark or coloured box: transcribe normally.
- If you cannot read a character with confidence, write [?] instead of guessing.
  If two readings are plausible, write [?A/B].
- If a page number is visible, write it once as <!-- page: N -->.
- Output only the transcription, with no preface and no code fence.
"""


# ---------------------------------------------------------------------------
# 画像の用意
# ---------------------------------------------------------------------------


def _register_heif() -> None:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass


def image_to_jpeg_b64(image, max_edge: int) -> str:
    """PIL画像を長辺 max_edge 以下に縮めて JPEG(base64)にする。

    大きすぎる画像はモデルが処理する「画像トークン」が増えて遅くなるだけで、
    ある大きさを超えると精度は上がらない。日本語の細かい文字が読める範囲で縮める。
    """
    image = image.convert("RGB")
    longest = max(image.size)
    if longest > max_edge:
        scale = max_edge / longest
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parse_pages(spec: str | None) -> set[int] | None:
    """'3-5,9' → {3,4,5,9}。None は全ページ。"""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        elif part:
            pages.add(int(part))
    return pages


def collect_jobs(inputs: list[str], pages: set[int] | None, dpi: int, max_edge: int):
    """入力(画像・フォルダ・PDF)を (出力名, base64画像, 元の名前) の列にする。遅延評価。"""
    from PIL import Image

    _register_heif()
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            files = sorted(f for f in path.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES or f.suffix.lower() == ".pdf")
        elif path.exists():
            files = [path]
        else:
            raise FileNotFoundError(f"入力が見つかりません: {path}")

        for f in files:
            if f.suffix.lower() == ".pdf":
                import fitz  # PyMuPDF

                with fitz.open(f) as doc:
                    for index in range(len(doc)):
                        number = index + 1
                        if pages is not None and number not in pages:
                            continue
                        pix = doc[index].get_pixmap(dpi=dpi)
                        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        yield f"{f.stem}_p{number:04d}", (lambda im=img: image_to_jpeg_b64(im, max_edge)), f"{f.name} p.{number}"
            else:
                yield f.stem, (lambda p=f: image_to_jpeg_b64(Image.open(p), max_edge)), f.name


# ---------------------------------------------------------------------------
# サーバーとの通信
# ---------------------------------------------------------------------------


def _post_json(url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_model(api: str, url: str, model: str, image_b64: str, num_ctx: int, max_tokens: int, timeout: float) -> str:
    """1枚の画像とプロンプトをモデルに渡し、返ってきた文章を返す。"""
    if api == "ollama":
        # Ollama は既定の文脈長が短く、画像が大きいと黙って切り捨てる。num_ctx を明示する
        reply = _post_json(
            f"{url.rstrip('/')}/api/chat",
            {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": PROMPT, "images": [image_b64]}],
                "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens},
            },
            timeout,
        )
        return reply["message"]["content"]

    reply = _post_json(
        f"{url.rstrip('/')}/chat/completions",
        {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ],
                }
            ],
        },
        timeout,
    )
    return reply["choices"][0]["message"]["content"]


def check_server(api: str, url: str, model: str) -> tuple[bool, str]:
    """サーバーが動いていて、モデルが入っているかを調べる。(OKか, 説明)"""
    try:
        if api == "ollama":
            names = [m["name"] for m in _get_json(f"{url.rstrip('/')}/api/tags").get("models", [])]
        else:
            names = [m["id"] for m in _get_json(f"{url.rstrip('/')}/models").get("data", [])]
    except (urllib.error.URLError, OSError) as error:
        hint = "ollama serve を実行するか、Ollamaアプリを起動してください" if api == "ollama" else "LM Studioのサーバーを起動してください"
        return False, f"サーバーに接続できません({url}): {error}\n  → {hint}"
    # タグを省いた名前(qwen2.5vl)は Ollama では :latest のことなので、それも一致とみなす
    if model not in names and f"{model}:latest" not in names:
        return False, f"モデル '{model}' が見つかりません。入っているモデル: {', '.join(names) or '(なし)'}"
    return True, f"OK: {url} / {model}"


# ---------------------------------------------------------------------------
# 出力の整形
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\n(.*?)\n```\s*$", re.DOTALL)


def clean_output(text: str) -> str:
    """モデルが付けがちな ``` の囲みを外す。"""
    text = text.strip()
    match = _FENCE.match(text)
    return (match.group(1) if match else text).strip()


def max_repeat(text: str) -> int:
    """同じ行が連続する最大回数。モデルが同じ行をくり返す暴走の検出に使う。"""
    best = run = 0
    previous = None
    for line in (ln.strip() for ln in text.splitlines()):
        if line and line == previous:
            run += 1
        else:
            run = 1 if line else 0
        previous = line
        best = max(best, run)
    return best


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="*", help="画像ファイル・フォルダ・PDF")
    parser.add_argument("--book", help="本の名前(出力先 ocr/<book>/)")
    parser.add_argument("--out", type=Path, default=Path("ocr"), help="出力の親フォルダ(既定: ocr)")
    parser.add_argument("--api", choices=["ollama", "openai"], default=DEFAULT_API, help="サーバーの種類(既定: ollama)")
    parser.add_argument("--url", help="サーバーのURL(既定はサーバーの種類による)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-edge", type=int, default=2200, help="画像の長辺の上限px")
    parser.add_argument("--dpi", type=int, default=200, help="PDFを画像にするときの解像度")
    parser.add_argument("--pages", help="PDFの対象ページ 例: 3-5,9")
    parser.add_argument("--num-ctx", type=int, default=16384, help="(Ollama)文脈長。画像が大きいほど必要")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=900, help="1ページあたりの待ち時間(秒)")
    parser.add_argument("--force", action="store_true", help="読み取り済みのページも読み直す")
    parser.add_argument("--check", action="store_true", help="サーバーとモデルを確認して終わる")
    args = parser.parse_args(argv)

    url = args.url or DEFAULT_URLS[args.api]
    ok, message = check_server(args.api, url, args.model)
    print(message)
    if args.check:
        return 0 if ok else 1
    if not ok:
        return 1
    if not args.inputs or not args.book:
        parser.error("inputs と --book が必要です(確認だけなら --check)")

    out_dir = args.out / args.book
    out_dir.mkdir(parents=True, exist_ok=True)

    done = skipped = failed = 0
    for name, load_b64, label in collect_jobs(args.inputs, parse_pages(args.pages), args.dpi, args.max_edge):
        target = out_dir / f"{name}.md"
        if target.exists() and target.stat().st_size > 0 and not args.force:
            skipped += 1
            continue
        started = time.time()
        try:
            text = clean_output(
                ask_model(args.api, url, args.model, load_b64(), args.num_ctx, args.max_tokens, args.timeout)
            )
        except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
            print(f"✗ {label}: {error}", file=sys.stderr)
            failed += 1
            continue

        warnings = []
        if not text:
            warnings.append("空の出力")
        if max_repeat(text) >= 6:
            warnings.append("同じ行のくり返し(暴走の疑い)")
        unreadable = text.count("[?")
        header = f"<!-- source: {label} | model: {args.model} | unreadable: {unreadable} | {time.strftime('%Y-%m-%d %H:%M')} -->"
        if warnings:
            header += f"\n<!-- WARN: {' / '.join(warnings)} -->"

        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(f"{header}\n\n{text}\n", encoding="utf-8")
        tmp.replace(target)
        done += 1
        flag = f"  ⚠ {' / '.join(warnings)}" if warnings else ""
        print(f"✓ {label} → {target}  ({time.time() - started:.0f}秒, 読めなかった箇所 {unreadable}){flag}")

    print(f"完了: 読み取り {done} / 済みのためスキップ {skipped} / 失敗 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
