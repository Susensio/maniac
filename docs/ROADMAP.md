# Roadmap

Sequenced plan for the provider model and authoritative-manpage work decided in [ADR-0015](adr/0015-installer-provider-model.md) and [ADR-0016](adr/0016-authoritative-manpages-first.md).
Unsequenced work lives in `docs/BACKLOG.md`; what is already built lives in `docs/STATE.md`.

Each stage lands green under `just check` and is independently useful.
Stages 1-3 change no behaviour and are proven by the existing tests continuing to pass untouched.

## Stage 1 — the core type and the protocol

Add `Installation` and the `Provider` protocol. Nothing calls them yet.

```python
@dataclass(frozen=True, slots=True)
class Installation:
    binary: str  # "hx"
    bin_path: Path  # ~/.local/bin/hx, the symlink or real file
    real_path: Path  # what it resolves to
    provider: str  # "mise"
    package: str  # identity in the provider's namespace
    version: str | None  # "25.01"
    root: Path  # install root; docs may live under it
    parent: "Installation | None" = None  # mise -> its backend
```

```python
class Provider(Protocol):
    name: str

    def detect(self, bin_path: Path) -> Installation | None: ...
    def resolve_source(self, inst: Installation) -> RepoSource | None: ...
    def local_docs(self, inst: Installation) -> list[Path]: ...
```

Boundaries, per ADR-0015: `detect` is pure-filesystem and cheap; `resolve_source` may consult a registry; `local_docs` is pure-filesystem.
No provider imports another. Composition happens through `parent` and the registry that owns the provider list.

Deliverable: the two definitions, a provider registry with ordering, and unit tests over `Installation` alone.

## Stage 2 — port the three existing providers

Move mise, uv tools and `~/.local/lib` behind the protocol.
`discover_candidate_source` becomes a loop over registered providers.

The correctness proof for this stage is that every existing test passes **unmodified**.
A test that has to change is a behaviour change and does not belong in this stage.

Deliverable: `sources/providers/{mise,uv,local_lib}.py`, `discovery.py` reduced to selection and the registry.

## Stage 3 — read mise's backend record

Try `<install root>/.mise.backend.toml` first: `full = "aqua:biomejs/biome"` gives backend and package identity directly.
Fall back to the registry keyed on the install directory name, which is installation-derived evidence and therefore permitted under ADR-0008's rule.

Removes the `github-` / `pipx-` / `npm-` / `cargo-https-github-com-` prefix parsing from `_resolve_from_mise`.
19 of ~50 installs on the development system carry the file; `cargo-https-github-com-nushell-nufmt` does not, and exercises the fallback.

Deliverable: backend-record reader, prefix special-cases deleted, both paths tested.

## Stage 4 — the remaining providers

Add npm global, pipx, cargo, go, Homebrew. Drop `is_symlink()` as a precondition; each provider decides its own evidence.

| provider | detect | identity | notes |
|---|---|---|---|
| npm global | `<prefix>/lib/node_modules/<pkg>/` | `package.json` `repository` | explicit field, no inference |
| pipx | `$PIPX_HOME/venvs/<pkg>/` | dist-info `METADATA` | same shape as uv |
| cargo | `$CARGO_HOME/bin/<bin>` (real file) | `$CARGO_HOME/.crates2.json` | **read `$CARGO_HOME`**, not `~/.cargo` |
| go | `$GOBIN` or `~/go/bin` (real file) | `go version -m <bin>` | subprocess |
| homebrew | `<prefix>/bin` -> `../Cellar/<pkg>/<ver>/` | `brew info --json` | same symlink shape as mise |

Verification gap, per ADR-0015: none of these five is installed on the development system, and mise's own cargo backend does not exercise a standalone cargo provider.
Cargo and go need a real `cargo install` and `go install` before they can be verified rather than assumed.

## Stage 5 — authoritative manpage discovery

The tier-1 source from ADR-0016: find a manual page inside the install root.

Fix the two defects in `find_repo_manpage` first, both currently latent because it is dead code:

- the glob `<bin>.[1-9]` does not match `pandoc.1.gz` — handle `.gz`, `.bz2`, `.xz`, `.zst`
- the search covers a repo root and `man`/`doc`/`docs` only — it must reach `share/man/man<N>/`

Then wire it to install roots. `pandoc` is the acceptance case: three pages shipped, none on the manpath.

Deliverable: `local_docs()` implemented generically over the install root, a manpage finder that handles compression and `share/man`, and `pandoc.1` installable end to end with no LLM call.

## Stage 6 — `generate` becomes `install`

Rename per ADR-0016, making it the inverse of `uninstall`.
Add `--generate` (tier 3 only) and `--no-generate` (tiers 1-2 only, never calls an LLM).

Tier order inside the command: install root, then repository or online with the version matched, then synthesis.
Report which tier answered:

```
$ maniac install pandoc
pandoc   upstream manpage from install root (3.10.2)   [no synthesis]
$ maniac install uv
uv       synthesized from --help + repo docs
```

Tier 2 must check the page matches the binary it claims to document and that the version corresponds — ADR-0016 records why a wrongly sourced page installed verbatim is worse than a wrongly sourced generated one.

## Stage 7 — `status` reports the new states

Remove `--candidates` and `min_words_per_flag` with the quality-judgement scope cut.
`status` reports three states, one of which is free to act on:

| state | action | cost |
|---|---|---|
| ships a page, not installed | install it | zero |
| no page anywhere | synthesize | LLM |
| MANIAC-managed | inventory | — |

Enumeration inverts to walking providers rather than scanning the manpath.
Justified by capability, not speed: measured at 1.82s against 1.96s, over a fixed 0.84s of startup.

The unit is the binary, not the package — upstream itself ships `pandoc.1`, `pandoc-lua.1` and `pandoc-server.1` separately, and `man` is looked up by command name.
Package identity is used only to share one fetched doc set across sibling binaries and to collapse status rows visually.

Keep the redirected-output contract from ADR-0013: bare names, one per line, so `maniac status | xargs maniac install` works.

## Open questions, to settle with measurement once providers exist

- **No matching upstream version.** Refusing is consistent with never guessing, but the coverage cost is unmeasured. Measure how many tools would be lost before choosing.
- **Two providers claim one binary.** Decided: first match in `$PATH` order, since that is the binary that runs. Losers recorded, not discarded.
