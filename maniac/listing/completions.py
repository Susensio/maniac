"""`scan`'s Completion column: where the login shell's completion would come from.

The answer is `sources.lookup.completions`'s, the same `install` acts on
(ADR-0066). Finding it can run the tool -- its `--help`, then the generator
that names -- so each answer is cached per binary, keyed by the shell, the
binary's name (a completion is named after it, and `python` and `python3`
can be one file), and the file that runs: its path, size and modification
time, and the installed version. A new or upgraded tool pays once; a warm
`scan` reads the cache. An answer a check could not finish is never cached
(rule 4).
"""

import hashlib
import json
from pathlib import Path

from ..config import Config
from ..logging import logger
from ..sources.lookup.completions import Subject, completion_for
from ..state.models import Candidate

NONE = "none"
"""The column's word for a tool no source has a completion for."""

_CACHE_DIRECTORY = "scan-completions"


def completion_source(candidate: Candidate, shell: str, config: Config) -> str | None:
    """The source `install` would take `candidate`'s `shell` completion from,
    `NONE`, or None when there is no binary to ask about."""
    inst = candidate.installation
    binary = inst.bin_path if inst is not None else candidate.binary
    if binary is None:
        return None
    name = inst.binary if inst is not None else candidate.tool
    real = inst.real_path if inst is not None else binary
    key = _cache_key(shell, name, real, inst.version if inst is not None else None)
    cached = _read(config, key)
    if cached is not None:
        return cached
    found = completion_for(
        Subject(
            name=name,
            executable=str(binary),
            config=config,
            cache_dir=config.cache_dir,
            bin_path=binary,
            installation=inst,
            provider=candidate.provider,
        ),
        shell,
    )
    source = found.source or NONE
    if found.complete and key is not None:
        _write(config, key, source)
    return source


def _cache_key(shell: str, name: str, real: Path, version: str | None) -> str | None:
    """Identity of one answer, or None when the file cannot be stat'd."""
    try:
        status = real.stat()
    except OSError:
        return None
    parts = (shell, name, str(real), str(status.st_size), str(status.st_mtime_ns))
    parts = (*parts, str(version))
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _path(config: Config, key: str) -> Path:
    return config.cache_dir / _CACHE_DIRECTORY / f"{key}.json"


def _read(config: Config, key: str | None) -> str | None:
    if key is None:
        return None
    try:
        source = json.loads(_path(config, key).read_text(encoding="utf-8"))["source"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return source if isinstance(source, str) else None


def _write(config: Config, key: str, source: str) -> None:
    path = _path(config, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps({"source": source}), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        logger.debug(
            "Could not cache a completion answer", path=str(path), error=str(error)
        )
