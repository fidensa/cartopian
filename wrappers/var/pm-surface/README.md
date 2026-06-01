# PM surface (isolated launch cwd)

This directory is the **isolated working directory** for the contained Claude
Code PM launched by `wrappers/bin/cartopian-claude-pm` (DEC-001 / FR-002 floor).

It is intentionally empty of project content. The contained PM has **no
filesystem tool** (`--tools ""`), so nothing under this directory — or anywhere
else — is reachable as raw files; the PM acts on the project only through the
Cartopian MCP tools, which run out-of-process. The cwd is kept benign and
separate from the product repo and work roots purely as defense-in-depth.

Do not place secrets or product files here.
