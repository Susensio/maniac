from pathlib import Path

import pytest

from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.docs import (
    cache,
    discover_repo_manpage,
    discover_repo_manpages,
    pages,
    release,
    repository,
)
from maniac.sources.docs.pages import discovered_manpage_uri


@pytest.mark.parametrize("status", [404, 410])
def test_github_release_not_found_is_a_definitive_probe_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    from email.message import Message
    from urllib.error import HTTPError

    calls = 0

    def missing(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise HTTPError(
            "https://api.github.com/release", status, "missing", Message(), None
        )

    monkeypatch.setattr(cache, "_open", missing)
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    cfg = Config(cache_dir=tmp_path)

    result = release._discover_github_release_manpages_result(
        pages._Probe(source, "tool", tmp_path, cfg, "1.2.3", "v1.2.3")
    )

    assert result == pages._ProbeResult([], True)
    assert calls == 1


def test_github_release_assets_accept_only_actual_matching_manpage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def archive(members: dict[str, bytes]) -> bytes:
        import io
        import tarfile

        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        return output.getvalue()

    def fake_download(url: str, cfg: object) -> bytes:
        if url.startswith("https://api.github.com/"):
            return b'{"assets": [{"name": "completions-0.23.5.tar.gz", "size": 5, "browser_download_url": "https://example.test/completions-0.23.5.tar.gz"}, {"name": "man-0.23.5.tar.gz", "size": 5, "browser_download_url": "https://example.test/man-0.23.5.tar.gz"}]}'
        if "completions" in url:
            return archive({"completions/eza.fish": b"complete -c eza"})
        return archive({"man/eza.1": b".TH EZA 1\n"})

    monkeypatch.setattr(cache, "_download", fake_download)
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "v0.23.5")
    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)

    page = discover_repo_manpage(source, "eza", cache_dir=tmp_path, version="0.23.5")
    assert page is not None
    assert page.read_text(encoding="utf-8") == ".TH EZA 1\n"


def test_github_release_archive_keeps_valid_companion_manpages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import io
    import tarfile

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, content in {
            "./target/man-0.23.5/eza.1": b".TH EZA 1\n",
            "./target/man-0.23.5/eza_colors.5": b".TH EZA_COLORS 5\n",
            "./target/man-0.23.5/eza_colors-explanation.5": b".TH EZA_COLORS_EXPLANATION 5\n",
            "./target/man-0.23.5/foo.1": b".TH FOO 1\n",
            "./target/completions/eza.fish": b"complete -c eza",
            "./target/man-0.23.5/not-a-page.txt": b".TH NOT_A_PAGE 1\n",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

    def fake_download(url: str, cfg: object) -> bytes:
        if url.startswith("https://api.github.com/"):
            return b'{"assets": [{"name": "eza-manpages.tar.gz", "size": 5, "browser_download_url": "https://example.test/eza-manpages.tar.gz"}]}'
        return output.getvalue()

    monkeypatch.setattr(cache, "_download", fake_download)
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "v0.23.5")
    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)

    found = discover_repo_manpages(source, "eza", cache_dir=tmp_path, version="0.23.5")

    assert [page.name for page in found] == [
        "eza.1",
        "eza_colors.5",
        "eza_colors-explanation.5",
    ]
    assert all(page.parent.parent.name == "manpages" for page in found)
    assert all(
        discovered_manpage_uri(page) == "https://example.test/eza-manpages.tar.gz"
        for page in found
    )


def test_release_probe_skips_large_non_man_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requested: list[str] = []

    def fake_download(url: str, cfg: object) -> bytes:
        requested.append(url)
        if url.startswith("https://api.github.com/"):
            return b'{"assets": [{"name": "eza_x86_64.tar.gz", "size": 780000, "browser_download_url": "https://example.test/linux"}, {"name": "eza.zip", "size": 1500000, "browser_download_url": "https://example.test/zip"}, {"name": "man-0.23.5.tar.gz", "size": 10500, "browser_download_url": "https://example.test/man"}]}'
        return b"not a tar archive"

    monkeypatch.setattr(cache, "_download", fake_download)
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "v0.23.5")
    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)

    assert (
        discover_repo_manpages(source, "eza", cache_dir=tmp_path, version="0.23.5")
        == []
    )
    assert requested == [
        "https://api.github.com/repos/eza-community/eza/releases/tags/v0.23.5",
        "https://example.test/man",
    ]


def test_malformed_release_asset_degrades_to_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cache, "_download", lambda *args: b"not a tar archive")
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "v1.0")
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    assert (
        discover_repo_manpage(source, "tool", cache_dir=tmp_path, version="1.0") is None
    )


def test_non_object_release_metadata_degrades_to_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cache, "_download", lambda *args: b"[]")
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "v1.0")
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert (
        discover_repo_manpages(source, "tool", cache_dir=tmp_path, version="1.0") == []
    )


def test_release_archive_rejects_an_oversized_member(tmp_path: Path) -> None:
    import io
    import tarfile

    content = b".TH TOOL 1\n" + b"x" * pages._RELEASE_MEMBER_LIMIT
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        info = tarfile.TarInfo("man/tool.1")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert (
        release._manpages_from_release_archive(
            output.getvalue(),
            pages._Probe(source, "tool", tmp_path, Config(), None),
            "v1.0",
        )
        == []
    )
