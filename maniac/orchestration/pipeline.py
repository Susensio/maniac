"""End-to-end pipeline orchestrator."""

from pathlib import Path

from ..config import Config
from ..exceptions import GenerationError
from ..generation.compiler import compile_to_man
from ..generation.llm import run_llm_synthesis
from ..generation.prompts import build_synthesis_prompt, load_system_prompt
from ..installer import install_manpage
from ..logging import logger
from ..manifest import Tier
from ..models import PipelineResult
from ..sources.crawler import find_subcommands, format_help_block, get_version
from ..sources.discovery import discover_repo, find_installation
from ..sources.docs import fetch_and_extract_docs, format_docs_section


def run_pipeline(
    tool_name: str,
    cache_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    intermediate_dir: str | Path | None = None,
    prompt_file: str | Path | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    install: bool = False,
    force: bool = False,
    dry_run: bool = False,
    config: Config | None = None,
    bin_dir: str | Path | None = None,
) -> PipelineResult:
    """Run the complete pipeline to extract docs, synthesize, and compile a manpage."""
    cfg = config or Config()
    c_dir = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
    out_dir = Path(output_dir) if output_dir is not None else cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    inter_dir = (
        Path(intermediate_dir) if intermediate_dir is not None else cfg.intermediate_dir
    )
    inter_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting CLI help and subcommands", tool=tool_name)
    executable = Path(bin_dir) / tool_name if bin_dir is not None else tool_name
    tree = find_subcommands([str(executable)])
    help_block = format_help_block(tree)

    logger.info("Discovering source and extracting documentation", tool=tool_name)
    source = (
        discover_repo(tool_name, bin_dir=bin_dir)
        if bin_dir is not None
        else discover_repo(tool_name)
    )
    # Resolved separately from `source` above: shares `find_installation`'s
    # own bin-path resolution rather than `source`'s, since only the
    # `Installation` carries the version tier-3 extraction needs to match a
    # tag against (ADR-0019). A caller with an `Installation` already in
    # hand (`run_install`'s tiers 1-2) has none to thread down when
    # `--generate` skips straight to tier 3, so this stays self-contained.
    found = find_installation(tool_name, bin_dir=bin_dir)
    installed_version = found[1].version if found is not None else None
    doc_files, version_matched = (
        fetch_and_extract_docs(
            source,
            cache_dir=c_dir,
            max_total_chars=cfg.max_total_doc_chars,
            version=installed_version,
        )
        if source is not None
        else ([], False)
    )
    docs_block = format_docs_section(doc_files)

    if len(tree) == 1 and not doc_files:
        raise GenerationError(
            f"Not enough source material for '{tool_name}': only root --help is "
            "available, with no subcommands or upstream documentation."
        )

    # Save intermediate extracted context
    context_content = (
        f"# {tool_name} Extracted Context\n\n"
        f"## CLI Help\n```text\n{help_block}\n```\n\n"
        f"## Repository Documentation\n{docs_block}\n"
    )
    context_file = inter_dir / f"{tool_name}_context.md"
    context_file.write_text(context_content, encoding="utf-8")
    logger.info("Saved intermediate context", path=str(context_file))

    system_prompt = load_system_prompt(prompt_file)
    full_prompt = build_synthesis_prompt(
        tool_name=tool_name,
        help_text=help_block,
        doc_text=docs_block,
        system_prompt=system_prompt,
    )

    # Save intermediate full prompt
    prompt_save_file = inter_dir / f"{tool_name}_prompt.md"
    prompt_save_file.write_text(full_prompt, encoding="utf-8")
    logger.info("Saved intermediate prompt", path=str(prompt_save_file))

    if dry_run:
        logger.info("Dry run: skipping LLM synthesis")
        dummy_md = (
            f"% {tool_name.upper()}(1) | User Commands\n\n# NAME\n{tool_name} - dry run"
        )
        md_file = out_dir / f"{tool_name}.1.md"
        md_file.write_text(dummy_md, encoding="utf-8")
        return PipelineResult(
            tool_name=tool_name,
            repo_source=source,
            command_count=len(tree),
            doc_file_count=len(doc_files),
            context_path=context_file,
            prompt_path=prompt_save_file,
            markdown_path=md_file,
            roff_path=None,
            installed_path=None,
            markdown_content=dummy_md,
        )

    markdown_content = run_llm_synthesis(
        full_prompt,
        tool_name=tool_name,
        model=model,
        reasoning_effort=reasoning_effort,
        config=cfg,
    )

    md_file = out_dir / f"{tool_name}.1.md"
    md_file.write_text(markdown_content, encoding="utf-8")
    logger.info("Saved Markdown manpage", path=str(md_file))

    roff_file = out_dir / f"{tool_name}.1"
    selected_model = cfg.model_for_metadata(model)
    compiled = compile_to_man(
        markdown_content, roff_file, tool_name=tool_name, model=selected_model
    )
    actual_roff_path = roff_file if compiled else None

    installed_path = None
    if install and actual_roff_path and actual_roff_path.exists():
        if found is not None:
            # `version_matched` -- not `installed_version is not None` -- is
            # the recorded fact (ADR-0019): a page built from default-branch
            # docs must record no version even though the binary has one.
            recorded_version = installed_version if version_matched else None
        else:
            # ADR-0020: no provider claims this binary, so there is no
            # `Installation` to match a doc tag against -- `installed_version`
            # is None and `version_matched` is always False here. Recording
            # the binary's own `--version` output is still consistent with
            # ADR-0019 rather than an exception to it: a help-only page
            # documents exactly the binary that was crawled, so that
            # binary's own version report is matched evidence, more directly
            # than a tag match is.
            recorded_version = get_version([str(executable)])
        installed_path = install_manpage(
            actual_roff_path,
            tool_name,
            Tier.SYNTHESIS,
            selected_model or "unknown",
            force=force,
            version=recorded_version,
        )

    return PipelineResult(
        tool_name=tool_name,
        repo_source=source,
        command_count=len(tree),
        doc_file_count=len(doc_files),
        context_path=context_file,
        prompt_path=prompt_save_file,
        markdown_path=md_file,
        roff_path=actual_roff_path,
        installed_path=installed_path,
        markdown_content=markdown_content,
    )
