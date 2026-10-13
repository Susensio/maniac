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
    assert "ANOTHER VERSION" not in built


def test_a_seed_page_is_labelled_and_ranked_below_the_help() -> None:
    built = build_synthesis_prompt(
        tool_name="mytool",
        help_text="Usage: mytool",
        doc_text="",
        seed_text='.TH MYTOOL 1 "" "mytool 1.0"',
        seed_label="the mytool package's page (1.0)",
    )
    assert (
        "## AN EXISTING MANUAL PAGE FOR ANOTHER VERSION: the mytool package's page (1.0)"
        in built
    )
    assert '.TH MYTOOL 1 "" "mytool 1.0"' in built
    assert "where they disagree, follow the help" in built
    # Context, inside the documentation block, before the closing marker.
    assert built.index("ANOTHER VERSION") < built.index("=== END CONTEXT")
