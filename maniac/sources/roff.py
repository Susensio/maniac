"""Verify a page's freshness from its own `.TH`/`.Dt` header, for a page
`dpkg` cannot own -- a page on a non-Debian system, or one `dpkg -S` cannot
attribute (`maniac/sources/packages.py::verify_external_page`'s own
`UNVERIFIED` case). Neither Debian-specific nor about *finding* a page, so
it sits beside `manpages.py` rather than inside it or `packages.py`,
importing the title-matching and page-opening machinery both already own
rather than duplicating either.
"""

import re
import shlex
from pathlib import Path

from ..logging import logger
from .manpages import _read_prefix, manpage_documents
from .packages import ExternalPageFreshness

_TH_ARGS = re.compile(r"(?m)^\.TH\s+(.*)$")
_DT_ARGS = re.compile(r"(?m)^\.Dt\s+(.*)$")
# roff's zero-width no-break escape: no semantic content, only stops a
# period being read as sentence-end spacing (`2\&.43\&.0` means `2.43.0`).
_ZERO_WIDTH_ESCAPE = "\\&"
# Any backslash left after stripping `\&` is an unexpanded escape or macro
# call (`\*(Dt`, `\f...`) this does not attempt to resolve.
_STRAY_BACKSLASH = re.compile(r"\\(?!&)")
_TRAILING_VERSION = re.compile(r"(\d+(?:\.\d+){1,3})$")


def verify_page_header(
    page: Path, *, binary_name: str, version: str | None
) -> ExternalPageFreshness:
    """Freshness from a page's own header alone, no package manager involved.

    Gate: the page's `.TH`/`.Dt` title (field 1) must name `binary_name`
    (`manpage_documents`) -- otherwise, or with no `.TH`/`.Dt` line at all,
    `UNVERIFIED` with no version extraction attempted. Past the gate, field
    4 (the footer) is the version candidate; its leading words are the
    package name and are not compared against anything (`ls`'s `"GNU
    coreutils 9.4"` names the package, not the binary, and is still valid).
    """
    if version is None:
        return ExternalPageFreshness.UNVERIFIED
    try:
        prefix = _read_prefix(page)
    except (OSError, UnicodeError) as error:
        logger.debug(
            "Unable to inspect manpage header", path=str(page), error=str(error)
        )
        return ExternalPageFreshness.UNVERIFIED
    if not manpage_documents(prefix, binary_name):
        return ExternalPageFreshness.UNVERIFIED
    header_version = _header_version(prefix)
    if header_version is None:
        return ExternalPageFreshness.UNVERIFIED
    if header_version == version:
        return ExternalPageFreshness.MATCH
    return ExternalPageFreshness.MISMATCH


def _header_version(prefix: str) -> str | None:
    """Field 4 (1-indexed) of the page's `.TH`/`.Dt` line, if it parses cleanly."""
    match = _TH_ARGS.search(prefix) or _DT_ARGS.search(prefix)
    if match is None:
        return None
    args_line = match.group(1)
    # Checked on the whole argument line, not only field 4: `grep`'s real
    # header (`GREP 1 \*(Dt "GNU grep 3.11" "User Commands"`) carries its
    # stray backslash in field 3, an unexpanded troff string-macro call
    # (`\*(Dt`) that would otherwise slip through undetected -- shlex's own
    # posix backslash-escaping silently consumes it before a field-4-only
    # check could see it, and its clean field 4 would then look parseable.
    if _STRAY_BACKSLASH.search(args_line):
        return None
    args_line = args_line.replace(_ZERO_WIDTH_ESCAPE, "")
    try:
        fields = shlex.split(args_line)
    except ValueError as error:
        logger.debug("Unable to parse .TH/.Dt arguments", error=str(error))
        return None
    if len(fields) < 4:
        return None
    version_match = _TRAILING_VERSION.search(fields[3])
    return version_match.group(1) if version_match else None
