"""Regression coverage for M8: skipped rows recorded, prior results merged not overwritten."""

import json
from pathlib import Path

import pytest

from maniac.bench import harness
from maniac.config import Config
from maniac.exceptions import ManiacError
from maniac.models import EvaluationResult


def test_merge_results_preserves_untouched_rows() -> None:
    existing = [
        {"model_key": "flash-high", "tool": "howdoi", "judge_score": 10},
        {"model_key": "flash-high", "tool": "uv", "judge_score": 20},
    ]
    new_entries = [{"model_key": "flash-high", "tool": "howdoi", "judge_score": 99}]

    merged = harness._merge_results(existing, new_entries)

    by_key = {(r["model_key"], r["tool"]): r for r in merged}
    assert by_key[("flash-high", "howdoi")]["judge_score"] == 99
    assert by_key[("flash-high", "uv")]["judge_score"] == 20
    assert len(merged) == 2


def test_run_benchmark_records_skips_and_preserves_prior_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir(parents=True)

    # A row from a prior run for a model/tool pair this run never touches.
    stale_row = {
        "model_key": "some-other-model",
        "model_name": "Some Other Model",
        "tool": "howdoi",
        "status": "success",
        "judge_score": 42,
    }
    (bench_dir / "results.json").write_text(json.dumps([stale_row]), encoding="utf-8")

    cfg = Config(bench_dir=bench_dir, intermediate_dir=tmp_path / "intermediate")
    cfg.intermediate_dir.mkdir(parents=True)

    models = [("flash-high", "Gemini 3.7 Flash (High)")]
    tools = ["howdoi", "hx"]

    def fake_generate(tool, model_name, cfg, out_dir, retries):
        if tool == "howdoi":
            context_path = cfg.intermediate_dir / f"{tool}_context.md"
            context_path.write_text("context", encoding="utf-8")
            result = type(
                "FakePipelineResult",
                (),
                {
                    "context_path": context_path,
                    "markdown_content": "% HOWDOI(1) | User Commands\n",
                },
            )()
            return result, None, 1.0
        return None, "boom", 1.0

    monkeypatch.setattr(harness, "_generate", fake_generate)
    monkeypatch.setattr(
        harness,
        "evaluate_manpage",
        lambda **kwargs: EvaluationResult(
            score=80, passed=True, rubric_breakdown={}, summary="ok"
        ),
    )

    merged = harness.run_benchmark(tools=tools, models=models, config=cfg)

    by_key = {(r["model_key"], r["tool"]): r for r in merged}

    # +1 for the stale row from a model/tool pair this run doesn't touch.
    assert len(merged) == len(models) * len(tools) + 1

    assert by_key[("flash-high", "howdoi")]["status"] == "success"
    assert by_key[("flash-high", "howdoi")]["judge_score"] == 80

    assert by_key[("flash-high", "hx")]["status"] == "skipped"
    assert "boom" in by_key[("flash-high", "hx")]["reason"]

    # A prior run's row for an untouched model/tool pair survives the write.
    assert by_key[("some-other-model", "howdoi")] == stale_row

    on_disk = json.loads((bench_dir / "results.json").read_text(encoding="utf-8"))
    assert len(on_disk) == len(merged)


def test_generate_returns_error_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def always_fails(**kwargs):
        raise ManiacError("nope")

    monkeypatch.setattr(harness, "run_pipeline", always_fails)
    monkeypatch.setattr(harness.time, "sleep", lambda _: None)

    result, error, _duration = harness._generate(
        "howdoi", "Gemini 3.7 Flash (High)", Config(), Path("/tmp/out"), retries=1
    )

    assert result is None
    assert error == "nope"
