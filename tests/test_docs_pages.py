from pathlib import Path

from maniac.models import RepoSource
from maniac.sources.docs import discover_repo_manpage, pages


def test_discover_repo_manpage_local(tmp_path: Path) -> None:
    man_dir = tmp_path / "man"
    man_dir.mkdir()
    manpage = man_dir / "tool.1"
    manpage.write_text(".TH TOOL 1\n", encoding="utf-8")
    source = RepoSource(
        name="tool", target=f"LOCAL:{tmp_path}", is_local=True, local_path=tmp_path
    )

    assert discover_repo_manpage(source, "tool") == manpage


def test_discover_repo_manpage_rejects_a_page_naming_a_different_binary(
    tmp_path: Path,
) -> None:
    man_dir = tmp_path / "man"
    man_dir.mkdir()
    (man_dir / "tool.1").write_text(".TH OTHER 1\n", encoding="utf-8")
    source = RepoSource(
        name="tool", target=f"LOCAL:{tmp_path}", is_local=True, local_path=tmp_path
    )

    assert discover_repo_manpage(source, "tool") is None


def test_discover_repo_manpage_no_match_returns_none(tmp_path: Path) -> None:
    source = RepoSource(
        name="tool", target=f"LOCAL:{tmp_path}", is_local=True, local_path=tmp_path
    )

    assert discover_repo_manpage(source, "tool") is None


def test_materialize_page_replaces_an_interrupted_write(tmp_path: Path) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    key = pages.sha256(f"{source.target}\0v1\0tool.1".encode()).hexdigest()
    destination = tmp_path / "manpages" / key / "tool.1"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b".TH TO")

    page = pages._materialize_page(tmp_path, source, "v1", "tool.1", b".TH TOOL 1\n")

    assert page == destination
    assert page.read_bytes() == b".TH TOOL 1\n"


def test_compressed_manpage_expanding_past_limit_is_rejected(tmp_path: Path) -> None:
    import gzip

    page = tmp_path / "tool.1.gz"
    with gzip.open(page, "wb") as file:
        file.write(b".TH TOOL 1\n" + b"x" * pages._RELEASE_MEMBER_LIMIT)

    assert pages._valid_page(page, "tool") is False


def test_truncated_compressed_manpage_is_rejected(tmp_path: Path) -> None:
    import gzip

    page = tmp_path / "tool.1.gz"
    page.write_bytes(gzip.compress(b".TH TOOL 1\n")[:-4])

    assert pages._valid_page(page, "tool") is False
