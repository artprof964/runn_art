import inspect
import unittest
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from harness_orchestrator import scheduler as scheduler_module
from harness_orchestrator.scheduler import Scheduler, SchedulerConfig


class SchedulerTests(unittest.TestCase):
    def test_defaults_include_vienna_morning_and_evening(self) -> None:
        config = SchedulerConfig()
        planner = Scheduler(config=config)

        upcoming = planner.upcoming(datetime(2026, 6, 3, 7, 30), count=2)

        self.assertEqual(config.timezone_name, "Europe/Vienna")
        self.assertEqual(config.scheduled_times, (time(8, 0), time(20, 0)))
        self.assertEqual(upcoming[0].scheduled_for.hour, 8)
        self.assertEqual(upcoming[1].scheduled_for.hour, 20)
        self.assertEqual(upcoming[0].scheduled_for.tzinfo, ZoneInfo("Europe/Vienna"))
        self.assertFalse(upcoming[0].due)

    def test_configurable_times_and_timezone_override(self) -> None:
        config = SchedulerConfig(
            timezone_name="UTC",
            scheduled_times=(time(14, 15), time(5, 45)),
        )
        planner = Scheduler(config=config)

        upcoming = planner.upcoming(datetime(2026, 6, 3, 6, 0), count=2)

        self.assertEqual(config.scheduled_times, (time(5, 45), time(14, 15)))
        self.assertEqual(upcoming[0].scheduled_for, datetime(2026, 6, 3, 14, 15, tzinfo=timezone.utc))
        self.assertEqual(upcoming[1].scheduled_for, datetime(2026, 6, 4, 5, 45, tzinfo=timezone.utc))

    def test_boundary_cases_for_default_schedule(self) -> None:
        planner = Scheduler()

        before_morning = datetime(2026, 6, 3, 7, 59, 59)
        at_morning = datetime(2026, 6, 3, 8, 0)
        between_runs = datetime(2026, 6, 3, 12, 0)
        at_evening = datetime(2026, 6, 3, 20, 0)
        after_evening = datetime(2026, 6, 3, 20, 1)

        self.assertEqual(planner.due_candidates(before_morning), ())
        self.assertEqual(
            [candidate.scheduled_for.hour for candidate in planner.due_candidates(at_morning)],
            [8],
        )
        self.assertEqual(planner.next_candidate(between_runs).scheduled_for.hour, 20)
        self.assertFalse(planner.next_candidate(between_runs).due)
        self.assertEqual(
            [candidate.scheduled_for.hour for candidate in planner.due_candidates(at_evening)],
            [8, 20],
        )
        self.assertEqual(planner.next_candidate(after_evening).scheduled_for.date().isoformat(), "2026-06-04")
        self.assertEqual(planner.next_candidate(after_evening).scheduled_for.hour, 8)

    def test_next_day_rollover_is_deterministic(self) -> None:
        planner = Scheduler()

        upcoming = planner.upcoming(datetime(2026, 6, 3, 21, 0), count=3)

        self.assertEqual(
            [(candidate.scheduled_for.date().isoformat(), candidate.scheduled_for.hour) for candidate in upcoming],
            [("2026-06-04", 8), ("2026-06-04", 20), ("2026-06-05", 8)],
        )

    def test_empty_schedule_returns_no_scheduled_candidates(self) -> None:
        planner = Scheduler(SchedulerConfig(scheduled_times=()))
        now = datetime(2026, 6, 3, 7, 30)

        self.assertEqual(planner.upcoming(now, count=2), ())
        self.assertIsNone(planner.next_candidate(now))
        self.assertEqual(planner.due_candidates(now), ())

    def test_aware_now_converts_to_configured_timezone(self) -> None:
        planner = Scheduler()

        candidate = planner.next_candidate(
            datetime(2026, 6, 3, 5, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(candidate.scheduled_for.hour, 8)
        self.assertEqual(candidate.scheduled_for.tzinfo, ZoneInfo("Europe/Vienna"))

    def test_dst_offset_uses_zoneinfo(self) -> None:
        planner = Scheduler()

        summer = planner.next_candidate(datetime(2026, 6, 3, 7, 0))
        winter = planner.next_candidate(datetime(2026, 12, 3, 7, 0))

        self.assertEqual(summer.scheduled_for.utcoffset().total_seconds(), 7200)
        self.assertEqual(winter.scheduled_for.utcoffset().total_seconds(), 3600)

    def test_clock_can_be_injected(self) -> None:
        planner = Scheduler(clock=lambda: datetime(2026, 6, 3, 20, 0))

        candidates = planner.due_candidates()

        self.assertEqual([candidate.scheduled_for.hour for candidate in candidates], [8, 20])

    def test_disabled_scheduler_has_no_due_scheduled_candidates(self) -> None:
        planner = Scheduler(SchedulerConfig(enabled=False))

        scheduled = planner.next_candidate(datetime(2026, 6, 3, 8, 0))
        due = planner.due_candidates(datetime(2026, 6, 3, 8, 0))
        manual = planner.manual_candidate(datetime(2026, 6, 3, 8, 0), reason="operator")

        self.assertEqual(due, ())
        self.assertFalse(scheduled.due)
        self.assertEqual(scheduled.status, "disabled")
        self.assertEqual(manual.trigger_type, "manual")
        self.assertEqual(manual.status, "manual")
        self.assertFalse(manual.due)
        self.assertTrue(manual.metadata["inert"])

    def test_manual_mode_returns_explicit_inert_record(self) -> None:
        candidate = Scheduler().manual_candidate(
            datetime(2026, 6, 3, 9, 30, tzinfo=ZoneInfo("Europe/Vienna")),
            reason="human requested review",
        )

        self.assertTrue(candidate.candidate_id.startswith("manual:"))
        self.assertEqual(candidate.trigger_type, "manual")
        self.assertEqual(candidate.reason, "human requested review")
        self.assertEqual(candidate.status, "manual")
        self.assertEqual(candidate.metadata["timezone_name"], "Europe/Vienna")
        self.assertTrue(candidate.metadata["inert"])

    def test_source_guard_excludes_runtime_terms(self) -> None:
        blocked_terms = (
            "sl" + "eep",
            "thr" + "ead",
            "ti" + "mer",
            "re" + "quests",
            "ht" + "tpx",
            "so" + "cket",
            "sub" + "process",
            "file" + "system",
            "pub" + "lish",
            "soc" + "ial",
            "wat" + "ch",
            "ser" + "vice",
            "scr" + "ape",
        )
        sources = [
            inspect.getsource(scheduler_module),
            inspect.getsource(SchedulerTests),
        ]

        for source in sources:
            lowered = source.lower()
            for term in blocked_terms:
                self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
