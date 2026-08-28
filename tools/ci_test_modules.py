#!/usr/bin/env python3
"""List the modules CI can install and test, grouped into matrix chunks.

Why this is computed instead of listed: a hand-kept list goes stale the first
time a module is added, and the failure is silent — the module simply never gets
tested and nobody notices. Here the answer is derived from the manifests every
run, so a new module is picked up (or reported as unsatisfiable) on its first
push.

A module is testable when it has a ``tests/`` package and its full transitive
dependency closure resolves inside the addons paths given on the command line.
Anything else is printed to stderr with the dependency that blocks it, so an
unsatisfiable module is visible rather than quietly skipped.

    python3 tools/ci_test_modules.py --chunks 8 /path/to/addons [...]
"""

import argparse
import ast
import json
import pathlib
import sys


def manifests(paths):
    """{module: manifest} across every addons directory, first path wins."""
    found = {}
    for root in paths:
        root = pathlib.Path(root)
        if not root.is_dir():
            continue
        for manifest in sorted(root.glob("*/__manifest__.py")):
            name = manifest.parent.name
            if name in found:
                continue
            try:
                found[name] = ast.literal_eval(manifest.read_text(encoding="utf-8"))
            except Exception as exc:               # noqa: BLE001 - report, don't crash
                print(f"::warning::unreadable manifest {manifest}: {exc}", file=sys.stderr)
    return found


def missing_dep(name, mans, seen=None):
    """First dependency of ``name`` that is not present, or None."""
    seen = seen or set()
    if name in seen:
        return None
    seen.add(name)
    for dep in mans.get(name, {}).get("depends", []):
        if dep not in mans:
            return dep
        blocked = missing_dep(dep, mans, seen)
        if blocked:
            return blocked
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".", help="the Symbifox checkout")
    parser.add_argument("--chunks", type=int, default=8)
    parser.add_argument("addons", nargs="+", help="every addons directory on the path")
    args = parser.parse_args()

    repo = pathlib.Path(args.repo)
    mans = manifests(args.addons)
    # ``<module>.bak.<stamp>`` rollback copies are valid modules by manifest, so
    # nothing rejects them on its own. They exist in the tenant tree this tool
    # can also be pointed at; a clean checkout has none.
    ours = sorted(m.parent.name for m in repo.glob("*/__manifest__.py")
                  if m.parent.name.isidentifier())
    tested = [m for m in ours if (repo / m / "tests" / "__init__.py").is_file()]

    runnable, blocked = [], []
    for name in tested:
        dep = missing_dep(name, mans)
        (blocked if dep else runnable).append((name, dep))

    for name, dep in blocked:
        print(f"::warning::{name} has tests but depends on missing '{dep}' — not run",
              file=sys.stderr)

    names = [n for n, _ in runnable]
    size = max(1, -(-len(names) // args.chunks))
    chunks = [",".join(names[i:i + size]) for i in range(0, len(names), size)]
    print(json.dumps(chunks))
    print(f"{len(ours)} modules, {len(tested)} with tests, {len(names)} runnable, "
          f"{len(blocked)} blocked", file=sys.stderr)


if __name__ == "__main__":
    main()
