#!/usr/bin/env python3
"""Write a PR-context note for the current branch, or a retrospective
lesson note (with `--scope lessons`).

PR-context (default):
    python3 save-note.py --topic "<topic>" [--kind ...]
    → pr-context/<branch-slug>/YYYY-MM-DD-HHMM--<initials>--<slug>.md
    Branch-scoped. For in-flight work on the current branch.

Lessons:
    python3 save-note.py --topic "<topic>" --scope lessons
    → lessons/YYYY-MM-DD-<slug>.md
    Branch-agnostic. For retrospectives, "we considered this in 2026",
    and bootstrap-extracted historical content that has no current
    branch context.

The body is written verbatim with a YAML frontmatter prefix. We do NOT
call the linter from here — `/strata:lint` runs explicitly, and the
pre-push git hook (if installed) will catch anything in the commit.
"""
from __future__ import annotations

import argparse
import sys

import lib_loader  # noqa: F401
from lib import (
    author_initials,
    author_name,
    branch_slug,
    current_branch,
    ensure_dir,
    is_git_repo,
    lessons_dir,
    pr_context_dir,
    safe_slug,
    stamp_minute,
    today,
    write_text,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default=None,
                    help="Short topic — used in filename. Required unless "
                         "--apply-draft is set (which supplies topic + body "
                         "from the Stop-hook stashed draft).")
    ap.add_argument("--kind", default="session",
                    choices=["session", "review", "decision-draft",
                             "investigation", "handoff"])
    ap.add_argument("--scope", default="pr-context",
                    choices=["pr-context", "lessons"],
                    help="Destination scope. Default `pr-context` writes "
                         "to the current branch's folder; `lessons` writes "
                         "to the branch-agnostic lessons/ for retrospective "
                         "and bootstrap-extracted content.")
    ap.add_argument("--source-file", action="append", default=[],
                    help="Provenance: project-relative path(s) of source doc(s) "
                         "this note was extracted from. Repeatable, or pass a "
                         "comma-joined list (used by /strata:bootstrap).")
    ap.add_argument("--project-dir", default=None,
                    help="Override the project root used for namespace "
                         "resolution. Same effect as setting STRATA_PROJECT_DIR. "
                         "Use this when invoking save-note.py from a directory "
                         "other than the target project's repo root.")
    ap.add_argument("--apply-draft", action="store_true",
                    help="Consume the pending draft stashed by the Stop hook. "
                         "Reads topic + body from ${PLUGIN_DATA}/pending-draft.json "
                         "and writes it as a pr-context session note. Mutually "
                         "exclusive with --topic + stdin body. Drafts older than "
                         "24h are silently ignored.")
    args = ap.parse_args()

    # --apply-draft hydrates topic + body from the stashed draft so the user
    # gets one-keystroke acceptance of the Stop-hook offer. The body still
    # gets written through the same code path below, so frontmatter +
    # indexing semantics stay identical.
    apply_draft_body: str | None = None
    if args.apply_draft:
        import draft_store
        draft = draft_store.load_draft()
        if draft is None:
            print("[strata] error: no pending draft to apply (none stashed, "
                  "or older than 24h)", file=sys.stderr)
            return 2
        if args.topic is None:
            args.topic = draft["topic"]
        args.kind = draft.get("kind", args.kind)
        apply_draft_body = draft["body"]

    if not args.topic:
        print("[strata] error: --topic is required (or pass --apply-draft "
              "to consume a stashed draft)", file=sys.stderr)
        return 2

    if args.project_dir:
        import os as _os
        _os.environ["STRATA_PROJECT_DIR"] = args.project_dir

    # Accept comma-joined values too, and dedupe while preserving order.
    expanded: list[str] = []
    for entry in args.source_file:
        for piece in (s.strip() for s in entry.split(",")):
            if piece and piece not in expanded:
                expanded.append(piece)
    args.source_file = expanded

    if apply_draft_body is not None:
        body = apply_draft_body.strip()
    else:
        body = sys.stdin.read().strip()
    if not body:
        print("[strata] error: empty body on stdin", file=sys.stderr)
        return 2

    if is_git_repo():
        branch = current_branch()
        slug = branch_slug(branch)
    else:
        branch = "(no-repo)"
        slug = "_no-branch"

    init = author_initials()
    topic_slug = safe_slug(args.topic)

    if args.scope == "lessons":
        # Branch-agnostic; date-only filename matches the existing
        # lessons/2026-04-29-build-velocity-vs-birdie.md convention.
        dir_ = lessons_dir()
        ensure_dir(dir_)
        fname = f"{today()}-{topic_slug}.md"
        path = dir_ / fname
    else:
        when = stamp_minute()
        dir_ = pr_context_dir(slug)
        ensure_dir(dir_)
        fname = f"{when}--{init}--{topic_slug}.md"
        path = dir_ / fname

    fm_lines = ["---"]
    if args.scope == "pr-context":
        fm_lines.append(f"branch: {branch}")
    fm_lines.extend([
        f"kind: {args.kind}",
        f"author: {author_name()}",
        f"author_initials: {init}",
        f"topic: {args.topic}",
        f"created: {today() if args.scope == 'lessons' else stamp_minute()}",
    ])
    if args.source_file:
        if len(args.source_file) == 1:
            fm_lines.append(f"source_file: {args.source_file[0]}")
        else:
            fm_lines.append("source_file:")
            for src in args.source_file:
                fm_lines.append(f"  - {src}")
        fm_lines.append(f"extracted_at: {today()}")
        fm_lines.append(f"extracted_by: {author_name()}")
    fm_lines.append("---\n\n")
    frontmatter_str = "\n".join(fm_lines)

    write_text(path, frontmatter_str + body + "\n")
    print(f"[strata] saved {path}")

    # Successful apply consumes the stashed draft so it can't be applied
    # twice. Failure paths above leave the draft in place for retry.
    if args.apply_draft:
        import draft_store
        draft_store.clear_draft()

    # Refresh index so the new note is discoverable
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "refresh_index",
        os.path.join(os.path.dirname(__file__), "refresh-index.py"),
    )
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.regenerate_index()

    return 0


if __name__ == "__main__":
    sys.exit(main())
