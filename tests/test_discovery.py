import io
import tarfile
from pathlib import Path
from typing import Self
from urllib.request import Request

import zstandard

from maniac.models import RepoSource
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
