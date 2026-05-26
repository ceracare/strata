# Pairing Strata with an Obsidian MCP

Strata operates on plain markdown, Obsidian doesn't need to be running. But
if you're using Obsidian as the human-facing UI and want Claude to do
Obsidian-specific operations (linking, daily-note creation, tag manipulation,
canvas, etc.), pair Strata with one of the public Obsidian MCP servers.

We don't bundle these, different teams have different trust models. Pick the
one that matches yours and install it as a separate MCP server in your
`settings.json`.

## The two main public options

### MarkusPfundstein/mcp-obsidian (Python)

- **What:** MCP server that talks to Obsidian via the official **Obsidian
  Local REST API community plugin**.
- **Why it's well known:** referenced in most Obsidian + Claude tutorials,
  high GitHub star count, MIT licensed, actively maintained.
- **Tools:** read notes, list directories, search, create/append/patch notes.
- **Trust signals:** small, focused codebase; depends on the Local REST API
  plugin which is well-audited; runs locally as stdio.
- **Caveats:**
  - Has **write tools** — anything Claude does (including via prompt
    injection from a search result it reads) can mutate your vault.
  - Requires Obsidian to be running with the Local REST API plugin enabled.
  - The Local REST API plugin listens on localhost:27124 by default — fine
    for single-user, but check your firewall rules.

### cyanheads/obsidian-mcp-server (TypeScript/Node)

- **What:** Alternative Obsidian MCP, also bridging via the Local REST API.
- **Why look at it:** Different language stack if you prefer Node, has a
  dedicated `SECURITY` page, slightly different tool set.
- **Trust signals:** newer, smaller community, but security-conscious
  presentation. Inspect commit history before adopting.

## How they relate to Strata

| Layer | Strata | Obsidian MCP |
|---|---|---|
| Sources of truth | Files on disk (vault) | Same files, accessed via Obsidian |
| Reads | FTS5 + path-sandboxed `memory_get` | Whatever the Obsidian plugin exposes |
| Writes | User-typed slash commands only | MCP tools — Claude can write directly |
| Sandbox | Vault root only | Whole vault (per Obsidian) |
| Network | None | Loopback to localhost:27124 |
| Deps | `mcp`, `python-frontmatter` | Whatever the plugin uses + Obsidian itself |

**Recommended pairing:** use Strata for structured memory (ADRs,
session notes, domain notes, the things the team agrees on the shape of),
and use the Obsidian MCP for Obsidian-specific actions (link a note,
attach to a canvas, render a graph, create a daily note).

## Hardening notes

If you install an Obsidian MCP alongside Strata:

1. **Pin the version** in your settings.json or package manifest. Don't
   track `latest`.
2. **Audit the tools** the MCP exposes. If you can disable write tools and
   only enable read tools, do so for regulated content.
3. **Restrict the Local REST API plugin** to localhost; never expose its
   port to the network.
4. **Keep Strata as the ADR / decision authority.** It's the one with
   no write surface, that's a feature, not a bug.

## Why we don't bundle one

- Trust is per-team. We don't want to silently pull in a third-party MCP
  with write tools.
- Bundling means inheriting the bundled MCP's update cadence and bugs.
- The Obsidian MCPs assume Obsidian is running — Strata doesn't.

If you build a wrapper plugin that combines them under one install, link to
it from your fork's README. We'll happily reference it.

---

## Pairing Graphify so code structure appears in Obsidian's graph view

[Graphify](https://graphifylabs.ai) extracts code structure into
`graphify-out/graph.json`. Strata reads that file and writes one
markdown note per node into `<vault>/<repo>/graphify/` with
`[[wikilinks]]` for every edge, so Obsidian's graph view shows code
nodes alongside your decisions and domain notes.

The integration is built into `/strata:graphify`:

```text
/strata:graphify
```

That single command:

1. Runs `graphify update .` (AST-only, no LLM, no network) to produce
   `graphify-out/graph.json`.
2. Reads `graph.json` and writes per-node markdown into
   `<vault>/<repo>/graphify/`. Pure-mechanical, no LLM key required.

Pass `--no-obsidian` if you want only the graph build without the
vault export. The vault wiring is the default.

Resulting layout:

```
~/StrataVault/<repo>/
├── decisions/                  # ADRs (Strata-managed)
├── domain/                     # vocabulary (Strata-managed)
├── pr-context/                 # per-branch notes (Strata-managed)
└── graphify/                   # per-function/-class/-module nodes
                                 (regenerated from graph.json by Strata)
```

Obsidian's graph view shows one continuous knowledge graph: a decision
note that wikilinks `[[MedicationService]]` visually connects to the
Strata-generated node for that class.

On the Strata side, `memory_graph` MCP tool **resolves unresolved
wikilinks against graph.json node names** — so a vault wikilink like
`[[MedicationService]]` returns a `graphify:MedicationService` match even
if no corresponding markdown file exists. The visual co-location is theirs;
the programmatic bridge is ours.

Filter recipes in Obsidian:
- `path:graphify` — show only code nodes
- `-path:graphify` — hide code nodes (decisions + domain + pr-context only)
- `path:decisions` — show only ADRs
