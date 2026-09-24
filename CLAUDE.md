# Project records

- `docs/adr/` — settled decisions.
- `docs/BACKLOG.md` — open work nobody has committed to.
- `docs/STATE.md` — work committed to and not yet finished; absent when nothing is.

# Shared master

`master` has taken commits from more than one session at a time, including a rewind that dropped a branch's work.
Before moving it, gate the move on `git merge-base --is-ancestor`, not just print the result.

# Moving fast before 1.0.0

No interface is frozen. Rename, move, merge or delete any module,
function, parameter or CLI surface when a better shape is found, and update
every caller in the same change. Do not add shims, aliases or deprecated
parameters to spare a caller — the repository is the whole world and
`just check` proves it still builds.

Shape is free; behavior is not. A change described as a refactor must leave
observable behavior identical and verify that it did.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

This project has a knowledge graph. Use it to narrow scope before reading source — cheaper than Grep and gives structural context (callers, dependents, test coverage).

- **Exploring code** → `semantic_search_nodes_tool` / `query_graph_tool` instead of Grep
- **Impact of a change** → `get_impact_radius_tool` / `get_affected_flows_tool` instead of tracing imports by hand
- **Code review** → `detect_changes_tool` + `get_review_context_tool` instead of reading whole files
- **Test coverage** → `query_graph_tool` pattern="tests_for"
- **Architecture questions** → `get_architecture_overview_tool`
- **Renames / dead code** → `refactor_tool`

Graph narrows scope; source is still ground truth — verify before changing behavior, and treat an empty result as "not indexed," not "doesn't exist."
<!-- /code-review-graph MCP tools -->
