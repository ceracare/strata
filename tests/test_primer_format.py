"""Tests for primer_format.py — SessionStart primer additions.

Covers the three Strata-style additions over the older primer:
- legend line (icons for each note kind)
- token economy summary (skim vs full)
- by-source-file inverse index
"""
from __future__ import annotations

import textwrap
from pathlib import Path


def _write_note(
    path: Path,
    kind: str = "session",
    topic: str | None = None,
    source_file: str | list[str] | None = None,
    body: str = "## Body\n\nSome content here.\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"kind: {kind}"]
    if topic:
        lines.append(f"topic: {topic}")
    if isinstance(source_file, list):
        lines.append("source_file:")
        for item in source_file:
            lines.append(f"  - {item}")
    elif isinstance(source_file, str):
        lines.append(f"source_file: {source_file}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines))


def test_kind_icon_falls_back_for_unknown(initialised_vault):
    import primer_format
    assert primer_format.kind_icon("session") == "🎯"
    assert primer_format.kind_icon("decision") == "⚖️"
    assert primer_format.kind_icon("DOMAIN") == "📚"  # case-insensitive
    assert primer_format.kind_icon(None) == "📄"
    assert primer_format.kind_icon("invented-kind") == "📄"


def test_legend_line_is_single_line_and_includes_core_kinds(
        initialised_vault):
    import primer_format
    line = primer_format.legend_line()
    assert "\n" not in line
    for token in ("session", "decision", "domain", "procedural", "lesson"):
        assert token in line


def test_compute_economy_zero_when_vault_empty(initialised_vault):
    """init-memory bootstraps empty scope dirs — no notes yet."""
    import primer_format
    econ = primer_format.compute_economy(initialised_vault)
    assert econ["notes"] == 0
    assert econ["savings_pct"] == 0


def test_compute_economy_reports_savings(initialised_vault):
    """A note with >500 bytes of body should produce savings_pct > 0
    because skim = first 500 bytes only."""
    import primer_format
    mem = initialised_vault
    body = "## Body\n\n" + ("padding line\n" * 200)  # well over 500 bytes
    _write_note(
        mem / "decisions" / "2026-05-26-big-note.md",
        kind="decision",
        topic="big-note",
        body=body,
    )
    econ = primer_format.compute_economy(mem)
    assert econ["notes"] == 1
    assert econ["full_tokens"] > econ["skim_tokens"]
    assert econ["savings_pct"] > 0


def test_format_economy_returns_empty_when_no_notes(initialised_vault):
    import primer_format
    assert primer_format.format_economy({"notes": 0}) == ""


def test_format_economy_includes_savings_percentage(initialised_vault):
    import primer_format
    line = primer_format.format_economy({
        "notes": 12, "skim_tokens": 1000,
        "full_tokens": 10000, "savings_pct": 90,
    })
    assert "12 notes" in line
    assert "1,000" in line
    assert "10,000" in line
    assert "90% savings" in line


def test_index_by_source_file_scalar_value(initialised_vault):
    """source_file as a single string puts the note under one file."""
    import primer_format
    mem = initialised_vault
    _write_note(
        mem / "lessons" / "2026-05-26-foo.md",
        kind="handoff", topic="foo",
        source_file="src/foo.py",
    )
    idx = primer_format.index_by_source_file(mem)
    assert len(idx) == 1
    file_path, notes = idx[0]
    assert file_path == "src/foo.py"
    assert len(notes) == 1


def test_index_by_source_file_list_value(initialised_vault):
    """source_file as a YAML list registers the note against each entry."""
    import primer_format
    mem = initialised_vault
    _write_note(
        mem / "lessons" / "2026-05-26-multi.md",
        kind="handoff", topic="multi",
        source_file=["src/a.py", "src/b.py"],
    )
    idx = primer_format.index_by_source_file(mem)
    files = {f for f, _ in idx}
    assert files == {"src/a.py", "src/b.py"}


def test_index_by_source_file_ranks_by_note_count(initialised_vault):
    """A file referenced by more notes ranks higher than a singleton."""
    import primer_format
    mem = initialised_vault
    for i in range(3):
        _write_note(
            mem / "pr-context" / "feat-test-branch" / f"n{i}.md",
            kind="session", topic=f"n{i}",
            source_file="src/hot.py",
        )
    _write_note(
        mem / "pr-context" / "feat-test-branch" / "cold.md",
        kind="session", topic="cold",
        source_file="src/cold.py",
    )
    idx = primer_format.index_by_source_file(mem)
    assert idx[0][0] == "src/hot.py"
    assert len(idx[0][1]) == 3


def test_index_by_source_file_skips_notes_without_source_file(
        initialised_vault):
    """A note with no source_file frontmatter is invisible to the
    inverse index — that's correct: it has no file to be indexed under."""
    import primer_format
    mem = initialised_vault
    _write_note(
        mem / "decisions" / "2026-05-26-no-source.md",
        kind="decision", topic="no-source",
    )
    assert primer_format.index_by_source_file(mem) == []


def test_format_files_section_emits_kind_icons(initialised_vault):
    """Each note ref under a file gets its kind's icon."""
    import primer_format
    mem = initialised_vault
    _write_note(
        mem / "decisions" / "2026-05-26-d.md",
        kind="decision", topic="d", source_file="src/x.py",
    )
    _write_note(
        mem / "pr-context" / "feat-test-branch" / "s.md",
        kind="session", topic="s", source_file="src/x.py",
    )
    idx = primer_format.index_by_source_file(mem)
    text = primer_format.format_files_section(mem, idx)
    assert "### Files with recent context" in text
    assert "src/x.py" in text
    assert "⚖️" in text  # decision icon
    assert "🎯" in text  # session icon


def test_format_files_section_scope_fallback_for_missing_kind(
        initialised_vault):
    """A note without a `kind:` field should still render with a
    sensible icon — derive it from the scope dir the note lives in
    (decisions/ → ⚖️, domain/ → 📚, etc.)."""
    import primer_format
    mem = initialised_vault
    # Domain note with NO `kind:` frontmatter — only source_file
    note = mem / "domain" / "some-concept.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("---\nsource_file: src/x.py\ntopic: thing\n---\n\nbody\n")
    idx = primer_format.index_by_source_file(mem)
    text = primer_format.format_files_section(mem, idx)
    assert "📚" in text, f"domain scope should give 📚 icon:\n{text}"


def test_format_files_section_empty_index(initialised_vault):
    import primer_format
    assert primer_format.format_files_section(initialised_vault, []) == ""


def test_corrupt_frontmatter_never_crashes_indexer(initialised_vault):
    """A malformed note (bad YAML in frontmatter, missing closing ---)
    must be silently skipped, not raise."""
    import primer_format
    mem = initialised_vault
    bad = mem / "lessons" / "2026-05-26-bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(textwrap.dedent("""\
        ---
        kind: : bad yaml :
        source_file: [unterminated
        ---
        body
    """))
    # Should not raise
    primer_format.index_by_source_file(mem)
    primer_format.compute_economy(mem)
