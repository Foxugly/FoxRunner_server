import unittest

from app.config import TaskConfig
from app.logger import Logger
from scenarios.events import StepEvent
from scenarios.loader import ScenarioData, ScenarioDefinition, ScenarioStep
from scenarios.runner import run_task


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


if __name__ == "__main__":
    unittest.main()
