from __future__ import annotations

import unittest

from mira.actions.action_executor import ActionExecutor
from mira.actions.action_models import ActionRequest, ActionResult
from mira.actions.action_registry import ActionRegistry


class RecordingEventBus:
    def __init__(self):
        self.events = []

    def emit(self, event_name, payload=None):
        self.events.append((event_name, payload))


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
            [event_name for event_name, _ in event_bus.events],
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
        self.assertEqual(
            [event_name for event_name, _ in event_bus.events],
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
        self.assertEqual(
            [event_name for event_name, _ in event_bus.events],
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
                    [event_name for event_name, _ in event_bus.events],
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

    def test_registry_rejects_invalid_registration(self):
        registry = ActionRegistry()

        with self.assertRaises(ValueError):
            registry.register("", lambda parameters: None)

        with self.assertRaises(TypeError):
            registry.register("not_callable", None)


if __name__ == "__main__":
    unittest.main()
