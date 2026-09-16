# Implementation State

Work committed to and not yet finished.
Landed work lives in `docs/adr/` and the git history; it is removed from here once it lands, so this file stays short enough to read before starting.

## Unfinished

Nothing outstanding.

## Live-system facts worth not rediscovering

Cross-host redirect stripping cannot be exercised end to end, and this is not a gap to close.
Release assets are fetched from `browser_download_url`, a `github.com` URL, so they never carry the header to strip.
It is defence in depth against a future caller authenticating against a redirecting endpoint, not a path production reaches today, so unit tests are the only coverage it can have.
The independent review of the `gh` credential work ran on 2026-09-16 and passed: no credential can reach a non-GitHub host on any path in the tree.
Its three low findings were closed in `5a3d0f7` -- the host allowlist grew regression tests for the suffix and subdomain lookalikes, the redirect handler now compares scheme as well as host so a same-host `https`-to-`http` downgrade drops the header, and an environment-supplied token is stripped rather than passed through raw.

The development machine is the only MANIAC installation in existence -- solo tool, solo developer, pre-1.0.
Its manifest holds two entries, `aichat` and `ty`, both `tier=synthesis` with targets set.
That is why both historical migrations were deleted rather than fixed: neither could ever have fired.

There is no populated Mise shim directory here, so shim discovery is unverified and `mise which -C $HOME` remains cwd-sensitive.
A live `mise activate bash` exports `MISE_SHELL`, `__MISE_EXE`, `__MISE_DIFF` and `__MISE_ORIG_PATH`.

Four stamped pages under `~/.local/share/maniac/manpages` carry provenance headers; tier-2 pages carry none, which is what lets recovery tell the two apart (ADR-0046).

A structural manifest scan costs ~0.046 ms per entry -- 0.157 ms for the live two-entry manifest, against an 11.8 s `maniac list`.
It walks what MANIAC owns, not what is on `$PATH`.

## Conventions learned the hard way

A test stub that cannot occur in production hides the bug it is standing in for.
`maniac install`'s silent-success defect -- a compile failure leaving no page while exiting 0 and printing bold green -- survived because two CLI tests stubbed a *successful* synthesis with `installed_path=None`, a combination `pipeline.py` cannot produce.
The suite was green on a state the program never reaches, so nothing pinned the state it does.
The same shape hid an uninstall reporting bug twice over: one test stubbed `removed` and `changed` both populated, which that path cannot do.
When a test builds a result object by hand, check the producer can actually emit that combination.

Green is not reviewed, for uninstall and exit codes especially.
Three rounds were needed on the 2026-09-16 install/uninstall rework: two independent reviews each returned FAIL against a green suite, and every finding of the second was the first round's defect class still reachable one path over.
A fix written against a reported reproduction tends to close that reproduction and not the class.
Ask which neighbouring paths reach the same code before calling one closed, and where two places encode one rule, collapse them rather than patching both.

A subagent told `docs/` is out of scope may still `git checkout` an uncommitted change there, reading it as stray.
Commit records before dispatching work that touches the same tree, or expect to rewrite them.

Three sessions allocated ADR numbers concurrently in September 2026 and collided twice; the manifest transaction ADR moved from 0044 to 0046.
Allocate a number when the decision lands, not when the work starts.

`master` is shared and has had commits from more than one session at a time, including a rewind that dropped an entire branch's work.
Check `git merge-base --is-ancestor` before moving it, and gate the move on that check rather than merely printing its result.
