"""Pending-draft store for the Stop-hook draft-acceptance flow.

The Stop hook stashes a pre-filled save-note draft here when enough signal
accumulates during a session (commits, uncommitted work, hot paths). The
user accepts it later via `/strata:save --apply-draft`. The vault is never
written without that explicit accept step.

State lives at `${PLUGIN_DATA}/pending-draft.json`. Drafts expire after
24h so a forgotten stash doesn't get applied days later against unrelated
work.
"""
from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from lib import plugin_data_dir

# Drafts older than this are treated as stale and silently dropped.
# Long enough to span a normal weekend; short enough that "I'll save this
# Monday" doesn't apply Friday's draft against Monday's branch.
DRAFT_TTL_SECONDS = 24 * 60 * 60


def _draft_path() -> Path:
    return plugin_data_dir() / "pending-draft.json"


def stash_draft(
    *,
    topic: str,
    branch_slug: str,
    body: str,
    kind: str = "session",
) -> Path:
    """Write a pending draft. Overwrites any previous one (newest wins —
    the user only ever sees the most recent draft offer)."""
    path = _draft_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "topic": topic,
        "branch_slug": branch_slug,
        "kind": kind,
        "body": body,
        "generated_at": time.time(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_draft() -> dict[str, Any] | None:
    """Read the pending draft, or return None if missing / unreadable /
    stale. Stale drafts are silently ignored but NOT auto-deleted, since
    a user might want to inspect the file by hand."""
    path = _draft_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    age = time.time() - float(payload.get("generated_at", 0))
    if age > DRAFT_TTL_SECONDS:
        return None
    return payload


def clear_draft() -> None:
    """Remove the pending-draft file. Called after a successful apply."""
    with contextlib.suppress(FileNotFoundError, OSError):
        _draft_path().unlink()
