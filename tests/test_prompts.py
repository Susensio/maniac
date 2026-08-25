from pathlib import Path

from maniac.generation.prompts import (
    build_synthesis_prompt,
    load_system_prompt,
)


def test_load_default_system_prompt() -> None:
    prompt = load_system_prompt(None)
    assert "% {TOOL_NAME}(1) | User Commands" in prompt
    assert "DEFINITION LISTS" in prompt


def test_load_custom_system_prompt(tmp_path: Path) -> None:
    custom_file = tmp_path / "custom.prompt"
    custom_file.write_text("Custom prompt for {TOOL_NAME}", encoding="utf-8")

    loaded = load_system_prompt(custom_file)
    assert loaded == "Custom prompt for {TOOL_NAME}"


def test_build_synthesis_prompt() -> None:
    built = build_synthesis_prompt(
        tool_name="mytool",
        help_text="> mytool --help\nUsage: mytool",
        doc_text="### README.md\n# My Tool",
    )
    assert "% MYTOOL(1) | User Commands" in built
    assert "=== CONTEXT DOCUMENTATION FOR mytool ===" in built
    assert "> mytool --help" in built
    assert "### README.md" in built
