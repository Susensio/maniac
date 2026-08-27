"""Analyze flag and command coverage deterministically."""

import re
from pathlib import Path

from maniac.config import Config


def extract_flags_from_help(help_text: str) -> set[str]:
    """Extract flags like --flag and -f from help text."""
    flags = set()
    for line in help_text.splitlines():
        matches = re.findall(r"(?:^|\s)(--[a-zA-Z0-9][-a-zA-Z0-9]*)", line)
        for m in matches:
            if not m.startswith("--help") and len(m) > 3:
                flags.add(m)
    return flags


def extract_subcommands_from_help(help_text: str) -> set[str]:
    """Extract subcommand invocation headers like '> uv add --help'."""
    cmds = set()
    for line in help_text.splitlines():
        if line.startswith("> "):
            parts = line.strip()[2:].replace(" --help", "").strip()
            cmds.add(parts)
    return cmds


def check_coverage(config: Config | None = None) -> None:
    cfg = config or Config()
    for tool in ["howdoi", "hx", "uv"]:
        ctx_file = cfg.intermediate_dir / f"{tool}_context.md"
        if not ctx_file.exists():
            continue
        ctx = ctx_file.read_text(encoding="utf-8")

        help_match = re.search(r"## CLI Help\s*```text\s*(.*?)\s*```", ctx, re.DOTALL)
        help_text = help_match.group(1) if help_match else ctx

        scraped_flags = extract_flags_from_help(help_text)
        scraped_cmds = extract_subcommands_from_help(help_text)

        print(
            f"\n=== {tool.upper()} (Scraped: {len(scraped_cmds)} commands, {len(scraped_flags)} unique long flags) ==="
        )

        for model_key in ["flash-high", "flash-medium", "flash-low", "flash-3.5-low"]:
            md_file = Path(f"data/benchmark/{model_key}/{tool}.1.md")
            if not md_file.exists():
                continue
            md = md_file.read_text(encoding="utf-8")

            found_flags = {f for f in scraped_flags if f in md}
            found_cmds = {c for c in scraped_cmds if c in md}
            missing_cmds = scraped_cmds - found_cmds

            flag_cov = (
                len(found_flags) / len(scraped_flags) * 100 if scraped_flags else 100
            )
            cmd_cov = len(found_cmds) / len(scraped_cmds) * 100 if scraped_cmds else 100

            print(
                f"[{model_key}] Flags: {len(found_flags)}/{len(scraped_flags)} ({flag_cov:.1f}%) | Cmds: {len(found_cmds)}/{len(scraped_cmds)} ({cmd_cov:.1f}%)"
            )
            if missing_cmds and len(missing_cmds) <= 5:
                print(f"   Missing commands: {sorted(missing_cmds)}")
            elif missing_cmds:
                print(
                    f"   Missing {len(missing_cmds)} commands (e.g. {sorted(missing_cmds)[:4]}...)"
                )


if __name__ == "__main__":
    check_coverage()
