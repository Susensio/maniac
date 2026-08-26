"""Regression coverage for M8: `scripts/evaluate_all_matrix.py`.

`scripts/` isn't a package (no `__init__.py`) and isn't on `sys.path` by
default, so the module under test is loaded via an explicit path insert
rather than a normal `scripts.evaluate_all_matrix` import.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evaluate_all_matrix

from maniac.config import Config
from maniac.models import EvaluationResult


def test_merge_results_preserves_untouched_rows() -> None:
    existing = [
        {"model_key": "flash-high", "tool": "howdoi", "judge_score": 10},
        {"model_key": "flash-high", "tool": "uv", "judge_score": 20},
    ]
    new_entries = [{"model_key": "flash-high", "tool": "howdoi", "judge_score": 99}]

    merged = evaluate_all_matrix._merge_results(existing, new_entries)

    by_key = {(r["model_key"], r["tool"]): r for r in merged}
    assert by_key[("flash-high", "howdoi")]["judge_score"] == 99
    assert by_key[("flash-high", "uv")]["judge_score"] == 20
    assert len(merged) == 2


def test_evaluate_matrix_records_skips_and_preserves_prior_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    benchmark_dir = tmp_path / "data" / "benchmark"
    benchmark_dir.mkdir(parents=True)

    # A row from a prior run for a model/tool pair this run never touches
    # (its model_key isn't in evaluate_all_matrix.MODELS).
    stale_row = {
        "model_key": "some-other-model",
        "model_name": "Some Other Model",
        "tool": "howdoi",
        "status": "success",
        "judge_score": 42,
    }
    (benchmark_dir / "results.json").write_text(
        json.dumps([stale_row]), encoding="utf-8"
    )

    # Only one (model, tool) combination has a generated file; every other
    # combination in MODELS x TOOLS is naturally exercised as "missing file".
    model_key, _model_name = evaluate_all_matrix.MODELS[0]
    tool = evaluate_all_matrix.TOOLS[0]
    md_dir = benchmark_dir / model_key
    md_dir.mkdir(parents=True)
    (md_dir / f"{tool}.1.md").write_text(
        "% TOOL(1) | User Commands\n", encoding="utf-8"
    )

    cfg = Config(intermediate_dir=tmp_path / "intermediate")
    cfg.intermediate_dir.mkdir(parents=True)
    (cfg.intermediate_dir / f"{tool}_context.md").write_text(
        "context", encoding="utf-8"
    )

    monkeypatch.setattr(
        evaluate_all_matrix,
        "evaluate_manpage",
        lambda **kwargs: EvaluationResult(
            score=80, passed=True, rubric_breakdown={}, summary="ok"
        ),
    )

    evaluate_all_matrix.evaluate_matrix(config=cfg)

    results = json.loads((benchmark_dir / "results.json").read_text(encoding="utf-8"))
    by_key = {(r["model_key"], r["tool"]): r for r in results}

    total_combos = len(evaluate_all_matrix.MODELS) * len(evaluate_all_matrix.TOOLS)
    # +1 for the stale row from a model/tool pair this run doesn't touch.
    assert len(results) == total_combos + 1

    # The one combo with a generated file succeeded and was recorded.
    assert by_key[(model_key, tool)]["status"] == "success"
    assert by_key[(model_key, tool)]["judge_score"] == 80

    # Every other combo in this run was skipped, not silently dropped.
    other_combos = [
        (mk, t)
        for mk, _ in evaluate_all_matrix.MODELS
        for t in evaluate_all_matrix.TOOLS
        if (mk, t) != (model_key, tool)
    ]
    for combo in other_combos:
        assert by_key[combo]["status"] == "skipped"
        assert by_key[combo]["reason"]

    # A prior run's row for an untouched model/tool pair survives the write.
    assert by_key[("some-other-model", "howdoi")] == stale_row
