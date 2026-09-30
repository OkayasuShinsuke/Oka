"""local_read.py のテスト。本物のモデルは使わず、Ollama / OpenAI 互換サーバーのふりをする小さなHTTPサーバーで確かめる。"""
import base64
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scaffold" / "tools"))
import local_read  # noqa: E402


class _FakeModelServer:
    """受け取った要求を記録し、決まった文章を返す。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # テスト出力を静かに
                pass

            def _send(self, payload: dict):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send({"models": [{"name": "qwen2.5vl:72b"}]})
                elif self.path == "/v1/models":
                    self._send({"data": [{"id": "qwen-vl"}]})
                else:
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body})
                if self.path == "/api/chat":
                    self._send({"message": {"content": outer.reply}})
                elif self.path == "/v1/chat/completions":
                    self._send({"choices": [{"message": {"content": outer.reply}}]})
                else:
                    self.send_error(404)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    fake = _FakeModelServer("```markdown\n# 電場\n\n$$E = \\frac{F}{q}$$ <!-- page: 63 -->\n```")
    yield fake
    fake.close()


def _photo(path: Path, size=(3000, 2000)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def test_reads_image_and_writes_markdown(tmp_path, server):
    photo = _photo(tmp_path / "IMG_0001.jpg")
    out = tmp_path / "ocr"
    code = local_read.main([str(photo), "--book", "em", "--out", str(out), "--url", server.url, "--max-edge", "1000"])
    assert code == 0

    text = (out / "em" / "IMG_0001.md").read_text(encoding="utf-8")
    assert "# 電場" in text and "```" not in text  # 囲みは外される
    assert "source: IMG_0001.jpg" in text

    # 送った画像は max-edge まで縮められ、Ollama の文脈長も指定されている
    request = server.requests[0]["body"]
    assert request["options"]["num_ctx"] == 16384 and request["options"]["temperature"] == 0
    sent = Image.open(io.BytesIO(base64.b64decode(request["messages"][0]["images"][0])))
    assert max(sent.size) == 1000


def test_skips_already_read_pages_unless_forced(tmp_path, server):
    photo = _photo(tmp_path / "a.jpg", (400, 300))
    args = [str(photo), "--book", "b", "--out", str(tmp_path / "ocr"), "--url", server.url]
    assert local_read.main(args) == 0
    assert local_read.main(args) == 0
    assert len(server.requests) == 1  # 2回目は読み取り済みなので問い合わせない
    assert local_read.main(args + ["--force"]) == 0
    assert len(server.requests) == 2


def test_folder_input_reads_every_image_in_order(tmp_path, server):
    folder = tmp_path / "photos"
    folder.mkdir()
    for name in ("b.jpg", "a.jpg"):
        _photo(folder / name, (200, 200))
    (folder / "memo.txt").write_text("画像ではない")
    assert local_read.main([str(folder), "--book", "x", "--out", str(tmp_path / "ocr"), "--url", server.url]) == 0
    assert sorted(p.name for p in (tmp_path / "ocr" / "x").glob("*.md")) == ["a.md", "b.md"]


def test_openai_compatible_api(tmp_path, server):
    photo = _photo(tmp_path / "a.jpg", (300, 300))
    code = local_read.main(
        [str(photo), "--book", "o", "--out", str(tmp_path / "ocr"), "--api", "openai",
         "--url", f"{server.url}/v1", "--model", "qwen-vl"]
    )
    assert code == 0
    content = server.requests[0]["body"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_pdf_pages_are_selected(tmp_path, server):
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "book.pdf"
    doc = fitz.open()
    for _ in range(4):
        doc.new_page()
    doc.save(pdf)
    assert local_read.main([str(pdf), "--book", "p", "--out", str(tmp_path / "ocr"), "--url", server.url, "--pages", "2-3"]) == 0
    assert sorted(p.name for p in (tmp_path / "ocr" / "p").glob("*.md")) == ["book_p0002.md", "book_p0003.md"]


def test_unreadable_marks_and_repeats_are_reported(tmp_path):
    fake = _FakeModelServer("誘[?電/計]体\n" + "同じ行\n" * 8)
    try:
        photo = _photo(tmp_path / "a.jpg", (200, 200))
        assert local_read.main([str(photo), "--book", "w", "--out", str(tmp_path / "ocr"), "--url", fake.url]) == 0
        text = (tmp_path / "ocr" / "w" / "a.md").read_text(encoding="utf-8")
        assert "unreadable: 1" in text and "同じ行のくり返し" in text
    finally:
        fake.close()


def test_check_reports_missing_model_and_dead_server(server):
    assert local_read.check_server("ollama", server.url, "qwen2.5vl:72b")[0] is True
    ok, message = local_read.check_server("ollama", server.url, "llama3")
    assert not ok and "qwen2.5vl:72b" in message
    ok, message = local_read.check_server("ollama", "http://127.0.0.1:9", "qwen2.5vl:72b")
    assert not ok and "接続できません" in message


def test_parse_pages_and_clean_output():
    assert local_read.parse_pages("3-5,9") == {3, 4, 5, 9}
    assert local_read.parse_pages(None) is None
    assert local_read.clean_output("```\nabc\n```") == "abc"
    assert local_read.clean_output("そのまま") == "そのまま"
    assert local_read.max_repeat("a\na\na\nb") == 3
