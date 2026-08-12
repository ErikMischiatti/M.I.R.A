from __future__ import annotations

import unittest

# A real EventBus subclass that records to `emitted` and still dispatches. No
# test here subscribes, so dispatch delivers to nobody and the recording is the
# only observable effect — which is what these tests assert on.
from doubles import RecordingEventBus

from mira.actions.action_contracts import ACTION_CONTRACTS, build_action_contract_registry
from mira.actions.action_executor import ActionExecutor
from mira.actions.action_models import ActionContract, ActionRequest, ActionResult
from mira.actions.action_registry import ActionRegistry


class ActionExecutorTests(unittest.TestCase):
    def test_registered_action_executes_successfully(self):
        registry = ActionRegistry()
        event_bus = RecordingEventBus()

        def handler(parameters):
            return ActionResult(
                success=True,
                action_name="echo",
                message="ok",
                data={"text": parameters["text"]},
            )

        registry.register("echo", handler)
        executor = ActionExecutor(registry, event_bus)

        result = executor.execute(ActionRequest("echo", {"text": "hello"}))

        self.assertEqual(
            result,
            ActionResult(
                success=True,
                action_name="echo",
                message="ok",
                data={"text": "hello"},
            ),
        )
        self.assertEqual(
            [event_name for event_name, _ in event_bus.emitted],
            ["action_started", "action_completed"],
        )

    def test_unknown_action_is_rejected(self):
        registry = ActionRegistry()
        event_bus = RecordingEventBus()
        executor = ActionExecutor(registry, event_bus)

        result = executor.execute(ActionRequest("missing_action"))

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "missing_action")
        self.assertIn("non disponibile", result.message)
        self.assertEqual(result.data["reason"], "action_unknown")
        self.assertEqual(
            [event_name for event_name, _ in event_bus.emitted],
            ["action_started", "action_failed"],
        )

    def test_action_exception_returns_failure_result(self):
        registry = ActionRegistry()
        event_bus = RecordingEventBus()

        def handler(parameters):
            raise RuntimeError("boom")

        registry.register("explode", handler)
        executor = ActionExecutor(registry, event_bus)

        result = executor.execute(ActionRequest("explode"))

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "explode")
        self.assertIn("boom", result.message)
        self.assertEqual(result.data["reason"], "action_exception")
        self.assertEqual(
            [event_name for event_name, _ in event_bus.emitted],
            ["action_started", "action_failed"],
        )

    def test_invalid_request_is_handled_safely(self):
        cases = [
            (object(), "request_type"),
            (ActionRequest(""), "action_name"),
            (ActionRequest("valid_name", parameters=[]), "parameters"),
        ]

        for request, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                registry = ActionRegistry()
                event_bus = RecordingEventBus()
                executor = ActionExecutor(registry, event_bus)

                result = executor.execute(request)

                self.assertFalse(result.success)
                self.assertEqual(result.data["reason"], expected_reason)
                self.assertEqual(
                    [event_name for event_name, _ in event_bus.emitted],
                    ["action_failed"],
                )

    def test_executor_does_not_bypass_registry(self):
        registry = ActionRegistry()
        called = False

        def handler(parameters):
            nonlocal called
            called = True
            return ActionResult(success=True, action_name="hidden", message="ran")

        executor = ActionExecutor(registry)
        request = ActionRequest("hidden", parameters={"handler": handler})

        result = executor.execute(request)

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "hidden")
        self.assertFalse(called)

    def test_handler_result_is_normalized_to_requested_action_name(self):
        registry = ActionRegistry()

        def handler(parameters):
            return ActionResult(
                success=True,
                action_name="different_action",
                message="ok",
                data={"value": 1},
            )

        registry.register("registered_action", handler)
        executor = ActionExecutor(registry)

        result = executor.execute(ActionRequest("registered_action"))

        self.assertTrue(result.success)
        self.assertEqual(result.action_name, "registered_action")
        self.assertEqual(result.data, {"value": 1})

    def test_registry_exposes_action_contract_metadata(self):
        registry = ActionRegistry()
        contract = ActionContract(
            name="echo",
            compatible_intents=frozenset({"echo_request"}),
            required_params={"text": str},
        )

        registry.register(
            "echo",
            lambda parameters: ActionResult(True, "echo", "ok"),
            contract=contract,
        )

        self.assertTrue(registry.has_contract("echo"))
        self.assertEqual(registry.list_contract_names(), ["echo"])
        self.assertEqual(registry.actions_for_intent("echo_request"), {"echo"})
        self.assertEqual(registry.required_params_for("echo"), {"text": str})
        self.assertTrue(registry.is_action_compatible_with_intent("echo", "echo_request"))

    def test_builtin_action_contracts_keep_required_params_and_intent_compatibility(self):
        registry = build_action_contract_registry()

        self.assertEqual(registry.required_params_for("echo_text"), {"text": str})
        self.assertEqual(registry.required_params_for("open_url"), {"url": str})
        self.assertEqual(registry.required_params_for("open_app"), {"app_name": str})
        self.assertEqual(registry.required_params_for("show_notification"), {"text": str})
        self.assertEqual(registry.required_params_for("open_directory"), {"directory": str})
        self.assertEqual(registry.actions_for_intent("unknown"), set())
        self.assertEqual(registry.actions_for_intent("time_query"), {"get_time"})
        self.assertEqual(registry.actions_for_intent("open_url_request"), {"open_url"})
        self.assertEqual(registry.actions_for_intent("project_path_query"), {"get_project_path"})
        self.assertEqual(set(registry.list_contract_names()), set(ACTION_CONTRACTS.keys()))


    def test_registry_rejects_invalid_registration(self):
        registry = ActionRegistry()

        with self.assertRaises(ValueError):
            registry.register("", lambda parameters: None)

        with self.assertRaises(TypeError):
            registry.register("not_callable", None)


if __name__ == "__main__":
    unittest.main()
