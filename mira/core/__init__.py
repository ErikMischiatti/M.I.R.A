"""Runtime orchestration.

Nothing is re-exported here; import from the defining module, as the sibling
package roots instruct.

This file used to hold a lazy `__getattr__` for `Brain`, because
`mira.cognition.session_context_builder` imported `SessionMemory` from this
package, which initialised it, which imported `mira.core.brain`, which imported
`mira.cognition.llm_intent_engine` while that module was still only partially
initialised.

Moving session memory to `mira.memory` and the event bus to `mira.messaging`
removed the cycle: cognition depends on those packages directly and no longer
initialises this one. Verified across every supported import path in a fresh
interpreter, because a cycle only bites for some import orders.

Keeping this file empty of imports also keeps `import mira.core.state_manager`
cheap: it loads 7 `mira` modules, against 27 if `Brain` were imported here,
because `Brain` pulls the whole cognition stack. Both figures are from
`python -c "import mira.core.state_manager, sys;
print(len([m for m in sys.modules if m.startswith('mira')]))"` on this tree.
"""
