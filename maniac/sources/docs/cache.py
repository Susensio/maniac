"""Shared on-disk cache primitives and bounded HTTP retrieval."""

import fcntl
import json
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ...config import Config
from ...logging import logger

_RELEASE_ARCHIVE_LIMIT = 10 * 1024 * 1024
_NEGATIVE_CACHE_TTL = 5 * 60
_CACHE_MAX_BYTES = 8 * 1024
_cache_locks: dict[Path, threading.Lock] = {}
_cache_locks_guard = threading.Lock()
_lookup_state = threading.local()


def _upstream_cache_path(cache_dir: Path, kind: str, *parts: str) -> Path:
    digest = sha256("\0".join(parts).encode()).hexdigest()
    return cache_dir / "upstream" / kind / f"{digest}.json"


@contextmanager
def _cache_lock(path: Path) -> Iterator[None]:
    """Serialize a cache key across threads and processes."""
    with _cache_locks_guard:
        thread_lock = _cache_locks.setdefault(path, threading.Lock())
    with thread_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        with lock_path.open("w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


def _read_json_cache(path: Path) -> dict[str, object] | None:
    try:
        if path.stat().st_size > _CACHE_MAX_BYTES:
            raise OSError("cache entry exceeds limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None
    if isinstance(payload, dict):
        return payload
    path.unlink(missing_ok=True)
    return None


def _write_json_cache(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, encoding="utf-8", delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(payload, file, separators=(",", ":"))
        temporary.replace(path)
    except OSError as error:
        logger.debug("Could not write upstream cache", path=str(path), error=str(error))


def _download_cached_result(
    url: str, cache_dir: Path, cfg: Config, max_age: int | None = None
) -> tuple[bytes | None, bool]:
    """Return cached bytes alongside whether absence is definitive.

    Release assets are immutable once named by a versioned URL.  GitHub's
    release metadata can gain assets after publication, so callers may give
    that response a short revalidation window.
    """
    destination = cache_dir / "releases" / sha256(url.encode()).hexdigest()
    negative_path = _upstream_cache_path(cache_dir, "downloads", url)
    freshness_path = _upstream_cache_path(cache_dir, "release-metadata", url)
    with _cache_lock(destination):
        try:
            if destination.stat().st_size <= _RELEASE_ARCHIVE_LIMIT:
                freshness = _read_json_cache(freshness_path)
                created = freshness.get("created") if freshness is not None else None
                if max_age is None or (
                    isinstance(created, (int, float))
                    and time.time() - created < max_age
                ):
                    return destination.read_bytes(), True
            else:
                destination.unlink()
        except OSError:
            pass
        negative = _read_json_cache(negative_path)
        created = negative.get("created") if negative is not None else None
        if (
            isinstance(created, (int, float))
            and time.time() - created < _NEGATIVE_CACHE_TTL
        ):
            return None, True
        _lookup_state.definitive = False
        content = _download(url, cfg)
        if content is None or len(content) > _RELEASE_ARCHIVE_LIMIT:
            if _lookup_state.definitive:
                _write_json_cache(negative_path, {"created": time.time()})
            return None, _lookup_state.definitive
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(destination)
        if max_age is not None:
            _write_json_cache(freshness_path, {"created": time.time()})
        return content, True


def _download(url: str, cfg: Config) -> bytes | None:
    """Download bytes, recording whether a missing response was definitive."""
    content, definitive = _download_result(url, cfg)
    _lookup_state.definitive = definitive
    return content


def _download_result(url: str, cfg: Config) -> tuple[bytes | None, bool]:
    try:
        with urlopen(
            Request(url, headers={"User-Agent": "maniac"}), timeout=cfg.timeout_git
        ) as response:
            content = response.read(_RELEASE_ARCHIVE_LIMIT + 1)
            if len(content) > _RELEASE_ARCHIVE_LIMIT:
                return None, False
            return content, True
    except HTTPError as error:
        logger.debug("Download failed", url=url, error=str(error))
        return None, error.code in {404, 410}
    except (OSError, URLError, ValueError) as error:
        logger.debug("Download failed", url=url, error=str(error))
        return None, False
