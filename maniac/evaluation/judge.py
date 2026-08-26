import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json_repair

from ..config import Config
from ..generation.llm import run_llm_synthesis
from ..generation.prompts import load_template
from ..logging import logger
from ..models import EvaluationResult
from ..sources.crawler import extract_subcommands

REQUIRED_SECTIONS: list[str | tuple[str, ...]] = [
    "NAME",
    "SYNOPSIS",
    "DESCRIPTION",
    ("OPTIONS", "COMMANDS"),  # At least one of OPTIONS or COMMANDS
    "EXIT STATUS",
    "EXAMPLES",
]

SECTION_SYNONYMS: dict[str, set[str]] = {
    "NAME": {"NAME"},
    "SYNOPSIS": {"SYNOPSIS", "USAGE"},
    "DESCRIPTION": {"DESCRIPTION"},
    "OPTIONS": {
        "OPTIONS",
        "FLAGS",
        "GLOBAL OPTIONS",
        "OPTIONS & FLAGS",
        "OPTIONS AND FLAGS",
        "COMMAND-LINE OPTIONS",
    },
    "COMMANDS": {"COMMANDS", "SUBCOMMANDS", "PRIMARY OPERATIONS", "ACTIONS"},
    "EXIT STATUS": {
        "EXIT STATUS",
        "EXIT CODES",
        "EXIT CODE",
        "RETURN VALUE",
        "DIAGNOSTICS",
    },
    "EXAMPLES": {"EXAMPLES", "EXAMPLE", "USAGE EXAMPLES", "WORKFLOW EXAMPLES"},
}


@dataclass(slots=True)
class CoverageStats:
    """Structured coverage statistics for CLI subcommands and flags."""

    found_cmds: list[str]
    missing_cmds: list[str]
    cmd_pct: float
    found_flags: list[str]
    missing_flags: list[str]
    flag_pct: float


def get_default_eval_prompt() -> str:
    """Load default evaluation rubric prompt from bundled markdown file."""
    return load_template("eval_prompt.md", "Evaluate manpage quality.\n")


def check_metadata_header(
    markdown_text: str, tool_name: str | None = None
) -> tuple[bool, str | None]:
    """Validate metadata header: % TOOL(1) | User Commands."""
    lines = [
        line.strip() for line in markdown_text.strip().splitlines() if line.strip()
    ]
    if not lines:
        return False, "Empty markdown content; missing metadata header."

    first_line = lines[0]
    if not first_line.startswith("% "):
        return False, f"First line must start with '% '. Found: '{first_line}'"

    match = re.match(r"^%\s*([A-Za-z0-9_\-\.]+)\s*\(\s*1\s*\)\s*\|\s*(.+)$", first_line)
    if not match:
        return False, (
            f"Metadata header does not match expected format '% TOOL(1) | User Commands'. "
            f"Found: '{first_line}'"
        )

    if tool_name:
        header_tool = match.group(1).upper()
        if header_tool != tool_name.upper():
            return False, (
                f"Metadata header tool name '{header_tool}' does not match expected tool '{tool_name.upper()}'."
            )

    return True, None


def check_standard_sections(
    markdown_text: str,
    required_sections: list[str | tuple[str, ...]] | None = None,
) -> tuple[bool, list[str]]:
    """Check presence of standard required sections with heading level and title normalization."""
    sections = required_sections if required_sections is not None else REQUIRED_SECTIONS
    errors: list[str] = []
    found_headers: set[str] = set()

    for line in markdown_text.splitlines():
        line_str = line.strip()
        match = re.match(r"^#{1,3}\s+(.+)$", line_str)
        if match:
            clean_title = match.group(1).strip().rstrip(":").strip().upper()
            found_headers.add(clean_title)

    def is_present(req_name: str) -> bool:
        synonyms = SECTION_SYNONYMS.get(req_name, {req_name})
        for header in found_headers:
            for syn in synonyms:
                if header == syn or header.startswith((f"{syn} ", f"{syn}:")):
                    return True
        return False

    for req in sections:
        if isinstance(req, tuple):
            if not any(is_present(alt) for alt in req):
                alts_str = " or ".join(f"#{alt}" for alt in req)
                errors.append(
                    f"Missing required section: at least one of {alts_str} must be present."
                )
        else:
            if not is_present(req):
                errors.append(f"Missing required section: #{req}")

    return len(errors) == 0, errors


def check_pandoc_compilation(
    markdown_text: str, timeout: int | None = None
) -> tuple[bool, str | None]:
    """Validate Pandoc roff compilation (`pandoc -s -f markdown-smart -t man`)."""
    pandoc_bin = shutil.which("pandoc")
    if not pandoc_bin:
        return True, None

    timeout_sec = timeout if timeout is not None else Config().timeout_pandoc
    try:
        res = subprocess.run(
            [pandoc_bin, "-s", "-f", "markdown-smart", "-t", "man"],
            input=markdown_text,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if res.returncode != 0:
            return False, f"Pandoc compilation failed: {res.stderr.strip()}"
        return True, None
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Error running pandoc: {e}"


def compute_coverage(markdown_text: str, context_text: str) -> CoverageStats:
    """Compute command and flag coverage of markdown text against reference CLI help."""
    cli_help_context = context_text
    match = re.search(
        r"##\s+CLI Help.*?(?=(?:\n##\s+|\Z))",
        context_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        cli_help_context = match.group(0)

    expected_cmds: list[str] = []
    for match in re.finditer(
        r"^>\s*([a-zA-Z0-9_-]+(?:\s+[a-zA-Z0-9_-]+)+)\s+--help",
        cli_help_context,
        re.MULTILINE,
    ):
        full_cmd = match.group(1).strip()
        tokens = full_cmd.split()
        if len(tokens) > 1:
            sub = " ".join(tokens[1:])
            if sub not in expected_cmds:
                expected_cmds.append(sub)
        elif full_cmd not in expected_cmds:
            expected_cmds.append(full_cmd)

    for sub in extract_subcommands(cli_help_context):
        if sub not in expected_cmds:
            expected_cmds.append(sub)

    raw_flags = re.findall(
        r"(?<![\w/-])(-[a-zA-Z0-9]|--[a-zA-Z0-9][a-zA-Z0-9_-]*)(?=[=\s,.:;)\]*`\"']|$)",
        cli_help_context,
    )
    expected_flags: list[str] = []
    for f in raw_flags:
        if f not in expected_flags and f not in {"-", "--", "---", "--help", "-h"}:
            expected_flags.append(f)

    found_cmds: list[str] = []
    missing_cmds: list[str] = []
    for cmd in expected_cmds:
        pattern = r"\b" + re.escape(cmd) + r"\b"
        if re.search(pattern, markdown_text, re.IGNORECASE):
            found_cmds.append(cmd)
        else:
            missing_cmds.append(cmd)

    found_flags: list[str] = []
    missing_flags: list[str] = []
    for flag in expected_flags:
        pattern = r"(?<![\w/-])" + re.escape(flag) + r"(?![\w/-])"
        if re.search(pattern, markdown_text):
            found_flags.append(flag)
        else:
            missing_flags.append(flag)

    cmd_pct = (
        round(len(found_cmds) / len(expected_cmds) * 100.0, 1)
        if expected_cmds
        else 100.0
    )
    flag_pct = (
        round(len(found_flags) / len(expected_flags) * 100.0, 1)
        if expected_flags
        else 100.0
    )

    return CoverageStats(
        found_cmds=found_cmds,
        missing_cmds=missing_cmds,
        cmd_pct=cmd_pct,
        found_flags=found_flags,
        missing_flags=missing_flags,
        flag_pct=flag_pct,
    )


def run_deterministic_checks(
    markdown_text: str,
    tool_name: str | None = None,
    context_text: str | None = None,
    cov: CoverageStats | None = None,
) -> tuple[bool, list[str]]:
    """Run all deterministic checks and collect defects.

    Hard fails occur on invalid metadata header, missing required structural sections,
    Pandoc compilation errors, or missing subcommands.
    Missing flags are advisory and do not trigger a hard boolean failure.
    """
    hard_defects: list[str] = []
    advisory_defects: list[str] = []

    header_ok, header_err = check_metadata_header(markdown_text, tool_name)
    if not header_ok and header_err:
        hard_defects.append(header_err)

    sections_ok, section_errs = check_standard_sections(markdown_text)
    if not sections_ok:
        hard_defects.extend(section_errs)

    pandoc_ok, pandoc_err = check_pandoc_compilation(markdown_text)
    if not pandoc_ok and pandoc_err:
        hard_defects.append(pandoc_err)

    if context_text:
        coverage = (
            cov if cov is not None else compute_coverage(markdown_text, context_text)
        )
        if coverage.missing_cmds:
            hard_defects.append(
                f"Missing subcommands from context ({coverage.cmd_pct:.1f}% covered): {', '.join(coverage.missing_cmds)}"
            )
        if coverage.missing_flags and coverage.flag_pct < 100.0:
            advisory_defects.append(
                f"Missing flags from context ({coverage.flag_pct:.1f}% covered): {', '.join(coverage.missing_flags)}"
            )

    all_defects = hard_defects + advisory_defects
    return len(hard_defects) == 0, all_defects


def render_manpage_to_terminal(
    markdown_text: str, timeout: int | None = None
) -> str | None:
    """Render markdown manpage to terminal formatted text (as seen in man)."""
    pandoc_bin = shutil.which("pandoc")
    groff_bin = shutil.which("groff")

    if not pandoc_bin:
        return None

    timeout_sec = timeout if timeout is not None else Config().timeout_pandoc
    try:
        if markdown_text.strip().startswith(('.\\"', ".TH", "'\\\"")):
            roff_text = markdown_text
        else:
            res = subprocess.run(
                [pandoc_bin, "-s", "-f", "markdown-smart", "-t", "man"],
                input=markdown_text,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if res.returncode != 0:
                return None
            roff_text = res.stdout

        if groff_bin:
            groff_res = subprocess.run(
                [groff_bin, "-Tutf8", "-man"],
                input=roff_text,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if groff_res.returncode == 0 and groff_res.stdout.strip():
                return groff_res.stdout

        plain_res = subprocess.run(
            [pandoc_bin, "-s", "-f", "markdown-smart", "-t", "plain"],
            input=markdown_text,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if plain_res.returncode == 0 and plain_res.stdout.strip():
            return plain_res.stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Failed to render manpage to terminal text", error=str(e))

    return None


def build_evaluation_prompt(
    tool_name: str,
    manpage_text: str,
    context_text: str,
    cov: CoverageStats | None = None,
    pass_threshold: int = 70,
) -> str:
    """Construct structured evaluation prompt for LLM judge with automated coverage analysis and rendered output."""
    raw_prompt = get_default_eval_prompt()
    system_prompt = raw_prompt.replace("{PASS_THRESHOLD}", str(pass_threshold))
    coverage = (
        cov
        if cov is not None
        else (compute_coverage(manpage_text, context_text) if context_text else None)
    )
    rendered_text = render_manpage_to_terminal(manpage_text)

    if coverage:
        coverage_summary = (
            f"=== AUTOMATED COVERAGE ANALYSIS ===\n"
            f"- Subcommand Coverage: {coverage.cmd_pct:.1f}% ({len(coverage.found_cmds)}/{len(coverage.found_cmds) + len(coverage.missing_cmds)} found)\n"
            f"- Missing Subcommands: {', '.join(coverage.missing_cmds) if coverage.missing_cmds else 'None'}\n"
            f"- Flag/Option Coverage: {coverage.flag_pct:.1f}% ({len(coverage.found_flags)}/{len(coverage.found_flags) + len(coverage.missing_flags)} found)\n"
            f"- Missing Flags: {', '.join(coverage.missing_flags) if coverage.missing_flags else 'None'}\n"
            f"=== END COVERAGE ANALYSIS ==="
        )
    else:
        coverage_summary = "=== AUTOMATED COVERAGE ANALYSIS ===\nNo reference context provided.\n=== END COVERAGE ANALYSIS ==="

    if rendered_text:
        rendered_section = (
            f"=== RENDERED TERMINAL MANUAL PAGE (WHAT THE USER SEES IN `man {tool_name}`) ===\n"
            f"{rendered_text}\n"
            f"=== END RENDERED MANUAL PAGE ==="
        )
    else:
        rendered_section = (
            "=== RENDERED TERMINAL MANUAL PAGE ===\n"
            "[Terminal rendering unavailable - pandoc/groff not installed or rendering failed]\n"
            "=== END RENDERED MANUAL PAGE ==="
        )

    return f"""{system_prompt}

=== REFERENCE CONTEXT FOR '{tool_name}' ===
{context_text}
=== END REFERENCE CONTEXT ===

{rendered_section}

=== RAW SOURCE MARKDOWN ===
{manpage_text}
=== END RAW SOURCE MARKDOWN ===

{coverage_summary}

Evaluate the manpage for '{tool_name}' against the reference context according to the rubric.
Judge the manual primarily on what the user experiences in the rendered output: clarity, complete flag documentation, practical workflow examples, and domain understanding.
Output ONLY the JSON object.
"""


def _extract_category_score(rubric: dict[str, Any], *candidate_keys: str) -> int:
    """Extract integer score from rubric dict attempting multiple candidate keys."""
    for key in candidate_keys:
        if key in rubric:
            try:
                return int(rubric[key])
            except (ValueError, TypeError):
                continue
    return 0


def parse_evaluation_json(raw_response: str) -> EvaluationResult:
    """Parse structured JSON from LLM response with robust fallback repair."""
    text = raw_response.strip()

    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            lines = [
                line for line in text.splitlines() if not line.strip().startswith("```")
            ]
            text = "\n".join(lines).strip()

    if not text.startswith("{"):
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    data: Any
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        repaired = json_repair.loads(text)
        if isinstance(repaired, dict):
            data = repaired
        else:
            raise json.JSONDecodeError(
                "Failed to parse JSON from LLM response", text, 0
            )

    if not isinstance(data, dict):
        raise json.JSONDecodeError("Parsed JSON is not an object", text, 0)

    rubric = data.get("rubric_breakdown", {})

    breakdown = {
        "domain_ontology": _extract_category_score(rubric, "domain_ontology"),
        "correctness_coverage": _extract_category_score(
            rubric, "correctness_coverage", "correctness", "workflow_examples"
        ),
        "formatting": _extract_category_score(
            rubric, "formatting", "readability", "terminal_presentation"
        ),
        "subsystem_grouping": _extract_category_score(
            rubric, "subsystem_grouping", "organization"
        ),
        "environment_reference_examples": _extract_category_score(
            rubric, "environment_reference_examples", "environment_files_exit"
        ),
    }

    calculated_score = sum(breakdown.values())
    score = int(data.get("score", calculated_score))
    passed = bool(data.get("passed", score >= 70))
    defects = [str(d) for d in data.get("defects", [])]
    summary = str(data.get("summary", ""))

    return EvaluationResult(
        score=score,
        passed=passed,
        rubric_breakdown=breakdown,
        defects=defects,
        summary=summary,
    )


def run_llm_judge(
    tool_name: str,
    manpage_text: str,
    context_text: str,
    model: str | None = None,
    pass_threshold: int = 70,
    work_base_dir: str | Path | None = None,
    cov: CoverageStats | None = None,
) -> EvaluationResult:
    """Run LLM-as-a-Judge quality evaluation against reference context."""
    target_work_dir = (
        Path(work_base_dir) if work_base_dir is not None else Config().work_base_dir
    )
    prompt = build_evaluation_prompt(
        tool_name,
        manpage_text,
        context_text,
        cov=cov,
        pass_threshold=pass_threshold,
    )
    raw_output = run_llm_synthesis(
        prompt=prompt,
        tool_name=tool_name,
        model=model,
        work_base_dir=target_work_dir,
        clean_header=False,
    )
    try:
        result = parse_evaluation_json(raw_output)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.error("Failed to parse LLM evaluation JSON", error=str(e))
        logger.debug("Raw LLM output was", raw_output=raw_output)
        return EvaluationResult(
            score=0,
            passed=False,
            rubric_breakdown={
                "domain_ontology": 0,
                "correctness_coverage": 0,
                "formatting": 0,
                "subsystem_grouping": 0,
                "environment_reference_examples": 0,
            },
            defects=[f"Failed to parse LLM judge response: {e}"],
            summary="Evaluation failed due to malformed LLM response.",
        )

    result.passed = result.score >= pass_threshold
    return result


def evaluate_manpage(
    tool_name: str,
    manpage_text: str,
    context_text: str,
    model: str | None = None,
    pass_threshold: int = 70,
    work_base_dir: str | Path | None = None,
) -> EvaluationResult:
    """Run deterministic checks and LLM-as-a-Judge quality evaluation."""
    cov = compute_coverage(manpage_text, context_text) if context_text else None

    # 1. Deterministic checks
    det_passed, det_defects = run_deterministic_checks(
        manpage_text, tool_name=tool_name, context_text=context_text, cov=cov
    )

    # 2. LLM Judge
    result = run_llm_judge(
        tool_name=tool_name,
        manpage_text=manpage_text,
        context_text=context_text,
        model=model,
        pass_threshold=pass_threshold,
        work_base_dir=work_base_dir,
        cov=cov,
    )

    result.deterministic_passed = det_passed
    result.deterministic_defects = det_defects
    if cov is not None:
        result.coverage = cov

    # Merge deterministic defects if any
    if det_defects:
        result.defects = det_defects + result.defects
    if not det_passed:
        result.passed = False

    return result
