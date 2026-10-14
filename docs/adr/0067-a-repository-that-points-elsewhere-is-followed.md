# ADR-0067: Follow a repository that points at the tool's own

Status: Accepted
Date: 2026-10-14

## Context

The repository a tool's documentation comes from is the one its installer records: a package's metadata, mise's registry, a backend's own record.
Sometimes that record names a repository that packages the tool rather than the one that writes it:

- pyright from PyPI records `RobertCraigie/pyright-python`, a Python wrapper that downloads and runs Microsoft's pyright. Its README documents the wrapper, so a generated page described the wrong program. It mentions 1 of the 18 options `pyright --help` prints; the right repositories of black, mypy, flake8 and poetry mention 84 to 100% of theirs.
- tmux from mise records `tmux/tmux-builds`, which builds static binaries. It has tags matching tmux's versions but no page, so without help maniac would generate one while `tmux/tmux` has `tmux.1` at the same tag.

tmux was handled by a hand-kept table in `defaults.toml` (`"tmux/tmux-builds" = "tmux/tmux"`), which every such case would have to join, and which a user could neither extend nor overrule.

## Decision

When the recorded repository is not named like the tool, maniac looks for the tool's own repository in what the recorded one says about itself, and follows it:

- Asked only when the repository's name is neither the binary's nor the package's: a repository named like its tool is never second-guessed.
- The recorded repository's README, build scripts and CI workflows, at the installed version's tag or else its default branch, are searched for GitHub repositories named exactly like the binary or the package. The one linked most is the candidate.
- It is followed only if it has a tag matching the installed version (ADR-0016): the redirect never trades a version-matched source for an unmatched one.
- Anything less is no redirect, and the recorded repository stands.

`install` warns whenever it follows a detected redirect, naming both repositories and the evidence; `why` explains every redirect and where it came from.

The user decides over detection: `config.toml` takes a `[documentation_repositories]` table keyed by the recorded repository.
An entry naming another repository redirects to it; an entry naming the repository itself keeps it, which turns detection off for it.
The order is the user's table, then detection, then the record as it stands.

The built-in table is deleted: detection covers tmux, and a user who disagrees has the table.

## Consequences

`docs/CONTRACT.md`'s `upstream` source names the tool's own repository, which may be the one its installer's repository points at.

The option overlap that measured pyright is evidence `why` could show but detection does not use: it needs every file of the recorded repository, and the name, the link and the matching tag already have to agree.
A redirect is asked once per recorded repository, binary, package and version, and its answer cached; one whose check did not finish is asked again (rule 4).

## Corrections

2026-10-14, later: three tightenings, after weighing where detection could misfire.
Names compare with `-`, `_` and `.` as one separator (`ripgrep_all` is `ripgrep-all`), so a repository named like its package is not searched for want of spelling.
The links must be evidence, not a mention: two of them, or one from a build script or workflow, which builds or fetches what it names; a single README line can be an "inspired by", and a young tool's `0.1.0` tag is one many namesakes share.
`install` also warns when the repository a page comes from differs from the one the page it replaces came from, so a redirect that applied last time and no longer does is said rather than left to a silently different page; the manifest records each page's repository for that comparison, and an upstream page recorded before then names it as its source.
A fork that links the project it forked, and keeps its tags, is still followed to that project: legitimately ambiguous, it is warned about and left to the user's table.
