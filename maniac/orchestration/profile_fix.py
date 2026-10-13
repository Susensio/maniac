"""The fix for a global install the login `$PATH` misses (CONTRACT.md rule 2).

`install` and `why` both say it: the setup builds `$PATH` in an interactive
rc file, and one line in the login profile puts the install where maniac
looks. Mise's own shims setup is that line for mise (its docs pair
`mise activate --shims` in the login profile with `mise activate` in the
rc file); any other installer's bin directory is added to `$PATH` there.
"""

import os
from pathlib import Path

from ..sources.lookup.resolution import Located, Outcome


def login_profile_fix(located: Located) -> str:
    """One sentence: which line to add to which login profile."""
    assert located.outcome is Outcome.OFF_LOGIN_PATH and located.hit is not None
    shell = Path(os.environ.get("SHELL") or "sh").name
    if located.provider is not None and located.provider.name == "mise":
        line = (
            f"mise activate {shell} --shims | source"
            if shell == "fish"
            else f'eval "$(mise activate {shell} --shims)"'
        )
    else:
        directory = _home(located.hit.parent)
        line = (
            f"fish_add_path {directory}"
            if shell == "fish"
            else f'export PATH="{directory}:$PATH"'
        )
    return f"Add `{line}` to {_profile(shell)}."


def _profile(shell: str) -> str:
    if shell == "zsh":
        return "~/.zprofile"
    if shell == "fish":
        return "~/.config/fish/config.fish (inside `if status is-login`)"
    if shell == "bash":
        has_own = (Path.home() / ".bash_profile").exists()
        return "~/.bash_profile" if has_own else "~/.profile"
    return "~/.profile"


def _home(path: Path) -> str:
    """`path` with `$HOME` spelled the way a profile line would spell it."""
    home = Path.home()
    return f"$HOME/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
