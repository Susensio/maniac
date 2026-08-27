import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maniac.generation.compiler import (
    PROVENANCE_SIGNATURE,
    build_provenance_header,
    compile_to_man,
)


def test_compile_to_man_no_pandoc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    out_file = tmp_path / "tool.1"
    success = compile_to_man("% TOOL(1)\n# NAME\ntool", out_file)
    assert not success


def test_compile_to_man_with_pandoc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd_args = args[0]
        assert "-f" in cmd_args
        assert cmd_args[cmd_args.index("-f") + 1] == "markdown-smart"
        out_file = Path(cmd_args[cmd_args.index("-o") + 1])
        out_file.write_text(".TH TOOL 1", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=cmd_args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out_file = tmp_path / "tool.1"
    success = compile_to_man(
        "% TOOL(1)\n# NAME\ntool",
        out_file,
        tool_name="tool",
        model="Gemini 3.7 Flash",
    )
    assert success
    assert out_file.exists()

    content = out_file.read_text(encoding="utf-8")
    assert PROVENANCE_SIGNATURE in content
    assert "Tool: tool" in content
    assert "Model: Gemini 3.7 Flash" in content


def test_compile_to_man_preserves_double_hyphens(tmp_path: Path) -> None:
    if not shutil.which("pandoc"):
        pytest.skip("pandoc not available in environment")

    out_file = tmp_path / "tool.1"
    md_text = "% TOOL(1) | User Commands\n\n# OPTIONS\n**--version**, **--update**\n:   Flag description.\n"
    success = compile_to_man(md_text, out_file, tool_name="tool")
    assert success
    content = out_file.read_text(encoding="utf-8")
    assert "\\-\\-version" in content
    assert "\\-\\-update" in content
    assert "\\(enversion" not in content
    assert "\\(enupdate" not in content


def test_build_provenance_header() -> None:
    header = build_provenance_header(tool_name="mytool", model="Gemini 3.7 Flash")
    assert PROVENANCE_SIGNATURE in header
    assert "Tool: mytool" in header
    assert "Model: Gemini 3.7 Flash" in header
