"""SessionStart primer formatting — legend, economy, by-file index.

Strata-flavoured adaptation of the structured-context-summary pattern.
We stay markdown-native: IDs are note paths (not numeric), types come
from each note's `kind:` frontmatter, and file grouping is built from
the `source_file:` field (which may be a string or a list).

The three additions over the previous primer:
- a one-line `Legend` so readers can decode the icons cold,
- a one-line `Context economy` so the value of trusting the primer
  is visible (skim vs full-read cost),
- a `Files with recent context` section that inverts notes → source
  file so users landing in `Foo.cs` immediately see which notes
  touched it.

Everything is best-effort: corrupt frontmatter, missing source files,
unreadable notes — all silently skipped. The primer must never crash
a SessionStart hook.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import frontmatter

# Icon set covers Strata's actual frontmatter `kind:` values plus the
# session-flavour tags that show up in note titles. Anything unknown
# gets the generic 📄 — better to render an unknown note than to
# silently drop it.
KIND_ICONS: dict[str, str] = {
    "session":     "🎯",
    "decision":    "⚖️",
    "adr":         "⚖️",
    "domain":      "📚",
    "procedural":  "📝",
    "handoff":     "🎓",
    "lesson":      "🎓",
    "proposition": "🌱",
    "bugfix":      "🔴",
    "feature":     "🟣",
    "refactor":    "🔄",
    "change":      "✅",
    "discovery":   "🔵",
}


def kind_icon(kind: str | None) -> str:
    if not kind:
        return "📄"
    return KIND_ICONS.get(str(kind).strip().lower(), "📄")


def legend_line() -> str:
    return (
        "Legend: "
        "🎯 session  ⚖️ decision  📚 domain  "
        "📝 procedural  🎓 lesson  🌱 proposition"
    )


# ---------- internal scanning helpers ---------------------------------


_SCOPE_DIRS: tuple[str, ...] = (
    "decisions", "domain", "pr-context",
    "lessons", "procedural", "propositions",
)

# Used to derive a kind icon when a note's frontmatter doesn't declare
# `kind:` — the scope dir is the next best signal.
_SCOPE_TO_KIND: dict[str, str] = {
    "decisions":    "decision",
    "domain":       "domain",
    "pr-context":   "session",
    "lessons":      "lesson",
    "procedural":   "procedural",
    "propositions": "proposition",
}


def _scope_kind(note_path: Path, mem_dir: Path) -> str:
    """Best-guess kind from scope dir when frontmatter doesn't declare one."""
    try:
        first = note_path.relative_to(mem_dir).parts[0]
    except (ValueError, IndexError):
        return ""
    return _SCOPE_TO_KIND.get(first, "")


def _is_note(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".md":
        return False
    return path.name not in ("README.md", "INDEX.md")


def _iter_notes(mem_dir: Path) -> Iterable[Path]:
    if not mem_dir.exists():
        return
    for scope in _SCOPE_DIRS:
        d = mem_dir / scope
        if not d.exists():
            continue
        for path in d.rglob("*.md"):
            if _is_note(path):
                yield path


def _approx_tokens(n_bytes: int) -> int:
    """Rough chars-to-tokens. 4 bytes/token is the standard English
    heuristic; markdown is slightly denser but the variance is
    rounding-error vs. the primer's other estimates."""
    return max(1, n_bytes // 4)


def _safe_metadata(path: Path) -> dict:
    """Parse a note's frontmatter, returning {} on any failure.

    Broad except is deliberate: PyYAML raises ScannerError /
    ParserError on malformed input (neither is OSError or ValueError),
    and the primer must never crash a SessionStart hook because one
    note happened to be malformed.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return dict(frontmatter.load(fh).metadata)
    except Exception:
        return {}


# ---------- public formatters -----------------------------------------


def compute_economy(mem_dir: Path) -> dict:
    """Return primer-level token economics.

    Skim cost ≈ what Claude reads when the primer summarises a note
    (frontmatter + heading + a sentence or two — ~500 bytes). Full
    cost is the entire file. Savings = body content Claude can avoid
    pulling in when the primer + a targeted Read suffice.
    """
    notes = list(_iter_notes(mem_dir))
    if not notes:
        return {
            "notes": 0,
            "skim_tokens": 0,
            "full_tokens": 0,
            "savings_pct": 0,
        }

    skim_bytes = 0
    full_bytes = 0
    for p in notes:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        full_bytes += size
        skim_bytes += min(size, 500)

    skim = _approx_tokens(skim_bytes)
    full = _approx_tokens(full_bytes)
    savings = round(100 * (1 - skim / full)) if full else 0
    return {
        "notes": len(notes),
        "skim_tokens": skim,
        "full_tokens": full,
        "savings_pct": savings,
    }


def format_economy(econ: dict) -> str:
    if not econ.get("notes"):
        return ""
    return (
        f"Context economy: {econ['notes']} notes • "
        f"skim {econ['skim_tokens']:,}t • "
        f"full read {econ['full_tokens']:,}t • "
        f"{econ['savings_pct']}% savings"
    )


def index_by_source_file(
    mem_dir: Path,
    limit: int = 6,
) -> list[tuple[str, list[Path]]]:
    """Inverse index: source-code file → notes that reference it.

    Reads each note's `source_file:` frontmatter (which can be a single
    string or a YAML list of strings). Returns the top `limit` files,
    ranked by note count then by most-recent note mtime.

    Limit is intentionally small (6 by default) — the primer is meant
    to surface, not enumerate. Users follow up with `memory_search`
    or `read_memory_note` for the long tail.
    """
    index: dict[str, list[Path]] = {}
    for p in _iter_notes(mem_dir):
        meta = _safe_metadata(p)
        sf = meta.get("source_file")
        if not sf:
            continue
        items = sf if isinstance(sf, list) else [sf]
        for item in items:
            if isinstance(item, str) and item.strip():
                index.setdefault(item.strip(), []).append(p)

    def _rank(entry: tuple[str, list[Path]]) -> tuple[int, float]:
        _, notes = entry
        try:
            latest = max(
                (n.stat().st_mtime for n in notes if n.exists()),
                default=0.0,
            )
        except OSError:
            latest = 0.0
        return (len(notes), latest)

    return sorted(index.items(), key=_rank, reverse=True)[:limit]


def format_files_section(
    mem_dir: Path,
    file_index: list[tuple[str, list[Path]]],
    notes_per_file: int = 3,
) -> str:
    """Render the by-file section. Each source file gets a bullet with
    its most-recent note refs nested below (kind icon + relative path
    + title).
    """
    if not file_index:
        return ""

    lines: list[str] = ["### Files with recent context", ""]
    for src_file, notes in file_index:
        lines.append(f"- `{src_file}`")
        recent = sorted(
            notes,
            key=lambda n: (n.stat().st_mtime if n.exists() else 0.0),
            reverse=True,
        )[:notes_per_file]
        for n in recent:
            meta = _safe_metadata(n)
            kind = meta.get("kind") or _scope_kind(n, mem_dir)
            title = (
                meta.get("topic")
                or meta.get("title")
                or n.stem
            )
            try:
                rel = n.relative_to(mem_dir).as_posix()
            except ValueError:
                rel = n.name
            lines.append(
                f"  - {kind_icon(str(kind))} "
                f"`{rel}` — {title}"
            )
    return "\n".join(lines)
