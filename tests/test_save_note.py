"""Tests for save-note.py — pr-context vs lessons scoping.

The split exists because bootstrap-extracted content is historical and
has no current branch context — it must land in `lessons/`, not
`pr-context/<current-branch>/`. An earlier bug routed bootstrap saves
to pr-context; these tests pin the fix.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAVE = HERE.parent / "scripts" / "save-note.py"


def _run(*args, body: str = "body content\n", env=None):
    return subprocess.run(
        [sys.executable, str(SAVE), *args],
        input=body, capture_output=True, text=True, check=False,
        env=env,
    )


def test_default_scope_writes_to_pr_context(initialised_vault):
    """No --scope flag → pr-context/<branch>/ (preserves existing behaviour)."""
    mem = initialised_vault
    r = _run("--topic", "auth-rewrite-session", "--kind", "session",
             env=os.environ.copy())
    assert r.returncode == 0
    pr = mem / "pr-context" / "feat-test-branch"
    matches = list(pr.glob("*--auth-rewrite-session.md"))
    assert matches, f"expected pr-context note, got nothing in {pr}"
    # No lessons file should have been written
    lessons = mem / "lessons"
    if lessons.exists():
        for f in lessons.glob("*.md"):
            assert "auth-rewrite-session" not in f.name


def test_scope_lessons_writes_to_lessons_dir(initialised_vault):
    """--scope lessons → lessons/YYYY-MM-DD-<topic>.md, no branch prefix."""
    mem = initialised_vault
    r = _run("--topic", "build-velocity-audit", "--scope", "lessons",
             "--kind", "handoff", "--source-file",
             "docs/audits/2026-04-29.md",
             env=os.environ.copy())
    assert r.returncode == 0, f"stderr: {r.stderr}"
    lessons = mem / "lessons"
    matches = list(lessons.glob("*-build-velocity-audit.md"))
    assert matches, f"expected lessons note, got nothing in {lessons}"
    body = matches[0].read_text()
    # Frontmatter: NO branch field for lessons scope
    assert "branch:" not in body.split("---")[1]
    # source_file preserved
    assert "source_file: docs/audits/2026-04-29.md" in body
    # kind preserved
    assert "kind: handoff" in body


def test_scope_lessons_filename_is_date_prefixed_only(initialised_vault):
    """Lessons filename: `YYYY-MM-DD-<topic>.md` — no time, no initials.
    Matches the existing lessons/2026-04-29-build-velocity-vs-birdie.md
    convention. PR-context uses HHMM + initials because branch work can
    have several notes per day per person; lessons should not."""
    mem = initialised_vault
    r = _run("--topic", "auth-rewrite-lessons", "--scope", "lessons",
             env=os.environ.copy())
    assert r.returncode == 0
    name = next((mem / "lessons").glob("*auth-rewrite-lessons.md")).name
    # YYYY-MM-DD prefix (10 chars + dash)
    assert name[:10].count("-") == 2
    assert name[10] == "-"
    # No HHMM segment, no `--<initials>--` segment
    assert "--" not in name


def test_scope_lessons_no_branch_in_frontmatter(initialised_vault):
    """Branch is irrelevant for a lesson — must be omitted, not set to
    whatever branch the user happens to be on at extraction time."""
    mem = initialised_vault
    r = _run("--topic", "historical-context", "--scope", "lessons",
             env=os.environ.copy())
    assert r.returncode == 0
    note = next((mem / "lessons").glob("*historical-context.md"))
    fm = note.read_text().split("---")[1]
    assert "branch:" not in fm


# ---------- --apply-draft (Stop-hook draft-acceptance flow) ----------

def test_apply_draft_writes_pr_context_note(initialised_vault):
    """A stashed draft applied via --apply-draft lands in pr-context/<branch>/
    with the draft's topic + body. The stash is cleared afterward."""
    import draft_store
    mem = initialised_vault
    draft_store.stash_draft(
        topic="feat-x-session",
        branch_slug="feat-test-branch",
        body="# feat-x-session\n\n## What was done\n- bullet\n",
    )

    r = _run("--apply-draft", body="", env=os.environ.copy())
    assert r.returncode == 0, f"stderr: {r.stderr}"

    pr = mem / "pr-context" / "feat-test-branch"
    matches = list(pr.glob("*--feat-x-session.md"))
    assert matches, f"expected note, got nothing in {pr}"
    saved = matches[0].read_text()
    assert "## What was done" in saved
    assert "- bullet" in saved
    # Stash should be cleared on successful apply
    assert draft_store.load_draft() is None


def test_apply_draft_fails_when_no_draft_stashed(initialised_vault):
    """No stash → --apply-draft exits non-zero with a clear message."""
    r = _run("--apply-draft", body="", env=os.environ.copy())
    assert r.returncode != 0
    assert "no pending draft" in r.stderr


def test_apply_draft_ignores_stale_drafts(initialised_vault):
    """A draft older than 24h is treated as no-draft."""
    import draft_store, json, time
    draft_store.stash_draft(topic="t", branch_slug="b", body="x")
    # Backdate to 25h ago
    path = draft_store._draft_path()
    payload = json.loads(path.read_text())
    payload["generated_at"] = time.time() - 25 * 60 * 60
    path.write_text(json.dumps(payload))

    r = _run("--apply-draft", body="", env=os.environ.copy())
    assert r.returncode != 0
    assert "no pending draft" in r.stderr


def test_topic_required_without_apply_draft(initialised_vault):
    """Without --topic and without --apply-draft, save-note refuses."""
    r = _run("--kind", "session", body="some body", env=os.environ.copy())
    assert r.returncode != 0
    assert "--topic is required" in r.stderr or "topic" in r.stderr.lower()
