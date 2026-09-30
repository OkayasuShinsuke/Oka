"""init.sh(作業場の作成)のテスト。育てた CLAUDE.md や辞書を上書きしないことが要。"""
import os
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def _init(dest: Path) -> str:
    return subprocess.run(["bash", str(KIT / "init.sh"), str(dest)], capture_output=True, text=True, check=True).stdout


def test_creates_workspace_with_hidden_files_and_folders(tmp_path):
    dest = tmp_path / "study"
    _init(dest)
    for rel in ("CLAUDE.md", "build.sh", ".gitignore", ".claude/commands/note.md", ".claude/agents/tex-fixer.md",
                ".claude/agents/checker.md", "template/preamble.tex", "template/unit.tex", "dict/terms.md",
                "tools/local_read.py", "notes/_example/main.tex"):
        assert (dest / rel).is_file(), rel
    for folder in ("sources", "ocr", "figures", "notes"):
        assert (dest / folder).is_dir()
    assert (dest / ".git").is_dir()
    assert os.access(dest / "build.sh", os.X_OK)


def test_rerun_never_overwrites_edited_files(tmp_path):
    dest = tmp_path / "study"
    _init(dest)
    (dest / "CLAUDE.md").write_text("自分で育てた内容", encoding="utf-8")
    (dest / "dict" / "terms.md").unlink()  # 消えたファイルは補充される
    out = _init(dest)
    assert (dest / "CLAUDE.md").read_text(encoding="utf-8") == "自分で育てた内容"
    assert (dest / "dict" / "terms.md").is_file()
    assert "新規 1 ファイル" in out


def test_agents_and_command_have_valid_frontmatter():
    """エージェント定義の先頭(---で囲んだ設定)が壊れていないこと。壊れると Claude Code が読み込まない。"""
    for path in [*(KIT / "scaffold/.claude/agents").glob("*.md"), *(KIT / "scaffold/.claude/commands").glob("*.md")]:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "---", path
        end = lines.index("---", 1)
        keys = {line.split(":")[0] for line in lines[1:end]}
        assert "description" in keys, path
        if path.parent.name == "agents":
            assert {"name", "tools", "model"} <= keys, path
