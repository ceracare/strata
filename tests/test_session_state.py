"""Tests for session_state — gathers git activity for the rich nudge."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

# Force the import path off scripts/ so we can `import session_state`
sys.path.insert(0, str(HERE.parent / "scripts"))


def _git(repo: Path, *args, **kw):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, **kw)


def _reload_session_state():
    for mod in ("session_state", "lib"):
        if mod in sys.modules:
            del sys.modules[mod]
    import session_state
    return session_state


@pytest.fixture
def session_state(env):
    """Reload module so env var monkeypatching is picked up."""
    return _reload_session_state()


def test_snapshot_unavailable_outside_repo(monkeypatch, tmp_path, session_state):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # Reload so project_dir() re-reads env
    _reload_session_state()
    import session_state as ss
    snap = ss.snapshot()
    assert snap["available"] is False


def test_snapshot_collects_commits(env, session_state):
    pd = env["repo"]
    (pd / "src.py").write_text("v1\n")
    _git(pd, "add", "src.py")
    _git(pd, "commit", "-qm", "feat: add src")
    (pd / "src.py").write_text("v2\n")
    _git(pd, "add", "src.py")
    _git(pd, "commit", "-qm", "fix: bump src")

    snap = session_state.snapshot()
    assert snap["available"] is True
    assert snap["branch"] == "feat/test-branch"
    # 3 commits total in the session window: init from conftest + 2 we
    # just made. The window is 2h fallback, so all should be present.
    subjects = [c["subject"] for c in snap["commits"]]
    assert "feat: add src" in subjects
    assert "fix: bump src" in subjects


def test_snapshot_uncommitted_picks_up_changes(env, session_state):
    pd = env["repo"]
    (pd / "WIP.md").write_text("uncommitted\n")
    snap = session_state.snapshot()
    uncommitted_paths = {e["path"] for e in snap["uncommitted"]}
    assert "WIP.md" in uncommitted_paths


def test_stop_nudge_text_is_one_liner(env, session_state):
    pd = env["repo"]
    (pd / "x.py").write_text("\n")
    _git(pd, "add", "x.py")
    _git(pd, "commit", "-qm", "feat: x")

    snap = session_state.snapshot()
    text = session_state.stop_nudge_text(snap)
    assert "feat-test-branch" in text or "test-branch" in text
    assert "/strata:save" in text
    # Single line — keep it terminal-friendly
    assert "\n" not in text


def test_stop_nudge_text_handles_unavailable(session_state):
    text = session_state.stop_nudge_text({"available": False})
    assert text == ""


def test_draft_note_body_has_required_sections(env, session_state):
    pd = env["repo"]
    (pd / "feat.py").write_text("\n")
    _git(pd, "add", "feat.py")
    _git(pd, "commit", "-qm", "feat: new thing")

    snap = session_state.snapshot()
    body = session_state.draft_note_body(snap)
    # All four section headings present
    for heading in ("# ", "## What was done", "## Decided", "## Left open"):
        assert heading in body
    # Commit subject appears in "What was done"
    assert "new thing" in body


def test_suggested_topic_derives_from_branch(env, session_state):
    snap = session_state.snapshot()
    # Branch is "feat/test-branch" → stripped to "test-branch"
    assert snap["suggested_topic"] == "test-branch"
