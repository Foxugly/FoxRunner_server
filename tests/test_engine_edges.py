from __future__ import annotations

import unittest

from scenarios.engine import EngineContext, execute_block_step, execute_parallel_steps, is_atomic_step
from scenarios.loader import ScenarioStep


class EngineEdgeTests(unittest.TestCase):
    def _engine(self, calls: list[tuple[str, object]] | None = None, fail_on: str | None = None) -> EngineContext:
        calls = calls if calls is not None else []

        def execute_scenario_step(step, *, driver, context, **kwargs):
            calls.append((step.type, driver))
            if step.type == fail_on:
                raise RuntimeError(f"failed {step.type}")
            context[step.type] = str(len(calls))
            return f"driver-{step.type}-{len(calls)}"

        return EngineContext(
            operation_registry={},
            execute_atomic_step=lambda *args, **kwargs: None,
            execute_scenario_step=execute_scenario_step,
            parallel_safe_steps=frozenset({"sleep", "notify", "set_context"}),
            driver="driver-0",
            config=None,
            logger=None,
            notifier=None,
            network_check=None,
            network_check_by_key=None,
            scenario_data=None,
            context={},
            dry_run=True,
        )

    def test_atomic_step_detection_and_unsupported_block(self):
        self.assertTrue(is_atomic_step("sleep"))
        self.assertFalse(is_atomic_step("group"))
        with self.assertRaises(ValueError):
            execute_block_step(ScenarioStep("unknown", {}), self._engine())

    def test_group_repeat_and_parallel_execute_children(self):
        calls: list[tuple[str, object]] = []
        engine = self._engine(calls)

        group_result = execute_block_step(
            ScenarioStep("group", {"steps": (ScenarioStep("sleep", {}), ScenarioStep("notify", {}))}),
            engine,
        )
        repeat_result = execute_block_step(
            ScenarioStep("repeat", {"times": 2, "steps": (ScenarioStep("set_context", {}),)}),
            engine,
        )
        parallel_result = execute_block_step(
            ScenarioStep("parallel", {"steps": (ScenarioStep("sleep", {}), ScenarioStep("notify", {}))}),
            engine,
        )

        self.assertEqual(group_result, "driver-notify-2")
        self.assertEqual(repeat_result, "driver-set_context-4")
        self.assertEqual(parallel_result, "driver-0")
        self.assertEqual([call[0] for call in calls], ["sleep", "notify", "set_context", "set_context", "sleep", "notify"])

    def test_parallel_rejects_unsupported_children(self):
        with self.assertRaises(ValueError):
            execute_parallel_steps((ScenarioStep("click", {}), ScenarioStep("sleep", {})), self._engine())

    def test_try_step_runs_catch_and_finally_or_reraises(self):
        calls: list[tuple[str, object]] = []
        engine = self._engine(calls, fail_on="sleep")
        result = execute_block_step(
            ScenarioStep(
                "try",
                {
                    "try_steps": (ScenarioStep("sleep", {}),),
                    "catch_steps": (ScenarioStep("notify", {}),),
                    "finally_steps": (ScenarioStep("set_context", {}),),
                },
            ),
            engine,
        )

        self.assertEqual(result, "driver-set_context-3")
        self.assertEqual(engine.context["error_message"], "failed sleep")
        self.assertEqual([call[0] for call in calls], ["sleep", "notify", "set_context"])

        with self.assertRaises(RuntimeError):
            execute_block_step(
                ScenarioStep("try", {"try_steps": (ScenarioStep("sleep", {}),), "catch_steps": (), "finally_steps": ()}),
                self._engine(fail_on="sleep"),
            )


if __name__ == "__main__":
    unittest.main()
