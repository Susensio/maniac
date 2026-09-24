# ADR-0059: Leave system-package-manager binaries out of scope instead of adding a Debian provider for them

Status: Accepted
Date: 2026-09-24

## Context

The backlog carried "serve system and distro-packaged binaries, starting with Debian package provenance".
No provider claimed binaries in the standard system bin directories (`/bin`, `/sbin`, `/usr/bin`, `/usr/sbin`), so they had no `Installation` and dropped out of enumeration.

Commit 3762f45 added a `DebianProvider` that claimed every dpkg-owned binary there.
It used batched `dpkg-query -S` ownership, took the version from `${Version}`, and took upstream identity only from a GitHub URL in the DEP-5 `copyright` `Source:` field or `${Homepage}`, never from `Vcs-*`.
It worked as specified: `jq` resolved to `jqlang/jq` and `curl`, whose URLs are both websites, resolved to nothing.
Measured on the development machine, `maniac list` went from 81 rows in 19s to 2680 rows in 93s.
It also broke ADR-0026's property that no package-manager query runs for the thousands of system `$PATH` entries no installer claims.

Three narrower shapes were weighed:
claim system binaries only on an explicit by-name lookup;
keep them in enumeration but batch every per-package query;
put them behind an opt-in `list` flag.
Each one kept the cost somewhere, and none answered what maniac would *do* for such a binary.

## Decision

Binaries owned by the system package manager are out of scope for maniac.
No provider claims them, and discovery, source resolution and installation do no work for them.
3762f45 was reverted.

The reason is actionability, not cost.
A distribution package already installs its manpage and shell completions alongside the binary.
The page is maintained by the packager and upgraded with the package, so maniac has nothing to install and nothing to keep fresh.
maniac's value lies with tools installed outside the package manager (mise, uv, cargo, go, npm, pipx, Homebrew, `~/.local/lib` checkouts), which routinely ship without a reachable page.

ADR-0026/0055/0056 remain: they verify an *external* page that shadows a tool maniac does manage, and asking dpkg who owns that page is evidence about the managed tool, not service to the system binary.

## Consequences

`list` and enumeration stay bounded by what installer providers claim, and ADR-0026's no-query property holds.

A system binary whose package ships no manpage gets nothing from maniac.
That gap was judged rare and belongs upstream with the packager.

Backlog work premised on serving system binaries was dropped with this: other package-manager providers, and extracting documentation from system packages.
Reopening the question means superseding this ADR, not reviving the reverted provider piecemeal.
