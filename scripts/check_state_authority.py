#!/usr/bin/env python3
"""Check that only the activity authority commits a face-state transition.

Standard library only. Exits non-zero when a rule is violated.

`mira/core/activity_authority.py` exists so that one component commits every
transition of the shared `FaceState` while the components around it only request
one. Nothing in the language enforces that: any component handed a
`StateManager` can call `set_state` and quietly become a second authority. These
rules make the arrangement mechanical.

To be precise about what that did and did not fix: the *commit* is centralized,
so there is one place to read and one place to enforce. Order sensitivity is
**not** removed — `Brain` and `InteractionManager` still both request LISTENING
for the same event, and which request lands first still decides the outcome,
with `StateManager`'s no-op guard absorbing the loser. That was preserved on
purpose; see `mira/core/activity_authority.py`.

Rule A (single committer)
    A call of the form `<something>.state_manager.set_state(...)`, or
    `state_manager.set_state(...)` on a bare name, commits the shared state.
    Only `mira/core/activity_authority.py` may make one.

Rule B (restricted handle)
    Only the modules in `MAY_HOLD_STATE_MANAGER` may reference the
    `mira.core.state_manager` module or the `StateManager` name, in any import
    form. A component that cannot name the type has no supported way to acquire
    the handle Rule A is about.

    This rule is only as good as the absence of other routes to a live manager.
    One existed and was removed rather than exempted: `Application` used to
    carry a `state_manager` field, so anything holding the composed record —
    `mira.ui` included — could take a manager without importing anything, and
    an aliased receiver then defeated Rule A. `Application` now exposes
    `activity` instead.

Rule C (restricted authority)
    Only the modules in `MAY_REQUEST_TRANSITIONS` may call a command on
    something named `activity`. Rule A stops a component committing through a
    `StateManager`; without this, the same component could reach the authority
    itself — `self.brain.activity.attend()` from the UI needs no import and
    would otherwise be invisible to every rule here.

Deliberately keyed on the receiver being named `state_manager` rather than on
its type: this is an AST check with no type inference, and `FaceController` also
has a `set_state` (`mira/ui/face/face_controller.py:86`) which is a rendering
call and no business of this checker. Naming the receiver is what separates them.

Known limits, stated rather than papered over. An alias defeats Rule A, and
`getattr` defeats both it and Rule C:

    manager = self.state_manager
    manager.set_state(...)              # not detected
    getattr(x, "set_state")(...)        # not detected

Rules B and C are what make a live handle hard to obtain in the first place. A
checker that resolved types would catch the rest; that is a much larger tool
than this tranche needs, and `tests/test_activity_state_authority.py` pins the
behaviour that would break. Two further gaps are known and deliberately left:
writing `state_manager.current_state` directly is caught only when it is a
plain attribute assignment (Rule D below), and only `mira/` is scanned.

Rule D (no silent writes)
    `current_state` is `StateManager`'s own storage. Assigning it commits a
    change while emitting nothing, which desynchronizes every subscriber from
    the state they are meant to render. Only `mira/core/state_manager.py` may
    assign it.

Rule E (semantic boundary)
    Cognition, application and core policy must use `EmbodimentIntent`,
    `ActivityState` and `AffectState`, not the legacy `FaceState` presentation
    model or `BrainResponse.face_state`. `StateManager` is the sole core
    exception because it is the compatibility presentation store. The legacy
    resolver itself may only be imported by the activity authority (the runtime
    bridge) and session memory (the existing serialized metadata bridge).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "mira"

# The one module allowed to commit. Not a set: singular is the whole point.
THE_AUTHORITY = "mira/core/activity_authority.py"

# Rule B: modules permitted to name `StateManager`.
#   - the authority holds one and commits through it
#   - the composition root constructs it and injects it into the authority
#   - the module that defines it
MAY_HOLD_STATE_MANAGER = frozenset(
    {
        THE_AUTHORITY,
        "mira/application/composition.py",
        "mira/core/state_manager.py",
    }
)

COMMIT_METHOD = "set_state"
STATE_MANAGER_ATTR = "state_manager"

# Rule C: modules permitted to request a transition from the authority. These
# are the three components that used to commit directly.
MAY_REQUEST_TRANSITIONS = frozenset(
    {
        THE_AUTHORITY,
        "mira/core/brain.py",
        "mira/core/interaction_manager.py",
        "mira/core/embodied_behavior.py",
    }
)
AUTHORITY_ATTR = "activity"
AUTHORITY_COMMANDS = frozenset(
    {"attend", "deliberate", "settle", "conclude", "express"}
)

# Rule D: the module that owns the storage.
STATE_STORAGE_ATTR = "current_state"
MAY_ASSIGN_STATE_STORAGE = frozenset({"mira/core/state_manager.py"})

FACE_STATE_CORE_BOUNDARY = "mira/core/state_manager.py"
SEMANTIC_LAYERS = ("mira/cognition/", "mira/application/", "mira/core/")
COMPATIBILITY_MODULE = "mira.domain.embodiment_compatibility"
MAY_IMPORT_COMPATIBILITY = frozenset(
    {
        THE_AUTHORITY,
        "mira/domain/embodiment_compatibility.py",
        "mira/memory/session_memory.py",
    }
)


class Finding(NamedTuple):
    location: str
    message: str
    detail: str

    def render(self) -> str:
        return f"{self.location}: {self.message}\n    {self.detail}"


def receiver_is_a_state_manager(node: ast.Call) -> bool:
    """True when the call reads `<...>.state_manager.set_state(...)`."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != COMMIT_METHOD:
        return False
    receiver = func.value
    if isinstance(receiver, ast.Attribute):
        return receiver.attr == STATE_MANAGER_ATTR
    if isinstance(receiver, ast.Name):
        return receiver.id == STATE_MANAGER_ATTR
    return False


def calls_an_authority_command(node: ast.Call) -> str | None:
    """Return the command name when the call reads `<...>.activity.<command>(...)`."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in AUTHORITY_COMMANDS:
        return None
    receiver = func.value
    if isinstance(receiver, ast.Attribute) and receiver.attr == AUTHORITY_ATTR:
        return func.attr
    if isinstance(receiver, ast.Name) and receiver.id == AUTHORITY_ATTR:
        return func.attr
    return None


def assigns_state_storage(node: ast.AST) -> bool:
    """True for `<...>.current_state = ...`, which commits without emitting."""
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = [node.target]
    return any(
        isinstance(target, ast.Attribute) and target.attr == STATE_STORAGE_ATTR
        for target in targets
    )


def check_file(path: Path) -> list[Finding]:
    relative = path.relative_to(REPO_ROOT).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [Finding(f"{relative}:{exc.lineno}", "[parse] could not parse", exc.msg)]

    findings: list[Finding] = []
    semantic_layer = relative.startswith(SEMANTIC_LAYERS)
    face_state_allowed = relative in {FACE_STATE_CORE_BOUNDARY, THE_AUTHORITY}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and receiver_is_a_state_manager(node):
            if relative != THE_AUTHORITY:
                findings.append(
                    Finding(
                        f"{relative}:{node.lineno}",
                        "[authority] only the activity authority may commit a state transition",
                        f"state_manager.{COMMIT_METHOD}(...)  "
                        f"(request one through ActivityAuthority instead; "
                        f"the authority is {THE_AUTHORITY})",
                    )
                )

        if isinstance(node, ast.Call):
            command = calls_an_authority_command(node)
            if command is not None and relative not in MAY_REQUEST_TRANSITIONS:
                findings.append(
                    Finding(
                        f"{relative}:{node.lineno}",
                        "[authority] this module may not request a state transition",
                        f"activity.{command}(...)  (permitted: "
                        f"{', '.join(sorted(MAY_REQUEST_TRANSITIONS))})",
                    )
                )

        if assigns_state_storage(node) and relative not in MAY_ASSIGN_STATE_STORAGE:
            findings.append(
                Finding(
                    f"{relative}:{node.lineno}",
                    "[authority] this module may not assign the state storage directly",
                    f"{STATE_STORAGE_ATTR} = ...  (an assignment commits without "
                    "emitting state_changed, so every subscriber goes stale; "
                    "request a transition through ActivityAuthority instead)",
                )
            )

        imported: list[tuple[int, str]] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            imported = [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.Import):
            imported = [(node.lineno, alias.name) for alias in node.names]

        if isinstance(node, ast.ImportFrom) and node.module == "mira.core.state_manager":
            # `from mira.core.state_manager import anything`
            imported.append((node.lineno, "StateManager"))

        if semantic_layer and not face_state_allowed:
            imports_legacy_state = (
                isinstance(node, ast.ImportFrom)
                and node.module == "mira.domain.state"
                and any(alias.name == "FaceState" for alias in node.names)
            ) or (
                isinstance(node, ast.Import)
                and any(alias.name == "mira.domain.state" for alias in node.names)
            ) or (
                isinstance(node, ast.ImportFrom)
                and node.module == "mira.domain"
                and any(alias.name in {"state", "FaceState", "*"} for alias in node.names)
            )
            if imports_legacy_state:
                findings.append(
                    Finding(
                        f"{relative}:{node.lineno}",
                        "[semantics] semantic policy may not import FaceState",
                        "use ActivityState, AffectState and EmbodimentIntent; "
                        "FaceState is confined to compatibility/presentation",
                    )
                )

        imports_compatibility = (
            isinstance(node, ast.ImportFrom)
            and node.module == COMPATIBILITY_MODULE
        ) or (
            isinstance(node, ast.Import)
            and any(alias.name == COMPATIBILITY_MODULE for alias in node.names)
        ) or (
            isinstance(node, ast.ImportFrom)
            and node.module == "mira.domain"
            and any(alias.name == "embodiment_compatibility" for alias in node.names)
        )
        if imports_compatibility and relative not in MAY_IMPORT_COMPATIBILITY:
            findings.append(
                Finding(
                    f"{relative}:{node.lineno}",
                    "[semantics] legacy resolver may only be used at a compatibility boundary",
                    f"permitted: {', '.join(sorted(MAY_IMPORT_COMPATIBILITY))}",
                )
            )

        if (
            semantic_layer
            and not face_state_allowed
            and isinstance(node, ast.Attribute)
            and node.attr == "face_state"
        ):
            findings.append(
                Finding(
                    f"{relative}:{node.lineno}",
                    "[semantics] semantic policy may not read response.face_state",
                    "consume BrainResponse.embodiment instead",
                )
            )

        for lineno, name in imported:
            # Every form that yields the module or the type:
            #   from mira.core.state_manager import StateManager
            #   import mira.core.state_manager [as sm]
            #   from mira.core import state_manager
            if name not in ("StateManager", "mira.core.state_manager", "state_manager"):
                continue
            if relative in MAY_HOLD_STATE_MANAGER:
                continue
            findings.append(
                Finding(
                    f"{relative}:{lineno}",
                    "[authority] this module may not hold a StateManager",
                    "import ActivityAuthority and request a transition instead "
                    f"(permitted: {', '.join(sorted(MAY_HOLD_STATE_MANAGER))})",
                )
            )

    return dedupe(findings)


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse repeats: one import statement can match two of Rule B's forms."""
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

    authority = REPO_ROOT / THE_AUTHORITY
    if not authority.is_file():
        # Without this, deleting the authority would make every rule vacuously
        # pass and the checker would report success on a codebase with none.
        print(f"error: no activity authority at {THE_AUTHORITY}", file=sys.stderr)
        return 2

    # The file existing is not enough. Emptying it — a bad merge, or moving the
    # class out and leaving the module behind — used to yield a green run
    # announcing an authority that was no longer there.
    defines_authority = any(
        isinstance(node, ast.ClassDef) and node.name == "ActivityAuthority"
        for node in ast.walk(ast.parse(authority.read_text(encoding="utf-8")))
    )
    if not defines_authority:
        print(
            f"error: {THE_AUTHORITY} defines no ActivityAuthority",
            file=sys.stderr,
        )
        return 2

    findings: list[Finding] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        findings.extend(check_file(path))

    if findings:
        print(f"State-authority violations ({len(findings)}):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.render()}", file=sys.stderr)
        print(
            "\nRules are documented at the top of scripts/check_state_authority.py.",
            file=sys.stderr,
        )
        return 1

    print(f"State authority OK: {THE_AUTHORITY} is the only committer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
