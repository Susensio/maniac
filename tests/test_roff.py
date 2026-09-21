from pathlib import Path

from maniac.sources.packages import ExternalPageFreshness
from maniac.sources.roff import verify_page_header

# Literal `.TH` lines gathered from real installed pages on this machine
# (python3, git, coreutils' ls, grep, gh-cli) -- see this module's docstring
# reference in docs/BACKLOG.md for the session that collected them.
_PYTHON_TH = '.TH PYTHON "1"\n'
_GIT_TH = '.TH "GIT" "1" "07/02/2025" "Git 2\\&.43\\&.0" "Git Manual"\n'
_LS_TH = '.TH LS "1" "January 2026" "GNU coreutils 9.4" "User Commands"\n'
_GREP_TH = '.TH GREP 1 \\*(Dt "GNU grep 3.11" "User Commands"\n'
_GH_TH = '.TH "GH-RELEASE-VIEW" "1" "Sep 2026" "GitHub CLI 2.100.0" "..."\n'


def _write(tmp_path: Path, content: str) -> Path:
    page = tmp_path / "page.1"
    page.write_text(content, encoding="utf-8")
    return page


def test_python_th_has_no_version_stays_unverified(tmp_path: Path) -> None:
    page = _write(tmp_path, _PYTHON_TH)

    result = verify_page_header(page, binary_name="python", version="3.12")

    assert result is ExternalPageFreshness.UNVERIFIED


def test_git_th_strips_zero_width_escape_and_matches(tmp_path: Path) -> None:
    page = _write(tmp_path, _GIT_TH)

    result = verify_page_header(page, binary_name="git", version="2.43.0")

    assert result is ExternalPageFreshness.MATCH


def test_git_th_zero_width_escape_reports_mismatch_on_differing_version(
    tmp_path: Path,
) -> None:
    page = _write(tmp_path, _GIT_TH)

    result = verify_page_header(page, binary_name="git", version="2.44.0")

    assert result is ExternalPageFreshness.MISMATCH


def test_ls_th_footer_names_package_not_binary_but_still_parses(
    tmp_path: Path,
) -> None:
    page = _write(tmp_path, _LS_TH)

    result = verify_page_header(page, binary_name="ls", version="9.4")

    assert result is ExternalPageFreshness.MATCH


def test_grep_th_unexpanded_macro_stays_unverified(tmp_path: Path) -> None:
    page = _write(tmp_path, _GREP_TH)

    result = verify_page_header(page, binary_name="grep", version="3.11")

    assert result is ExternalPageFreshness.UNVERIFIED


def test_gh_th_plain_version_matches(tmp_path: Path) -> None:
    page = _write(tmp_path, _GH_TH)

    result = verify_page_header(page, binary_name="gh-release-view", version="2.100.0")

    assert result is ExternalPageFreshness.MATCH


def test_title_mismatch_stays_unverified_without_extracting_version(
    tmp_path: Path,
) -> None:
    """`ls`'s footer would otherwise parse to a version; the wrong binary
    name must never reach version extraction at all."""
    page = _write(tmp_path, _LS_TH)

    result = verify_page_header(page, binary_name="dir", version="9.4")

    assert result is ExternalPageFreshness.UNVERIFIED


def test_no_th_or_dt_line_stays_unverified(tmp_path: Path) -> None:
    page = _write(tmp_path, "just some prose, no macro at all\n")

    result = verify_page_header(page, binary_name="tool", version="1.0")

    assert result is ExternalPageFreshness.UNVERIFIED


def test_no_installed_version_stays_unverified_without_reading_page(
    tmp_path: Path,
) -> None:
    """A missing page must not raise when there is no version to check
    against in the first place -- the page is never opened."""
    page = tmp_path / "does-not-exist.1"

    result = verify_page_header(page, binary_name="tool", version=None)

    assert result is ExternalPageFreshness.UNVERIFIED


def test_unreadable_page_stays_unverified(tmp_path: Path) -> None:
    page = tmp_path / "does-not-exist.1"

    result = verify_page_header(page, binary_name="tool", version="1.0")

    assert result is ExternalPageFreshness.UNVERIFIED
