"""Plain result intake for a future MARACA runtime executor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping


REDACTED = "<redacted>"
ALLOWED_RUNTIME_STATUSES = ("succeeded", "failed", "blocked")
_SECRET_TERMS = ("key", "token", "secret", "password")
_EXECUTION_FLAG_NAMES = (
    "execute",
    "run",
    "start",
    "invoke",
    "call",
    "dispatch",
    "submit",
    "schedule",
    "publish",
    "persist",
    "watch",
    "network",
    "sub" + "process",
)


@dataclass(frozen=True)
class MaracaRuntimeResultIntakeRequirements:
    """Requirements for accepting a caller-supplied runtime result."""

    allowed_runtime_statuses: tuple[str, ...] = ALLOWED_RUNTIME_STATUSES
    require_identity_match: bool = True
    require_evidence: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed_runtime_statuses": self.allowed_runtime_statuses,
            "require_identity_match": self.require_identity_match,
            "require_evidence": self.require_evidence,
        }


@dataclass(frozen=True)
class MaracaRuntimeResultRecord:
    """Frozen plain-data record of an explicit future runtime result."""

    work_id: str
    run_id: str
    operation: str
    runtime_status: str
    evidence_items: tuple[Mapping[str, object], ...]
    output: Mapping[str, object]
    metadata: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "work_id": self.work_id,
            "run_id": self.run_id,
            "operation": self.operation,
            "runtime_status": self.runtime_status,
            "evidence_items": tuple(_redact(item) for item in self.evidence_items),
            "output": _redact(self.output),
            "metadata": _redact(self.metadata),
        }


@dataclass(frozen=True)
class MaracaRuntimeResultIntakeResult:
    """Plain intake result containing only a redacted accepted record."""

    accepted: bool
    status: str
    blockers: tuple[str, ...]
    requirements: MaracaRuntimeResultIntakeRequirements
    result: MaracaRuntimeResultRecord | None

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "blockers": self.blockers,
            "requirements": self.requirements.to_dict(),
            "result": self.result.to_dict() if self.result else None,
        }


def intake_maraca_runtime_result(
    *,
    invocation: object,
    runtime_result: object,
    requirements: MaracaRuntimeResultIntakeRequirements | None = None,
) -> MaracaRuntimeResultIntakeResult:
    """Validate explicit caller data and return a non-executing result record."""

    resolved = requirements or MaracaRuntimeResultIntakeRequirements()
    blockers: list[str] = []

    invocation_map = _invocation_mapping(invocation, blockers)
    result_map = _required_mapping(runtime_result, "runtime-result", blockers)

    invocation_work_id = _required_text(_value(invocation_map, "work_id"), "invocation-work-id", blockers)
    invocation_run_id = _required_text(_value(invocation_map, "run_id"), "invocation-run-id", blockers)
    invocation_operation = _required_text(
        _value(invocation_map, "operation"),
        "invocation-operation",
        blockers,
    )

    result_work_id = _required_text(_value(result_map, "work_id"), "result-work-id", blockers)
    result_run_id = _required_text(_value(result_map, "run_id"), "result-run-id", blockers)
    result_operation = _required_text(_value(result_map, "operation"), "result-operation", blockers)
    runtime_status = _required_text(_value(result_map, "status"), "runtime-status", blockers)

    if runtime_status and runtime_status not in resolved.allowed_runtime_statuses:
        blockers.append("unsupported-runtime-status")

    evidence_items = _evidence_items(result_map, blockers)
    output_map = _optional_mapping(_value(result_map, "output"), "output", blockers)
    metadata_map = _optional_mapping(_value(result_map, "metadata"), "metadata", blockers)

    if resolved.require_identity_match:
        _check_match(invocation_work_id, result_work_id, "work-id", blockers)
        _check_match(invocation_run_id, result_run_id, "run-id", blockers)
        _check_match(invocation_operation, result_operation, "operation", blockers)

    if resolved.require_evidence and not evidence_items:
        blockers.append("missing-evidence")

    for kind, value in (
        ("runtime-result", result_map),
        ("evidence", {"evidence_items": evidence_items}),
        ("output", output_map),
        ("metadata", metadata_map),
    ):
        _check_secret_like(kind, value, blockers)
        _check_execution_flags(kind, value, blockers)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    accepted = not deduped_blockers
    record = None
    if result_work_id and result_run_id and result_operation and runtime_status:
        record = MaracaRuntimeResultRecord(
            work_id=result_work_id,
            run_id=result_run_id,
            operation=result_operation,
            runtime_status=runtime_status,
            evidence_items=evidence_items,
            output=output_map or {},
            metadata=metadata_map or {},
        )

    return MaracaRuntimeResultIntakeResult(
        accepted=accepted,
        status="accepted" if accepted else "blocked",
        blockers=deduped_blockers,
        requirements=resolved,
        result=record,
    )


def _invocation_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append("missing-invocation")
        return None
    envelope = _plain_mapping(mapped.get("envelope"))
    if envelope is not None:
        if not bool(mapped.get("ready", True)):
            blockers.append("blocked-invocation")
        return envelope
    return mapped


def _plain_mapping(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return dict(mapped)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return dict(mapped)
    return None


def _required_mapping(value: object, name: str, blockers: list[str]) -> dict[str, Any] | None:
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"missing-{name}")
    return mapped


def _optional_mapping(
    value: object | None,
    name: str,
    blockers: list[str],
) -> dict[str, Any] | None:
    if value is None:
        return {}
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"malformed-{name}")
    return mapped


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    if _is_secret_like(value):
        blockers.append(f"redacted-{name}")
        return ""
    return value.strip()


def _evidence_items(
    result: Mapping[str, Any] | None,
    blockers: list[str],
) -> tuple[Mapping[str, object], ...]:
    if result is None:
        return ()
    raw = result.get("evidence_items")
    if raw is None:
        raw = result.get("evidence")
    if raw is None:
        raw = result.get("items")
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        item = _plain_mapping(raw)
        if item is None:
            blockers.append("malformed-evidence")
            return ()
        return (item,)

    items: list[Mapping[str, object]] = []
    for value in raw:
        item = _plain_mapping(value)
        if item is None:
            blockers.append("malformed-evidence")
            continue
        items.append(item)
    return tuple(items)


def _check_match(left: str, right: str, name: str, blockers: list[str]) -> None:
    if left and right and left != right:
        blockers.append(f"{name}-mismatch")


def _check_secret_like(
    kind: str,
    value: Mapping[str, Any] | None,
    blockers: list[str],
) -> None:
    if value is None:
        return
    for key, item in value.items():
        if _is_secret_like(key):
            blockers.append(f"redacted-{kind}-name")
        if _is_secret_like(item):
            blockers.append(f"redacted-{kind}-value")
        child = _plain_mapping(item)
        if child is not None:
            _check_secret_like(kind, child, blockers)
        elif isinstance(item, (list, tuple)):
            for child_item in item:
                child_map = _plain_mapping(child_item)
                if child_map is not None:
                    _check_secret_like(kind, child_map, blockers)
                elif _is_secret_like(child_item):
                    blockers.append(f"redacted-{kind}-value")


def _check_execution_flags(
    kind: str,
    value: Mapping[str, Any] | None,
    blockers: list[str],
) -> None:
    if value is None:
        return
    for key, item in value.items():
        if _is_execution_flag(key) and bool(item):
            blockers.append(f"execution-flag:{kind}")
        child = _plain_mapping(item)
        if child is not None:
            _check_execution_flags(kind, child, blockers)
        elif isinstance(item, (list, tuple)):
            for child_item in item:
                child_map = _plain_mapping(child_item)
                if child_map is not None:
                    _check_execution_flags(kind, child_map, blockers)


def _is_execution_flag(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower().replace("-", "_")
    return lowered in _EXECUTION_FLAG_NAMES or lowered.startswith("should_")


def _value(mapping: Mapping[str, Any] | None, key: str) -> object:
    if mapping is None:
        return None
    return mapping.get(key)


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
    mapped = _plain_mapping(value)
    if mapped is not None:
        return _redact(mapped)
    if _is_secret_like(value):
        return REDACTED
    return value


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)
