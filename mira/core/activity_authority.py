"""The single component permitted to commit a face-state transition.

Before this module, three components wrote state directly: `Brain` (6 call
sites), `InteractionManager` (10) and `EmbodiedBehavior` (4). They did not
partition the work — `Brain` and `InteractionManager` both drove LISTENING and
THINKING for the same events, and which one took effect depended on subscription
order, with `StateManager`'s no-op guard (`mira/core/state_manager.py:11-12`)
hiding the overlap. One case genuinely disagreed: a message arriving mid-turn,
where `InteractionManager` deliberately abstains and `Brain` does not.

The split this module draws is the one the task calls for:

    components request or cause transitions
    one authority commits

So the requests stay exactly where they were — the callers still decide *when* a
transition is warranted, and the mid-turn disagreement above is preserved rather
than resolved. What moved is the commit itself and the vocabulary: no caller
names a `FaceState` for an activity transition any more, and no caller reaches
`StateManager`. `scripts/check_state_authority.py` enforces that mechanically.

Activity and affect
-------------------
`FaceState` is one variable carrying two things:

    activity  IDLE, LISTENING, THINKING          what the system is doing
    affect    HAPPY, TIRED, ANGRY, CONFUSED      how it is reacting
    mixed     SPEAKING                            delivering a response, which
                                                  is both an activity and an
                                                  expression

Because affect lands in the same variable, an activity authority that ignored it
would leave a second writer for the same slot — so `express` is here too, marked
as affect rather than pretending to be activity. Separating them properly means
two independent variables, a `FaceState` split, and a face-subsystem that can
render a combination; that is an embodiment redesign, deliberately not attempted
here. `TIRED` and `ANGRY` are unreachable today: nothing constructs them outside
`mira/ui/debug_panel.py`'s manual override.
"""

from __future__ import annotations

from mira.core.state_manager import StateManager
from mira.domain.state import FaceState


class ActivityAuthority:
    """Owns every transition of the shared face state.

    Thin on purpose. It holds no policy of its own: each method is one
    transition a caller already made, named for the reason rather than for the
    value. Adding a decision here would move behaviour out of the components
    that own it, which this tranche explicitly does not do.
    """

    def __init__(self, state_manager: StateManager) -> None:
        # Public so tests can read what was committed without reaching through
        # a private name; nothing in production touches it.
        self.state_manager = state_manager

    # --- activity -------------------------------------------------------

    def attend(self) -> None:
        """The user is engaging: input focused, text present, or a turn opened."""
        self._commit(FaceState.LISTENING)

    def deliberate(self) -> None:
        """Interpretation has started."""
        self._commit(FaceState.THINKING)

    def settle(self, *, engaged: bool) -> None:
        """Return to a passive state, choosing by whether the user is still there.

        Four call sites collapse into this one: leaving the input, clearing the
        text, restoring after a response, and the expressive decay. All four
        asked the same question and answered it the same way, so the branch
        belongs here rather than being rewritten at each of them.
        """
        if engaged:
            self.attend()
        else:
            self._commit(FaceState.IDLE)

    def conclude(self, face_state: FaceState) -> None:
        """A response is ready; it carries the state it wants to be seen in.

        The value comes from the response rather than from this class, which is
        why it is a parameter — `BrainResponse.face_state` is decided by the
        response builder and is part of the reply, not of the activity model.
        """
        self._commit(face_state)

    # --- affect ---------------------------------------------------------

    def express(self, face_state: FaceState) -> None:
        """Commit an affective reaction.

        Separate from the activity methods because it is a different kind of
        transition that happens to share the variable. See the module docstring
        for why the two are not yet separate variables.
        """
        self._commit(face_state)

    # --- reading --------------------------------------------------------

    def current(self) -> FaceState:
        return self.state_manager.get_state()

    # --- the one commit point -------------------------------------------

    def _commit(self, face_state: FaceState) -> None:
        self.state_manager.set_state(face_state)
