import unittest

from scenarios.events import StepEvent


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


if __name__ == "__main__":
    unittest.main()
