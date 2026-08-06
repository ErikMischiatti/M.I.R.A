#!/usr/bin/env python3
"""Check import direction and Qt containment inside the mira package.

Standard library only. Exits non-zero when a rule is violated.

The rules are stated here in full, so this file is self-sufficient. They are
also the subject of proposed ADR 0001 (layering and dependency direction),
ADR 0002 (shared vocabulary ownership) and ADR 0005 (scheduler port); those
records are not yet in the repository, so do not go looking for them.

Rule D (direction)
    Every layer declares the layers it may import. Anything not declared is
    forbidden. A module may always import from its own layer.

        mira            -> nothing
        mira.domain     -> nothing            (shared vocabulary; no mira deps)
        mira.config     -> nothing
        mira.actions    -> domain
        mira.cognition  -> domain, actions
        mira.core       -> domain, actions, cognition
        mira.adapters   -> domain              (port implementations)
        mira.ui         -> domain, core, adapters
        mira.main       -> unrestricted        (composition entry point)

    Known exceptions are listed in DIRECTION_DEBT, reported on every run, and
    expected to shrink.

Rule Q (Qt containment)
    Only mira.ui, mira.adapters and mira.main may import PySide6. Timing and
    other technology bindings belong in mira.adapters behind a domain port.
    QT_DEBT holds exceptions and is currently empty.

Default-deny: any package under mira/ that is not assigned a layer below is a
failure, not a free pass. Adding a package therefore requires a deliberate
layering decision.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "mira"

QT_ROOTS = ("PySide6", "PyQt5", "PyQt6", "shiboken6")

# Rule D: layer -> the other mira layers it may import.
LAYER_IMPORTS: dict[str, frozenset[str]] = {
    "mira": frozenset(),
    "mira.domain": frozenset(),
    "mira.config": frozenset(),
    "mira.actions": frozenset({"mira.domain"}),
    "mira.cognition": frozenset({"mira.domain", "mira.actions"}),
    "mira.core": frozenset({"mira.domain", "mira.actions", "mira.cognition"}),
    "mira.adapters": frozenset({"mira.domain"}),
    "mira.ui": frozenset({"mira.domain", "mira.core", "mira.adapters"}),
}

# Layers exempt from every rule. The composition entry point must be able to
# reach everything in order to assemble the application.
UNRESTRICTED: frozenset[str] = frozenset({"mira.main"})

# Rule Q: layers permitted to import a GUI toolkit.
QT_ALLOWED_LAYERS: frozenset[str] = frozenset({"mira.ui", "mira.adapters"})

# Rule D exceptions: (importing module, imported module) -> justification.
# These are debt, not design. Each entry names what removes it.
DIRECTION_DEBT: dict[tuple[str, str], str] = {
    (
        "mira.actions.builtin_actions",
        "mira.core.session_memory",
    ): "SessionMemory is a memory-tier concept co-located with orchestration. "
    "Removed when a memory layer is extracted.",
    (
        "mira.actions.action_executor",
        "mira.core.events",
    ): "EventBus is a messaging mechanism co-located with orchestration; this "
    "import is TYPE_CHECKING-only. Removed when a messaging layer is extracted.",
    (
        "mira.cognition.llm_intent_engine",
        "mira.core.session_memory",
    ): "As above; annotation only.",
    (
        "mira.cognition.session_context_builder",
        "mira.core.session_memory",
    ): "As above; annotation only.",
}

# Rule Q exceptions: module -> justification.
# Empty since the scheduler port moved Qt timing into mira.adapters.
QT_DEBT: dict[str, str] = {}


class Finding(NamedTuple):
    """One rule violation or one reported exception."""

    location: str
    message: str
    detail: str

    def render(self) -> str:
        return f"{self.location}: {self.message}\n    {self.detail}"


def module_name_for(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def layer_of(module: str) -> str | None:
    """Return the declared layer owning this module, or None if undeclared.

    Unrestricted entries match exactly, so turning `mira/main.py` into a
    `mira/main/` package does not exempt the whole package. Otherwise the layer
    is the top-level subpackage, so a new package under mira/ is undeclared
    rather than silently absorbed into the root layer.
    """
    if module in UNRESTRICTED:
        return module
    parts = module.split(".")
    if parts[0] != "mira":
        return None
    if len(parts) == 1:
        return "mira"
    candidate = f"mira.{parts[1]}"
    return candidate if candidate in LAYER_IMPORTS else None


def is_qt(module: str) -> bool:
    return any(module == root or module.startswith(root + ".") for root in QT_ROOTS)


def collect_imports(tree: ast.AST) -> list[tuple[int, str, str, bool]]:
    """Yield (lineno, imported_module, source_text, is_relative)."""
    found: list[tuple[int, str, str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name, f"import {alias.name}", False))
        elif isinstance(node, ast.ImportFrom):
            names = ", ".join(alias.name for alias in node.names)
            module = node.module or ""
            if node.level:
                dots = "." * node.level
                source = f"from {dots}{module} import {names}"
                found.append((node.lineno, f"{dots}{module}", source, True))
            elif node.module:
                source = f"from {module} import {names}"
                found.append((node.lineno, module, source, False))
                if module == "mira":
                    # `from mira import ui` imports the submodule mira.ui.
                    # Deeper paths are already fully qualified, so expanding
                    # their names would invent modules that do not exist.
                    for alias in node.names:
                        found.append((node.lineno, f"mira.{alias.name}", source, False))
    return found


def check_file(path: Path) -> tuple[list[Finding], list[Finding]]:
    module = module_name_for(path)
    layer = layer_of(module)
    rel = path.relative_to(REPO_ROOT)

    if layer is None:
        return [
            Finding(
                str(rel),
                f"[unclassified] {module} belongs to no declared layer",
                "Assign it in LAYER_IMPORTS (or UNRESTRICTED) before adding code.",
            )
        ], []

    if module in UNRESTRICTED:
        return [], []

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [Finding(f"{rel}:{exc.lineno}", "[parse] could not parse", exc.msg)], []

    violations: list[Finding] = []
    exceptions: list[Finding] = []
    allowed = LAYER_IMPORTS[layer]

    for lineno, imported, source, relative in collect_imports(tree):
        location = f"{rel}:{lineno}"

        if relative:
            violations.append(
                Finding(
                    location,
                    "[relative] relative imports are not allowed in mira",
                    f"{source}  (use an absolute path so direction is checkable)",
                )
            )
            continue

        if is_qt(imported):
            if layer in QT_ALLOWED_LAYERS:
                continue
            reason = QT_DEBT.get(module)
            if reason is None:
                violations.append(
                    Finding(location, f"[qt] {layer} must not import a GUI toolkit", source)
                )
            else:
                exceptions.append(Finding(location, f"[qt-debt] {module}", reason))
            continue

        if not imported.startswith("mira."):
            # Third-party/stdlib imports are out of scope for Rule D, and the
            # empty root package is harmless to import.
            continue

        target = layer_of(imported)
        if target is None:
            violations.append(
                Finding(
                    location,
                    f"[unclassified] {imported} belongs to no declared layer",
                    f"{source}  (assign its package in LAYER_IMPORTS)",
                )
            )
            continue
        if target == layer or target in allowed:
            continue

        reason = DIRECTION_DEBT.get((module, imported))
        if reason is None:
            violations.append(
                Finding(
                    location,
                    f"[direction] {layer} must not import {target}",
                    f"{source}  (allowed: {', '.join(sorted(allowed)) or 'nothing'})",
                )
            )
        else:
            exceptions.append(
                Finding(location, f"[direction-debt] {module} -> {imported}", reason)
            )

    return dedupe(violations), dedupe(exceptions)


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse findings that repeat because one statement imports many names."""
    seen: set[tuple[str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.location, finding.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def main() -> int:
    if not PACKAGE_ROOT.is_dir():
        print(f"error: package root not found: {PACKAGE_ROOT}", file=sys.stderr)
        return 2

    violations: list[Finding] = []
    exceptions: list[Finding] = []

    # rglob does not follow directory symlinks, so a symlinked package would
    # hide an importable subtree from every rule below. Reject it outright.
    for path in sorted(PACKAGE_ROOT.rglob("*")):
        if path.is_symlink() and path.is_dir():
            violations.append(
                Finding(
                    str(path.relative_to(REPO_ROOT)),
                    "[symlink] directory symlinks under mira/ are not allowed",
                    "They stay importable but are invisible to this checker; "
                    "use a real directory.",
                )
            )

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        file_violations, file_exceptions = check_file(path)
        violations.extend(file_violations)
        exceptions.extend(file_exceptions)

    if exceptions:
        print(f"Declared exceptions ({len(exceptions)}) — expected to shrink:")
        for finding in exceptions:
            print(f"  {finding.render()}")
        print()

    if violations:
        sys.stdout.flush()
        print(f"Layering violations ({len(violations)}):", file=sys.stderr)
        for finding in violations:
            print(f"  {finding.render()}", file=sys.stderr)
        print(
            "\nRules are documented at the top of scripts/check_layering.py.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Layering OK: direction and Qt containment hold "
        f"({len(exceptions)} declared exceptions)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
