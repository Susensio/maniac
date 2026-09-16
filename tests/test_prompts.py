from maniac.generation.prompts import build_synthesis_prompt, get_default_system_prompt


def test_load_default_system_prompt() -> None:
    prompt = get_default_system_prompt()
    assert "% {TOOL_NAME}(1) | User Commands" in prompt
    assert "DEFINITION LISTS" in prompt


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
