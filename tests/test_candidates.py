"""Direct evidence-contract tests for ADR-0040 candidates."""

from pathlib import Path

from maniac.models import Installation, LocalRepoSource, RemoteRepoSource
from maniac.sources import candidates


def _installation(root: Path) -> Installation:
    return Installation(
        "tool", root / "bin/tool", root / "bin/tool", "test", "tool", "1.0", root
    )


class _Provider:
    name = "test"

    def __init__(self, pages: list[Path], target: Path | None = None) -> None:
        self.pages, self.target = pages, target

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(self, inst, *, config, sources):
        return None

    def local_docs(self, inst: Installation) -> list[Path]:
        return self.pages

    def direct_page_target(self, inst: Installation, page: Path) -> Path | None:
        return self.target

    def is_direct_page_target_current(self, inst: Installation, target: Path) -> bool:
        return True


def test_install_root_candidate_keeps_discovery_and_derives_ownership_from_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    page = root / "man" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    escaped = tmp_path / "outside" / "tool.1"
    escaped.parent.mkdir()
    escaped.write_text(".TH TOOL 1\n", encoding="utf-8")

    evidence = candidates.select_install_root(
        _Provider([page], escaped), _installation(root)
    )

    assert evidence is not None
    assert evidence.discovered_page == page
    assert evidence.final_target == escaped
    assert not evidence.provider_owned
    assert evidence.primary.uri == page.absolute().as_uri()


def test_repository_candidate_requires_a_version_for_remote_and_keeps_page_uris(
    monkeypatch, tmp_path: Path
) -> None:
    first, second = tmp_path / "tool.1", tmp_path / "tool-extra.1"
    first.write_text(".TH TOOL 1\n", encoding="utf-8")
    second.write_text(".TH TOOL-EXTRA 1\n", encoding="utf-8")
    remote = RemoteRepoSource.from_identifier("tool", "owner/tool")
    local = LocalRepoSource("tool", tmp_path)
    monkeypatch.setattr(
        candidates, "discover_repo_manpages", lambda *args, **kwargs: [first, second]
    )
    monkeypatch.setattr(
        candidates,
        "discovered_manpage_uri",
        lambda page: f"https://example.test/{page.name}",
    )

    assert candidates.select_repository(remote, "tool") is None
    evidence = candidates.select_repository(remote, "tool", version="1.0")
    assert evidence is not None
    assert evidence.version_matched is True
    assert [page.uri for page in evidence.pages] == [
        "https://example.test/tool.1",
        "https://example.test/tool-extra.1",
    ]
    assert candidates.select_repository(local, "tool") is not None


def test_historical_selection_never_uses_provider_aliases(tmp_path: Path) -> None:
    root = tmp_path / "root"
    page = root / "man" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")

    evidence = candidates.select_historical_install_root(root, "tool")

    assert evidence is not None
    assert evidence.final_target == page
    assert evidence.provider_owned
