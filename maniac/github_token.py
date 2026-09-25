"""Resolve a GitHub API token, memoized once per process.

`gh auth token` spawns a subprocess, and a single `list` run makes dozens
of GitHub requests -- paying that cost per request would be unacceptable.
A failed lookup memoizes too: `gh`'s credential can be keyring-backed, and
a keyring needs a session bus that a container, CI runner or sandbox does
not have, so one transient unavailability disables authentication for the
rest of the process rather than retrying.
"""

import os
import subprocess
from functools import cache

from .logging import logger

# Guards against `gh` hanging rather than the ordinary subprocess cost.
_GH_AUTH_TOKEN_TIMEOUT = 5


@cache
def resolve_github_token() -> str | None:
    """A GitHub API token, or `None` to proceed unauthenticated.

    Order matches `gh`'s own precedence: `GH_TOKEN`, then `GITHUB_TOKEN`
    (CI sets these), then `gh auth token`. `gh` missing, not on `PATH`,
    not authenticated, exiting non-zero, timing out, or raising `OSError`
    all degrade to `None` -- this must never be the reason `maniac list`
    fails.
    """
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name)
        if token and token.strip():
            return token.strip()
    try:
        # --hostname pins the lookup to github.com: bare `gh auth token`
        # returns whatever host GH_HOST selects, and a GitHub Enterprise
        # credential must never reach api.github.com.
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_GH_AUTH_TOKEN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.debug("gh auth token unavailable", error=str(error))
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None
