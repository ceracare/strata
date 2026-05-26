#!/usr/bin/env python3
"""Vault health: stale-proposed ADRs, orphan domain notes, files missing
frontmatter, unresolved wikilinks, stale PR-context dirs, stale Graphify
graph. Read-only."""
from __future__ import annotations

import argparse
import sys
import time

import db
import lib_loader  # noqa: F401
from lib import info, memory_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-days", type=int, default=14,
                    help="ADRs in `proposed` older than this are flagged.")
    ap.add_argument("--pr-stale-days", type=int, default=30,
                    help="PR-context dirs older than this are flagged.")
    args = ap.parse_args()

    mem = memory_dir()
    if not mem.exists():
        print("[strata] no vault — run /strata:init", file=sys.stderr)
        return 2

    # Cheap reindex so the report reflects what's on disk
    try:
        db.reindex(force=False)
    except Exception as e:
        info(f"reindex skipped: {e}")

    out: list[str] = ["# Vault Review", ""]
    push = out.append

    # 1. Stale-proposed ADRs
    stale = db.stale_decisions(args.stale_days)
    push(f"## Stale-proposed ADRs (> {args.stale_days} days)")
    if not stale:
        push("_none — good_")
    else:
        for row in stale:
            push(f"- `{row['path']}` — {row['title']}  "
                 f"_({row['age_days']} days)_")
    push("")

    # 2. Orphan domain notes
    orphans = db.orphan_notes("domain")
    push("## Orphan domain notes (no wikilinks in or out)")
    if not orphans:
        push("_none — good_")
    else:
        for row in orphans:
            push(f"- `{row['path']}` — {row['title']}")
    push("")

    # 3. Missing frontmatter
    missing = db.files_missing_frontmatter()
    push("## Files missing required frontmatter")
    if not missing:
        push("_none — good_")
    else:
        # Group by scope for readability
        by_scope: dict[str, list[dict]] = {}
        for r in missing:
            by_scope.setdefault(r.get("scope", "?"), []).append(r)
        for scope, rows in sorted(by_scope.items()):
            push(f"### {scope}/")
            for r in rows:
                push(f"- `{r['path']}` — {r['title']}")
    push("")

    # 4. Unresolved wikilinks
    broken = db.unresolved_links()
    push("## Unresolved wikilinks (typo / deleted target)")
    if not broken:
        push("_none — good_")
    else:
        for r in broken[:30]:
            push(f"- `{r['src']}` → `[[{r['dst']}]]`")
        if len(broken) > 30:
            push(f"… and {len(broken) - 30} more")
    push("")

    # 5. Graphify code-graph staleness (only when graph.json is present)
    try:
        import code_graph as _cg
        age_info = _cg.graph_age_relative_to_head()
        push("## Code graph (Graphify)")
        if age_info is None:
            push("- _not present (or unreadable) — skipping_")
        else:
            marker = " 🔴 STALE" if age_info["stale"] else ""
            push(
                f"- graph.json built {age_info['graph_age_days']}d ago, "
                f"{age_info['commits_since']} commit(s) since"
                f"{marker}"
            )
            if age_info["stale"]:
                push(
                    "  → reason: " + age_info["reason"]
                )
                push("  → rebuild: `/strata:graphify`")
        push("")

        # 5b. Drifted notes — dual-axis drift detection
        drifted = _cg.find_drifted_notes()
        push("## Drifted notes")
        if not drifted:
            push("_none — good_")
        else:
            n_structural = sum(1 for d in drifted if d.get("unresolved"))
            n_temporal = sum(1 for d in drifted if d.get("churn_signal"))
            push(f"_{len(drifted)} note(s) likely need correction. "
                 f"Structural drift: {n_structural} (code_refs no longer "
                 f"resolve). Temporal drift: {n_temporal} (source code has "
                 f"churned heavily since note was written)._")
            push("")
            for d in drifted[:30]:
                push(f"- `{d['path']}` — {d['title']}")
                if d.get("unresolved"):
                    unresolved_str = ", ".join(
                        f"`{s}`" for s in d["unresolved"][:5])
                    more = (f" (+{len(d['unresolved']) - 5} more)"
                            if len(d["unresolved"]) > 5 else "")
                    push(f"  structural: unresolved {unresolved_str}{more}")
                if d.get("churn_signal"):
                    cs = d["churn_signal"]
                    push(f"  temporal: `{cs['source_file']}` has "
                         f"{cs['commits_since']} commits since "
                         f"{cs['created']}")
            if len(drifted) > 30:
                push(f"… and {len(drifted) - 30} more")
            push("")
            push("_Decide for each: `/strata:correct` (fix the note), "
                 "`/strata:invalidate` (mark superseded), or rebuild "
                 "graph if symbols genuinely still exist._")
        push("")

        # 5c. Top hotspots — Tornhill behavioural signal
        try:
            import commit_graph
            hot = commit_graph.hotspots(days=90, top=10)
            if hot:
                push("## Hot files (last 90 days)")
                push("_Top-churn files. High-leverage targets for review, "
                     "refactor, or `code_map` focus._")
                push("")
                for h in hot:
                    push(f"- `{h['path']}` — {h['commits']} commits")
                push("")
        except Exception:
            pass

        # 5d. ADR ↔ commit linkage — which decisions have shipped?
        try:
            import commit_graph
            mem = memory_dir()
            dec_dir = mem / "decisions"
            if dec_dir.is_dir():
                slugs = [f.stem for f in dec_dir.glob("*.md")
                        if f.name not in ("README.md", "INDEX.md")]
                links = commit_graph.adr_implementations(slugs)
                if links:
                    push("## ADR implementations")
                    push(f"_{len(links)} ADR(s) referenced in recent commit "
                         "messages. Bidirectional traceability: which "
                         "decisions have actually become code._")
                    push("")
                    for slug, commits in sorted(links.items()):
                        c = commits[0]
                        more = (f" (+{len(commits) - 1} more)"
                                if len(commits) > 1 else "")
                        push(f"- `decisions/{slug}.md` ← `{c['sha']}` "
                             f"({c['date'][:10]}): {c['subject'][:60]}{more}")
                    push("")
        except Exception:
            pass
    except Exception as e:  # never fail review on graph issues
        info(f"graphify check skipped: {e}")

    # 6. Stale active PR contexts (by directory mtime)
    pr_root = mem / "pr-context"
    if pr_root.exists():
        push(f"## Stale PR-context dirs (> {args.pr_stale_days} days, "
             "not yet archived)")
        cutoff = time.time() - args.pr_stale_days * 86400
        stale_dirs: list[tuple[str, int]] = []
        for sub in sorted(p for p in pr_root.iterdir() if p.is_dir()):
            try:
                mtime = sub.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                age_days = int((time.time() - mtime) // 86400)
                stale_dirs.append((sub.name, age_days))
        if not stale_dirs:
            push("_none — good_")
        else:
            for name, age in stale_dirs:
                push(f"- `pr-context/{name}/` — {age} days "
                     "(consider `/strata:archive` if branch is merged)")
        push("")

    summary = (
        f"\n**Summary**: stale_adrs={len(stale)}  orphans={len(orphans)}  "
        f"missing_frontmatter={len(missing)}  unresolved_links={len(broken)}"
    )
    push(summary)

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
