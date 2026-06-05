"""Deterministic in-memory run ledger for Harness orchestration status."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision


Metadata = Mapping[str, Any]


@dataclass(frozen=True)
class DependencyRecord:
    """Plain reference to a dependency needed by a governed run."""

    dependency_id: str
    work_id: str
    reference: str
    order: int
    dependency_type: str = "unspecified"
    required: bool = True
    status: str = "pending"
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditEvent:
    """Plain audit/status event captured for a governed run."""

    event_id: str
    work_id: str
    event_type: str
    status: str
    message: str = ""
    occurred_at: str = ""
    actor: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskStatus:
    """Plain task status record for unfinished and completed run work."""

    task_id: str
    work_id: str
    title: str
    status: str = "open"
    blockers: tuple[str, ...] = ()
    assigned_to: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def unfinished(self) -> bool:
        return self.status not in {"done", "complete", "completed", "closed"}


@dataclass(frozen=True)
class RunLedgerSnapshot:
    """Immutable snapshot of all ledger records for one run."""

    run_id: str
    gate_decisions: tuple[GateDecision, ...] = ()
    dependencies: tuple[DependencyRecord, ...] = ()
    audit_events: tuple[AuditEvent, ...] = ()
    tasks: tuple[TaskStatus, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "gate_decisions": tuple(decision.to_dict() for decision in self.gate_decisions),
            "dependencies": tuple(record.to_dict() for record in self.dependencies),
            "audit_events": tuple(event.to_dict() for event in self.audit_events),
            "tasks": tuple(task.to_dict() for task in self.tasks),
            "metadata": dict(self.metadata),
        }

    def unfinished_tasks(self) -> tuple[TaskStatus, ...]:
        return tuple(task for task in self.tasks if task.unfinished)


class RunLedger:
    """Collect run status records in memory and persist only by explicit path."""

    def __init__(
        self,
        run_id: str,
        *,
        gate_decisions: tuple[GateDecision, ...] = (),
        dependencies: tuple[DependencyRecord, ...] = (),
        audit_events: tuple[AuditEvent, ...] = (),
        tasks: tuple[TaskStatus, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self._gate_decisions = list(gate_decisions)
        self._dependencies = list(dependencies)
        self._audit_events = list(audit_events)
        self._tasks = list(tasks)
        self.metadata = dict(metadata or {})

    def record_gate_decision(
        self,
        decision: GateDecision | None = None,
        **kwargs: Any,
    ) -> GateDecision:
        record = decision or GateDecision(**kwargs)
        self._gate_decisions.append(record)
        return record

    def record_dependency(
        self,
        dependency: DependencyRecord | None = None,
        **kwargs: Any,
    ) -> DependencyRecord:
        record = dependency or DependencyRecord(**kwargs)
        self._dependencies.append(record)
        self._dependencies.sort(key=lambda item: (item.order, item.dependency_id))
        return record

    def record_audit_event(
        self,
        event: AuditEvent | None = None,
        **kwargs: Any,
    ) -> AuditEvent:
        record = event or AuditEvent(**kwargs)
        self._audit_events.append(record)
        return record

    def record_task(
        self,
        task: TaskStatus | None = None,
        **kwargs: Any,
    ) -> TaskStatus:
        record = task or TaskStatus(**kwargs)
        self._tasks.append(record)
        return record

    def unfinished_tasks(self) -> tuple[TaskStatus, ...]:
        return self.snapshot().unfinished_tasks()

    def snapshot(self) -> RunLedgerSnapshot:
        return RunLedgerSnapshot(
            run_id=self.run_id,
            gate_decisions=tuple(self._gate_decisions),
            dependencies=tuple(self._dependencies),
            audit_events=tuple(self._audit_events),
            tasks=tuple(self._tasks),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot().to_dict()

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RunLedger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunLedger":
        return cls(
            run_id=str(data["run_id"]),
            gate_decisions=tuple(
                _gate_decision(record) for record in data.get("gate_decisions", ())
            ),
            dependencies=tuple(
                _dependency_record(record) for record in data.get("dependencies", ())
            ),
            audit_events=tuple(
                _audit_event(record) for record in data.get("audit_events", ())
            ),
            tasks=tuple(_task_status(record) for record in data.get("tasks", ())),
            metadata=_metadata(data.get("metadata")),
        )


def _gate_decision(record: Mapping[str, Any]) -> GateDecision:
    return GateDecision(
        decision_id=str(record["decision_id"]),
        work_id=str(record["work_id"]),
        gate_name=str(record["gate_name"]),
        passed=bool(record["passed"]),
        reason=str(record["reason"]),
        blockers=_strings(record.get("blockers")),
        evidence_bundle_id=_optional_text(record.get("evidence_bundle_id")),
        reviewer=_optional_text(record.get("reviewer")),
        metadata=_metadata(record.get("metadata")),
    )


def _dependency_record(record: Mapping[str, Any]) -> DependencyRecord:
    return DependencyRecord(
        dependency_id=str(record["dependency_id"]),
        work_id=str(record["work_id"]),
        reference=str(record["reference"]),
        order=int(record["order"]),
        dependency_type=str(record.get("dependency_type", "unspecified")),
        required=bool(record.get("required", True)),
        status=str(record.get("status", "pending")),
        metadata=_metadata(record.get("metadata")),
    )


def _audit_event(record: Mapping[str, Any]) -> AuditEvent:
    return AuditEvent(
        event_id=str(record["event_id"]),
        work_id=str(record["work_id"]),
        event_type=str(record["event_type"]),
        status=str(record["status"]),
        message=str(record.get("message", "")),
        occurred_at=str(record.get("occurred_at", "")),
        actor=_optional_text(record.get("actor")),
        metadata=_metadata(record.get("metadata")),
    )


def _task_status(record: Mapping[str, Any]) -> TaskStatus:
    return TaskStatus(
        task_id=str(record["task_id"]),
        work_id=str(record["work_id"]),
        title=str(record["title"]),
        status=str(record.get("status", "open")),
        blockers=_strings(record.get("blockers")),
        assigned_to=_optional_text(record.get("assigned_to")),
        metadata=_metadata(record.get("metadata")),
    )


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
