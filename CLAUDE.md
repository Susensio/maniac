# Project records

- `docs/BACKLOG.md` — open work nobody has committed to. The main agent writes it.
- `docs/STATE.md` — work committed to and not yet finished. The main agent writes it.

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
