from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from maniac.compiler import compile_to_man, install_manpage
from maniac.crawler import find_subcommands, format_help_block
from maniac.discovery import RepoSource, discover_repo
from maniac.docs import fetch_and_extract_docs, format_docs_section
from maniac.llm import run_llm_synthesis
from maniac.prompts import build_synthesis_prompt, load_system_prompt


@dataclass
class PipelineResult:
    tool_name: str
    repo_source: RepoSource
    command_count: int
    doc_file_count: int
    context_path: Path | None
    prompt_path: Path | None
    markdown_path: Path
    roff_path: Path | None
    installed_path: Path | None
    markdown_content: str


def run_pipeline(
    tool_name: str,
    cache_dir: str | Path = "data/repos",
    output_dir: str | Path = "data/manpages",
    intermediate_dir: str | Path = "data/intermediate",
    prompt_file: str | Path | None = None,
    install: bool = False,
    dry_run: bool = False,
) -> PipelineResult:
    """Run the complete pipeline to extract docs, synthesize, and compile a manpage."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    inter_dir = Path(intermediate_dir)
    inter_dir.mkdir(parents=True, exist_ok=True)

    # 1. Scrape CLI help
    logger.info("Extracting CLI help and subcommands for '{}'...", tool_name)
    tree = find_subcommands([tool_name])
    help_block = format_help_block(tree)

    # 2. Discover repository and extract documentation
    logger.info(
        "Discovering source and extracting documentation for '{}'...", tool_name
    )
    source = discover_repo(tool_name)
    doc_files = fetch_and_extract_docs(source, cache_dir=cache_dir)
    docs_block = format_docs_section(doc_files)

    # Save intermediate extracted context
    context_content = f"# {tool_name} Extracted Context\n\n## CLI Help\n```text\n{help_block}\n```\n\n## Repository Documentation\n{docs_block}\n"
    context_file = inter_dir / f"{tool_name}_context.md"
    context_file.write_text(context_content, encoding="utf-8")
    logger.info("Saved intermediate context to {}", context_file)

    # 3. Build prompt
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
    logger.info("Saved intermediate prompt to {}", prompt_save_file)

    if dry_run:
        logger.info("Dry run: skipping LLM synthesis.")
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

    # 4. Run LLM synthesis
    markdown_content = run_llm_synthesis(full_prompt, tool_name=tool_name)

    # 5. Save Markdown manpage
    md_file = out_dir / f"{tool_name}.1.md"
    md_file.write_text(markdown_content, encoding="utf-8")
    logger.info("Saved Markdown manpage to {}", md_file)

    # 6. Compile to roff using pandoc
    roff_file = out_dir / f"{tool_name}.1"
    compiled = compile_to_man(markdown_content, roff_file)
    actual_roff_path = roff_file if compiled else None

    # 7. Install to user's manpath if requested
    installed_path = None
    if install and actual_roff_path and actual_roff_path.exists():
        installed_path = install_manpage(actual_roff_path)

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
