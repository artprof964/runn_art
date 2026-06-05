"""Pure preflight summary for future Harness runtime integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


DEFAULT_REQUIRED_GATES = (
    "policy",
    "evidence",
    "ai-art-safety",
    "human-review",
    "manual-run-final",
)
DEFAULT_REQUIRED_DEPENDENCY_TYPES = ("evidence",)
DEFAULT_REQUIRED_AUDIT_EVENT_TYPES = (
    "gate:policy",
    "gate:evidence",
    "gate:ai-art-safety",
    "gate:human-review",
    "approval-audit",
    "gate:manual-run-final",
)
REDACTED = "<redacted>"
_SECRET_TERMS = ("key", "token", "secret", "password")
_DONE_STATUSES = {"done", "complete", "completed", "closed"}
_READY_STATUSES = {"ready", "passed", "complete", "completed", "done"}


@dataclass(frozen=True)
class RuntimeIntegrationPreflightRequirements:
    """Explicit requirements for a caller-provided runtime preflight snapshot."""

    required_gates: tuple[str, ...] = DEFAULT_REQUIRED_GATES
    required_dependency_types: tuple[str, ...] = DEFAULT_REQUIRED_DEPENDENCY_TYPES
    required_audit_event_types: tuple[str, ...] = DEFAULT_REQUIRED_AUDIT_EVENT_TYPES
    require_no_unfinished_tasks: bool = True
    require_maraca_readiness: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "required_gates": _redacted_tuple(self.required_gates),
            "required_dependency_types": _redacted_tuple(self.required_dependency_types),
            "required_audit_event_types": _redacted_tuple(
                self.required_audit_event_types,
            ),
            "require_no_unfinished_tasks": self.require_no_unfinished_tasks,
            "require_maraca_readiness": self.require_maraca_readiness,
        }


@dataclass(frozen=True)
class RuntimeIntegrationPreflightSummary:
    """Plain preflight result built only from caller-provided data."""

    ready: bool
    status: str
    blockers: tuple[str, ...]
    requirements: RuntimeIntegrationPreflightRequirements
    summary: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "requirements": self.requirements.to_dict(),
            "summary": asdict(self)["summary"],
        }


def evaluate_runtime_integration_preflight(
    *,
    ledger_snapshot: object | None,
    maraca_readiness: object | None = None,
    requirements: RuntimeIntegrationPreflightRequirements | None = None,
    work_id: str | None = None,
) -> RuntimeIntegrationPreflightSummary:
    """Evaluate future runtime readiness from explicit supplied records only."""

    resolved = requirements or RuntimeIntegrationPreflightRequirements()
    blockers: list[str] = []
    snapshot = _plain_mapping(ledger_snapshot)
    readiness = _plain_mapping(maraca_readiness)

    blockers.extend(_requirement_name_blockers("gate", resolved.required_gates))
    blockers.extend(
        _requirement_name_blockers(
            "dependency",
            resolved.required_dependency_types,
        ),
    )
    blockers.extend(
        _requirement_name_blockers("audit-event", resolved.required_audit_event_types),
    )

    if snapshot is None:
        blockers.append("missing-ledger-snapshot")
        run_id = ""
        expected_work_id = str(work_id or "")
        gate_records: tuple[Mapping[str, Any], ...] = ()
        dependency_records: tuple[Mapping[str, Any], ...] = ()
        audit_records: tuple[Mapping[str, Any], ...] = ()
        task_records: tuple[Mapping[str, Any], ...] = ()
    else:
        run_id = _text(snapshot.get("run_id"))
        expected_work_id = str(work_id or run_id)
        if not run_id:
            blockers.append("missing-run-id")
        if work_id is not None and run_id and run_id != work_id:
            blockers.append("run-id-work-id-mismatch")

        gate_records = _record_tuple(snapshot.get("gate_decisions"), "gate", blockers)
        dependency_records = _record_tuple(
            snapshot.get("dependencies"),
            "dependency",
            blockers,
        )
        audit_records = _record_tuple(snapshot.get("audit_events"), "audit-event", blockers)
        task_records = _record_tuple(snapshot.get("tasks"), "task", blockers)

        _check_record_work_ids(gate_records, expected_work_id, "gate", blockers)
        _check_record_work_ids(
            dependency_records,
            expected_work_id,
            "dependency",
            blockers,
        )
        _check_record_work_ids(audit_records, expected_work_id, "audit-event", blockers)
        _check_record_work_ids(task_records, expected_work_id, "task", blockers)

    _check_required_gates(gate_records, resolved.required_gates, blockers)
    _check_required_dependencies(
        dependency_records,
        resolved.required_dependency_types,
        blockers,
    )
    _check_required_audit_events(
        audit_records,
        resolved.required_audit_event_types,
        blockers,
    )
    if resolved.require_no_unfinished_tasks:
        _check_unfinished_tasks(task_records, blockers)
    _check_maraca_readiness(readiness, resolved.require_maraca_readiness, blockers)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    ready = not deduped_blockers
    summary = {
        "run_id": run_id,
        "work_id": expected_work_id,
        "gates": _summarize_gates(gate_records),
        "dependencies": _summarize_dependencies(dependency_records),
        "audit_events": _summarize_audit_events(audit_records),
        "unfinished_tasks": _summarize_unfinished_tasks(task_records),
        "maraca_readiness": _redact(readiness or {"provided": False}),
    }
    return RuntimeIntegrationPreflightSummary(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped_blockers,
        requirements=resolved,
        summary=_redact(summary),
    )


def _plain_mapping(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return dict(mapped)
    return None


def _record_tuple(
    value: object,
    kind: str,
    blockers: list[str],
) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        blockers.append(f"malformed-{kind}-records")
        return ()
    records: list[Mapping[str, Any]] = []
    for item in value:
        mapped = _plain_mapping(item)
        if mapped is None:
            blockers.append(f"malformed-{kind}-record")
            continue
        records.append(mapped)
    return tuple(records)


def _check_record_work_ids(
    records: tuple[Mapping[str, Any], ...],
    expected_work_id: str,
    kind: str,
    blockers: list[str],
) -> None:
    if not expected_work_id:
        return
    for record in records:
        record_work_id = _text(record.get("work_id"))
        if not record_work_id:
            blockers.append(f"missing-{kind}-work-id")
        elif record_work_id != expected_work_id:
            blockers.append(f"{kind}-work-id-mismatch")


def _check_required_gates(
    records: tuple[Mapping[str, Any], ...],
    required: tuple[str, ...],
    blockers: list[str],
) -> None:
    by_name = {_text(record.get("gate_name")): record for record in records}
    for gate_name in required:
        if not _valid_requirement_name(gate_name):
            continue
        record = by_name.get(gate_name)
        if record is None:
            blockers.append(f"missing-gate:{gate_name}")
            continue
        if not bool(record.get("passed")):
            blockers.append(f"blocked-gate:{gate_name}")


def _check_required_dependencies(
    records: tuple[Mapping[str, Any], ...],
    required: tuple[str, ...],
    blockers: list[str],
) -> None:
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_type.setdefault(_text(record.get("dependency_type")), []).append(record)

    for dependency_type in required:
        if not _valid_requirement_name(dependency_type):
            continue
        matching = by_type.get(dependency_type, [])
        if not matching:
            blockers.append(f"missing-dependency:{dependency_type}")
            continue
        if not any(_status_ready(record.get("status")) for record in matching):
            blockers.append(f"blocked-dependency:{dependency_type}")


def _check_required_audit_events(
    records: tuple[Mapping[str, Any], ...],
    required: tuple[str, ...],
    blockers: list[str],
) -> None:
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_type.setdefault(_text(record.get("event_type")), []).append(record)

    for event_type in required:
        if not _valid_requirement_name(event_type):
            continue
        matching = by_type.get(event_type, [])
        if not matching:
            blockers.append(f"missing-audit-event:{event_type}")
            continue
        if not any(_status_ready(record.get("status")) for record in matching):
            blockers.append(f"blocked-audit-event:{event_type}")


def _check_unfinished_tasks(
    records: tuple[Mapping[str, Any], ...],
    blockers: list[str],
) -> None:
    for record in records:
        status = _text(record.get("status", "open")).lower()
        if status not in _DONE_STATUSES:
            task_id = _text(record.get("task_id")) or "unknown"
            blockers.append(f"unfinished-task:{task_id}")


def _check_maraca_readiness(
    readiness: Mapping[str, Any] | None,
    required: bool,
    blockers: list[str],
) -> None:
    if readiness is None:
        if required:
            blockers.append("missing-maraca-readiness")
        return
    if not bool(readiness.get("ready")):
        blockers.append("blocked-maraca-readiness")


def _summarize_gates(records: tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "gate_name": _text(record.get("gate_name")),
            "passed": bool(record.get("passed")),
            "decision_id": _text(record.get("decision_id")),
            "metadata": _redact(record.get("metadata", {})),
        }
        for record in records
    )


def _summarize_dependencies(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "dependency_id": _text(record.get("dependency_id")),
            "dependency_type": _text(record.get("dependency_type")),
            "status": _text(record.get("status")),
            "metadata": _redact(record.get("metadata", {})),
        }
        for record in records
    )


def _summarize_audit_events(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "event_id": _text(record.get("event_id")),
            "event_type": _text(record.get("event_type")),
            "status": _text(record.get("status")),
            "metadata": _redact(record.get("metadata", {})),
        }
        for record in records
    )


def _summarize_unfinished_tasks(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "task_id": _text(record.get("task_id")),
            "status": _text(record.get("status", "open")),
            "metadata": _redact(record.get("metadata", {})),
        }
        for record in records
        if _text(record.get("status", "open")).lower() not in _DONE_STATUSES
    )


def _requirement_name_blockers(kind: str, names: tuple[str, ...]) -> tuple[str, ...]:
    blockers: list[str] = []
    for name in names:
        if not isinstance(name, str):
            blockers.append(f"malformed-{kind}-requirement")
        elif not name.strip():
            blockers.append(f"blank-{kind}-requirement")
        elif _is_secret_like(name):
            blockers.append(f"redacted-{kind}-requirement")
    return tuple(blockers)


def _valid_requirement_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _is_secret_like(value)


def _status_ready(value: object) -> bool:
    return _text(value).lower() in _READY_STATUSES


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _redacted_tuple(values: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(REDACTED if _is_secret_like(value) else value for value in values)


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            if _is_secret_like(key):
                redacted[REDACTED] = REDACTED
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, list):
        return tuple(_redact(item) for item in value)
    if _is_secret_like(value):
        return REDACTED
    return value


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)
