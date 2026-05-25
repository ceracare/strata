<p align="center">
  <img src="docs/public/favicon.svg" width="56" alt="Strata">
</p>

<h1 align="center">Strata</h1>

<p align="center">
  Local-first memory for Claude Code. Episodic + semantic + procedural,
  kept distinct in one markdown vault on your disk.
</p>

<p align="center">
  <a href="https://github.com/ceracare/strata/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ceracare/strata/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/ceracare/strata/blob/main/LICENSE"><img alt="License: 0BSD" src="https://img.shields.io/badge/license-0BSD-blue"></a>
  <img alt="Python ≥ 3.10" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="No network" src="https://img.shields.io/badge/network-zero%20calls-success">
</p>

<p align="center">
  <a href="https://ceracare.github.io/strata/">Documentation</a> ·
  <a href="https://ceracare.github.io/strata/guide/getting-started/">Getting started</a> ·
  <a href="https://ceracare.github.io/strata/guide/faq/">FAQ</a>
</p>

---

```text
$ /plugin marketplace add https://github.com/ceracare/strata
$ /plugin install strata@strata
$ /strata:init
✓ Strata initialised at ~/StrataVault/<repo>/
  scopes: decisions/ domain/ lessons/ procedural/ propositions/ pr-context/
```

That's the whole setup. Strata is a Claude Code plugin that captures durable knowledge (decisions, domain vocabulary, recipes, per-branch work) into a markdown vault on disk and surfaces it automatically in Claude conversations via the Model Context Protocol.

## How it works

```
   Claude Code session
   ─────────────────────────────────────────────────────────
   You ─→ Claude ─┬─→ slash commands (write)   ─→ Vault (~/StrataVault)
                  │                                  ├── decisions/
                  │                                  ├── domain/
                  │                                  ├── procedural/
                  │                                  ├── lessons/
                  │                                  ├── propositions/
                  │                                  └── pr-context/<branch>/
                  └─→ MCP tools     (read)     ─→ FTS5 + fastembed index
                                                    (local SQLite, never synced)
```

- **Writes are explicit.** You type `/strata:save`, `/strata:decide`, `/strata:domain`. No tool call can silently mutate the vault.
- **Reads are ambient.** Claude consults the vault on its own via 18 read-only MCP tools whenever the conversation touches a topic the vault knows about.
- **Local-first.** SQLite FTS5 for keyword search, on-device fastembed embeddings for semantic recall. No network calls in the runtime path.
- **Sync agnostic.** The vault is plain markdown + YAML frontmatter. Use Obsidian Sync, Syncthing, iCloud, git, or anything else.

## Memory model

Strata splits memory into three kinds, each on its own retrieval path with its own lifetime:

| Type | Holds | Vault scope | Lifetime |
|---|---|---|---|
| **Episodic** | What happened on a branch | `pr-context/<branch>/` | Until the branch merges |
| **Semantic** | Vocabulary, decisions, invariants | `domain/` + `decisions/` | Until superseded or invalidated |
| **Procedural** | Recipes and runbooks | `procedural/` | Until the recipe changes |

`lessons/` bridges episodic and procedural (retrospectives). `propositions/` tracks open questions through their lifecycle.

## What's in the box

- **24 slash commands** — `save`, `decide`, `domain`, `propose`, `procedure`, `correct`, `invalidate`, `forget`, `find`, `lint`, `bootstrap`, `archive`, `review`, `export-to-repo`, `promote-to-pr`, … most auto-invoke on intent
- **18-tool MCP server** — `memory_search`, `memory_semantic_search`, `memory_insights`, `recent_decisions`, `pr_context_for_branch`, `decision_chain`, … all read-only
- **Bootstrap pipeline** — `/strata:bootstrap` migrates existing planning docs (`docs/`, `.planning/`, etc.) into the vault via parallel worker subagents
- **Lint presets** — `secrets`, `pii`, `phi-uk`, `phi-us`, `financial-iban`. Pluggable JSON.
- **Optional Graphify integration** — code-structure graph for verified `code_refs` on notes and drift detection
- **228-test pytest suite** — covers scan, lib, MCP resources, lint presets, decision-symbol resolution

## Daily flow

```
/strata:save               # 30+ min on a branch and no note? Strata nudges. Write a few bullets.
/strata:decide "..."       # Architectural choice → ADR with reasoning
/strata:domain order       # New vocabulary term that the team agrees on

Ask Claude something:
  "what's our token rotation approach?"
  → Strata surfaces decisions/2026-05-12-jwt-rotation.md
  → Claude answers with citations
```

You almost never call `find` or `recall` directly. The MCP layer does it.

## Privacy & threat model

- **No network in the runtime path.** Greppable. Scripts only touch the filesystem and (optionally) `gh` for PR metadata.
- **No telemetry.** No analytics, no error reporting, no version-check ping.
- **No write tools over MCP.** Writes are user-confirmable by design; prompt injection can't mutate memory.
- **Sandboxed reads.** `memory_get` resolves only paths inside `<vault>/<repo>/`, rejects symlinks, and validates against traversal.

See [`SECURITY.md`](./SECURITY.md) for the full threat model and hardening notes for regulated data.

## Install

In any Claude Code session:

```text
/plugin marketplace add https://github.com/ceracare/strata
/plugin install strata@strata
/strata:init
```

Requirements: Python 3.10+ on `PATH`. A `.venv/` is auto-created inside the plugin directory on first run with two pinned deps (`mcp`, `python-frontmatter`). Nothing global.

Full install notes, team-config patterns, pre-push lint hook setup: [`INSTALL.md`](./INSTALL.md).

## Documentation

- [What is Strata](https://ceracare.github.io/strata/guide/what-is-strata/) — plain-English intro
- [Getting started](https://ceracare.github.io/strata/guide/getting-started/) — five-minute setup
- [Memory architecture](https://ceracare.github.io/strata/guide/memory-architecture/) — why three kinds
- [Skills](https://ceracare.github.io/strata/guide/skills/) — every slash command
- [MCP tools](https://ceracare.github.io/strata/guide/mcp-tools/) — every tool exposed to Claude
- [Bootstrap](https://ceracare.github.io/strata/guide/bootstrap/) — migrating existing docs
- [Correcting the vault](https://ceracare.github.io/strata/guide/correcting/) — four operations with audit trail
- [Architecture](https://ceracare.github.io/strata/guide/architecture/) — how the pieces fit
- [FAQ](https://ceracare.github.io/strata/guide/faq/)

## What's intentionally NOT here

- **No bundled Obsidian MCP.** The vault is plain markdown; pair with any community Obsidian MCP if you want it. See [`OBSIDIAN.md`](./OBSIDIAN.md).
- **No write tools over MCP.** Writes go through user-confirmable slash commands.
- **No background monitors.** Hooks and explicit commands only.
- **No chat-history import.** PII risk and a search-noise multiplier.
- **No telemetry, no network.** Greppable in the source.

## License

[0BSD](./LICENSE). Use it, modify it, ship it commercially. No attribution required.

## Contributing

Issues and PRs welcome. Keep it stdlib-leaning, keep the threat model intact, and put new functionality behind opt-in flags rather than enabling by default.
