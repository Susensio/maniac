"""Tests for `MiseProvider`'s project-scope check (ADR-0061)."""

import json
import subprocess
from pathlib import Path

import pytest

from maniac.exceptions import MalformedToolMetadata, ProjectScopedInstall
from maniac.sources.providers import mise


def _make_mise_install(tmp_path: Path, tool: str, version: str, real_name: str) -> Path:
    """Build `<tmp>/.local/share/mise/installs/<tool>/<version>/.../<real_name>`."""
    root = tmp_path / ".local" / "share" / "mise" / "installs" / tool / version
    real = root / "extracted-dir" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / ".local" / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path


def _fake_run(stdout: str, returncode: int = 0):
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout)

    return run


def test_detect_claims_a_globally_active_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_path = _make_mise_install(tmp_path, "ripgrep", "14.1.0", "rg")
    root = tmp_path / ".local" / "share" / "mise" / "installs" / "ripgrep" / "14.1.0"
    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(
        mise.subprocess,
        "run",
        _fake_run(json.dumps({"ripgrep": [{"install_path": str(root)}]})),
    )

    inst = mise.MiseProvider().detect(bin_path)

    assert inst is not None
    assert inst.package == "ripgrep"


def test_detect_refuses_a_project_only_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mise install pinned only by a project's `mise.toml` is not among
    what `mise ls --current --json` reports from `$HOME` -- ADR-0061 refuses
    it rather than serve a page for a binary this machine doesn't otherwise
    resolve to.
    """
    bin_path = _make_mise_install(tmp_path, "ripgrep", "13.0.0", "rg")
    root = tmp_path / ".local" / "share" / "mise" / "installs" / "ripgrep" / "13.0.0"
    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(
        mise.subprocess,
        "run",
        _fake_run(json.dumps({"ripgrep": [{"install_path": "/other/root"}]})),
    )

    with pytest.raises(ProjectScopedInstall) as excinfo:
        mise.MiseProvider().detect(bin_path)
    assert excinfo.value.tool == "rg"
    assert excinfo.value.path == root


def test_detect_matches_globally_active_install_by_identity_not_raw_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit-backend install directory (`aqua-yadm-dev-yadm`) and its
    registry-name sibling (`yadm`) share one `.mise.backend.toml` identity
    at the same version -- `mise ls --current` reports only the sibling's
    path, and the explicit-backend binary must still be claimed rather than
    refused as project-only (executed finding: `yadm`, `nufmt` on the
    development machine).
    """
    bin_path = _make_mise_install(tmp_path, "aqua-yadm-dev-yadm", "3.5.0", "yadm")
    explicit_root = (
        tmp_path
        / ".local"
        / "share"
        / "mise"
        / "installs"
        / "aqua-yadm-dev-yadm"
        / "3.5.0"
    )
    registry_root = (
        tmp_path / ".local" / "share" / "mise" / "installs" / "yadm" / "3.5.0"
    )
    (explicit_root.parent / ".mise.backend.toml").write_text(
        'full = "aqua:yadm-dev/yadm"\n', encoding="utf-8"
    )
    registry_root.parent.mkdir(parents=True)
    (registry_root.parent / ".mise.backend.toml").write_text(
        'full = "aqua:yadm-dev/yadm"\n', encoding="utf-8"
    )
    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(
        mise.subprocess,
        "run",
        _fake_run(json.dumps({"yadm": [{"install_path": str(registry_root)}]})),
    )

    inst = mise.MiseProvider().detect(bin_path)

    assert inst is not None
    assert inst.package == "aqua-yadm-dev-yadm"


def test_mise_query_scrubs_mise_and_dunder_mise_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`MISE_CONFIG_FILE` alone was measured to leak a project config into a
    `$HOME` query (ADR-0061) -- every `MISE_*`/`__MISE_*` variable is
    scrubbed before the call, not only that one.
    """
    monkeypatch.setenv("MISE_CONFIG_FILE", "/project/mise.toml")
    monkeypatch.setenv("__MISE_ORIG_PATH", "/project/.mise/bin")
    monkeypatch.setenv("MISE_DATA_DIR", "/project/.mise-data")
    observed_env: dict[str, str] = {}
    observed_cwd: Path | None = None

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal observed_cwd
        env = kwargs["env"]
        cwd = kwargs["cwd"]
        assert isinstance(env, dict)
        assert isinstance(cwd, Path)
        observed_env.update(env)
        observed_cwd = cwd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(mise.subprocess, "run", fake_run)

    assert mise._mise_global_install_identities() == frozenset()

    assert "MISE_CONFIG_FILE" not in observed_env
    assert "__MISE_ORIG_PATH" not in observed_env
    assert "MISE_DATA_DIR" not in observed_env
    assert observed_cwd == Path.home()


def test_mise_query_runs_once_across_many_lookups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(mise.subprocess, "run", fake_run)

    for _ in range(5):
        mise._mise_global_install_identities()

    assert calls == 1


def test_missing_mise_binary_raises_malformed_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mise-managed install was already found, so a missing `mise` binary
    contradicts that evidence -- ADR-0060 absence does not apply.
    """
    monkeypatch.setattr(mise.shutil, "which", lambda name: None)

    with pytest.raises(MalformedToolMetadata):
        mise._mise_global_install_identities()


def test_mise_query_failure_raises_malformed_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(mise.subprocess, "run", _fake_run("", returncode=1))

    with pytest.raises(MalformedToolMetadata):
        mise._mise_global_install_identities()


def test_mise_query_unparseable_output_raises_malformed_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(mise.subprocess, "run", _fake_run("not json"))

    with pytest.raises(MalformedToolMetadata):
        mise._mise_global_install_identities()
