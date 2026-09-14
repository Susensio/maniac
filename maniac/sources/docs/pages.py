"""Manpage identity, provenance recording, materialization, and validation."""

import bz2
import gzip
import json
import lzma
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatch
from hashlib import sha256
from io import TextIOWrapper
from pathlib import Path
from urllib.parse import quote

import zstandard

from ...config import Config
from ...models import RepoSource
from ..manpages import REPO_MANPAGE_DIRS, is_help2man_content, manpage_documents
from . import cache
from .cache import _NEGATIVE_CACHE_TTL

_RELEASE_MEMBER_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class _Probe:
    """One binary's upstream manpage lookup, with its resolved tag once known."""

    source: RepoSource
    binary_name: str
    cache_dir: Path
    cfg: Config
    version: str | None
    tag: str | None = None


@dataclass(frozen=True)
class _ProbeResult:
    """Probe pages alongside whether every applicable source completed."""

    pages: list[Path]
    definitive: bool


def discovered_manpage_uri(page: Path) -> str | None:
    """Return the upstream-hosted URI recorded when ``page`` was materialized."""
    try:
        metadata = json.loads(
            (page.parent / ".source.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    uri = metadata.get("uri")
    return (
        uri
        if isinstance(uri, str) and uri.startswith(("http://", "https://"))
        else None
    )


def _record_page_uri(page: Path, uri: str | None) -> None:
    if uri is not None:
        cache._write_json_cache(page.parent / ".source.json", {"uri": uri})


def _repository_page_uri(source: RepoSource, ref: str, path: str) -> str | None:
    """Build the browser URL for an exact file in a GitHub repository ref."""
    clone_url = source.clone_url
    if clone_url is None or not clone_url.startswith("https://github.com/"):
        return None
    repository = clone_url.removeprefix("https://github.com/").removesuffix(".git")
    return (
        f"https://github.com/{repository}/blob/{quote(ref, safe='')}/"
        f"{quote(path, safe='/')}"
    )


def _materialize_page(
    cache_dir: Path, source: RepoSource, ref: str, original_path: str, content: bytes
) -> Path:
    key = sha256(f"{source.target}\0{ref}\0{original_path}".encode()).hexdigest()
    destination = cache_dir / "manpages" / key / Path(original_path).name
    with cache._cache_lock(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(destination)
    return destination


def _matching_manpage_paths(
    paths: Iterator[str] | list[str], binary_name: str
) -> Iterator[str]:
    exact = [
        f"{binary_name}.[1-9]{suffix}" for suffix in ("", ".gz", ".bz2", ".xz", ".zst")
    ]
    nested = [
        f"{binary_name}-*.[1-9]{suffix}"
        for suffix in ("", ".gz", ".bz2", ".xz", ".zst")
    ]
    candidates = sorted(path for path in paths if _path_can_be_repo_manpage(path))
    for patterns in (exact, nested):
        for path in candidates:
            if any(fnmatch(Path(path).name, pattern) for pattern in patterns):
                yield path


def _path_can_be_repo_manpage(path: str) -> bool:
    parts = tuple(part.lower() for part in Path(path).parts)
    return len(parts) == 1 or any(
        parts[: len(prefix)] == prefix for prefix in REPO_MANPAGE_DIRS
    )


def _is_manpage_filename(path: str) -> bool:
    name = Path(path).name
    for suffix in (".gz", ".bz2", ".xz", ".zst"):
        if name.endswith(suffix):
            name = name.removesuffix(suffix)
            break
    return re.search(r"\.[1-9]$", name) is not None


def _matches_primary_manpage_name(path: str, binary_name: str) -> bool:
    name = Path(path).name
    patterns = [
        f"{binary_name}{variant}.[1-9]{suffix}"
        for variant in ("", "-*")
        for suffix in ("", ".gz", ".bz2", ".xz", ".zst")
    ]
    return any(fnmatch(name, pattern) for pattern in patterns)


def _is_related_bundle_page(path: str, binary_name: str) -> bool:
    name = Path(path).name
    stem = name
    for suffix in (".gz", ".bz2", ".xz", ".zst"):
        stem = stem.removesuffix(suffix)
    stem = stem.rsplit(".", 1)[0]
    return stem == binary_name or stem.startswith(
        (f"{binary_name}-", f"{binary_name}_")
    )


def _valid_page(page: Path, binary_name: str) -> bool:
    try:
        content = _read_bounded_manpage(page)
    except (EOFError, OSError, UnicodeError, lzma.LZMAError, zstandard.ZstdError):
        return False
    return not is_help2man_content(content) and manpage_documents(content, binary_name)


def _is_valid_bundle_page(page: Path) -> bool:
    try:
        content = _read_bounded_manpage(page)
    except (EOFError, OSError, UnicodeError, lzma.LZMAError, zstandard.ZstdError):
        return False
    return (
        not is_help2man_content(content)
        and re.search(r"(?m)^\.(?:TH|Dt)\s+", content[:8192]) is not None
    )


def _read_bounded_manpage(page: Path) -> str:
    """Read at most one release member's decoded text, rejecting compression bombs."""
    limit = _RELEASE_MEMBER_LIMIT + 1
    if page.suffix == ".gz":
        opener = gzip.open
    elif page.suffix == ".bz2":
        opener = bz2.open
    elif page.suffix in {".xz", ".lzma"}:
        opener = lzma.open
    elif page.suffix == ".zst":
        stream = zstandard.ZstdDecompressor().stream_reader(page.open("rb"))
        with TextIOWrapper(stream, encoding="utf-8", errors="replace") as file:
            content = file.read(limit)
        if len(content) > _RELEASE_MEMBER_LIMIT:
            raise OSError("decompressed manpage exceeds limit")
        return content
    else:
        opener = open
    with opener(page, "rt", encoding="utf-8", errors="replace") as file:
        content = file.read(limit)
    if len(content) > _RELEASE_MEMBER_LIMIT:
        raise OSError("decompressed manpage exceeds limit")
    return content


def _read_probe_cache(
    path: Path, cache_dir: Path, binary_name: str
) -> list[Path] | None:
    cached = cache._read_json_cache(path)
    if cached is None:
        return None
    pages = cached.get("pages")
    if isinstance(pages, list) and all(isinstance(page, str) for page in pages):
        resolved = [Path(page) for page in pages]
        manpages_dir = cache_dir / "manpages"
        if (
            resolved
            and all(
                page.is_relative_to(manpages_dir) and page.exists() for page in resolved
            )
            and _valid_page(resolved[0], binary_name)
            and all(_is_valid_bundle_page(page) for page in resolved[1:])
        ):
            return resolved
    created = cached.get("created")
    if (
        pages == []
        and isinstance(created, (int, float))
        and time.time() - created < _NEGATIVE_CACHE_TTL
    ):
        return []
    path.unlink(missing_ok=True)
    return None


def _write_probe_cache(path: Path, pages: list[Path]) -> None:
    cache._write_json_cache(
        path, {"pages": [str(page) for page in pages], "created": time.time()}
    )
