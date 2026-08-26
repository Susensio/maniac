import subprocess
from pathlib import Path
from typing import Any

import pytest

from maniac.config import Config
from maniac.generation.llm import (
    clean_manpage_markdown,
    run_llm_synthesis,
)


def test_clean_manpage_markdown_raw() -> None:
    raw = "% TOOL(1) | User Commands\n\n# NAME\ntool"
    cleaned = clean_manpage_markdown(raw, "tool")
    assert cleaned == raw


def test_clean_manpage_markdown_wrapped_fences() -> None:
    wrapped = "```markdown\n% TOOL(1) | User Commands\n\n# NAME\ntool\n```"
    cleaned = clean_manpage_markdown(wrapped, "tool")
    assert cleaned.startswith("% TOOL(1) | User Commands")
    assert not cleaned.startswith("```")
    assert not cleaned.endswith("```")


def test_clean_manpage_markdown_missing_header() -> None:
    missing = "# NAME\ntool - description"
    cleaned = clean_manpage_markdown(missing, "mytool")
    assert cleaned.startswith("% MYTOOL(1) | User Commands")
    assert "# NAME" in cleaned


def test_model_aliases() -> None:
    cfg = Config()
    assert cfg.resolve_model("flash") == "Gemini 3.7 Flash (High)"
    assert cfg.resolve_model("pro") == "Gemini 3.1 Pro (High)"
    assert cfg.resolve_model("sonnet") == "Claude Sonnet 4.6 (Thinking)"


def test_run_llm_synthesis_mock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "--model" in args[0]
        assert "Gemini 3.7 Flash (High)" in args[0]
        return subprocess.CompletedProcess(
            args=["agy", "-p"],
            returncode=0,
            stdout="% TOOL(1) | User Commands\n\n# NAME\ntool - synthesized",
            stderr="",
        )

    import shutil

    # Only "agy" resolves; every other binary (notably "sandbox") is
    # reported absent so the sandbox-vs-agy branch taken doesn't depend on
    # what happens to be on the host running the test.
    monkeypatch.setattr(
        shutil, "which", lambda cmd: "/usr/bin/agy" if cmd == "agy" else None
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_llm_synthesis(
        "prompt text", "tool", model="flash", work_base_dir=tmp_path
    )
    assert result.startswith("% TOOL(1) | User Commands")
    assert "synthesized" in result
