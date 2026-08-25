"""Automated quality evaluation pipeline with deterministic checks and LLM-as-a-Judge."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from loguru import logger

from maniac.generation.llm import run_llm_synthesis
from maniac.models import EvaluationResult

EVALUATION_SYSTEM_PROMPT = """You are an expert technical documentation evaluator and Unix manual quality judge.
Your task is to evaluate a generated Unix manpage (in Markdown format) against the raw reference documentation/context of the CLI tool.

=== EVALUATION RUBRIC (Total 0-100 Points) ===

1. Domain Ontology & Architecture (0-20 Points)
- Does the DESCRIPTION section clearly explain what the tool does in terse prose?
- Does it define the tool's core architectural concepts/entities (e.g. sessions, buffers, workspaces) in Pandoc definition list format BEFORE jumping into commands/options?
- Score 17-20: Outstanding domain model with clear definitions of fundamental entities.
- Score 10-16: Basic explanation but missing key conceptual definitions.
- Score 0-9: No conceptual model, vague or missing description.

2. Flag & Command Formatting (0-20 Points)
- Are commands, subcommands, and flags structured strictly using Pandoc definition list syntax (term followed by 3-space indented ':   description')?
- Are literal flags/commands bolded (**--flag**) and user parameters italicized (*param*)?
- Are flags separated into individual entries instead of compressed prose sentences?
- Score 17-20: Flawless Pandoc definition lists, perfect typography and parameter styling.
- Score 10-16: Minor formatting lapses or occasional inline flag mentions.
- Score 0-9: Broken definition list syntax, missing formatting.

3. Subsystem Grouping (0-20 Points)
- Are commands and options categorized under intuitive H2 (##) conceptual subheaders (e.g., '## Search & Query Tuning', '## Connection & Security') rather than a flat alphabetical list?
- Are subcommands and subcommand flags cleanly organized?
- Score 17-20: Logical, intuitive conceptual grouping that simplifies navigation.
- Score 10-16: Partially grouped, or subheaders too broad/flat.
- Score 0-9: No subheaders, flat list of flags/commands.

4. Environment, Files, Exit Status, and Reference (0-20 Points)
- Does the manual include # ENVIRONMENT (documenting env vars), # FILES (config/cache paths), # EXIT STATUS (e.g., 0 success, >0 error codes)?
- Are # BUGS and # SEE ALSO sections appropriately populated?
- Score 17-20: Comprehensive reference sections covering all known env vars, config paths, and exit codes.
- Score 10-16: Missing 1-2 minor sections or sparse details.
- Score 0-9: Missing critical reference sections.

5. Workflow Examples (0-20 Points)
- Are there 3-5 realistic, practical workflow examples under # EXAMPLES?
- Does each example include an explanatory comment with the "why" (context/goal)?
- Are commands wrapped in ```bash code blocks?
- Score 17-20: High quality, realistic end-to-end workflows with clear comments.
- Score 10-16: 1-2 trivial or unannotated examples.
- Score 0-9: Missing or broken examples.

=== OUTPUT FORMAT ===
You MUST return ONLY a valid JSON object matching this schema:
```json
{
  "score": <total_integer_0_to_100>,
  "passed": <boolean_true_if_score_>=_70_and_no_critical_flaws>,
  "rubric_breakdown": {
    "domain_ontology": <integer_0_to_20>,
    "formatting": <integer_0_to_20>,
    "subsystem_grouping": <integer_0_to_20>,
    "environment_files_exit": <integer_0_to_20>,
    "workflow_examples": <integer_0_to_20>
  },
  "defects": [
    "<specific defect or suggestion for improvement>"
  ],
  "summary": "<1-2 sentence concise qualitative summary>"
}
```
"""


class DeterministicCheck:
    """Deterministic validation of Markdown manpages."""

    REQUIRED_SECTIONS: ClassVar[list[str | tuple[str, ...]]] = [
        "NAME",
        "SYNOPSIS",
        "DESCRIPTION",
        ("OPTIONS", "COMMANDS"),  # At least one of OPTIONS or COMMANDS
        "EXIT STATUS",
        "EXAMPLES",
    ]

    @classmethod
    def check_metadata_header(
        cls, markdown_text: str, tool_name: str | None = None
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

        match = re.match(
            r"^%\s*([A-Za-z0-9_\-\.]+)\s*\(\s*1\s*\)\s*\|\s*(.+)$", first_line
        )
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

    @classmethod
    def check_standard_sections(cls, markdown_text: str) -> tuple[bool, list[str]]:
        """Check presence of standard required sections."""
        errors: list[str] = []
        found_sections: set[str] = set()
        for line in markdown_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("# ") and not line_str.startswith("##"):
                section_title = line_str[2:].strip().upper()
                found_sections.add(section_title)

        for req in cls.REQUIRED_SECTIONS:
            if isinstance(req, tuple):
                if not any(alt in found_sections for alt in req):
                    alts_str = " or ".join(f"#{alt}" for alt in req)
                    errors.append(
                        f"Missing required section: at least one of {alts_str} must be present."
                    )
            else:
                if req not in found_sections:
                    errors.append(f"Missing required section: #{req}")

        return len(errors) == 0, errors

    @classmethod
    def check_pandoc_compilation(cls, markdown_text: str) -> tuple[bool, str | None]:
        """Validate Pandoc roff compilation (`pandoc -s -f markdown -t man`)."""
        pandoc_bin = shutil.which("pandoc")
        if not pandoc_bin:
            return False, "pandoc is not installed or not in PATH."

        try:
            res = subprocess.run(
                [pandoc_bin, "-s", "-f", "markdown", "-t", "man"],
                input=markdown_text,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if res.returncode != 0:
                return False, f"Pandoc compilation failed: {res.stderr.strip()}"
            return True, None
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"Error running pandoc: {e}"

    @classmethod
    def run(
        cls, markdown_text: str, tool_name: str | None = None
    ) -> tuple[bool, list[str]]:
        """Run all deterministic checks and collect defects."""
        defects: list[str] = []

        header_ok, header_err = cls.check_metadata_header(markdown_text, tool_name)
        if not header_ok and header_err:
            defects.append(header_err)

        sections_ok, section_errs = cls.check_standard_sections(markdown_text)
        if not sections_ok:
            defects.extend(section_errs)

        pandoc_ok, pandoc_err = cls.check_pandoc_compilation(markdown_text)
        if not pandoc_ok and pandoc_err:
            defects.append(pandoc_err)

        return len(defects) == 0, defects


def build_evaluation_prompt(
    tool_name: str,
    manpage_text: str,
    context_text: str,
) -> str:
    """Construct structured evaluation prompt for LLM judge."""
    return f"""{EVALUATION_SYSTEM_PROMPT}

=== REFERENCE CONTEXT FOR '{tool_name}' ===
{context_text}
=== END REFERENCE CONTEXT ===

=== GENERATED MANPAGE FOR '{tool_name}' ===
{manpage_text}
=== END GENERATED MANPAGE ===

Evaluate the generated manpage for '{tool_name}' against the reference context according to the rubric.
Output ONLY the JSON object.
"""


def parse_evaluation_json(raw_response: str) -> EvaluationResult:
    """Parse structured JSON from LLM response."""
    text = raw_response.strip()

    # Unwrap triple backticks if present
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            lines = [
                line for line in text.splitlines() if not line.strip().startswith("```")
            ]
            text = "\n".join(lines).strip()

    # Extract JSON substring if surrounded by other characters
    if not text.startswith("{"):
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    data = json.loads(text)

    rubric = data.get("rubric_breakdown", {})
    breakdown = {
        "domain_ontology": int(rubric.get("domain_ontology", 0)),
        "formatting": int(rubric.get("formatting", 0)),
        "subsystem_grouping": int(rubric.get("subsystem_grouping", 0)),
        "environment_files_exit": int(rubric.get("environment_files_exit", 0)),
        "workflow_examples": int(rubric.get("workflow_examples", 0)),
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


class LLMJudge:
    """LLM-as-a-Judge quality evaluator for synthesized Unix manpages."""

    def __init__(
        self,
        model: str | None = None,
        pass_threshold: int = 70,
    ) -> None:
        self.model = model
        self.pass_threshold = pass_threshold

    def evaluate(
        self,
        tool_name: str,
        manpage_text: str,
        context_text: str,
        work_base_dir: str | Path = "data/tmp",
    ) -> EvaluationResult:
        """Run LLM evaluation against reference context."""
        prompt = build_evaluation_prompt(tool_name, manpage_text, context_text)
        raw_output = run_llm_synthesis(
            prompt=prompt,
            tool_name=tool_name,
            model=self.model,
            work_base_dir=work_base_dir,
            clean_header=False,
        )
        try:
            result = parse_evaluation_json(raw_output)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.error("Failed to parse LLM evaluation JSON: {}", e)
            logger.debug("Raw LLM output was: {}", raw_output)
            return EvaluationResult(
                score=0,
                passed=False,
                rubric_breakdown={
                    "domain_ontology": 0,
                    "formatting": 0,
                    "subsystem_grouping": 0,
                    "environment_files_exit": 0,
                    "workflow_examples": 0,
                },
                defects=[f"Failed to parse LLM judge response: {e}"],
                summary="Evaluation failed due to malformed LLM response.",
            )

        if result.score < self.pass_threshold:
            result.passed = False

        return result


def evaluate_manpage(
    tool_name: str,
    manpage_text: str,
    context_text: str,
    model: str | None = None,
    pass_threshold: int = 70,
    work_base_dir: str | Path = "data/tmp",
) -> EvaluationResult:
    """Run deterministic checks and LLM-as-a-Judge quality evaluation."""
    # 1. Deterministic checks
    det_passed, det_defects = DeterministicCheck.run(manpage_text, tool_name=tool_name)

    # 2. LLM Judge
    judge = LLMJudge(model=model, pass_threshold=pass_threshold)
    result = judge.evaluate(
        tool_name=tool_name,
        manpage_text=manpage_text,
        context_text=context_text,
        work_base_dir=work_base_dir,
    )

    # Merge deterministic defects if any
    if det_defects:
        result.defects = det_defects + result.defects
    if not det_passed:
        result.passed = False

    return result
