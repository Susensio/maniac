"""Tier-3 synthesis: crawl help, extract repository docs, compile a manpage.

`synthesize` takes facts, never a tool name: a `ResolvedTool` its caller
already holds. `run_install` reaches here only after tiers 1-2 declined, so
it passes down the installation, provider and documentation source it
resolved for them. A caller entering synthesis directly, with no prior
install, calls `resolve_tool` first -- the one explicit entry that turns a
name into those facts, and the only way to obtain them.
"""

from pathlib import Path

from ..exceptions import CrawlerError, GenerationError
from ..generation.compiler import compile_to_man
from ..generation.llm import run_llm_synthesis
from ..generation.prompts import build_synthesis_prompt, load_system_prompt
from ..installer import install_manpage
from ..logging import logger
from ..manifest import Tier
from ..models import DocFile, PipelineResult
from ..sources.crawler import find_subcommands, format_help_block, get_version
from ..sources.docs import fetch_and_extract_docs
from ..sources.docs.extraction import format_docs_section
from .context import ResolvedTool

__all__ = ["synthesize"]


def synthesize(
    tool: ResolvedTool,
    *,
    output_dir: str | Path | None = None,
    intermediate_dir: str | Path | None = None,
    prompt_file: str | Path | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    install: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> PipelineResult:
    """Extract source material for an already-resolved tool, synthesize, compile."""
    cfg = tool.config
    out_dir = Path(output_dir) if output_dir is not None else cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    inter_dir = (
        Path(intermediate_dir) if intermediate_dir is not None else cfg.intermediate_dir
    )
    inter_dir.mkdir(parents=True, exist_ok=True)

    tree = _crawl_help(tool)
    doc_files, version_matched = _extract_docs(tool)
    help_block = format_help_block(tree)
    docs_block = format_docs_section(doc_files)
    _report_material(tool, tree=tree, doc_files=doc_files, matched=version_matched)

    context_file = _write_intermediate(
        inter_dir / f"{tool.tool_name}_context.md",
        f"# {tool.tool_name} Extracted Context\n\n"
        f"## CLI Help\n```text\n{help_block}\n```\n\n"
        f"## Repository Documentation\n{docs_block}\n",
        "Saved intermediate context",
    )
    full_prompt = build_synthesis_prompt(
        tool_name=tool.tool_name,
        help_text=help_block,
        doc_text=docs_block,
        system_prompt=load_system_prompt(prompt_file),
    )
    prompt_save_file = _write_intermediate(
        inter_dir / f"{tool.tool_name}_prompt.md",
        full_prompt,
        "Saved intermediate prompt",
    )

    md_file = out_dir / f"{tool.tool_name}.1.md"
    if dry_run:
        logger.info("Dry run: skipping LLM synthesis")
        dummy_md = (
            f"% {tool.tool_name.upper()}(1) | User Commands\n\n"
            f"# NAME\n{tool.tool_name} - dry run"
        )
        md_file.write_text(dummy_md, encoding="utf-8")
        return _result(
            tool,
            tree=tree,
            doc_files=doc_files,
            context_path=context_file,
            prompt_path=prompt_save_file,
            markdown_path=md_file,
            roff_path=None,
            installed_path=None,
            markdown_content=dummy_md,
        )

    markdown_content = run_llm_synthesis(
        full_prompt,
        tool_name=tool.tool_name,
        model=model,
        reasoning_effort=reasoning_effort,
        config=cfg,
    )
    md_file.write_text(markdown_content, encoding="utf-8")
    logger.info("Saved Markdown manpage", path=str(md_file))

    roff_file = out_dir / f"{tool.tool_name}.1"
    selected_model = cfg.model_for_metadata(model)
    compiled = compile_to_man(
        markdown_content,
        roff_file,
        tool_name=tool.tool_name,
        model=selected_model,
        config=cfg,
    )
    actual_roff_path = roff_file if compiled else None

    installed_path = None
    if install and actual_roff_path and actual_roff_path.exists():
        installed_path = install_manpage(
            actual_roff_path,
            tool.tool_name,
            Tier.SYNTHESIS,
            selected_model or "unknown",
            force=force,
            version=_recorded_version(tool, matched=version_matched),
            config=cfg,
        )

    return _result(
        tool,
        tree=tree,
        doc_files=doc_files,
        context_path=context_file,
        prompt_path=prompt_save_file,
        markdown_path=md_file,
        roff_path=actual_roff_path,
        installed_path=installed_path,
        markdown_content=markdown_content,
    )


def _crawl_help(tool: ResolvedTool) -> dict[str, str]:
    """Crawled command tree, empty when the binary's `--help` yielded nothing usable."""
    logger.info("Extracting CLI help and subcommands", tool=tool.tool_name)
    try:
        return find_subcommands([tool.executable], config=tool.config)
    except CrawlerError as error:
        # Repository documentation can still be enough to synthesize a page.
        logger.warning(
            "CLI help crawl failed; continuing with repository docs", error=str(error)
        )
        return {}


def _extract_docs(tool: ResolvedTool) -> tuple[list[DocFile], bool]:
    """Documentation files and whether they came from a version-matched tag."""
    logger.info("Discovering source and extracting documentation", tool=tool.tool_name)
    source = tool.documentation_source
    if source is None:
        return [], False
    return fetch_and_extract_docs(
        source,
        cache_dir=tool.cache_dir,
        max_total_chars=tool.config.max_total_doc_chars,
        version=tool.installed_version,
        config=tool.config,
    )


def _report_material(
    tool: ResolvedTool, *, tree: dict[str, str], doc_files: list[DocFile], matched: bool
) -> None:
    """Report what synthesis will run on, refusing when neither source yielded material."""
    if not tree and not doc_files:
        raise GenerationError(
            f"Not enough source material for '{tool.tool_name}': no usable --help "
            "output or repository documentation was found."
        )

    source = tool.documentation_source
    logger.warning(
        "Synthesis source material found",
        tool=tool.tool_name,
        commands=len(tree),
        subcommands=max(len(tree) - 1, 0),
        repository=source.identity if source is not None else None,
        repository_docs=len(doc_files),
        repository_docs_version_matched=matched,
    )
    if len(tree) == 1 and not doc_files:
        logger.warning(
            "Limited source material: synthesizing from root --help only",
            tool=tool.tool_name,
        )


def _recorded_version(tool: ResolvedTool, *, matched: bool) -> str | None:
    """Version the manifest may claim for a synthesized page (ADR-0019)."""
    if tool.installation is not None:
        # `matched` -- not `installed_version is not None` -- is the recorded
        # fact (ADR-0019): a page built from default-branch docs must record
        # no version even though the binary has one.
        return tool.installed_version if matched else None
    # ADR-0020: no provider claims this binary, so there is no `Installation`
    # to match a doc tag against -- `installed_version` is None and `matched`
    # is always False here. Recording the binary's own `--version` output is
    # still consistent with ADR-0019 rather than an exception to it: a
    # help-only page documents exactly the binary that was crawled, so that
    # binary's own version report is matched evidence, more directly than a
    # tag match is.
    return get_version([tool.executable], config=tool.config)


def _write_intermediate(path: Path, content: str, event: str) -> Path:
    path.write_text(content, encoding="utf-8")
    logger.info(event, path=str(path))
    return path


def _result(
    tool: ResolvedTool,
    *,
    tree: dict[str, str],
    doc_files: list[DocFile],
    context_path: Path | None,
    prompt_path: Path | None,
    markdown_path: Path,
    roff_path: Path | None,
    installed_path: Path | None,
    markdown_content: str,
) -> PipelineResult:
    return PipelineResult(
        tool_name=tool.tool_name,
        repo_source=tool.documentation_source,
        command_count=len(tree),
        doc_file_count=len(doc_files),
        context_path=context_path,
        prompt_path=prompt_path,
        markdown_path=markdown_path,
        roff_path=roff_path,
        installed_path=installed_path,
        markdown_content=markdown_content,
    )
