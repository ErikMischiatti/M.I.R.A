"""The single component permitted to commit an embodiment transition.

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

Activity and affect are now independent in `EmbodimentIntent`. The current face
still accepts one `FaceState`, so this authority resolves the semantic intent at
the compatibility boundary before committing it. That preserves every existing
event and renderer consumer while preventing new core policy from using
`FaceState` as its semantic model.
"""

from __future__ import annotations

from mira.core.state_manager import StateManager
from mira.domain.embodiment import (
    ActivityState,
    AffectState,
    EmbodimentIntent,
)
from mira.domain.embodiment_compatibility import resolve_face_state
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
        self._intent = EmbodimentIntent(activity=ActivityState.IDLE)

    # --- activity -------------------------------------------------------

    def attend(self) -> None:
        """The user is engaging: input focused, text present, or a turn opened."""
        self._replace(EmbodimentIntent(activity=ActivityState.LISTENING))

    def deliberate(self) -> None:
        """Interpretation has started."""
        self._replace(EmbodimentIntent(activity=ActivityState.THINKING))

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
            self._replace(EmbodimentIntent(activity=ActivityState.IDLE))

    def conclude(self, intent: EmbodimentIntent) -> None:
        """A response is ready; commit its complete embodiment intent.

        The response builder chooses this value. This authority owns committing
        it, not deciding which response affect is appropriate.
        """
        self._replace(intent)

    # --- affect ---------------------------------------------------------

    def express(self, affect: AffectState) -> None:
        """Colour the current activity without replacing that semantic axis.

        The legacy face may still collapse the combination to the affective
        profile, but the underlying intent retains both values.
        """
        self._replace(EmbodimentIntent(activity=self._intent.activity, affect=affect))

    # --- reading --------------------------------------------------------

    def current(self) -> FaceState:
        """Return the legacy presentation state while the bridge exists."""
        return self.state_manager.get_state()

    def is_presenting(self, intent: EmbodimentIntent) -> bool:
        """Whether the legacy presentation still represents ``intent``.

        Projection stays here, at the compatibility commit/read boundary, so
        semantic policy never needs to import the temporary resolver.
        """
        return self.current() is resolve_face_state(intent)

    def current_intent(self) -> EmbodimentIntent:
        return self._intent

    # --- the one commit point -------------------------------------------

    def _replace(self, intent: EmbodimentIntent) -> None:
        self._intent = intent
        self.state_manager.set_state(resolve_face_state(intent), embodiment=intent)
