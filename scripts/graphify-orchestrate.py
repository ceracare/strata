#!/usr/bin/env python3
"""Shell-out wrapper around `graphify`. Default is AST-only (local, no LLM).
`--obsidian` runs our local writer over graph.json — replicates Graphify's
`--obsidian` flag without its LLM API-key requirement."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import lib_loader  # noqa: F401
from lib import info, is_git_repo, project_dir, repo_name, vault_root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="Pass --force to `graphify update` — overwrites "
                         "graph.json even if the rebuild has fewer nodes. "
                         "Useful after refactors that deleted code.")
    ap.add_argument("--status", action="store_true",
                    help="Print code-graph status and exit.")
    ap.add_argument("--obsidian", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="After graphify produces graph.json, write our own "
                         "per-node markdown into <vault>/<repo>/graphify/ "
                         "so code nodes appear in Obsidian's graph view "
                         "alongside Strata's decisions and domain notes. "
                         "Pure-mechanical, no LLM, no API key, no network. "
                         "Replicates Graphify's --obsidian without its LLM "
                         "requirement. Default: ON. Pass --no-obsidian to "
                         "skip the export.")
    ap.add_argument("--deep", action="store_true",
                    help="Pass --mode deep to graphify (semantic edges via "
                         "LLM). Costs tokens AND sends file content to an "
                         "external LLM. Do NOT use for regulated content.")
    ap.add_argument("--include-symbols", action="store_true",
                    help="Include symbol-level nodes (every const, hook, "
                         "function, class, JSX component) in the Obsidian "
                         "export. Default exports only file-level nodes — "
                         "one .md per source file — which keeps Obsidian's "
                         "graph view browsable. Use --include-symbols when "
                         "you want full call-graph richness and accept the "
                         "performance cost.")
    args = ap.parse_args()

    if args.status:
        import code_graph
        s = code_graph.summary()
        if s is None:
            print("[strata] no graph.json — run /strata:graphify to build")
            return 0
        if not s.get("available"):
            print(f"[strata] graph.json present but unreadable: "
                  f"{s.get('error', '?')}")
            return 1
        print(f"[strata] graph.json: {s['nodes']} nodes, {s['edges']} edges, "
              f"{s['age_hours']}h old")
        try:
            age = code_graph.graph_age_relative_to_head()
            if age:
                marker = " 🔴 STALE" if age["stale"] else ""
                print(f"[strata] vs HEAD: {age['commits_since']} commits since "
                      f"build{marker}")
        except Exception:
            pass
        return 0

    if shutil.which("graphify") is None:
        print("[strata] graphify not installed.",
              file=sys.stderr)
        print("[strata] install: pip install graphifyy && graphify install",
              file=sys.stderr)
        return 2

    if not is_git_repo():
        print("[strata] not in a git repo", file=sys.stderr)
        return 2

    pd = project_dir()
    if pd is None:
        print("[strata] no project dir", file=sys.stderr)
        return 2

    # `graphify update .` is the AST-only path in current Graphify versions
    # — their help text literally says "(no LLM needed)". The bare
    # `graphify .` form now always requires an LLM API key. We use update
    # by default and only opt into the full LLM path when --deep is set.
    if args.deep:
        cmd: list[str] = ["graphify", "."]
        cmd.extend(["--mode", "deep"])
    else:
        cmd = ["graphify", "update", "."]
        if args.rebuild:
            # Force a full re-extract even when the cache thinks nothing
            # changed. Useful after refactors that deleted code.
            cmd.append("--force")

    info(f"$ {' '.join(cmd)}  (cwd: {pd})")
    r = subprocess.run(cmd, cwd=str(pd))
    if r.returncode != 0:
        return r.returncode

    # Local Obsidian export (no LLM, no network) from the freshly-produced
    # graph.json. This replaces Graphify's own --obsidian flag.
    if args.obsidian:
        graph_json = pd / "graphify-out" / "graph.json"
        if not graph_json.exists():
            info("warning: graph.json not found after graphify run; "
                 "skipping obsidian export")
            return 0
        obsidian_dir = vault_root() / repo_name() / "graphify"
        count = _write_obsidian_notes(
            graph_json, obsidian_dir, pd,
            include_symbols=args.include_symbols,
        )
        info(f"wrote {count} per-node Obsidian notes to {obsidian_dir}")

    return 0


# ---------------------------------------------------------------------------
# Local Obsidian export — pure-mechanical, no LLM, no network
#
# Replicates the structure of Graphify's `to_obsidian()` (in their
# graphify/export.py) but driven from graph.json instead of their in-memory
# graph. Defensive parsing — works across schema variations.
# ---------------------------------------------------------------------------


def _safe_filename(label: str) -> str:
    """Filesystem-safe + Obsidian-safe name. Replaces path separators with
    `__` (so scoped npm names like `@dnd-kit/core` stay readable as
    `@dnd-kit__core` instead of collapsing to `@dnd-kitcore`). Strips
    chars Obsidian disallows in wikilinks (`#^[]`) and the OS disallows
    on Windows (`*?:"<>|`)."""
    import re
    label = str(label).strip()
    # First: path separators become double-underscore for readability
    label = label.replace("/", "__").replace("\\", "__")
    # Then strip the remaining forbidden chars
    label = re.sub(r'[*?:"<>|#^\[\]]', "", label).strip()
    return label or "unnamed"


# Dependency / build / cache paths that should NEVER export as Obsidian
# notes. They blow up the graph view with thousands of useless nodes and
# crash Obsidian. The match is "starts with this segment in the path."
_EXCLUDED_PATH_PREFIXES = (
    "node_modules/", ".venv/", "venv/", "env/",
    "dist/", "build/", "out/", "target/",
    ".next/", ".nuxt/", ".cache/", ".turbo/",
    "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    "vendor/", "bower_components/", ".pnp/",
    "site-packages/", "Pods/", ".gradle/",
    ".astro/", ".svelte-kit/",
)

# Node-type values that mean "external dependency / language built-in",
# not user code. Defensive: Graphify schema varies across versions.
_EXTERNAL_NODE_TYPES = {
    "external", "external_module", "external_package",
    "dependency", "package", "npm_package", "pip_package",
    "builtin", "stdlib",
}

# File extensions we count as "source code". Graphify can emit nodes for
# anything it parses, including identifier-level nodes inside package.json
# (every devDependency entry, every JSON key). We only want notes for
# nodes whose source file is actual source code. Config / lockfile / docs
# extensions are deliberately excluded.
_SOURCE_CODE_EXTENSIONS = {
    # Python
    ".py", ".pyi", ".pyx",
    # TypeScript / JavaScript
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
    # Web frameworks with single-file components
    ".vue", ".svelte", ".astro",
    # Systems / native
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".m", ".mm", ".swift",
    # JVM
    ".java", ".kt", ".kts", ".scala", ".groovy", ".clj", ".cljs", ".cljc",
    # Go / Rust / Zig
    ".go", ".rs", ".zig",
    # Ruby / PHP / Perl
    ".rb", ".php", ".pl", ".pm",
    # Functional
    ".ex", ".exs", ".erl", ".hrl", ".elm", ".hs", ".lhs", ".ml", ".mli",
    ".fs", ".fsx", ".fsi",
    # .NET
    ".cs", ".vb",
    # Shell / build (count as code when they're scripts a team owns)
    ".sh", ".bash", ".zsh", ".fish",
    # Other languages we want to acknowledge
    ".dart", ".lua", ".r", ".jl", ".nim", ".cr", ".d",
}


def _tracked_files(pd: Path) -> set[str] | None:
    """Set of repo-relative paths git knows about (tracked + untracked
    not-gitignored, so brand-new WIP files still count). Returns None
    if pd isn't a git repo or git is unavailable, in which case the
    caller falls back to existence-only checks."""
    try:
        r = subprocess.run(
            ["git", "-C", str(pd), "ls-files",
             "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


def _is_user_code_node(n: dict, pd: Path,
                       tracked: set[str] | None) -> bool:
    """True if a graph node is code we built. Strongest possible filter:
    the node's `source_file` must resolve to a real path inside `pd`,
    must not live in a dependency / build / cache directory, AND must
    be a file that git knows about (tracked or untracked-not-ignored).

    If `tracked` is None (not a git repo / git missing), we fall back
    to existence-on-disk only.
    """
    if not isinstance(n, dict):
        return False
    ntype = (n.get("type") or n.get("node_type")
             or n.get("file_type") or "").lower()
    if ntype in _EXTERNAL_NODE_TYPES:
        return False
    src = n.get("source_file") or n.get("file") or n.get("path") or ""
    if not isinstance(src, str) or not src.strip():
        return False

    candidate = Path(src) if Path(src).is_absolute() else (pd / src)
    try:
        resolved = candidate.resolve()
        pd_resolved = pd.resolve()
        rel = resolved.relative_to(pd_resolved).as_posix()
    except (ValueError, OSError):
        return False
    if any(rel.startswith(prefix) or f"/{prefix}" in rel
           for prefix in _EXCLUDED_PATH_PREFIXES):
        return False
    if not resolved.is_file():
        return False
    # Source-code extension only. Graphify extracts identifier-level
    # nodes from inside package.json / tsconfig.json / yaml configs
    # (every devDependency entry becomes a node, every __dirname
    # identifier, every JSON key). We only want notes for nodes whose
    # source file is actual source code.
    if resolved.suffix.lower() not in _SOURCE_CODE_EXTENSIONS:
        return False
    # Strict: must be a path git knows about. This is what "code we
    # built" means — anything else (build output, vendored copies,
    # generated stubs not in source control) is excluded.
    return tracked is None or rel in tracked


def _is_file_level_node(n: dict) -> bool:
    """True if the node represents a source file itself (label matches
    the file's basename), not an identifier inside that file. Graphify
    creates both — file-level nodes are the manageable ones for an
    Obsidian graph view (~hundreds), symbol-level adds tens of
    thousands of consts / hooks / components / JSX elements."""
    if not isinstance(n, dict):
        return False
    label = str(n.get("label") or n.get("name") or "").strip()
    src = n.get("source_file") or n.get("file") or n.get("path") or ""
    if not label or not isinstance(src, str) or not src.strip():
        return False
    basename = Path(src).name
    # Match either the full basename ('__root.tsx') or its stem ('__root')
    return label == basename or label == basename.rsplit(".", 1)[0]


# Top-level directories that conventionally contain one package per
# subdirectory in modern monorepo layouts (pnpm workspaces, Turborepo,
# Nx, .NET solutions, microservice repos). Anything matching
# `<category>/<pkg>/...` is treated as belonging to `<pkg>`. The list
# is intentionally conservative to avoid false positives on
# non-monorepo repos that happen to use the same dir names.
_MONOREPO_CATEGORY_DIRS: frozenset[str] = frozenset({
    "apps", "packages", "services", "web", "libs",
    "gateways", "integrations", "clients", "servers",
})


def _classify_package(source_file: str) -> str | None:
    """Best-effort monorepo package detection from path structure.

    Returns the package name if the file lives under
    `<category>/<pkg>/<anything>...` where `<category>` is one of the
    common monorepo top-level dirs. Otherwise returns None.

    We require at least 3 path segments so `web/index.html` (where the
    second segment is a file, not a package dir) doesn't get tagged.
    """
    if not source_file:
        return None
    parts = source_file.replace("\\", "/").strip("/").split("/")
    if len(parts) >= 3 and parts[0] in _MONOREPO_CATEGORY_DIRS:
        return parts[1]
    return None


def _classify_file_role(source_file: str) -> str:
    """Best-effort role classification from path + filename conventions.

    Returns one of: test, route, component, hook, service, controller,
    repository, model, util, code (fallback). The label drives a
    `graphify/<role>` tag so Obsidian's graph view can colour by role.

    Order matters: tests check first (a `UserServiceTests.cs` is a test,
    not a service), then specific naming conventions, then path-based
    fallbacks. Heuristics target the C# + TS/JS conventions we see in
    practice; languages with weaker naming conventions fall back to
    `code` (still useful — it just doesn't get its own colour).
    """
    if not source_file:
        return "code"
    src = source_file.replace("\\", "/")
    src_lc = src.lower()
    name = Path(src).name

    # Tests — anything inside a test dir or named with a test suffix.
    if any(seg in src_lc for seg in
           ("/tests/", "/test/", "/__tests__/", "/spec/", "/specs/")):
        return "test"
    if any(name.endswith(s) for s in (
            "Tests.cs", "Test.cs", "Spec.cs",
            ".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".test.mjs",
            ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
            "_test.py", "_test.go", "_spec.rb", "Test.java", "Spec.scala",
            ".test.swift")):
        return "test"

    # File-system routing (TanStack Router, Next.js, Astro, SvelteKit).
    if any(seg in src_lc for seg in ("/routes/", "/pages/")) and any(
            name.endswith(s) for s in
            (".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".astro")):
        return "route"

    # React hooks — convention is `useFoo.ts(x)`.
    if name.startswith("use") and len(name) > 3 and name[3].isupper() and any(
            name.endswith(s) for s in (".ts", ".tsx", ".js", ".jsx")):
        return "hook"

    # Backend layered-architecture conventions (mostly C#, but the
    # suffixes are common across TS as well).
    if name.endswith(("Controller.cs", "Controller.ts")) \
            or "/controllers/" in src_lc:
        return "controller"
    if name.endswith(("Repository.cs", "Repository.ts", "Repo.cs", "Dao.cs")):
        return "repository"
    if name.endswith(("Service.cs", "Service.ts")) or "/services/" in src_lc:
        return "service"

    # Data shapes — models, entities, type definitions.
    if name.endswith(("Model.cs", "Entity.cs", "Dto.cs",
                      "Types.cs", ".types.ts", ".types.tsx",
                      ".model.ts", ".entity.ts")):
        return "model"
    if any(seg in src_lc for seg in
           ("/models/", "/entities/", "/types/", "/schemas/")):
        return "model"

    # UI components — explicit /components/ dir OR PascalCase TS/JSX file.
    if "/components/" in src_lc and any(
            name.endswith(s) for s in
            (".tsx", ".jsx", ".vue", ".svelte")):
        return "component"
    if name and name[0:1].isupper() and any(
            name.endswith(s) for s in (".tsx", ".jsx", ".vue", ".svelte")):
        return "component"

    # Utility / helper code — last specific bucket before fallback.
    if any(name.endswith(s) for s in
           ("Utils.cs", "Helpers.cs", "Helper.cs",
            ".utils.ts", ".helpers.ts", ".util.ts")):
        return "util"
    if any(seg in src_lc for seg in ("/utils/", "/lib/", "/helpers/")):
        return "util"

    return "code"


def _write_obsidian_notes(graph_json: Path, obsidian_dir: Path,
                          pd: Path, *, include_symbols: bool = False) -> int:
    """Write one .md per node in graph.json with [[wikilinks]] for edges.

    `pd` is the project root; the filter uses it to verify each node's
    `source_file` actually exists inside the repo. When `include_symbols`
    is False (default), only file-level nodes are exported — keeps the
    Obsidian graph view browsable. Returns the number of notes written.
    """
    import json
    try:
        data = json.loads(graph_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        info(f"obsidian export: cannot read graph.json: {e}")
        return 0

    nodes = data.get("nodes") or []
    # Graphify writes NetworkX node-link JSON: edges live under `links`,
    # not `edges`. Fall back to `edges` for older / non-NetworkX shapes.
    edges = data.get("links") or data.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        info("obsidian export: unexpected graph.json shape; skipping")
        return 0

    # Keep the pre-filter node list around: we need it later to project
    # symbol-level edges down to file-level. Without this projection,
    # almost every edge gets dropped (Graphify's edges connect symbols,
    # not files) and the resulting Obsidian graph is a cloud of
    # disconnected dots.
    all_nodes = list(nodes)

    # Filter to code we built. Strongest possible test: source_file
    # resolves to a real file inside the repo, NOT under a dependency
    # / build / cache directory, AND is in git's index (tracked or
    # untracked-not-ignored). Catches the cases the previous pass
    # missed: vendored copies, build artefacts that happen to be
    # committed, generated stubs, and Graphify's habit of putting
    # package IDs like `@dnd-kit/core` in source_file with no real file.
    tracked = _tracked_files(pd)
    total_nodes = len(nodes)
    nodes = [n for n in nodes if _is_user_code_node(n, pd, tracked)]
    after_code_filter = len(nodes)
    if total_nodes - after_code_filter:
        info(f"obsidian export: excluded "
             f"{total_nodes - after_code_filter}/{total_nodes} non-code nodes "
             f"(dependencies, builtins, build artefacts, configs)")

    # Default: file-level only. Symbol-level adds tens of thousands of
    # extracted identifiers (every const, hook, JSX component) which
    # crashes Obsidian's graph view at any real-codebase scale.
    if not include_symbols:
        nodes = [n for n in nodes if _is_file_level_node(n)]
        if after_code_filter - len(nodes):
            info(f"obsidian export: collapsed "
                 f"{after_code_filter - len(nodes)} symbol-level nodes "
                 f"into their parent files (pass --include-symbols to keep)")

        # De-dupe: Graphify often emits multiple file-level nodes for the
        # same source_file (e.g. `Service.cs` and the `Service` class
        # inside it both pass the file-level check). Keep exactly one
        # per source_file, preferring the node whose label is the full
        # basename (more informative than the stem-only variant).
        before_dedup = len(nodes)
        by_file: dict[str, dict] = {}
        for n in nodes:
            src = n.get("source_file") or n.get("file") or n.get("path") or ""
            if not isinstance(src, str) or not src:
                continue
            label = str(n.get("label") or "")
            basename = Path(src).name
            existing = by_file.get(src)
            if existing is None or (
                label == basename
                and str(existing.get("label")) != basename
            ):
                by_file[src] = n
        nodes = list(by_file.values())
        if before_dedup - len(nodes):
            info(f"obsidian export: de-duped "
                 f"{before_dedup - len(nodes)} redundant file-level nodes "
                 f"(one note per source file)")

    # Build a fast lookup of which node ids survived the filter, then
    # drop any edge whose endpoints aren't both user-code nodes. Otherwise
    # we'd have dangling wikilinks pointing at non-existent notes.
    kept_ids: set[str] = set()
    for n in nodes:
        nid = n.get("id") or n.get("name") or n.get("label")
        if isinstance(nid, str):
            kept_ids.add(nid)

    obsidian_dir.mkdir(parents=True, exist_ok=True)

    # Clear any previous export so deleted / renamed nodes don't leave
    # stale notes behind, and excluded-now-but-kept-before nodes (e.g.
    # we tightened the filter) get cleaned up. Safe because the folder
    # is fully regenerated from graph.json on every run.
    for stale in obsidian_dir.glob("*.md"):
        stale.unlink()

    # Project symbol-level edges down to their parent file's kept node.
    # Graphify emits edges between symbols (functions, classes, JSX
    # components); when we collapse to file-level, those edges would all
    # die at the keep-check. The projection rewrites each endpoint to
    # the kept node for its source_file — so if symbol `foo` in `a.cs`
    # calls symbol `bar` in `b.cs`, we record `a.cs ↔ b.cs` instead of
    # dropping the edge.
    node_by_id: dict[str, dict] = {}
    for n in all_nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id") or n.get("name") or n.get("label")
        if isinstance(nid, str):
            node_by_id[nid] = n

    source_file_to_kept_id: dict[str, str] = {}
    for n in nodes:
        nid = n.get("id") or n.get("name") or n.get("label")
        src = n.get("source_file") or n.get("file") or n.get("path") or ""
        if isinstance(nid, str) and isinstance(src, str) and src:
            source_file_to_kept_id[src] = nid

    def _project(node_id: str) -> str | None:
        """Map a raw edge endpoint to the node id we actually kept.

        If the endpoint itself survived the filter, use it. Else, look
        up its source_file and return the kept file-level node for that
        path. Returns None if neither the endpoint nor its parent file
        was kept.
        """
        if node_id in kept_ids:
            return node_id
        meta = node_by_id.get(node_id)
        if not meta:
            return None
        src = meta.get("source_file") or meta.get("file") or meta.get("path") or ""
        if not isinstance(src, str):
            return None
        return source_file_to_kept_id.get(src)

    # Build neighbor index from edges (defensive across schema keys).
    # Project each endpoint, drop self-loops (a symbol calling another
    # symbol in the same file is not a file-level edge), and skip pairs
    # we already have to avoid N duplicate wikilinks for N intra-file
    # call sites between the same two files.
    neighbors: dict[str, list[tuple[str, str]]] = {}
    seen_pairs: set[tuple[str, str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src = edge.get("src") or edge.get("source") or edge.get("from")
        dst = edge.get("dst") or edge.get("target") or edge.get("to")
        rel = edge.get("relation") or edge.get("type") or ""
        if not (isinstance(src, str) and isinstance(dst, str)):
            continue
        src_p = _project(src)
        dst_p = _project(dst)
        if not src_p or not dst_p or src_p == dst_p:
            continue
        if src_p not in kept_ids or dst_p not in kept_ids:
            continue
        pair = (src_p, dst_p, rel)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        neighbors.setdefault(src_p, []).append((dst_p, rel))
        neighbors.setdefault(dst_p, []).append((src_p, rel))

    # Map node id → safe filename. When two nodes have the same label
    # (e.g. `__root.tsx` from web/tickets/src/routes/ AND from
    # web/scheduling/src/routes/), prefix the second with its parent
    # directory so the filenames stay informative instead of becoming
    # `__root.tsx_1`, `__root.tsx_2`.
    id_to_fname: dict[str, str] = {}
    used: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id") or n.get("name") or n.get("label")
        if not isinstance(nid, str):
            continue
        label = n.get("label") or n.get("name") or nid
        src = n.get("source_file") or n.get("file") or n.get("path") or ""
        base = _safe_filename(str(label))
        candidate = base

        if candidate in used and isinstance(src, str) and src:
            # Walk up the source path one segment at a time until we get
            # a unique name. So `__root.tsx` → `routes__root.tsx` →
            # `src/routes__root.tsx` (with `/` → `__` for filesystem).
            parts = src.replace("\\", "/").strip("/").split("/")
            for depth in range(1, len(parts)):
                prefix = "__".join(parts[-(depth + 1):-1])
                trial = _safe_filename(f"{prefix}__{base}")
                if trial not in used:
                    candidate = trial
                    break

        # Last-resort numeric suffix only if path-based disambiguation
        # still collided (e.g. two identical files in identical sub-paths
        # somehow — extremely rare).
        i = 1
        while candidate in used:
            candidate = f"{base}_{i}"
            i += 1

        used.add(candidate)
        id_to_fname[nid] = candidate

    # Write per-node notes
    count = 0
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id") or n.get("name") or n.get("label")
        if not isinstance(nid, str) or nid not in id_to_fname:
            continue
        label = n.get("label") or n.get("name") or nid

        src_for_role = n.get("source_file") or n.get("file") or n.get("path") or ""
        src_str = str(src_for_role) if isinstance(src_for_role, str) else ""
        role = _classify_file_role(src_str)
        package = _classify_package(src_str)

        lines: list[str] = ["---"]
        for key in ("source_file", "type", "file_type", "language", "location"):
            v = n.get(key)
            if isinstance(v, str) and v:
                lines.append(f'{key}: "{v}"')
        ftype = n.get("file_type") or n.get("type") or "node"
        lines.append(f'role: "{role}"')
        if package:
            lines.append(f'package: "{package}"')
        lines.append("tags:")
        lines.append(f"  - graphify/{ftype}")
        # Emit the role as a second tag so Obsidian's graph view can be
        # coloured by it. `graphify/code` stays as the umbrella tag for
        # "show all"; `graphify/<role>` is the discriminator.
        if role != ftype:
            lines.append(f"  - graphify/{role}")
        if package:
            lines.append(f"  - graphify/pkg/{package}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {label}")
        lines.append("")

        if nid in neighbors:
            lines.append("## Connections")
            seen_targets: set[str] = set()
            # Sort by target's display name for stable output
            for target_id, rel in sorted(
                neighbors[nid],
                key=lambda t: id_to_fname.get(t[0], t[0]),
            ):
                if target_id in seen_targets:
                    continue
                seen_targets.add(target_id)
                target_fname = id_to_fname.get(target_id, _safe_filename(target_id))
                rel_str = f" - `{rel}`" if rel else ""
                lines.append(f"- [[{target_fname}]]{rel_str}")
            lines.append("")

        # Inline tags at bottom for Obsidian's tag panel + graph filter.
        inline = [f"#graphify/{ftype}"]
        if role != ftype:
            inline.append(f"#graphify/{role}")
        if package:
            inline.append(f"#graphify/pkg/{package}")
        lines.append(" ".join(inline))

        fname = id_to_fname[nid] + ".md"
        (obsidian_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        count += 1

    return count


if __name__ == "__main__":
    sys.exit(main())
