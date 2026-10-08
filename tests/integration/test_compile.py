"""Synthesized Markdown through real pandoc and groff, judged on what `man` shows."""

import os
import re
import subprocess
from pathlib import Path

import pytest

from maniac.evaluation.judge import check_pandoc_compilation, render_manpage_to_terminal
from maniac.generation.compiler import compile_to_man
from maniac.generation.llm import clean_manpage_markdown
from maniac.manifest import PROVENANCE_SIGNATURE, read_provenance_header

from .conftest import FIXTURES


@pytest.fixture
def page(tmp_path: Path, model_response: str) -> Path:
    """The fixture model response, cleaned and compiled as synthesis does."""
    markdown = clean_manpage_markdown(model_response, "faketool")
    out = tmp_path / "faketool.1"
    assert compile_to_man(
        markdown, out, tool_name="faketool", model="test-model", footer="faketool 2.3.1"
    )
    return out


def _render(page: Path, tmac: Path) -> str:
    """`groff -man -Tutf8` as an unpatched system renders it, formatting stripped."""
    result = subprocess.run(
        ["groff", "-M", str(tmac), "-man", "-Tutf8", "-P-cbou", "-rLL=100n", str(page)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _flags_in_fixture() -> set[str]:
    text = (FIXTURES / "faketool.1.md").read_text(encoding="utf-8")
    return set(re.findall(r"\*\*(--?[a-z][a-z-]*)\*\*", text))


def test_groff_accepts_the_page_without_a_single_warning(page: Path) -> None:
    """`-ww` turns on every warning groff has; a page `man` prints with
    complaints, or silently drops part of, fails here."""
    result = subprocess.run(
        ["groff", "-man", "-ww", "-z", str(page)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_every_option_renders_with_ascii_hyphens(
    page: Path, vanilla_tmac: Path
) -> None:
    """An option has to be typeable from what the page shows. Pandoc before
    3.1.10 writes `-` bare, which unpatched groff renders as U+2010 HYPHEN:
    such a `--release` cannot be pasted into a shell or found with `/--release`."""
    rendered = _render(page, vanilla_tmac)
    flags = _flags_in_fixture()
    assert {"--release", "--port", "-j", "-v", "--version"} <= flags

    missing = {
        flag
        for flag in flags
        if not re.search(rf"(?<![\w\u2010-]){re.escape(flag)}(?![\w-])", rendered)
    }

    assert missing == set()


def test_example_commands_render_one_per_line(page: Path, vanilla_tmac: Path) -> None:
    """Code blocks survive the model's own outer fence and render verbatim,
    each command on its own line, as typed."""
    lines = {line.strip() for line in _render(page, vanilla_tmac).splitlines()}

    assert "faketool build --release ./site" in lines
    assert "faketool serve --port 9000 --format json" in lines
    assert "cat fake.toml | faketool build -" in lines


def test_header_carries_provenance_and_version(page: Path) -> None:
    roff = page.read_text(encoding="utf-8")

    assert roff.startswith(PROVENANCE_SIGNATURE + "\n")
    assert '.TH "FAKETOOL" "1" "" "faketool 2.3.1" "User Commands"' in roff
    provenance = read_provenance_header(page)
    assert provenance is not None
    assert (provenance["tool"], provenance["model"]) == ("faketool", "test-model")


def test_man_itself_renders_the_page(page: Path) -> None:
    """`man -l`, the reader the page is for, not just the formatter."""
    result = subprocess.run(
        ["man", "-l", str(page)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "MANPAGER": "cat",
            "MANWIDTH": "100",
            "MAN_KEEP_FORMATTING": "",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "faketool - build and serve fake projects" in result.stdout
    assert "FAKETOOL_HOME" in result.stdout


def test_evaluation_compiles_and_renders_with_groff(model_response: str) -> None:
    """`maniac evaluate` judges what `man` shows only if groff really ran."""
    markdown = clean_manpage_markdown(model_response, "faketool")

    assert check_pandoc_compilation(markdown) == (True, None)
    rendered = render_manpage_to_terminal(markdown)
    assert rendered is not None
    text, renderer = rendered
    assert renderer == "man"
    assert "FAKETOOL_HOME" in text
