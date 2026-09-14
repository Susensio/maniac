"""GitHub release retrieval: metadata, bounded assets, and archive validation."""

import json
import re
import tarfile
from io import BytesIO
from pathlib import Path

from . import cache, pages, repository
from .cache import _NEGATIVE_CACHE_TTL
from .pages import _RELEASE_MEMBER_LIMIT, _Probe, _ProbeResult

_RELEASE_ARCHIVE_CANDIDATE_LIMIT = 256 * 1024
_RELEASE_EXTRACTED_LIMIT = 8 * 1024 * 1024
_MAN_ASSET_TOKEN = re.compile(
    r"(?:^|[-_.])man(?:page|pages)?(?:[-_.]|$)", re.IGNORECASE
)


def _discover_github_release_manpages_result(probe: _Probe) -> _ProbeResult:
    """Probe GitHub release assets and retain whether the response was complete."""
    tag_result = _release_tag(probe)
    if isinstance(tag_result, _ProbeResult):
        return tag_result
    assets_result = _release_assets(probe, tag_result)
    if isinstance(assets_result, _ProbeResult):
        return assets_result

    definitive = True
    for asset in assets_result:
        result = _fetch_and_materialize_release_asset(asset, probe, tag_result)
        if result.pages:
            return result
        definitive = definitive and result.definitive
    return _ProbeResult([], definitive)


def _release_tag(probe: _Probe) -> str | _ProbeResult:
    """Validate the GitHub source and resolve its version-matched release tag."""
    if probe.version is None or probe.source.target.count("/") != 1:
        return _ProbeResult([], True)
    clone_url = probe.source.clone_url
    if clone_url is None:
        return _ProbeResult([], False)
    resolved_tag = probe.tag or repository._find_matching_tag_cached(
        probe.cache_dir, clone_url, probe.version, probe.cfg
    )
    return resolved_tag if resolved_tag is not None else _ProbeResult([], True)


def _release_assets(probe: _Probe, tag: str) -> list[object] | _ProbeResult:
    """Fetch and validate GitHub release metadata before inspecting assets."""
    metadata, definitive = cache._download_cached_result(
        f"https://api.github.com/repos/{probe.source.target}/releases/tags/{tag}",
        probe.cache_dir,
        probe.cfg,
        max_age=_NEGATIVE_CACHE_TTL,
    )
    if metadata is None:
        return _ProbeResult([], definitive)
    try:
        document = json.loads(metadata)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _ProbeResult([], False)
    if not isinstance(document, dict):
        return _ProbeResult([], False)
    assets = document.get("assets", [])
    return assets if isinstance(assets, list) else _ProbeResult([], False)


def _fetch_and_materialize_release_asset(
    asset: object, probe: _Probe, tag: str
) -> _ProbeResult:
    """Validate one asset, fetch eligible bytes, then materialize matching pages."""
    if not isinstance(asset, dict):
        return _ProbeResult([], False)
    url = asset.get("browser_download_url")
    name = asset.get("name")
    if not isinstance(url, str) or not isinstance(name, str):
        return _ProbeResult([], False)
    direct = any(pages._matching_manpage_paths([name], probe.binary_name))
    archive_candidate = _is_release_archive(name) and (
        _MAN_ASSET_TOKEN.search(name) is not None
        or (
            isinstance(asset.get("size"), int)
            and asset["size"] <= _RELEASE_ARCHIVE_CANDIDATE_LIMIT
        )
    )
    if not direct and not archive_candidate:
        return _ProbeResult([], True)
    content, definitive = cache._download_cached_result(url, probe.cache_dir, probe.cfg)
    if content is None:
        return _ProbeResult([], definitive)
    if direct:
        page = pages._materialize_page(
            probe.cache_dir, probe.source, tag, name, content
        )
        pages._record_page_uri(page, url)
        return (
            _ProbeResult([page], True)
            if pages._valid_page(page, probe.binary_name)
            else _ProbeResult([], False)
        )
    try:
        archive_pages = _manpages_from_release_archive(content, probe, tag, url)
    except (OSError, tarfile.TarError):
        return _ProbeResult([], False)
    return _ProbeResult(archive_pages, True)


def _is_release_archive(name: str) -> bool:
    return name.lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"))


def _manpages_from_release_archive(
    archive: bytes,
    probe: _Probe,
    tag: str,
    source_uri: str | None = None,
) -> list[Path]:
    """Materialize every valid page from an archive with a valid primary page."""
    found: list[Path] = []
    primary: Path | None = None
    extracted_total = 0
    with tarfile.open(fileobj=BytesIO(archive), mode="r|*") as tar:
        for member in tar:
            if (
                not member.isfile()
                or not _safe_release_member_name(member.name)
                or not pages._is_manpage_filename(member.name)
                or not pages._is_related_bundle_page(member.name, probe.binary_name)
                or member.size > _RELEASE_MEMBER_LIMIT
            ):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            content = extracted.read(_RELEASE_MEMBER_LIMIT + 1)
            if len(content) > _RELEASE_MEMBER_LIMIT:
                continue
            extracted_total += len(content)
            if extracted_total > _RELEASE_EXTRACTED_LIMIT:
                return []
            page = pages._materialize_page(
                probe.cache_dir, probe.source, tag, member.name, content
            )
            pages._record_page_uri(page, source_uri)
            if not pages._is_valid_bundle_page(page):
                continue
            found.append(page)
            if pages._matches_primary_manpage_name(
                member.name, probe.binary_name
            ) and pages._valid_page(page, probe.binary_name):
                primary = page
    if primary is None:
        return []
    return [primary, *(page for page in found if page != primary)]


def _safe_release_member_name(path: str) -> bool:
    member_path = Path(path)
    return (
        not member_path.is_absolute()
        and ".." not in member_path.parts
        and len(member_path.name.encode()) <= 255
    )
