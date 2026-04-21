from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class TimeSlot:
    slot_id: str
    days: tuple[int, ...]
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    scenario_id: str

    def contains_weekday(self, weekday: int) -> bool:
        return weekday in self.days

    def to_key(self, day: datetime) -> str:
        return f"{day.date().isoformat()}|{self.slot_id}|{self.start_hour:02d}:{self.start_minute:02d}-{self.end_hour:02d}:{self.end_minute:02d}"


def build_slots(raw_slots: tuple[tuple[tuple[int, ...], int, int, int, int], ...]) -> tuple[TimeSlot, ...]:
    return tuple(
        TimeSlot(
            slot_id=f"slot_{index}",
            days=days,
            start_hour=start_hour,
            start_minute=start_minute,
            end_hour=end_hour,
            end_minute=end_minute,
            scenario_id="default",
        )
        for index, (days, start_hour, start_minute, end_hour, end_minute) in enumerate(raw_slots)
    )


def make_dt(base: datetime, hour: int, minute: int, second: int = 0) -> datetime:
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)


def random_datetime_in_slot(day: datetime, slot: TimeSlot, now: datetime | None = None) -> datetime | None:
    start_dt = make_dt(day, slot.start_hour, slot.start_minute)
    end_dt = make_dt(day, slot.end_hour, slot.end_minute)

    if now is not None and day.date() == now.date() and now > start_dt:
        start_dt = now.replace(microsecond=0)

    if start_dt >= end_dt:
        return None

    delta_seconds = int((end_dt - start_dt).total_seconds())
    if delta_seconds <= 0:
        return None

    return start_dt + timedelta(seconds=random.randint(0, delta_seconds - 1))


def pick_next_execution(
    now: datetime,
    slots: tuple[TimeSlot, ...],
    lookahead_days: int = 10,
) -> tuple[datetime, TimeSlot, datetime]:
    candidates: list[tuple[datetime, TimeSlot, datetime]] = []

    for day_offset in range(lookahead_days):
        day = (now + timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        weekday = day.weekday()

        for slot in slots:
            if not slot.contains_weekday(weekday):
                continue

            candidate = random_datetime_in_slot(day, slot, now=now)
            if candidate is not None and candidate > now:
                candidates.append((candidate, slot, day))

        if candidates:
            return min(candidates, key=lambda item: item[0])

    raise RuntimeError("Aucun creneau futur valide trouve.")


def find_next_pending_execution(
    now: datetime,
    slots: tuple[TimeSlot, ...],
    is_slot_executed,
    lookahead_days: int = 10,
) -> tuple[datetime, TimeSlot, datetime]:
    probe = now
    for _ in range(len(slots) * lookahead_days + 1):
        next_run, slot, day = pick_next_execution(probe, slots, lookahead_days=lookahead_days)
        slot_key = slot.to_key(day)
        if not is_slot_executed(slot_key):
            return next_run, slot, day

        probe = day.replace(
            hour=slot.end_hour,
            minute=slot.end_minute,
            second=1,
            microsecond=0,
        )

    raise RuntimeError("Aucun creneau en attente trouve.")


def format_remaining(seconds: int) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
