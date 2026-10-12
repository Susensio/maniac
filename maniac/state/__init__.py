"""The state of a tool's page: shared by every command that reports one.

| state      | means                                                                    |
|------------|--------------------------------------------------------------------------|
| ok         | a page resolves through `man` now and is current by local evidence       |
| unknown    | a page resolves but nothing proves which version it documents           |
| outdated   | a page resolves, and positive evidence says it documents another version |
| available  | nothing resolves, but a page can be had without an LLM                   |
| missing    | nothing resolves and no free page is known                               |
| error      | this tool's own metadata file is present but malformed (ADR-0060)        |

`classification` judges one page from local evidence (`man`, the manifest,
the install root); `pages` judges the pages maniac installed (`list`);
`candidates` turns a lookup into the row it stands for. `listing` (`scan`'s
discovery), `orchestration` (`install`, `why`) and `cli` build on this; it
imports none of them.
"""
