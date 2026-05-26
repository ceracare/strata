"""Tests for /strata:graphify orchestration — uses a stubbed `graphify` binary."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORCH = HERE.parent / "scripts" / "graphify-orchestrate.py"


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, str(ORCH), *args],
        capture_output=True, text=True, check=False,
        env=env,
    )


def _install_graphify_stub(tmp_path, monkeypatch, exit_code: int = 0):
    """Drop a fake `graphify` binary on PATH that echoes its argv and exits."""
    stub_dir = tmp_path / "graphify-stub-bin"
    stub_dir.mkdir()
    gh = stub_dir / "graphify"
    gh.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "STUB graphify called with: $*"
        exit {exit_code}
        """))
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")


def test_orchestrate_missing_graphify_exits_2(env, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    r = _run(env=os.environ.copy())
    assert r.returncode == 2
    assert "graphify not installed" in r.stderr


def test_orchestrate_default_uses_update_subcommand(
        env, monkeypatch, tmp_path):
    """Default: `graphify update .` — AST-only, no LLM API key needed.

    Current Graphify versions made the bare `graphify .` form require
    an LLM. The `update` subcommand is the canonical no-LLM path.
    """
    _install_graphify_stub(tmp_path, monkeypatch)
    r = _run(env=os.environ.copy())
    assert r.returncode == 0
    assert "STUB graphify called with: update ." in r.stdout
    assert "--obsidian" not in r.stdout
    assert "--mode" not in r.stdout


def test_orchestrate_obsidian_does_not_pass_flag_to_graphify(
        env, monkeypatch, tmp_path):
    """--obsidian must NOT propagate to graphify (it requires an LLM key
    there). We do the obsidian export ourselves from graph.json."""
    _install_graphify_stub(tmp_path, monkeypatch)
    # Have the stub write a minimal graph.json so our local export runs.
    # The filter requires real files to exist for the nodes to pass.
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    (env["repo"] / "src" / "b.py").write_text("# b\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "A", "label": "a.py", "type": "function", "source_file": "src/a.py"},
            {"id": "B", "label": "b.py", "type": "class", "source_file": "src/b.py"},
        ],
        "edges": [{"src": "A", "dst": "B", "relation": "calls"}],
    }))

    r = _run("--obsidian", env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    # graphify CLI args should NOT include --obsidian (that's our path now)
    stub_line = [
        line for line in r.stdout.splitlines()
        if line.startswith("STUB graphify called with:")
    ]
    assert stub_line, r.stdout
    assert "--obsidian" not in stub_line[0]
    # Per-node notes should land in the vault's graphify/ dir
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    assert obsidian_dir.exists()
    md_files = list(obsidian_dir.glob("*.md"))
    assert len(md_files) == 2
    # Verify wikilink connection wrote out
    a_note = next(p for p in md_files if p.name == "a.py.md")
    assert "[[b.py]]" in a_note.read_text()
    assert "`calls`" in a_note.read_text()


def test_orchestrate_rebuild_passes_force_flag(env, monkeypatch, tmp_path):
    """--rebuild becomes --force on the update subcommand (current Graphify)."""
    _install_graphify_stub(tmp_path, monkeypatch)
    r = _run("--rebuild", env=os.environ.copy())
    assert r.returncode == 0
    assert "update . --force" in r.stdout


def test_orchestrate_deep_passes_mode_flag(env, monkeypatch, tmp_path):
    """--deep falls back to the bare `graphify .` form (LLM required)
    plus --mode deep. Does NOT use the `update` subcommand."""
    _install_graphify_stub(tmp_path, monkeypatch)
    r = _run("--deep", env=os.environ.copy())
    assert r.returncode == 0
    assert "--mode deep" in r.stdout
    # --deep uses the bare form, not `update`
    assert "update" not in r.stdout
    # We removed graphify's --obsidian path; our local export is opt-in
    assert "--obsidian" not in r.stdout
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    assert not obsidian_dir.exists()


def test_orchestrate_status_uses_code_graph(env, tmp_path):
    """--status doesn't invoke graphify — it queries graph.json directly."""
    # With no graph.json present, status returns 0 with "no graph.json" msg
    r = _run("--status", env=os.environ.copy())
    assert r.returncode == 0
    assert "no graph.json" in r.stdout or "graph.json" in r.stdout


def test_orchestrate_default_writes_obsidian_notes(env, monkeypatch, tmp_path):
    """Default (no flags) writes per-node Obsidian notes when graph.json
    exists. Fixes the previous bug where --obsidian was opt-in but the
    SKILL.md and OBSIDIAN.md both claimed it was the default."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [{"id": "A", "label": "a.py", "type": "function",
                   "source_file": "src/a.py"}],
        "edges": [],
    }))
    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    assert obsidian_dir.exists(), \
        "default invocation should write Obsidian notes (--obsidian is now default-on)"
    assert (obsidian_dir / "a.py.md").exists()


def test_orchestrate_no_obsidian_opts_out(env, monkeypatch, tmp_path):
    """--no-obsidian skips the local export even when graph.json exists."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [{"id": "A", "label": "a.py", "type": "function",
                   "source_file": "src/a.py"}],
        "edges": [],
    }))
    r = _run("--no-obsidian", env=os.environ.copy())
    assert r.returncode == 0
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    assert not obsidian_dir.exists(), \
        "--no-obsidian must skip the local export"


def test_orchestrate_excludes_dependency_nodes(env, monkeypatch, tmp_path):
    """Nodes that don't resolve to real files inside the repo must NOT
    be exported. Catches:
      • source_file pointing to node_modules/.venv/build/etc.
      • source_file that's actually a package ID (`@dnd-kit/core`) and
        doesn't exist on disk at all
      • explicit external_module type
      • no source_file
    """
    _install_graphify_stub(tmp_path, monkeypatch)
    # Real user file the filter should accept
    (env["repo"] / "src" / "services").mkdir(parents=True, exist_ok=True)
    (env["repo"] / "src" / "services" / "my_service.py").write_text("# x\n")
    # Real file under node_modules (so the path exists but the filter
    # should still reject it by the prefix rule)
    (env["repo"] / "node_modules" / "@dnd-kit" / "core" / "dist").mkdir(
        parents=True, exist_ok=True
    )
    (env["repo"] / "node_modules" / "@dnd-kit" / "core" / "dist" / "index.js").write_text("// pkg\n")

    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            # User code, file-level — should export
            {"id": "u1", "label": "my_service.py",
             "type": "class", "source_file": "src/services/my_service.py"},
            # npm dep (real file, but in node_modules) — should NOT export
            {"id": "d1", "label": "@dnd-kit/core",
             "type": "module", "source_file": "node_modules/@dnd-kit/core/dist/index.js"},
            # Python venv — should NOT export (path-prefix exclude even
            # without the file existing)
            {"id": "d2", "label": "pytest",
             "type": "module", "source_file": ".venv/lib/python3.13/site-packages/pytest/__init__.py"},
            # Build artefact — should NOT export
            {"id": "d3", "label": "bundle",
             "type": "module", "source_file": "dist/bundle.js"},
            # Explicit external_module type — should NOT export
            {"id": "d4", "label": "react", "type": "external_module",
             "source_file": "anything"},
            # No source_file at all — should NOT export
            {"id": "d5", "label": "__dirname", "type": "function"},
            # Package ID in source_file but no real file — should NOT export
            # (this is the case my earlier filter missed!)
            {"id": "d6", "label": "@radix-ui/react-dialog",
             "type": "module", "source_file": "@radix-ui/react-dialog"},
        ],
        "edges": [{"src": "u1", "dst": "d1", "relation": "imports"}],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    md_files = sorted(p.name for p in obsidian_dir.glob("*.md"))
    assert md_files == ["my_service.py.md"], (
        f"expected only my_service.py.md, got {md_files}"
    )
    body = (obsidian_dir / "my_service.py.md").read_text()
    assert "@dnd-kit" not in body, "filtered nodes must not leak via edges"


def test_orchestrate_scoped_npm_names_keep_separator_readable(
        env, monkeypatch, tmp_path):
    """When --include-symbols is set and a node with `/` in the label
    comes through (e.g. a monorepo internal package), the filename
    should preserve readability: `/` → `__`, not silent removal that
    collapses `@foo/bar` and `@foobar` to the same name."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "packages" / "core" / "src").mkdir(parents=True, exist_ok=True)
    (env["repo"] / "packages" / "core" / "src" / "index.ts").write_text("// x\n")
    (env["repo"] / "packages" / "ui" / "src").mkdir(parents=True, exist_ok=True)
    (env["repo"] / "packages" / "ui" / "src" / "index.ts").write_text("// y\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "p1", "label": "@my-org/core", "type": "module",
             "source_file": "packages/core/src/index.ts"},
            {"id": "p2", "label": "@my-org/ui", "type": "module",
             "source_file": "packages/ui/src/index.ts"},
        ],
        "edges": [{"src": "p1", "dst": "p2", "relation": "imports"}],
    }))

    r = _run("--include-symbols", env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    md_files = sorted(p.name for p in obsidian_dir.glob("*.md"))
    assert md_files == ["@my-org__core.md", "@my-org__ui.md"], (
        f"expected slash → __, got {md_files}"
    )


def test_orchestrate_clears_stale_notes_on_rerun(env, monkeypatch, tmp_path):
    """Re-running graphify after a deletion / rename must clear stale
    notes from a previous export. Without this, deleted code lingers
    forever in the Obsidian graph view."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    (env["repo"] / "src" / "b.py").write_text("# b\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json

    # First export: A + B
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "A", "label": "a.py", "type": "function", "source_file": "src/a.py"},
            {"id": "B", "label": "b.py", "type": "function", "source_file": "src/b.py"},
        ],
        "edges": [],
    }))
    _run(env=os.environ.copy())
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    assert (obsidian_dir / "a.py.md").exists()
    assert (obsidian_dir / "b.py.md").exists()

    # Second export: only A (B was deleted from code AND from disk)
    (env["repo"] / "src" / "b.py").unlink()
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "A", "label": "a.py", "type": "function", "source_file": "src/a.py"},
        ],
        "edges": [],
    }))
    _run(env=os.environ.copy())
    assert (obsidian_dir / "a.py.md").exists()
    assert not (obsidian_dir / "b.py.md").exists(), \
        "stale b.py.md from previous export must be cleaned up"


def test_orchestrate_excludes_gitignored_files(env, monkeypatch, tmp_path):
    """A file that exists on disk but isn't in git's index (e.g.
    gitignored, like a build artefact or local cache) must NOT be
    exported. This is the 'code we built' rule: presence on disk
    isn't enough; git has to know about the file."""
    _install_graphify_stub(tmp_path, monkeypatch)
    repo = env["repo"]
    (repo / ".gitignore").write_text("local-stuff/\n")
    import subprocess as sp
    sp.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "ignore"], check=True)

    # Tracked: should export
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "tracked.py").write_text("# kept\n")
    sp.run(["git", "-C", str(repo), "add", "src/tracked.py"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "add tracked"], check=True)

    # Gitignored: real file, but git doesn't know about it → should NOT export
    (repo / "local-stuff").mkdir(exist_ok=True)
    (repo / "local-stuff" / "ignored.py").write_text("# generated\n")

    graph_dir = repo / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "T", "label": "tracked.py", "type": "function",
             "source_file": "src/tracked.py"},
            {"id": "I", "label": "ignored.py", "type": "function",
             "source_file": "local-stuff/ignored.py"},
        ],
        "edges": [],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    md = sorted(p.name for p in obsidian_dir.glob("*.md"))
    assert md == ["tracked.py.md"], (
        f"expected only tracked.py.md (ignored is gitignored), got {md}"
    )


def test_orchestrate_default_drops_symbol_level_nodes(env, monkeypatch, tmp_path):
    """Default behaviour: only file-level nodes get notes. Symbol-level
    nodes (constants, hooks, JSX components, identifiers inside a file)
    are collapsed into their parent file. Pass --include-symbols to keep
    them. This is what stops Obsidian from crashing on real codebases."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "service.py").write_text("def foo(): pass\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            # File-level — keeps
            {"id": "f", "label": "service.py", "type": "module",
             "source_file": "src/service.py"},
            # Symbol-level — collapse by default
            {"id": "s1", "label": "foo", "type": "function",
             "source_file": "src/service.py"},
            {"id": "s2", "label": "MY_CONST", "type": "const",
             "source_file": "src/service.py"},
        ],
        "edges": [],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    md = sorted(p.name for p in obsidian_dir.glob("*.md"))
    assert md == ["service.py.md"], (
        f"default should keep only file-level node, got {md}"
    )


def test_orchestrate_include_symbols_keeps_everything(env, monkeypatch, tmp_path):
    """--include-symbols re-enables symbol-level nodes for users who
    want the full call-graph richness in Obsidian."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "service.py").write_text("def foo(): pass\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "f", "label": "service.py", "type": "module",
             "source_file": "src/service.py"},
            {"id": "s1", "label": "foo", "type": "function",
             "source_file": "src/service.py"},
        ],
        "edges": [],
    }))

    r = _run("--include-symbols", env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    md = sorted(p.name for p in obsidian_dir.glob("*.md"))
    assert "service.py.md" in md and "foo.md" in md, (
        f"--include-symbols should keep both, got {md}"
    )


def test_orchestrate_reads_networkx_links_key(env, monkeypatch, tmp_path):
    """Graphify writes NetworkX node-link JSON — edges live under `links`,
    not `edges`. Missing this caused every Obsidian note to have 0
    backlinks on real codebases (tens of thousands of edges silently
    ignored)."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    (env["repo"] / "src" / "b.py").write_text("# b\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": "fa", "label": "a.py", "type": "module",
             "source_file": "src/a.py"},
            {"id": "fb", "label": "b.py", "type": "module",
             "source_file": "src/b.py"},
        ],
        "links": [  # NetworkX schema
            {"source": "fa", "target": "fb", "relation": "imports"},
        ],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    a_note = (env["vault"] / "myrepo" / "graphify" / "a.py.md").read_text()
    assert "[[b.py]]" in a_note, (
        f"NetworkX `links` key must be honoured:\n{a_note}"
    )


def test_orchestrate_projects_symbol_edges_to_file_level(
        env, monkeypatch, tmp_path):
    """The whole point of Obsidian's graph view is showing connections.
    Symbol-level edges (foo() in a.py calls bar() in b.py) must project
    down to file-level wikilinks (a.py → b.py), otherwise file notes
    end up with 0 backlinks and the graph is a cloud of isolated dots."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    (env["repo"] / "src" / "b.py").write_text("# b\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            # File-level nodes — kept by default
            {"id": "fa", "label": "a.py", "type": "module",
             "source_file": "src/a.py"},
            {"id": "fb", "label": "b.py", "type": "module",
             "source_file": "src/b.py"},
            # Symbol-level nodes — collapsed away by default
            {"id": "sa_foo", "label": "foo", "type": "function",
             "source_file": "src/a.py"},
            {"id": "sb_bar", "label": "bar", "type": "function",
             "source_file": "src/b.py"},
            {"id": "sb_baz", "label": "baz", "type": "function",
             "source_file": "src/b.py"},
        ],
        "edges": [
            # Symbol → symbol across files: project to a.py ↔ b.py
            {"src": "sa_foo", "dst": "sb_bar", "relation": "calls"},
            # Another call between the SAME two files — must not produce
            # a duplicate wikilink in either note.
            {"src": "sa_foo", "dst": "sb_baz", "relation": "calls"},
        ],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    a_note = (obsidian_dir / "a.py.md").read_text()
    b_note = (obsidian_dir / "b.py.md").read_text()

    # Projected wikilink in both directions
    assert "[[b.py]]" in a_note, f"a.py should link to b.py:\n{a_note}"
    assert "[[a.py]]" in b_note, f"b.py should link to a.py:\n{b_note}"
    # And — crucially — only once each, despite two underlying edges
    assert a_note.count("[[b.py]]") == 1, a_note
    assert b_note.count("[[a.py]]") == 1, b_note


def test_orchestrate_skips_intra_file_symbol_edges(
        env, monkeypatch, tmp_path):
    """Symbol-to-symbol edges within the same file project to a self-loop
    at file-level, which is meaningless — drop them so notes don't show
    `[[a.py]]` as their own connection."""
    _install_graphify_stub(tmp_path, monkeypatch)
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "a.py").write_text("# a\n")
    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": "fa", "label": "a.py", "type": "module",
             "source_file": "src/a.py"},
            {"id": "sa_foo", "label": "foo", "type": "function",
             "source_file": "src/a.py"},
            {"id": "sa_bar", "label": "bar", "type": "function",
             "source_file": "src/a.py"},
        ],
        "edges": [
            {"src": "sa_foo", "dst": "sa_bar", "relation": "calls"},
        ],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    a_note = (env["vault"] / "myrepo" / "graphify" / "a.py.md").read_text()
    assert "[[a.py]]" not in a_note, (
        f"a.py must not link to itself:\n{a_note}"
    )
    # And no Connections section if there are no real edges
    assert "## Connections" not in a_note, a_note


def test_orchestrate_tags_files_by_role(env, monkeypatch, tmp_path):
    """Each note carries a `graphify/<role>` tag in addition to the
    generic `graphify/code`. Role drives Obsidian's graph-view colour
    grouping so services, routes, tests, components etc. are visually
    distinct instead of collapsing into a single mass."""
    _install_graphify_stub(tmp_path, monkeypatch)
    repo = env["repo"]
    # Layout that exercises the major role buckets
    layout = {
        "src/services/UserService.cs": "// svc\n",
        "src/Controllers/HomeController.cs": "// ctrl\n",
        "src/repositories/UserRepository.cs": "// repo\n",
        "src/models/UserModel.cs": "// model\n",
        "tests/UserServiceTests.cs": "// test\n",
        "web/src/routes/index.tsx": "// route\n",
        "web/src/components/Button.tsx": "// comp\n",
        "web/src/hooks/useAuth.ts": "// hook\n",
        "web/src/utils/format.ts": "// util\n",
    }
    for rel, body in layout.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    graph_dir = repo / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": f"n{i}", "label": Path(rel).name, "type": "module",
             "source_file": rel}
            for i, rel in enumerate(layout)
        ],
        "links": [],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"

    expected = {
        "UserService.cs.md": "service",
        "HomeController.cs.md": "controller",
        "UserRepository.cs.md": "repository",
        "UserModel.cs.md": "model",
        "UserServiceTests.cs.md": "test",  # test wins over service
        "index.tsx.md": "route",
        "Button.tsx.md": "component",
        "useAuth.ts.md": "hook",
        "format.ts.md": "util",
    }
    for fname, role in expected.items():
        note = (obsidian_dir / fname).read_text()
        assert f'role: "{role}"' in note, (
            f"{fname} should carry role={role}\n{note}"
        )
        assert f"graphify/{role}" in note, (
            f"{fname} missing graphify/{role} tag\n{note}"
        )


def test_orchestrate_tags_files_by_monorepo_package(
        env, monkeypatch, tmp_path):
    """Modern monorepos put each package under a category dir like
    `web/<pkg>/...`, `integrations/<pkg>/...`, `gateways/<pkg>/...`.
    The package is more useful for navigation than the file role, so
    notes get a `graphify/pkg/<name>` tag in addition to the role tag.

    Files outside a monorepo category structure (e.g. `scripts/foo.py`
    or a root-level file) get NO package tag — only the umbrella role
    classification."""
    _install_graphify_stub(tmp_path, monkeypatch)
    repo = env["repo"]
    layout = {
        "web/billing/src/routes/index.tsx": "// route\n",
        "web/storefront/src/components/Button.tsx": "// comp\n",
        "integrations/legacy-crm/src/Client/Model/Foo.cs": "// code\n",
        "gateways/checkout/tests/Tests/BarTests.cs": "// test\n",
        # No monorepo category — no package tag
        "scripts/buildtool.py": "# script\n",
    }
    for rel, body in layout.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    graph_dir = repo / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            {"id": f"n{i}", "label": Path(rel).name, "type": "module",
             "source_file": rel}
            for i, rel in enumerate(layout)
        ],
        "links": [],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"

    expected_pkg = {
        "index.tsx.md": "billing",
        "Button.tsx.md": "storefront",
        "Foo.cs.md": "legacy-crm",
        "BarTests.cs.md": "checkout",
    }
    for fname, pkg in expected_pkg.items():
        note = (obsidian_dir / fname).read_text()
        assert f'package: "{pkg}"' in note, (
            f"{fname} should carry package={pkg}\n{note}"
        )
        assert f"graphify/pkg/{pkg}" in note, (
            f"{fname} missing graphify/pkg/{pkg} tag\n{note}"
        )

    # The non-monorepo file must NOT have a package field or tag
    plain = (obsidian_dir / "buildtool.py.md").read_text()
    assert "package:" not in plain, plain
    assert "graphify/pkg/" not in plain, plain


def test_orchestrate_filters_nodes_in_non_source_files(env, monkeypatch, tmp_path):
    """Nodes whose source_file is a config / JSON / YAML must NOT be
    exported even if the file is committed to git. Catches Graphify's
    habit of extracting every devDependency entry from package.json as
    a separate node."""
    _install_graphify_stub(tmp_path, monkeypatch)
    # Committed but not source code
    (env["repo"] / "package.json").write_text('{"name":"x"}\n')
    import subprocess as sp
    sp.run(["git", "-C", str(env["repo"]), "add", "package.json"], check=True)
    sp.run(["git", "-C", str(env["repo"]), "commit", "-qm", "+pkg"], check=True)
    # Real source file alongside
    (env["repo"] / "src").mkdir(exist_ok=True)
    (env["repo"] / "src" / "app.ts").write_text("// app\n")

    graph_dir = env["repo"] / "graphify-out"
    graph_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (graph_dir / "graph.json").write_text(_json.dumps({
        "nodes": [
            # devDependency entry inside package.json → drop
            {"id": "d1", "label": "@eslint/js", "type": "module",
             "source_file": "package.json"},
            # The package.json file itself → drop (not source code)
            {"id": "p", "label": "package.json", "type": "module",
             "source_file": "package.json"},
            # Real source file → keep
            {"id": "a", "label": "app.ts", "type": "module",
             "source_file": "src/app.ts"},
        ],
        "edges": [],
    }))

    r = _run(env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    obsidian_dir = env["vault"] / "myrepo" / "graphify"
    md = sorted(p.name for p in obsidian_dir.glob("*.md"))
    assert md == ["app.ts.md"], (
        f"only the real .ts source should export, got {md}"
    )
