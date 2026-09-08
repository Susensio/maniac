import io
import tarfile
from pathlib import Path
from typing import Self
from urllib.request import Request

import pytest
import zstandard

from maniac.models import Installation, RepoSource
from maniac.sources import discovery
from maniac.sources.discovery import (
    _check_mise_toml,
    _clean_git_url,
    _extract_mise_tool_id,
    _load_mise_registry,
    _parse_mise_registry,
    _read_mise_registry_archive,
    _resolve_from_mise,
    discover_candidate_source,
    discover_repo,
    enumerate_installations,
)


def _compressed_mise_registry(entries: dict[str, bytes]) -> bytes:
    contents = io.BytesIO()
    with tarfile.open(fileobj=contents, mode="w") as archive:
        for name, entry in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(entry)
            archive.addfile(info, io.BytesIO(entry))
    return zstandard.ZstdCompressor().compress(contents.getvalue())


def test_clean_git_url() -> None:
    assert _clean_git_url("https://github.com/gleitz/howdoi.git") == "gleitz/howdoi"
    assert _clean_git_url("git@github.com:astral-sh/uv.git") == "astral-sh/uv"
    assert (
        _clean_git_url("https://github.com/helix-editor/helix") == "helix-editor/helix"
    )
    assert _clean_git_url("https://github.com/d4nj1/TLPUI/") == "d4nj1/TLPUI"


def test_extract_mise_tool_id() -> None:
    p = Path("/home/user/.local/share/mise/installs/glow/2.1.2/glow")
    assert _extract_mise_tool_id(p) == "glow"

    p_non_mise = Path("/usr/local/bin/something")
    assert _extract_mise_tool_id(p_non_mise) is None


def test_check_mise_toml_tool_alias(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[tool_alias]
gh-cli = "github:cli/cli"
custom = "owner/custom"
""",
        encoding="utf-8",
    )
    assert _check_mise_toml(cfg, "gh-cli", "gh") == "cli/cli"
    assert _check_mise_toml(cfg, "other", "custom") == "owner/custom"
    assert _check_mise_toml(cfg, "unknown", "unknown") is None


def test_check_mise_toml_tools_github_and_cargo(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[tools]
"github:sharkdp/fd" = "latest"
"cargo:https://github.com/BurntSushi/ripgrep" = "latest"
"github:junegunn/fzf" = { filter_bins = ["fzf-tmux"] }
""",
        encoding="utf-8",
    )
    assert _check_mise_toml(cfg, "fd", "fd") == "sharkdp/fd"
    assert _check_mise_toml(cfg, "ripgrep", "rg") == "BurntSushi/ripgrep"
    assert _check_mise_toml(cfg, "unknown", "fzf-tmux") == "junegunn/fzf"
    assert _check_mise_toml(cfg, "fmt", "fmt") is None
    assert _check_mise_toml(cfg, "od", "od") is None
    assert _check_mise_toml(cfg, "unknown", "unknown") is None


def test_check_mise_toml_invalid(tmp_path: Path) -> None:
    cfg = tmp_path / "invalid.toml"
    cfg.write_text("invalid = [toml", encoding="utf-8")
    assert _check_mise_toml(cfg, "foo", "foo") is None

    missing = tmp_path / "nonexistent.toml"
    assert _check_mise_toml(missing, "foo", "foo") is None


def test_discover_repo_fallback(monkeypatch, tmp_path: Path) -> None:
    """A binary nothing on disk resolves to is unresolvable, not a bare-name guess."""
    monkeypatch.setattr(discovery, "_load_mise_registry", dict)
    assert discover_repo("nonexistent_unknown_tool", bin_dir=tmp_path) is None


def test_discover_repo_does_not_use_the_registry_without_an_installation(
    monkeypatch, tmp_path: Path
) -> None:
    """ADR-0015's ruling on Stage 2's gap: name-only registry matching is gone
    everywhere, not only from `discover_candidate_source`. A binary with no
    detected installation stays unresolved even where the registry would
    have matched it by bare name -- this is `discover_repo("envsubst")`
    ceasing to return "a8m/envsubst".
    """
    monkeypatch.setattr(discovery.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        discovery,
        "_load_mise_registry",
        lambda: {"envsubst": "a8m/envsubst"},
    )

    assert discover_repo("envsubst", bin_dir=tmp_path) is None


def test_discover_candidate_source_does_not_guess_from_a_system_binary(
    monkeypatch, tmp_path: Path
) -> None:
    system_binary = tmp_path / "fmt"
    system_binary.touch()
    monkeypatch.setattr(discovery.shutil, "which", lambda name: str(system_binary))
    monkeypatch.setattr(discovery, "_load_mise_registry", lambda: {"fmt": "wrong/fmt"})

    assert discover_candidate_source("fmt") is None


def test_discover_candidate_source_delegates_to_the_provider_registry(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    target.touch()
    binary = tmp_path / "candidate"
    binary.symlink_to(target)
    monkeypatch.setattr(discovery.shutil, "which", lambda name: str(binary))
    observed: dict[str, object] = {}

    def resolve(name: str, path: Path) -> RepoSource:
        observed["name"] = name
        observed["path"] = path
        return RepoSource(name=name, target="owner/tool", is_local=False)

    monkeypatch.setattr(discovery, "_resolve_symlink_target", resolve)

    source = discover_candidate_source("candidate")
    assert source is not None
    assert source.target == "owner/tool"
    assert observed == {"name": "candidate", "path": binary}


def _fake_installation(binary: str) -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider="fake",
        package=binary,
        version=None,
        root=Path("/root"),
    )


def test_enumerate_installations_walks_path_and_keeps_only_claimed_binaries(
    monkeypatch, tmp_path: Path
) -> None:
    claimed = tmp_path / "claimed"
    claimed.touch(mode=0o755)
    unclaimed = tmp_path / "unclaimed"
    unclaimed.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_detect(bin_path: Path):
        if bin_path.name == "claimed":
            return ("fake-provider", _fake_installation("claimed"))
        return None

    monkeypatch.setattr(discovery, "_detect_via_registry", fake_detect)

    found = enumerate_installations()

    assert [inst.binary for _, inst in found] == ["claimed"]


def test_enumerate_installations_resolves_a_name_once_at_its_first_path_entry(
    monkeypatch, tmp_path: Path
) -> None:
    """ADR-0016's tie-break: two providers claiming one name, the first `$PATH` wins."""
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "tool").touch(mode=0o755)
    (second_dir / "tool").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{first_dir}:{second_dir}")

    seen_paths: list[Path] = []

    def fake_detect(bin_path: Path):
        seen_paths.append(bin_path)
        return ("fake-provider", _fake_installation("tool"))

    monkeypatch.setattr(discovery, "_detect_via_registry", fake_detect)

    enumerate_installations()

    assert seen_paths == [first_dir / "tool"]


def test_enumerate_installations_skips_non_executable_files(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "not_executable").touch(mode=0o644)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        discovery, "_detect_via_registry", lambda p: pytest.fail("must not be called")
    )

    assert enumerate_installations() == []


def test_enumerate_installations_on_start_and_on_scan_are_optional_and_no_op_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    """Existing callers omitting the callbacks see unchanged behaviour."""
    claimed = tmp_path / "claimed"
    claimed.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        discovery,
        "_detect_via_registry",
        lambda p: ("fake-provider", _fake_installation(p.name)),
    )

    found = enumerate_installations()

    assert [inst.binary for _, inst in found] == ["claimed"]


def test_enumerate_installations_reports_candidate_count_then_one_scan_per_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    for name in ("one", "two", "three"):
        (tmp_path / name).touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        discovery,
        "_detect_via_registry",
        lambda p: ("fake-provider", _fake_installation(p.name)),
    )

    starts: list[int] = []
    scans = 0

    def on_start(total: int) -> None:
        starts.append(total)

    def on_scan() -> None:
        nonlocal scans
        scans += 1

    enumerate_installations(on_start=on_start, on_scan=on_scan)

    assert starts == [3]
    assert scans == 3


def test_resolve_from_mise_checks_all_local_config_before_registry(
    monkeypatch, tmp_path: Path
) -> None:
    mise_dir = tmp_path / "mise"
    mise_dir.mkdir()
    (mise_dir / "a.toml").write_text("[tools]\nrg = 'latest'\n", encoding="utf-8")
    (mise_dir / "b.toml").write_text(
        "[tool_alias]\nrg = 'github:private/rg'\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        discovery,
        "_load_mise_registry",
        lambda: {"rg": "BurntSushi/ripgrep"},
    )

    assert _resolve_from_mise("rg", "rg") == "private/rg"


def test_parse_mise_registry_resolves_short_names_aliases_and_bins() -> None:
    entry = (
        b'backends = ["aqua:BurntSushi/ripgrep"]\n'
        b'aliases = ["ripgrep-cli"]\n'
        b'bins = ["rg"]\n'
    )
    compressed = _compressed_mise_registry({"registry/ripgrep.toml": entry})

    assert _parse_mise_registry(compressed) == {
        "ripgrep": "BurntSushi/ripgrep",
        "ripgrep-cli": "BurntSushi/ripgrep",
        "rg": "BurntSushi/ripgrep",
    }


def test_parse_mise_registry_prefers_canonical_names_and_normalizes_aqua() -> None:
    compressed = _compressed_mise_registry(
        {
            "registry/aws-copilot.toml": (
                b'backends = ["github:aws/copilot-cli"]\naliases = ["copilot"]\n'
            ),
            "registry/copilot.toml": b'backends = ["github:github/copilot-cli"]\n',
            "registry/kubectl.toml": (
                b'backends = ["aqua:kubernetes/kubernetes/kubectl"]\n'
            ),
        }
    )

    registry = _parse_mise_registry(compressed)

    assert registry["copilot"] == "github/copilot-cli"
    assert registry["kubectl"] == "kubernetes/kubernetes"


def test_malformed_mise_registry_falls_back_without_error(monkeypatch) -> None:
    archive = _compressed_mise_registry({"registry/broken.toml": b"\xff"})
    monkeypatch.setattr(discovery, "_read_mise_registry_archive", lambda: archive)
    _load_mise_registry.cache_clear()

    assert _load_mise_registry() == {}

    _load_mise_registry.cache_clear()


def test_mise_registry_download_sends_user_agent(monkeypatch, tmp_path: Path) -> None:
    observed_request: Request | None = None
    observed_timeout: int | None = None

    class Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"registry"

    def fake_urlopen(request: Request, timeout: int) -> Response:
        nonlocal observed_request, observed_timeout
        observed_request = request
        observed_timeout = timeout
        return Response()

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(discovery, "urlopen", fake_urlopen)

    assert _read_mise_registry_archive() == b"registry"
    assert observed_timeout == 10
    assert observed_request is not None
    assert observed_request.get_header("User-agent") == "maniac/0.1"
