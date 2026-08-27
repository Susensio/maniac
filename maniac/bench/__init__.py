"""Developer benchmark harness: generate manpages across models/tools and judge them."""

from .harness import DEFAULT_MODELS, DEFAULT_TOOLS, run_benchmark

__all__ = ["DEFAULT_MODELS", "DEFAULT_TOOLS", "run_benchmark"]
