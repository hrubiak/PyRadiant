#!/usr/bin/env python3
"""Audit which repo-root files an installation needs.

Two passes:

1. Runtime trace — launches run_pyradiant.py with QApplication.exec patched to a
   no-op so it returns immediately, then walks sys.modules to collect every .py
   file loaded under the repo root.

2. Static AST scan — parses every .py file in the repo and records any
   `import X` / `from X import ...` where X resolves to a bare module at the
   repo root (e.g. `config.py`). This catches lazy imports inside methods that
   the runtime pass won't fire.

Prints anything missing from package_pyradiant.py's TOP_LEVEL_FILES list.
Does not modify any files.
"""
import ast
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def runtime_trace() -> set[Path]:
    try:
        from PyQt6.QtWidgets import QApplication
        QApplication.exec = lambda self: 0
        if hasattr(QApplication, "exec_"):
            QApplication.exec_ = lambda self: 0
    except ImportError:
        pass

    try:
        runpy.run_path(str(ROOT / "run_pyradiant.py"), run_name="__main__")
    except SystemExit:
        pass

    loaded: set[Path] = set()
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        p = Path(f).resolve()
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            continue
        if p.suffix == ".py":
            loaded.add(rel)
    return loaded


def static_scan() -> set[str]:
    """Return the set of top-level module names referenced from the pyradiant
    package or run_pyradiant.py that resolve to a bare .py file at the repo root.

    Restricted to the app's own source tree so unrelated root-level scripts
    (installers, standalone IOCs, packagers) don't pollute the report.
    """
    root_modules = {
        p.stem for p in ROOT.glob("*.py") if p.stem != "__init__"
    }
    referenced: set[str] = set()

    scan_targets = [ROOT / "run_pyradiant.py"] + list((ROOT / "pyradiant").rglob("*.py"))
    for py in scan_targets:
        if any(part.startswith(".") or part.endswith("_pack") for part in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    head = alias.name.split(".")[0]
                    if head in root_modules:
                        referenced.add(head)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    head = node.module.split(".")[0]
                    if head in root_modules:
                        referenced.add(head)
    return referenced


def main() -> None:
    from package_pyradiant import TOP_LEVEL_FILES

    covered = set(TOP_LEVEL_FILES)
    pkg_dir = Path("pyradiant")

    print("=" * 60)
    print("PASS 1: runtime trace (startup imports)")
    print("=" * 60)
    loaded = runtime_trace()
    tooling = {"audit_pack.py", "package_pyradiant.py", "versioneer.py", "setup.py"}
    missing_runtime = []
    for rel in sorted(loaded):
        if rel.parts[0] == pkg_dir.name:
            continue
        if str(rel) in covered or str(rel) in tooling:
            continue
        missing_runtime.append(rel)

    print(f"  imported {len(loaded)} .py files under repo root")
    if missing_runtime:
        print("  NOT covered by TOP_LEVEL_FILES:")
        for rel in missing_runtime:
            print(f"    {rel}")
    else:
        print("  all imported files covered")

    print()
    print("=" * 60)
    print("PASS 2: static AST scan (catches lazy imports)")
    print("=" * 60)
    referenced = static_scan()
    missing_static = []
    for name in sorted(referenced):
        fname = f"{name}.py"
        if fname in covered:
            continue
        missing_static.append(fname)

    print(f"  found {len(referenced)} root modules referenced anywhere")
    if missing_static:
        print("  NOT covered by TOP_LEVEL_FILES (add these):")
        for fname in missing_static:
            print(f"    {fname}")
    else:
        print("  all referenced root modules covered")

    print()
    if not (missing_runtime or missing_static):
        print("OK — packager appears complete.")
    else:
        print("Review the entries above and add them to TOP_LEVEL_FILES.")


if __name__ == "__main__":
    main()
