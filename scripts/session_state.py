"""Snapshot of in-flight git activity. Read-only.

Window: last cooldown stamp or 2h ago, whichever's more recent.
"""
from __future__ import annotations

import contextlib
import subprocess
import time
from pathlib import Path
from typing import Any

from lib import author_name, current_branch, plugin_data_dir, project_dir

# Hard floor on the session window — even if cooldown has never fired,
# we'd rather show 2h of work than guess.
_FALLBACK_WINDOW_SECONDS = 2 * 60 * 60


def _session_window_start() -> float:
    """Estimate when the current session started — last cooldown stamp
    or 2 hours ago, whichever is more recent."""
    cooldown = plugin_data_dir() / ".stop-last"
    candidates: list[float] = [time.time() - _FALLBACK_WINDOW_SECONDS]
    if cooldown.exists():
        with contextlib.suppress(OSError, ValueError):
            candidates.append(float(cooldown.read_text().strip()))
    return max(candidates)


def _git(pd: Path, *args: str, timeout: int = 5) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(pd), *args],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _commits_since(pd: Path, since_unix: float, author: str) -> list[dict[str, str]]:
    """Commits by `author` on the current branch since `since_unix`."""
    iso = time.strftime("%Y-%m-%dT%H:%M:%S",
                        time.gmtime(since_unix)) + "Z"
    out = _git(
        pd, "log",
        f"--since={iso}",
        f"--author={author}",
        "--format=%H|%cI|%s",
        "HEAD",
    )
    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        if line.count("|") >= 2:
            sha, date, subject = line.split("|", 2)
            commits.append({"sha": sha[:10], "date": date, "subject": subject})
    return commits


def _uncommitted_files(pd: Path) -> list[dict[str, str]]:
    """Working-tree changes vs HEAD. Returns [{status, path}]."""
    out = _git(pd, "status", "--porcelain=v1", "-z")
    if not out:
        return []
    entries: list[dict[str, str]] = []
    for chunk in out.split("\x00"):
        if not chunk or len(chunk) < 3:
            continue
        status_code = chunk[:2].strip() or "?"
        path = chunk[3:]
        if path:
            entries.append({"status": status_code, "path": path})
    return entries


def _hot_paths(commits: list[dict[str, str]], pd: Path,
               limit: int = 5) -> list[str]:
    """Files touched most across the session's commits."""
    if not commits:
        return []
    counts: dict[str, int] = {}
    for c in commits:
        out = _git(pd, "show", "--name-only", "--format=", c["sha"])
        for line in out.splitlines():
            line = line.strip()
            if line:
                counts[line] = counts.get(line, 0) + 1
    return [p for p, _ in sorted(counts.items(),
                                 key=lambda kv: -kv[1])[:limit]]


def _suggested_topic(branch: str, commits: list[dict[str, str]]) -> str:
    """Pick a topic slug from the branch name (after `feat/`, `fix/`, etc.)
    or from the most recent commit subject when the branch is generic."""
    # Strip common prefixes
    name = branch
    for prefix in ("feat/", "feature/", "fix/", "bug/", "chore/",
                   "refactor/", "docs/", "release/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.replace("/", "-")
    if name and name not in ("main", "master", "develop"):
        return name
    if commits:
        return commits[0]["subject"][:40].lower().strip()
    return "session-summary"


def snapshot() -> dict[str, Any]:
    """Build the structured session snapshot. Returns empty-ish dict
    when not in a git repo or no project dir."""
    pd = project_dir()
    if pd is None:
        return {"available": False, "reason": "no project dir"}
    branch = current_branch()
    if branch == "unknown":
        return {"available": False, "reason": "no branch"}

    window_start = _session_window_start()
    author = author_name()
    commits = _commits_since(pd, window_start, author)
    uncommitted = _uncommitted_files(pd)
    hot = _hot_paths(commits, pd)
    topic = _suggested_topic(branch, commits)

    return {
        "available": True,
        "branch": branch,
        "author": author,
        "window_started_at": window_start,
        "commits": commits,
        "uncommitted": uncommitted,
        "hot_paths": hot,
        "suggested_topic": topic,
    }


def stop_nudge_text(snap: dict[str, Any]) -> str:
    """One-line summary for the Stop hook nudge. Trimmed to fit a single
    visible row in most terminals (~120 chars)."""
    if not snap.get("available"):
        return ""
    branch = snap["branch"]
    cnt_commits = len(snap.get("commits", []))
    cnt_uncommitted = len(snap.get("uncommitted", []))
    topic = snap.get("suggested_topic", "")

    parts: list[str] = []
    if cnt_commits:
        parts.append(f"{cnt_commits} commit{'s' if cnt_commits != 1 else ''}")
    if cnt_uncommitted:
        parts.append(
            f"{cnt_uncommitted} uncommitted edit"
            f"{'s' if cnt_uncommitted != 1 else ''}"
        )
    state = " + ".join(parts) if parts else "no visible activity"

    hot = snap.get("hot_paths") or []
    hot_str = ""
    if hot:
        first = hot[0]
        # Compress to top-level dir for the one-liner
        seg = first.split("/", 2)
        if len(seg) >= 2:
            hot_str = f" in {seg[0]}/{seg[1]}/"
        else:
            hot_str = f" in {first}"

    return (
        f"`{branch}`: {state}{hot_str}. "
        f"Suggested topic: `{topic}`. "
        f"Run `/strata:save --topic {topic}` to save, "
        f"or `/strata:save --draft` to review a pre-filled draft first."
    )


def draft_note_body(snap: dict[str, Any]) -> str:
    """Multi-line markdown draft used by /strata:save when invoked from the
    Stop-hook flow. Pre-fills bullets from the session state — the user
    or Claude edits before saving."""
    if not snap.get("available"):
        return "_no session data available_"

    out: list[str] = []
    out.append(f"# {snap['suggested_topic']}")
    out.append("")
    if snap.get("commits"):
        out.append("## What was done")
        for c in snap["commits"][:5]:
            out.append(f"- {c['subject']}  (`{c['sha']}`)")
        out.append("")
    if snap.get("uncommitted"):
        out.append("## In progress")
        for e in snap["uncommitted"][:8]:
            out.append(f"- {e['status']} `{e['path']}`")
        if len(snap["uncommitted"]) > 8:
            out.append(f"- _(+{len(snap['uncommitted']) - 8} more)_")
        out.append("")
    out.append("## Decided")
    out.append("- _<edit: anything you committed to that should survive>_")
    out.append("")
    out.append("## Left open")
    out.append("- _<edit: the thing the next session should pick up>_")
    out.append("")
    return "\n".join(out)
