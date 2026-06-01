# FR-001 candidate Tier-1 floor — Claude Code PM containment

The floor is a **launch profile** (CLI flags to `claude`), not in-repo settings.
Settings allow-lists govern *permission to use* a tool; they cannot re-create a
tool that `--tools ""` has removed from the session. The launch profile is
therefore authoritative and not overridable by a permissive `settings.json`.

## Exact green (contained PM) launch

```
claude --print \
  --tools "" \                                  # remove ALL built-in tools (Bash/Read/Write/Edit/Glob/Grep/WebFetch/Task/...)
  --strict-mcp-config \                          # ignore every globally-registered MCP server
  --mcp-config <cartopian-only.json> \           # expose ONLY the Cartopian MCP server
  --allowedTools "mcp__cartopian" \              # grant the fixed Cartopian toolset so the PM can actually work
  --disable-slash-commands \                     # defense-in-depth: no skill/prompt expansions
  --output-format stream-json --verbose          # (evidence capture only; not part of the floor)
# cwd = an isolated PM surface; NO --add-dir to the product repo or work roots
# NEVER pass --dangerously-skip-permissions (it re-enables the bypass posture)
```

`cartopian-only.json`:

```json
{ "mcpServers": { "cartopian": { "command": "/Users/scott/.cartopian/bin/cartopian-mcp", "args": [], "env": {} } } }
```

## Mechanism roles

| Mechanism | Removes / guarantees |
| --- | --- |
| `--tools ""` | Structurally removes every built-in tool — shell (`Bash`), raw write/edit (`Write`/`Edit`/`NotebookEdit`), raw read/glob/grep (`Read`/`Glob`/`Grep`), web, sub-agents. The session's built-in tool set becomes empty. |
| `--strict-mcp-config` + `--mcp-config` | The only MCP server in the session is Cartopian; globally-registered servers (claude.ai Gmail/Drive/Calendar) are excluded. |
| `--allowedTools "mcp__cartopian"` | Non-interactive `-p` auto-denies un-allowed tools; this grants the Cartopian server so the PM is functional rather than deadlocked. |
| isolated cwd, no `--add-dir` | No filesystem path outside cwd is granted — moot once `--tools ""` removes all FS tools, but keeps the surface tight. |
| `--disable-slash-commands` | Skills are prompt expansions, not tool grants, but disabling them removes a confusing affordance. |

## Reproduce the evidence

```
./run-probes.sh          # 7 runs: red/green × {shell, raw-write, product-repo-read}
                         #   + one green positive probe (mcp__cartopian__discover_projects)
                         # writes transcripts + inventories + sentinel/positive checks +
                         # on-disk results to ./evidence/
```

The harness's `GREEN_FLAGS` is exactly the security-relevant flag set above —
`--tools "" --strict-mcp-config --mcp-config <cartopian-only> --allowedTools "mcp__cartopian" --disable-slash-commands`
— so the captured green transcripts reflect the exact documented floor. `--max-turns`/`--model`
are harness knobs, and `--output-format stream-json --verbose` is evidence capture only.

All four green runs (the three prohibited-operation probes and the positive
`discover_projects` probe) use this one profile and the same isolated cwd
(`pm-surface/`), so the inventory, the prohibited-operation transcripts, and the
positive-tool transcript all describe one and the same contained PM.
