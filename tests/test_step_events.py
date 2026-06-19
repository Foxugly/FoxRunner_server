import unittest

from app.config import TaskConfig
from app.logger import Logger
from scenarios.events import StepEvent
from scenarios.loader import ScenarioData, ScenarioDefinition, ScenarioStep
from scenarios.runner import _execute_scenario_step, run_task


class StepEventTests(unittest.TestCase):
    def test_step_event_defaults(self):
        ev = StepEvent(step_id="steps[0]", event_type="step_started", step_type="open_url")
        self.assertEqual(ev.level, "info")
        self.assertEqual(ev.message, "")
        self.assertIsNone(ev.traceback)
        self.assertEqual(ev.payload, {})

    def test_failed_event_carries_traceback_and_level(self):
        ev = StepEvent(
            step_id="steps[2]",
            event_type="step_failed",
            step_type="wait_for_element",
            level="error",
            message="boom",
            traceback="Traceback…",
            payload={"screenshot": "abc.png"},
        )
        self.assertEqual(ev.level, "error")
        self.assertEqual(ev.payload["screenshot"], "abc.png")


def _scn(steps):
    return ScenarioDefinition(scenario_id="s", description="", steps=tuple(steps))


class RunTaskEventTests(unittest.TestCase):
    def setUp(self):
        self.events: list[StepEvent] = []
        self.cfg = TaskConfig()
        self.data = ScenarioData(pushovers={}, networks={})

    def _run(self, scn):
        return run_task(
            self.cfg,
            Logger(debug_enabled=False),
            scenario=scn,
            scenario_data=self.data,
            dry_run=True,
            on_event=self.events.append,
        )

    def test_emits_started_then_succeeded_per_step(self):
        scn = _scn([ScenarioStep(type="notify", payload={"message": "hi"})])
        result = self._run(scn)
        self.assertTrue(result.success)
        kinds = [(e.step_id, e.event_type) for e in self.events]
        self.assertIn(("steps[0]", "step_started"), kinds)
        self.assertIn(("steps[0]", "step_succeeded"), kinds)

    def test_skipped_when_condition_false(self):
        scn = _scn([ScenarioStep(type="notify", payload={"message": "x"}, when="context_exists:never")])
        self._run(scn)
        kinds = [e.event_type for e in self.events if e.step_id == "steps[0]"]
        self.assertIn("step_skipped", kinds)
        self.assertNotIn("step_succeeded", kinds)

    def test_failed_carries_traceback(self):
        scn = _scn([ScenarioStep(type="format_context", payload={"key": "k"})])  # missing 'template'
        result = self._run(scn)
        self.assertFalse(result.success)
        failed = [e for e in self.events if e.event_type == "step_failed"]
        self.assertTrue(failed)
        self.assertTrue(failed[0].traceback)
        self.assertTrue(failed[0].message)

    def test_before_steps_emit_with_collection_id(self):
        scn = ScenarioDefinition(
            scenario_id="s",
            description="",
            before_steps=(ScenarioStep(type="notify", payload={"message": "b"}),),
            steps=(ScenarioStep(type="notify", payload={"message": "s"}),),
        )
        self._run(scn)
        ids = {e.step_id for e in self.events}
        self.assertIn("before_steps[0]", ids)
        self.assertIn("steps[0]", ids)

    def test_continue_on_error_step_is_not_green(self):
        # ``format_context`` without ``template`` raises; with
        # continue_on_error the run keeps going but the step must NOT render
        # green — it should emit a warning-level step_failed (continued=True).
        scn = _scn(
            [
                ScenarioStep(type="format_context", payload={"key": "k"}, continue_on_error=True),
                ScenarioStep(type="notify", payload={"message": "after"}),
            ]
        )
        result = self._run(scn)
        self.assertTrue(result.success)  # the run continued past the failure
        step0 = [e for e in self.events if e.step_id == "steps[0]"]
        types = {e.event_type for e in step0}
        self.assertIn("step_failed", types)
        self.assertNotIn("step_succeeded", types)
        failed = next(e for e in step0 if e.event_type == "step_failed")
        self.assertEqual(failed.level, "warning")
        self.assertTrue(failed.payload.get("continued"))
        # the following step still ran to success
        self.assertIn(("steps[1]", "step_succeeded"), [(e.step_id, e.event_type) for e in self.events])

    def test_group_children_emit_nested_events(self):
        scn = _scn(
            [
                ScenarioStep(
                    type="group",
                    payload={
                        "steps": [
                            ScenarioStep(type="set_context", payload={"key": "a", "value": "1"}),
                            ScenarioStep(type="set_context", payload={"key": "b", "value": "2"}),
                        ]
                    },
                )
            ]
        )
        self._run(scn)
        ids = {(e.step_id, e.event_type) for e in self.events}
        self.assertIn(("steps[0]>group[0]", "step_started"), ids)
        self.assertIn(("steps[0]>group[0]", "step_succeeded"), ids)
        self.assertIn(("steps[0]>group[1]", "step_started"), ids)
        self.assertIn(("steps[0]>group[1]", "step_succeeded"), ids)


class RetryEventTests(unittest.TestCase):
    def test_step_retrying_emitted_before_each_backoff(self):
        # A step that fails the first attempt then succeeds must emit a
        # ``step_retrying`` event (warning, payload.attempt) before the retry.
        events: list[StepEvent] = []
        attempts = {"n": 0}

        def flaky_handler(op_context, payload):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient")

        # ``set_context`` is an atomic step type; injecting a flaky handler
        # under that key lets us exercise the retry path deterministically.
        step = ScenarioStep(type="set_context", payload={}, retry=1)
        _execute_scenario_step(
            step,
            operation_registry={"set_context": flaky_handler},
            driver=None,
            config=TaskConfig(),
            logger=Logger(debug_enabled=False),
            notifier=None,
            network_check=None,
            network_check_by_key=None,
            scenario_data=ScenarioData(pushovers={}, networks={}),
            context={},
            dry_run=False,
            parallel_safe_steps=frozenset(),
            on_event=events.append,
            step_id="steps[0]",
        )
        retrying = [e for e in events if e.event_type == "step_retrying"]
        self.assertEqual(len(retrying), 1)
        self.assertEqual(retrying[0].level, "warning")
        self.assertEqual(retrying[0].payload.get("attempt"), 1)
        self.assertEqual(retrying[0].step_id, "steps[0]")
        self.assertEqual(attempts["n"], 2)  # failed once, succeeded on retry


if __name__ == "__main__":
    unittest.main()
