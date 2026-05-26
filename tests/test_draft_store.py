"""Tests for the pending-draft store used by the Stop-hook → save-note
draft-acceptance flow."""
from __future__ import annotations

import json
import time


def test_stash_then_load_roundtrip(env):
    import draft_store
    p = draft_store.stash_draft(
        topic="feat-x",
        branch_slug="feat-x",
        body="# feat-x\n\n- bullet\n",
    )
    assert p.exists()

    loaded = draft_store.load_draft()
    assert loaded is not None
    assert loaded["topic"] == "feat-x"
    assert loaded["branch_slug"] == "feat-x"
    assert loaded["kind"] == "session"  # default
    assert loaded["body"] == "# feat-x\n\n- bullet\n"
    assert loaded["generated_at"] > 0


def test_load_returns_none_when_no_draft(env):
    import draft_store
    assert draft_store.load_draft() is None


def test_stale_draft_returns_none(env, monkeypatch):
    import draft_store
    draft_store.stash_draft(topic="t", branch_slug="b", body="x")

    # Hand-edit generated_at to 25h ago
    path = draft_store._draft_path()
    payload = json.loads(path.read_text())
    payload["generated_at"] = time.time() - 25 * 60 * 60
    path.write_text(json.dumps(payload))

    assert draft_store.load_draft() is None
    # Stale drafts are NOT auto-deleted (we want to keep them inspectable)
    assert path.exists()


def test_clear_draft_removes_file(env):
    import draft_store
    draft_store.stash_draft(topic="t", branch_slug="b", body="x")
    assert draft_store._draft_path().exists()
    draft_store.clear_draft()
    assert not draft_store._draft_path().exists()
    # Idempotent: clearing a missing draft is fine
    draft_store.clear_draft()


def test_stash_overwrites_previous(env):
    import draft_store
    draft_store.stash_draft(topic="first", branch_slug="b", body="one")
    draft_store.stash_draft(topic="second", branch_slug="b", body="two")
    loaded = draft_store.load_draft()
    assert loaded["topic"] == "second"
    assert loaded["body"] == "two"


def test_stash_supports_kind_override(env):
    import draft_store
    draft_store.stash_draft(topic="t", branch_slug="b", body="x", kind="handoff")
    loaded = draft_store.load_draft()
    assert loaded["kind"] == "handoff"
