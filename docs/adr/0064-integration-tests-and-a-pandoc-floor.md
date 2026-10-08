# ADR-0064: Test against real pandoc, groff and man, and require pandoc 3.1.10

Status: Accepted
Date: 2026-10-08

## Context

Every pandoc path in the unit suite was mocked.
Two tests compiled with real pandoc when it happened to be installed and skipped otherwise.
So the suite's result depended on the developer's pandoc, and CI, which installs none, never compiled a page.
Nothing checked what `man` finally shows.

Running real tools on 2026-10-08 found three defects the mocks could not:

- **Option dashes.** Pandoc before 3.1.10 writes `-` bare in roff; from 3.1.10 it writes `\-` (bisected against release binaries 3.1.3 to 3.1.11).
  An unpatched groff (Arch, Fedora, Homebrew) renders a bare `-` as U+2010 HYPHEN, so every option on the page reads `‐‐flag`.
  It cannot be pasted into a shell or found with `/--flag`.
  Debian's site `man.local` maps `-` to `\-` and hides this, which is why it went unseen.
  Ubuntu 24.04, the CI runner, ships pandoc 3.1.3.
- **Code blocks.** `clean_manpage_markdown` unwrapped a model's outer ```` ``` ```` fence by deleting every fence line.
  Every EXAMPLES block in a wrapped response became one run-on paragraph.
- **Footer.** An unclaimed binary's recorded version is its verbatim `--version` output (ADR-0020), which already names the tool.
  The `.TH` footer prefixed the name again (`faketool faketool 2.3.1`), and pandoc folded a multi-line one (gcc's copyright notice) into it.

One unit test also passed only where `/bin/pandoc-lua` did not exist: its fake installations used real `/bin` paths, and ADR-0049's identity checks read the filesystem.

## Decision

`tests/integration/` holds tests that run real pandoc, groff and man.
They are marked `integration`, deselected from `just test`, run by `just integration`, and both run under `just check`.
They fail rather than skip when a tool is missing.
A skipped integration suite would read as green and prove nothing.
Only the model is replaced, at `llm._complete_litellm`.
Crawling, prompt building, response cleaning, compilation, installation, the manifest, `man -w`, `list` and `uninstall` all run for real against a small shell CLI with subcommands.

What the page shows is judged as an unpatched system shows it.
Rendering uses groff with empty `man.local`/`mdoc.local` placed first on the macro path, so Debian's remapping cannot hide a defect.
Pages must pass `groff -ww -z` with no output, every option in the fixture must render with ASCII hyphens, and each example command must render on its own line, as typed.

maniac requires pandoc 3.1.10 or newer.
`require_supported_pandoc` reads `pandoc --version` once per process.
It raises `UnsupportedPandoc`, naming the found version, why it matters and where to get a newer one, for an older release or an unreadable answer (ADR-0060).
Synthesis checks it before calling the model, so an old pandoc never costs a synthesis it cannot compile.
`compile_to_man` checks it too, and `install --dry-run` reports it.
A missing pandoc keeps its existing handling.
Post-processing old pandoc's output to escape dashes was considered and declined in favour of the floor.

CI installs pandoc from its release `.deb`, pinned in the workflow (3.12.1 at the time of writing), plus `groff-base` and `man-db`.

The three defects are fixed: only the outermost fence is unwrapped, and `compile_to_man` takes a finished `footer`, which `version_footer` builds as `name version` for a provider's version and as the first line of a verbatim one.
The listing test helper uses paths under `/nonexistent`.

## Consequences

`just check` now needs pandoc 3.1.10+, groff and man-db locally; `just test` alone needs none of them.

Users on Ubuntu 24.04's packaged pandoc, and older distributions, must install pandoc from its releases before maniac will synthesize.
Tiers 1 and 2, which install pages they did not compile, are unaffected.

The integration suite takes about five seconds.

Unit tests read pandoc's version as supported through a conftest seam; integration tests keep the real check.
