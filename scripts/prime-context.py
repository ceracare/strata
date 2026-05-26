#!/usr/bin/env python3
"""SessionStart hook — print a small context primer for the current branch.

Output budget: ~1500 chars. Goal is to surface "what was Claude told last time
on this branch" without dumping the whole memory dir.

The hook stdout is appended to the session context by Claude Code.
"""
from __future__ import annotations

import json
import sys

import lib_loader  # noqa: F401  (sets sys.path so `lib` import works)
from lib import (
    branch_slug,
    current_branch,
    first_heading,
    is_git_repo,
    memory_dir,
    memory_display,
    pr_context_dir,
    repo_name,
    vault_root,
)

PRIMER_CHAR_BUDGET = 3500
RECENT_DECISIONS = 3
PR_NOTES_TAIL = 3
FILES_WITH_CONTEXT_LIMIT = 6


def _excerpt(path, max_lines: int = 12) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = []
            for i, line in enumerate(fh):
                if i >= max_lines:
                    head.append("…")
                    break
                head.append(line.rstrip())
        return "\n".join(head)
    except OSError:
        return ""


def build_primer() -> str:
    out: list[str] = []
    push = out.append

    branch = current_branch()
    slug = branch_slug(branch) if is_git_repo() else None
    mem = memory_dir()

    push(f"## Strata primer — `{repo_name()}`"
         + (f" @ branch `{branch}`" if is_git_repo() else ""))
    push(f"_vault: {vault_root()}_")
    push("")

    # Legend + token economy. Both are best-effort — never block the
    # primer if they fail to compute.
    try:
        import primer_format
        push(primer_format.legend_line())
        econ_line = primer_format.format_economy(
            primer_format.compute_economy(mem)
        )
        if econ_line:
            push(econ_line)
        push("")
    except Exception:
        pass

    if not mem.exists():
        # Zero-prompt auto-init on first SessionStart. Safe: just creates
        # empty scope dirs + README scaffolding under the vault root.
        try:
            import importlib.util
            import os
            spec = importlib.util.spec_from_file_location(
                "init_memory",
                os.path.join(os.path.dirname(__file__), "init-memory.py"),
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                # Stash argv to avoid the script's argparse picking up
                # whatever Claude Code passed to the hook.
                old_argv = sys.argv
                sys.argv = ["init-memory.py"]
                try:
                    mod.main()
                finally:
                    sys.argv = old_argv
            push(f"_Vault auto-initialised at `{memory_display()}`._")
            push("")
        except Exception as e:
            push(f"_No memory at `{memory_display()}` yet "
                 f"(auto-init failed: {e}). Run `/strata:init`._")
            return "\n".join(out) + "\n"

    # Files with recent context — inverse index from notes' source_file
    # frontmatter. Lets readers landing in a file see which notes touched
    # it without running a search. Placed early because it's the most
    # navigation-relevant block and stays compact.
    try:
        import primer_format
        file_index = primer_format.index_by_source_file(
            mem, limit=FILES_WITH_CONTEXT_LIMIT
        )
        files_block = primer_format.format_files_section(mem, file_index)
        if files_block:
            push(files_block)
            push("")
    except Exception:
        pass

    # Active PR context for this branch (only if we have a branch)
    prdir = pr_context_dir(slug) if slug else None
    if prdir and prdir.exists():
        notes = sorted(prdir.glob("*.md"))
        if notes:
            push(f"### PR context — `{slug}` ({len(notes)} note(s))")
            push("")
            for n in notes[-PR_NOTES_TAIL:]:
                title = first_heading(n) or n.stem
                push(f"#### {n.name} — {title}")
                push("```")
                push(_excerpt(n))
                push("```")
                push("")

    # Most recent live decisions (by filename, which is YYYY-MM-DD-prefixed).
    # Hide superseded ones — they're available via the decision_chain tool.
    dec = mem / "decisions"
    if dec.exists():
        superseded: set[str] = set()
        try:
            import db as _db
            superseded = _db.superseded_paths()
        except Exception:
            pass
        candidates = [
            f for f in sorted(dec.glob("*.md"), reverse=True)
            if f.name not in ("README.md", "INDEX.md")
            and f.relative_to(mem).as_posix() not in superseded
        ][:RECENT_DECISIONS]
        if candidates:
            push(f"### Recent decisions ({len(candidates)})")
            for f in candidates:
                title = first_heading(f) or f.stem
                push(f"- `{f.relative_to(mem)}` — {title}")
            if superseded:
                push(f"_({len(superseded)} superseded hidden)_")
            push("")

    # Domain notes index (titles only)
    dom = mem / "domain"
    if dom.exists():
        files = [f for f in sorted(dom.glob("*.md"))
                 if f.name not in ("README.md", "INDEX.md")]
        if files:
            push(f"### Domain notes available ({len(files)})")
            titles = [first_heading(f) or f.stem for f in files]
            push(", ".join(f"`{t}`" for t in titles[:20]))
            if len(files) > 20:
                push(f"… and {len(files) - 20} more")
            push("")

    # Open PR context (if `gh` is installed + authed and the branch has a PR)
    try:
        import pr_context as _pr
        pr = _pr.fetch_for_current_branch()
        block = _pr.format_for_primer(pr)
        if block:
            push(block)
    except Exception as e:  # never let PR fetch break the primer
        push(f"_(PR context skipped: {e})_")
        push("")

    # Code graph (Graphify) — if `graphify-out/graph.json` exists at the
    # project root. We don't depend on graphify; we just surface its output.
    try:
        import code_graph as _cg
        cg = _cg.summary()
        if cg:
            push(_cg.format_primer_block(cg))
    except Exception as e:
        push(f"_(code graph skipped: {e})_")
        push("")

    push("_Use `/strata:find <query>` for full search, "
         "`/strata:save` to write a session note, "
         "`/strata:decide` for an ADR._")

    primer = "\n".join(out)
    if len(primer) > PRIMER_CHAR_BUDGET:
        primer = primer[:PRIMER_CHAR_BUDGET - 20] + "\n… [primer truncated]"
    return primer + "\n"


def main() -> int:
    # SessionStart hook receives a small JSON blob on stdin; we don't need it.
    import contextlib
    with contextlib.suppress(Exception):
        _ = sys.stdin.read()

    # Emit as additionalContext if Claude Code supports the structured form.
    # Falling back to plain stdout works on older versions too.
    primer = build_primer()
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": primer,
        }
    }
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
