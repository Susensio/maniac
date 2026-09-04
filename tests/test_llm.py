from types import SimpleNamespace

import pytest

from maniac.config import Config
from maniac.generation.llm import clean_manpage_markdown, run_llm_synthesis


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


def _fake_complete(observed: dict[str, object]):
    def fake_complete(request: dict[str, object]) -> object:
        observed.update(request)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="# NAME\ntool"))]
        )

    return fake_complete


def test_run_llm_synthesis_calls_litellm_with_resolved_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm", _fake_complete(observed)
    )
    cfg = Config(llm_api_key="maniac-only-key")

    result = run_llm_synthesis(
        "prompt text", "tool", model="anthropic/claude-sonnet-4-6", config=cfg
    )

    assert result.startswith("% TOOL(1) | User Commands")
    assert observed["model"] == "anthropic/claude-sonnet-4-6"
    assert observed["api_key"] == "maniac-only-key"


def test_run_llm_synthesis_uses_provider_native_key_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm", _fake_complete(observed)
    )

    run_llm_synthesis(
        "prompt text",
        "tool",
        model="anthropic/claude-sonnet-4-6",
        config=Config(llm_api_key=None),
    )

    assert "api_key" not in observed


def test_run_llm_synthesis_sends_reasoning_effort_when_model_supports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm", _fake_complete(observed)
    )
    monkeypatch.setattr(litellm, "supports_reasoning", lambda model: True)

    run_llm_synthesis(
        "prompt text",
        "tool",
        model="anthropic/claude-sonnet-4-6",
        reasoning_effort="high",
        config=Config(llm_api_key=None),
    )

    assert observed["reasoning_effort"] == "high"


def test_run_llm_synthesis_omits_reasoning_effort_when_model_does_not_support_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm", _fake_complete(observed)
    )
    monkeypatch.setattr(litellm, "supports_reasoning", lambda model: False)

    run_llm_synthesis(
        "prompt text",
        "tool",
        model="openai/gpt-5.1-chat-latest",
        reasoning_effort="high",
        config=Config(llm_api_key=None),
    )

    assert "reasoning_effort" not in observed


def test_run_llm_synthesis_explicit_reasoning_effort_outranks_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm", _fake_complete(observed)
    )
    monkeypatch.setattr(litellm, "supports_reasoning", lambda model: True)
    cfg = Config(llm_api_key=None)
    cfg.llm_reasoning_effort = "low"

    run_llm_synthesis(
        "prompt text",
        "tool",
        model="anthropic/claude-sonnet-4-6",
        reasoning_effort="high",
        config=cfg,
    )

    assert observed["reasoning_effort"] == "high"
