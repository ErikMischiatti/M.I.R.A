from collections.abc import Callable
from typing import Any

from mira.actions.action_models import ActionContract, ActionResult


ActionHandler = Callable[[dict], ActionResult]


class ActionRegistry:
    def __init__(self):
        self._handlers: dict[str, ActionHandler] = {}
        self._contracts: dict[str, ActionContract] = {}

    def register(
        self,
        name: str,
        handler: ActionHandler,
        contract: ActionContract | None = None,
    ) -> None:
        self._validate_action_name(name)

        if not callable(handler):
            raise TypeError("Action handler must be callable.")

        self._handlers[name] = handler

        if contract is not None:
            if contract.name != name:
                raise ValueError("Action contract name must match action name.")
            self.register_contract(contract)

    def register_contract(self, contract: ActionContract) -> None:
        if not isinstance(contract, ActionContract):
            raise TypeError("Action contract must be an ActionContract.")

        self._validate_action_name(contract.name)

        if not isinstance(contract.compatible_intents, frozenset):
            raise TypeError("Action compatible intents must be a frozenset.")

        if not isinstance(contract.required_params, dict):
            raise TypeError("Action required params must be a dict.")

        for param_name, param_type in contract.required_params.items():
            if not isinstance(param_name, str) or not param_name.strip():
                raise ValueError("Action required param names must be non-empty strings.")
            if not isinstance(param_type, type):
                raise TypeError("Action required param types must be types.")

        self._contracts[contract.name] = contract

    def get(self, name: str) -> ActionHandler | None:
        return self._handlers.get(name)

    def get_contract(self, name: str) -> ActionContract | None:
        return self._contracts.get(name)

    def has(self, name: str) -> bool:
        return name in self._handlers

    def has_contract(self, name: str) -> bool:
        return name in self._contracts

    def list_actions(self) -> list[str]:
        return sorted(self._handlers.keys())

    def list_contracts(self) -> list[ActionContract]:
        return [self._contracts[name] for name in sorted(self._contracts.keys())]

    def list_contract_names(self) -> list[str]:
        return sorted(self._contracts.keys())

    def actions_for_intent(self, intent: str) -> set[str]:
        return {
            contract.name
            for contract in self._contracts.values()
            if intent in contract.compatible_intents
        }

    def required_params_for(self, action_name: str) -> dict[str, type]:
        contract = self.get_contract(action_name)
        if contract is None:
            return {}
        return dict(contract.required_params)

    def is_action_compatible_with_intent(self, action_name: str, intent: str) -> bool:
        contract = self.get_contract(action_name)
        if contract is None:
            return False
        return intent in contract.compatible_intents

    def _validate_action_name(self, name: Any) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Action name must be a non-empty string.")
