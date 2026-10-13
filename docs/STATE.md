# State

Work committed to and not yet finished. Delete this file when it is done.

## Seed generation with an older, human-written page of the same software

From `docs/BACKLOG.md` (Features). Started 2026-10-13.

When `install` falls to generation, the model also gets one page written by people for another version of the same software, labelled with the version it documents.
The current `--help` wins wherever they disagree; options it no longer lists are dropped.

Decided:
- Provenance: the page's header gains `Seed: <what, which version>`; the recorded version stays the current binary's (ADR-0019), so `list` and `update` are unchanged.
- Only human-written pages: never one maniac generated (provenance signature) or help2man made.
- Same software must be proven, never assumed from a shared name.

Seed sources, first found wins:
1. The page `man` shows today, when dpkg proves it is the same software at another version (`verify_external_page` MISMATCH): a distro's older page for a tool installed newer by uv, cargo, npm...
2. The repository's own page from its default branch (or latest release): the winner's repository, so the same software by construction; version from the page's `.TH` header, else unknown.

Later, not in this change (back to the backlog when this file goes):
- A losing `$PATH` copy's shipped page (`Installation.losers`, same package or repository as the winner): `locate` does not collect losers yet.
- The page maniac replaced (its backup): only a shared name ties it to the tool.

Steps:
- [ ] `seed_for` in the source-order seam (`sources/lookup/candidates.py`), with tests
- [ ] Prompt section and system-prompt guidance
- [ ] Provenance header `Seed:`; `install` and `why` say which seed was used (rule 3)
- [ ] Docs: backlog item, README's generation section; snapshots reviewed
- [ ] Measured with `maniac dev bench` (needs an API key: the user's run)
