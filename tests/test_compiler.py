import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maniac.generation.compiler import compile_to_man, install_manpage


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
        # simulate pandoc creating output file
        out_file = Path(args[0][args[0].index("-o") + 1])
        out_file.write_text(".TH TOOL 1", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out_file = tmp_path / "tool.1"
    success = compile_to_man("% TOOL(1)\n# NAME\ntool", out_file)
    assert success
    assert out_file.exists()


def test_install_manpage(tmp_path: Path) -> None:
    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1", encoding="utf-8")

    target_dir = tmp_path / "man1"
    installed = install_manpage(src_file, target_dir=target_dir)

    assert installed == target_dir / "tool.1"
    assert installed.exists()
    assert installed.read_text(encoding="utf-8") == ".TH TOOL 1"
