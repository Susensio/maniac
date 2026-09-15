# Implementation State

Work committed to and not yet finished.
Landed work lives in `docs/adr/` and the git history; it is removed from here once it lands, so this file stays short enough to read before starting.

## Unfinished

### The `gh` credential change was never independently reviewed

`b60459d` made GitHub API requests reuse the `gh` credential, and the `coding` skill asks for an independent review of a finished behaviour-changing feature.
That review never ran.
Verified live outside the sandbox on 2026-09-16 -- token resolved, allowlist admitted `api.github.com` and refused `github.com`, a lookalike and a subdomain, a real request reported a 5000 ceiling rather than 60 -- but a live check is not a review.

### Cross-host redirect stripping cannot be exercised end to end

Covered by unit tests only.
Release assets are fetched from `browser_download_url`, a `github.com` URL, so they never carry the header to strip.
Defence in depth against a future caller authenticating against a redirecting endpoint, not a path production reaches today.

### `Read.links` is computed and never shown

The manifest's structural link scan runs on every read (ADR-0046) and `Read` carries the result, but nothing renders it.
`maniac list` therefore still cannot tell you the manifest disagrees with the disk.
This waited on the list fact cache and is now simply open; `docs/BACKLOG.md` carries it.

## Live-system facts worth not rediscovering

The development machine is the only MANIAC installation in existence -- solo tool, solo developer, pre-1.0.
Its manifest holds two entries, `aichat` and `ty`, both `tier=synthesis` with targets set.
That is why both historical migrations were deleted rather than fixed: neither could ever have fired.

There is no populated Mise shim directory here, so shim discovery is unverified and `mise which -C $HOME` remains cwd-sensitive.
A live `mise activate bash` exports `MISE_SHELL`, `__MISE_EXE`, `__MISE_DIFF` and `__MISE_ORIG_PATH`.

Four stamped pages under `~/.local/share/maniac/manpages` carry provenance headers; tier-2 pages carry none, which is what lets recovery tell the two apart (ADR-0046).

A structural manifest scan costs ~0.046 ms per entry -- 0.157 ms for the live two-entry manifest, against an 11.8 s `maniac list`.
It walks what MANIAC owns, not what is on `$PATH`.

## Conventions learned the hard way

Three sessions allocated ADR numbers concurrently in September 2026 and collided twice; the manifest transaction ADR moved from 0044 to 0046.
Allocate a number when the decision lands, not when the work starts.

`master` is shared and has had commits from more than one session at a time, including a rewind that dropped an entire branch's work.
Check `git merge-base --is-ancestor` before moving it, and gate the move on that check rather than merely printing its result.
