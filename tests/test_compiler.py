import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maniac.exceptions import UnsupportedPandoc
from maniac.generation import compiler
from maniac.generation.compiler import (
    PROVENANCE_SIGNATURE,
    build_provenance_header,
    compile_to_man,
    require_supported_pandoc,
    version_footer,
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


def test_compile_to_man_passes_footer_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd_args = args[0]
        assert "--metadata" in cmd_args
        assert cmd_args[cmd_args.index("--metadata") + 1] == "footer=tool 1.2.3"
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
        footer="tool 1.2.3",
    )
    assert success


def test_compile_to_man_without_footer_omits_footer_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd_args = args[0]
        assert "--metadata" not in cmd_args
        out_file = Path(cmd_args[cmd_args.index("-o") + 1])
        out_file.write_text(".TH TOOL 1", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=cmd_args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out_file = tmp_path / "tool.1"
    success = compile_to_man("% TOOL(1)\n# NAME\ntool", out_file, tool_name="tool")
    assert success


def test_build_provenance_header() -> None:
    header = build_provenance_header(tool_name="mytool", model="Gemini 3.7 Flash")
    assert PROVENANCE_SIGNATURE in header
    assert "Tool: mytool" in header
    assert "Model: Gemini 3.7 Flash" in header


def test_version_footer_names_the_tool_before_a_provider_version() -> None:
    assert version_footer("gh", "2.63.0", verbatim=False) == "gh 2.63.0"


def test_version_footer_uses_a_verbatim_version_line_as_it_stands() -> None:
    """An unclaimed binary's `--version` already names itself (ADR-0020);
    prefixing it again read `faketool faketool 2.3.1`, and pandoc folded a
    multi-line one (gcc's copyright notice) into the footer."""
    gcc = "gcc (Debian 14.2.0-19) 14.2.0\nCopyright (C) 2024 Free Software Foundation, Inc."

    assert (
        version_footer("faketool", "faketool 2.3.1", verbatim=True) == "faketool 2.3.1"
    )
    assert version_footer("gcc", gcc, verbatim=True) == "gcc (Debian 14.2.0-19) 14.2.0"


@pytest.mark.parametrize(
    ("line", "supported"),
    [
        ("pandoc 3.1.9", False),
        ("pandoc 3.1.3", False),
        ("pandoc 2.17.1.1", False),
        ("pandoc 3.1.10", True),
        ("pandoc 3.12.1", True),
        ("pandoc.exe 3.2", True),
    ],
)
def test_require_supported_pandoc_draws_the_line_at_3_1_10(
    monkeypatch: pytest.MonkeyPatch, line: str, supported: bool
) -> None:
    """3.1.10 is the first release that escapes `-` (bisected, ADR-0064)."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(compiler, "_pandoc_version_line", lambda pandoc_bin: line)

    if supported:
        assert require_supported_pandoc() == "/usr/bin/pandoc"
    else:
        with pytest.raises(UnsupportedPandoc, match=r"3\.1\.10 or newer"):
            require_supported_pandoc()


def test_require_supported_pandoc_refuses_an_unreadable_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0060: a pandoc that cannot say its version is not guessed at."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        compiler,
        "_pandoc_version_line",
        lambda pandoc_bin: "/usr/bin/pandoc (--version exited 1)",
    )

    with pytest.raises(UnsupportedPandoc, match="exited 1"):
        require_supported_pandoc()


def test_require_supported_pandoc_leaves_a_missing_pandoc_to_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    assert require_supported_pandoc() is None


def test_compile_to_man_refuses_an_old_pandoc_without_running_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        compiler, "_pandoc_version_line", lambda pandoc_bin: "pandoc 3.1.3"
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("compiled with old pandoc")
    )

    with pytest.raises(UnsupportedPandoc):
        compile_to_man("% TOOL(1)\n# NAME\ntool", tmp_path / "tool.1", tool_name="tool")
