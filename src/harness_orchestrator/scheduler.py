"""Pure twice-daily schedule planning records for Harness orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable, Mapping
from zoneinfo import ZoneInfo


Metadata = Mapping[str, object]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SchedulerConfig:
    """Configuration for deterministic schedule calculations."""

    timezone_name: str = "Europe/Vienna"
    scheduled_times: tuple[time, ...] = (time(8, 0), time(20, 0))
    enabled: bool = True

    def __post_init__(self) -> None:
        normalized_times = tuple(sorted(tuple(self.scheduled_times)))
        object.__setattr__(self, "scheduled_times", normalized_times)


@dataclass(frozen=True)
class ScheduleCandidate:
    """Inert candidate record returned by schedule planning."""

    candidate_id: str
    trigger_type: str
    scheduled_for: datetime
    due: bool
    reason: str
    status: str
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Scheduler:
    """Calculate deterministic schedule candidates without side effects."""

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config or SchedulerConfig()
        self.clock = clock

    def upcoming(
        self,
        now: datetime | None = None,
        count: int = 2,
    ) -> tuple[ScheduleCandidate, ...]:
        """Return the next scheduled candidates at or after the supplied time."""

        if count <= 0 or not self.config.scheduled_times:
            return ()

        local_now = self._local_now(now)
        candidates: list[ScheduleCandidate] = []
        current_day = local_now.date()

        while len(candidates) < count:
            for scheduled_time in self.config.scheduled_times:
                scheduled_for = self._scheduled_datetime(current_day, scheduled_time)
                if scheduled_for >= local_now:
                    candidates.append(
                        self._scheduled_candidate(
                            scheduled_for=scheduled_for,
                            now=local_now,
                        )
                    )
                    if len(candidates) == count:
                        break
            current_day += timedelta(days=1)

        return tuple(candidates)

    def next_candidate(self, now: datetime | None = None) -> ScheduleCandidate | None:
        """Return the next scheduled candidate, if one is configured."""

        candidates = self.upcoming(now=now, count=1)
        if not candidates:
            return None
        return candidates[0]

    def due_candidates(
        self,
        now: datetime | None = None,
    ) -> tuple[ScheduleCandidate, ...]:
        """Return scheduled candidates for today whose planned time has arrived."""

        if not self.config.enabled:
            return ()

        local_now = self._local_now(now)
        due: list[ScheduleCandidate] = []
        for scheduled_time in self.config.scheduled_times:
            scheduled_for = self._scheduled_datetime(local_now.date(), scheduled_time)
            if scheduled_for <= local_now:
                due.append(
                    self._scheduled_candidate(
                        scheduled_for=scheduled_for,
                        now=local_now,
                    )
                )
        return tuple(due)

    def manual_candidate(
        self,
        now: datetime | None = None,
        reason: str = "manual",
    ) -> ScheduleCandidate:
        """Return an explicit inert manual trigger record."""

        local_now = self._local_now(now)
        return ScheduleCandidate(
            candidate_id=f"manual:{local_now.isoformat()}",
            trigger_type="manual",
            scheduled_for=local_now,
            due=False,
            reason=reason,
            status="manual",
            metadata={
                "timezone_name": self.config.timezone_name,
                "inert": True,
            },
        )

    def _local_now(self, now: datetime | None) -> datetime:
        if now is None:
            if self.clock is None:
                raise ValueError("now or clock is required")
            now = self.clock()

        zone = ZoneInfo(self.config.timezone_name)
        if now.tzinfo is None:
            return now.replace(tzinfo=zone)
        return now.astimezone(zone)

    def _scheduled_datetime(self, day, scheduled_time: time) -> datetime:
        zone = ZoneInfo(self.config.timezone_name)
        if scheduled_time.tzinfo is not None:
            scheduled_time = scheduled_time.replace(tzinfo=None)
        return datetime.combine(day, scheduled_time, tzinfo=zone)

    def _scheduled_candidate(
        self,
        *,
        scheduled_for: datetime,
        now: datetime,
    ) -> ScheduleCandidate:
        if not self.config.enabled:
            due = False
            status = "disabled"
            reason = "Schedule is disabled."
        else:
            due = scheduled_for <= now
            status = "due" if due else "pending"
            reason = (
                "Scheduled time has arrived."
                if due
                else "Scheduled time is pending."
            )

        return ScheduleCandidate(
            candidate_id=f"scheduled:{scheduled_for.isoformat()}",
            trigger_type="scheduled",
            scheduled_for=scheduled_for,
            due=due,
            reason=reason,
            status=status,
            metadata={
                "timezone_name": self.config.timezone_name,
                "scheduled_time": scheduled_for.timetz().isoformat(),
                "inert": True,
            },
        )
