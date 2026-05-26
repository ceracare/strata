---
name: strata:save
description: Capture a session note. Two modes, direct (write immediately) and draft (build a draft from git session state, present for review, save on accept). Invoke autonomously at the end of substantive work (decisions made, files touched, context risks being lost). Pass `--draft` (or invoke when the user says "draft a save", "nudge me with a save", "what would you save") for the interactive review flow. Default is direct write. Skip if purely exploratory or <5 turns.
---

# strata:save

Persist what just happened so the next session can pick up where this one
Left off. Lands in `<vault>/<repo>/pr-context/<branch-slug>/<timestamp>--<initials>--<topic>.md`.

## Two modes

### Direct (default)

User says "save this as <topic>" or you decide to save autonomously.

1. Compose the note body in your reply.
2. Write:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/run-python.sh" \
  "${CLAUDE_PLUGIN_ROOT}/scripts/save-note.py" \
  --topic "<topic>" --kind session <<'STRATA_NOTE'
<note body>
STRATA_NOTE
```

### Draft (interactive review)

User says "draft a save", "what would you save", or you're at the Stop-hook
nudge and want help. Build a draft from real git state, then ask before
writing.

1. Snapshot the session:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/run-python.sh" -c \
  "import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts'); \
   import session_state; \
   print(session_state.draft_note_body(session_state.snapshot()))"
```

The draft has four sections: `What was done` (from commits), `In progress`
(from uncommitted), `Decided` (placeholder), `Left open` (placeholder).

2. Present the draft. Offer:
   - **Accept** — save with the suggested topic
   - **Edit** — apply changes (or fill Decided/Left open from conversation),
     re-confirm
   - **Skip** — abandon

3. On accept, pipe the final body through save-note.py with the topic
   from the draft.

### Apply-draft (one-keystroke acceptance of a Stop-hook offer)

When the Stop hook stashes a pre-filled draft (it does this when the
session crossed a signal threshold: 3+ commits, or 1+ commit with 3+
uncommitted files, or 8+ uncommitted files), the user can save it as-is
with no further prompting:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/run-python.sh" \
  "${CLAUDE_PLUGIN_ROOT}/scripts/save-note.py" \
  --apply-draft
```

The script reads `${PLUGIN_DATA}/pending-draft.json`, writes it to the
current branch's `pr-context/` folder, and clears the stash. Drafts older
than 24h are silently dropped (treat as no-op).

If the user wants to edit the draft first, snapshot it, present it, then
apply with the edited body (drop the stash via `import draft_store;
draft_store.clear_draft()` after, or let it expire naturally).

The stash only fires from the Stop hook when there's enough signal; the
user never sees a draft offer for trivial sessions.

## What to write (in a save body)

- **What was done** — 2-5 bullets.
- **Decisions made** — anything affecting future code, especially divergence
  from CLAUDE.md or established patterns.
- **Open questions / blockers** — things deliberately left unresolved.
- **Files touched** — relative paths, no diffs.

No large diffs, no command transcripts, no build output. Prose for humans
and me-on-Monday.

## --kind values

- `session` (default) — general working note
- `review` — feedback received on this branch
- `investigation` — bug hunt or spike notes
- `handoff` — explicit "next person, do X" note
- `decision-draft` — early thinking that may graduate to an ADR

## Safety

`save-note.py` doesn't run the PHI/secret linter automatically (`/strata:lint`
is explicit). Don't paste identifiers, NHS numbers, postcodes, or credentials.
The vault is shared.
