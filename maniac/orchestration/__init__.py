"""`install` and `why`: acting on one tool, and explaining what would happen.

`install` resolves the tool once (`context`), walks the source order from
`sources.lookup.candidates`, installs a release (`install`) or generates a
page (`pipeline`); `why` reports the same lookups and decisions without
acting.

`pipeline` stays out of this namespace deliberately: importing it pulls the
LLM stack in, and `run_install` must be able to promise `--no-generate`
never reaches one.
"""

from .context import ResolvedTool, resolve_tool

__all__ = ["ResolvedTool", "resolve_tool"]
