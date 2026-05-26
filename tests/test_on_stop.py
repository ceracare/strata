"""Tests for the Stop hook — filtering, cooldown, JSON output shape."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ON_STOP = HERE.parent / "scripts" / "on-stop.py"


def _run(env=None):
    return subprocess.run(
        [sys.executable, str(ON_STOP)],
        input="{}",
        capture_output=True, text=True, check=False,
        env=env,
    )


def test_skips_on_trunk_branch(env):
    """No nudge when on main."""
    import subprocess as sp
    sp.run(["git", "-C", str(env["repo"]), "checkout", "-q", "-b", "main"],
           check=True)
    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    assert r.stdout == ""  # silent


def test_no_nudge_when_vault_missing(env):
    """No nudge when vault isn't initialised."""
    # env fixture creates feat/test-branch, no vault
    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    assert r.stdout == ""


def test_nudges_on_feature_branch_with_empty_vault(initialised_vault):
    """Vault initialised, on feature branch, no save yet → should nudge."""
    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    assert r.stdout, "expected a JSON nudge payload"
    payload = json.loads(r.stdout)
    # Stop hooks use top-level `systemMessage` (per Claude Code schema).
    # The previous hookSpecificOutput.additionalContext shape was wrong
    # for Stop and Claude Code rejected it.
    assert "hookSpecificOutput" not in payload
    msg = payload["systemMessage"]
    assert "strata:save" in msg
    assert "feat/test-branch" in msg


def test_cooldown_silences_second_call(initialised_vault):
    """A second call within 30 minutes should be silent."""
    r1 = _run(env=os.environ.copy())
    assert r1.stdout, "first call should nudge"
    r2 = _run(env=os.environ.copy())
    assert r2.returncode == 0
    assert r2.stdout == "", "second call within cooldown should be silent"


def test_no_nudge_when_recent_save_exists(initialised_vault):
    """If pr-context for the branch has a fresh note, no nudge."""
    mem = initialised_vault
    # Plant a note timestamped "now" in pr-context/feat-test-branch/
    branch_dir = mem / "pr-context" / "feat-test-branch"
    branch_dir.mkdir(parents=True, exist_ok=True)
    note = branch_dir / "2026-05-22-1000--user--recent-save.md"
    note.write_text("recent\n")
    os.utime(note, (time.time(), time.time()))  # fresh mtime

    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    assert r.stdout == "", "should be silent when a recent save exists"


def test_nudges_when_save_is_old(initialised_vault):
    """An old save shouldn't suppress the nudge."""
    mem = initialised_vault
    branch_dir = mem / "pr-context" / "feat-test-branch"
    branch_dir.mkdir(parents=True, exist_ok=True)
    note = branch_dir / "2026-05-20-0900--user--old-save.md"
    note.write_text("old\n")
    # 90 minutes old — well past the 30-minute window
    old_time = time.time() - 90 * 60
    os.utime(note, (old_time, old_time))

    r = _run(env=os.environ.copy())
    assert r.stdout, "should nudge despite old save"


# ---------- Stop-hook draft-stashing (signal-threshold path) ----------

def _git_in(repo, *args):
    import subprocess as sp
    sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_stop_hook_stashes_draft_when_threshold_met(initialised_vault, env):
    """3+ commits on a feature branch → draft stashed in plugin-data and
    Stop output mentions /strata:save --apply-draft."""
    import draft_store
    repo = env["repo"]
    # Make 3 commits
    for i in range(3):
        (repo / f"f{i}.py").write_text(f"# {i}\n")
        _git_in(repo, "add", f"f{i}.py")
        _git_in(repo, "commit", "-qm", f"feat: change {i}")

    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    assert r.stdout, "expected systemMessage payload"

    payload = json.loads(r.stdout)
    msg = payload["systemMessage"]
    assert "--apply-draft" in msg, f"missing apply-draft hint: {msg!r}"

    draft = draft_store.load_draft()
    assert draft is not None
    assert "What was done" in draft["body"]
    # Topic should reflect the branch
    assert "test-branch" in draft["topic"] or draft["topic"] == "session-summary"


def test_stop_hook_no_draft_below_threshold(initialised_vault, env):
    """0 commits + 0 uncommitted → still nudge, but NO draft stashed."""
    import draft_store
    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    # Nudge fires (vault initialised, feature branch) but no draft
    assert r.stdout
    payload = json.loads(r.stdout)
    assert "--apply-draft" not in payload["systemMessage"]
    assert draft_store.load_draft() is None
