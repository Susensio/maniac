"""End-to-end pipeline orchestrator."""

from pathlib import Path

from ..config import Config
from ..exceptions import GenerationError
from ..generation.compiler import compile_to_man
from ..generation.llm import run_llm_synthesis
from ..generation.prompts import build_synthesis_prompt, load_system_prompt
from ..installer import install_manpage
from ..logging import logger
from ..models import PipelineResult
from ..sources.crawler import find_subcommands, format_help_block
from ..sources.discovery import discover_repo
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
    doc_files = fetch_and_extract_docs(
        source, cache_dir=c_dir, max_total_chars=cfg.max_total_doc_chars
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
    selected_model = cfg.resolve_model(model)
    compiled = compile_to_man(
        markdown_content, roff_file, tool_name=tool_name, model=selected_model
    )
    actual_roff_path = roff_file if compiled else None

    installed_path = None
    if install and actual_roff_path and actual_roff_path.exists():
        installed_path = install_manpage(actual_roff_path, force=force)

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
