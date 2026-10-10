"""Install orchestration: one resolved tool, ADR-0016's tiers, tier-3 synthesis.

`pipeline` stays out of this namespace deliberately: importing it pulls the
LLM stack in, and `run_install` must be able to promise `--no-generate`
never reaches one.
"""

from .context import ResolvedTool, resolve_tool

__all__ = ["ResolvedTool", "resolve_tool"]
