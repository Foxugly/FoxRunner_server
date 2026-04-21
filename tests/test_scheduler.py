import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scheduler.model import TimeSlot, find_next_pending_execution, format_remaining


class SchedulerTests(unittest.TestCase):
    def test_format_remaining(self):
        self.assertEqual(format_remaining(3661), "01:01:01")

    def test_find_next_pending_execution_skips_executed(self):
        tz = ZoneInfo("Europe/Brussels")
        now = datetime(2026, 4, 6, 8, 0, 0, tzinfo=tz)
        slots = (
            TimeSlot("morning", (0,), 8, 0, 8, 15, "scenario"),
            TimeSlot("evening", (0,), 17, 30, 18, 0, "scenario"),
        )

        executed = {"2026-04-06|morning|08:00-08:15"}
        next_run, slot, _ = find_next_pending_execution(now, slots, executed.__contains__)
        self.assertEqual(slot.slot_id, "evening")
        self.assertGreater(next_run.hour, 8)
