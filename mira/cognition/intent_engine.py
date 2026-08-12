from __future__ import annotations

from abc import ABC, abstractmethod

from mira.domain.models import IntentResult, UserInput


class IntentEngine(ABC):
    """Abstract contract for intent inference backends."""

    @abstractmethod
    def infer(self, user_input: UserInput) -> IntentResult:
        """Infer a normalized intent result from user input."""
        raise NotImplementedError
