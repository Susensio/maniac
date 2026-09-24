---
paths:
  - "maniac/listing/**"
  - "maniac/cli/listing.py"
---

`maniac list` states are only visible in a real terminal -- piped output degrades to bare names and the Live view truncates rows below roughly 220 columns, so verify a state change in a wide tmux pane, not a pipe.
Under the agent sandbox `man` cannot open `~/.local/share/man` and silently falls through to `/usr/share/man`, so sandboxed `maniac list` is not the table the user sees -- any reachability or grouping claim must come from an unsandboxed run (`man --debug -w <name>` shows the fall-through).
