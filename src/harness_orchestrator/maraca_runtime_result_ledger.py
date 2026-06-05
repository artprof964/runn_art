"""Record accepted MARACA runtime result intake into an explicit ledger."""

from __future__ import annotations

from dataclasses import dataclass, is_dataclass, asdict
import hashlib
import json
from typing import Any, Mapping

from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
)


_SECRET_TERMS = ("key", "token", "secret", "password")


@dataclass(frozen=True)
class MaracaRuntimeResultLedgerRecord:
    """Plain records prepared for a MARACA runtime result ledger write."""

    dependency: DependencyRecord
    audit_event: AuditEvent
    payload_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency": self.dependency.to_dict(),
            "audit_event": self.audit_event.to_dict(),
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True)
class MaracaRuntimeResultLedgerResult:
    """Result from attempting to record an accepted MARACA runtime result."""

    recorded_dependency_ids: tuple[str, ...] = ()
    recorded_event_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    skipped_payload_digests: tuple[str, ...] = ()
    ledger_record: MaracaRuntimeResultLedgerRecord | None = None
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_dependency_ids": self.recorded_dependency_ids,
            "recorded_event_ids": self.recorded_event_ids,
            "blockers": self.blockers,
            "skipped_payload_digests": self.skipped_payload_digests,
            "ledger_record": (
                self.ledger_record.to_dict() if self.ledger_record is not None else None
            ),
            "ledger_snapshot": (
                self.ledger_snapshot.to_dict()
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_maraca_runtime_result_intake(
    intake_result: object,
    *,
    ledger: RunLedger | None,
    evidence_summary: object,
) -> MaracaRuntimeResultLedgerResult:
    """Record an already-accepted runtime result into an injected ledger."""

    if not isinstance(ledger, RunLedger):
        return MaracaRuntimeResultLedgerResult(blockers=("ledger-missing",))

    blockers: list[str] = []
    intake_map = _required_mapping(intake_result, "intake-result", blockers)
    summary_map = _required_mapping(evidence_summary, "evidence-summary", blockers)
    result_map = _required_mapping(_value(intake_map, "result"), "result-record", blockers)

    if intake_map is not None:
        accepted = bool(intake_map.get("accepted"))
        status = _text(intake_map.get("status"))
        intake_blockers = _strings(intake_map.get("blockers"))
        if not accepted or status != "accepted" or intake_blockers:
            blockers.append("runtime-result-intake-blocked")

    work_id = _required_text(_value(result_map, "work_id"), "work-id", blockers)
    run_id = _required_text(_value(result_map, "run_id"), "run-id", blockers)
    operation = _required_text(_value(result_map, "operation"), "operation", blockers)
    runtime_status = _required_text(
        _value(result_map, "runtime_status"),
        "runtime-status",
        blockers,
    )
    evidence_items = _evidence_items(_value(result_map, "evidence_items"), blockers)
    metadata = _optional_mapping(_value(result_map, "metadata"), "metadata", blockers)

    if run_id and ledger.run_id != run_id:
        blockers.append("ledger-run-id-mismatch")
    if not evidence_items:
        blockers.append("missing-result-evidence")
    _validate_evidence_summary(summary_map, evidence_items, blockers)

    if _contains_secret_like(result_map) or _contains_secret_like(summary_map):
        blockers.append("secret-like-runtime-result-ledger-data")

    record = None
    if work_id and run_id and operation and runtime_status and evidence_items:
        record = _ledger_record(
            work_id=work_id,
            run_id=run_id,
            operation=operation,
            runtime_status=runtime_status,
            evidence_items=evidence_items,
            evidence_summary=summary_map or {},
            metadata=metadata,
        )
        blockers.extend(_duplicate_blockers(record, ledger))

    deduped = tuple(dict.fromkeys(blockers))
    if deduped or record is None:
        return MaracaRuntimeResultLedgerResult(
            blockers=deduped,
            skipped_payload_digests=(
                (record.payload_digest,) if record is not None else ()
            ),
            ledger_record=record,
            ledger_snapshot=ledger.snapshot(),
        )

    ledger.record_dependency(dependency=record.dependency)
    ledger.record_audit_event(event=record.audit_event)
    return MaracaRuntimeResultLedgerResult(
        recorded_dependency_ids=(record.dependency.dependency_id,),
        recorded_event_ids=(record.audit_event.event_id,),
        ledger_record=record,
        ledger_snapshot=ledger.snapshot(),
    )


def record_maraca_runtime_result_in_ledger(
    intake_result: object,
    *,
    ledger: RunLedger | None,
    evidence_summary: object,
) -> MaracaRuntimeResultLedgerResult:
    """Compatibility alias for the explicit ledger recorder."""

    return record_maraca_runtime_result_intake(
        intake_result,
        ledger=ledger,
        evidence_summary=evidence_summary,
    )


def _ledger_record(
    *,
    work_id: str,
    run_id: str,
    operation: str,
    runtime_status: str,
    evidence_items: tuple[Mapping[str, Any], ...],
    evidence_summary: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> MaracaRuntimeResultLedgerRecord:
    source_ids = _source_ids(evidence_items)
    canonical_payload = {
        "work_id": work_id,
        "run_id": run_id,
        "operation": operation,
        "runtime_status": runtime_status,
        "evidence_count": len(evidence_items),
        "source_ids": source_ids,
        "evidence_summary": dict(evidence_summary),
    }
    payload_digest = _digest(canonical_payload)
    suffix = payload_digest[:16]
    dependency_id = f"maraca-runtime-result:{work_id}:{operation}:{suffix}"
    event_id = f"maraca-runtime-result-recorded:{work_id}:{operation}:{suffix}"
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"maraca-runtime-result:{run_id}:{operation}",
        order=80,
        dependency_type="maraca-runtime-result",
        required=True,
        status="ready" if runtime_status == "succeeded" else runtime_status,
        metadata={
            "run_id": run_id,
            "operation": operation,
            "runtime_status": runtime_status,
            "payload_digest": payload_digest,
            "evidence_count": len(evidence_items),
            "source_ids": source_ids,
        },
    )
    audit_event = AuditEvent(
        event_id=event_id,
        work_id=work_id,
        event_type="maraca-runtime-result-ledger-record",
        status=runtime_status,
        message="Accepted MARACA runtime result intake recorded in ledger.",
        actor="harness",
        metadata={
            "run_id": run_id,
            "operation": operation,
            "payload_digest": payload_digest,
            "dependency_id": dependency_id,
            "evidence_count": len(evidence_items),
            "source_ids": source_ids,
            "evidence_summary": dict(evidence_summary),
            "result_metadata": dict(metadata),
        },
    )
    return MaracaRuntimeResultLedgerRecord(
        dependency=dependency,
        audit_event=audit_event,
        payload_digest=payload_digest,
    )


def _duplicate_blockers(
    record: MaracaRuntimeResultLedgerRecord,
    ledger: RunLedger,
) -> tuple[str, ...]:
    snapshot = ledger.snapshot()
    blockers: list[str] = []
    if any(
        dependency.dependency_id == record.dependency.dependency_id
        for dependency in snapshot.dependencies
    ):
        blockers.append("maraca-runtime-result-dependency-id-already-recorded")
    if any(event.event_id == record.audit_event.event_id for event in snapshot.audit_events):
        blockers.append("maraca-runtime-result-event-id-already-recorded")
    existing_digests = {
        event.metadata.get("payload_digest")
        for event in snapshot.audit_events
        if isinstance(event.metadata.get("payload_digest"), str)
    }
    if record.payload_digest in existing_digests:
        blockers.append("maraca-runtime-result-payload-digest-already-recorded")
    return tuple(blockers)


def _validate_evidence_summary(
    summary: Mapping[str, Any] | None,
    evidence_items: tuple[Mapping[str, Any], ...],
    blockers: list[str],
) -> None:
    if summary is None:
        return
    evidence_count = summary.get("evidence_count")
    if not isinstance(evidence_count, int) or evidence_count < 1:
        blockers.append("missing-evidence-summary-count")
    elif evidence_count != len(evidence_items):
        blockers.append("evidence-summary-count-mismatch")

    expected_source_ids = set(_source_ids(evidence_items))
    summary_source_ids = set(_strings(summary.get("source_ids")))
    if not summary_source_ids:
        blockers.append("missing-evidence-summary-source-ids")
    elif expected_source_ids and summary_source_ids != expected_source_ids:
        blockers.append("evidence-summary-source-id-mismatch")


def _required_mapping(
    value: object,
    name: str,
    blockers: list[str],
) -> dict[str, Any] | None:
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"missing-{name}")
    return mapped


def _optional_mapping(
    value: object,
    name: str,
    blockers: list[str],
) -> dict[str, Any]:
    if value is None:
        return {}
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"malformed-{name}")
        return {}
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


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    if _is_secret_like(value):
        blockers.append(f"redacted-{name}")
        return ""
    return value.strip()


def _evidence_items(value: object, blockers: list[str]) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        blockers.append("malformed-result-evidence")
        return ()
    items: list[Mapping[str, Any]] = []
    for item in value:
        mapped = _plain_mapping(item)
        if mapped is None:
            blockers.append("malformed-result-evidence")
            continue
        items.append(mapped)
    return tuple(items)


def _source_ids(evidence_items: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    values: list[str] = []
    for item in evidence_items:
        value = item.get("source_id") or item.get("source") or item.get("id")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return tuple(sorted(dict.fromkeys(values)))


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _contains_secret_like(value: object) -> bool:
    if _is_secret_like(value):
        return True
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            _is_secret_like(key) or _contains_secret_like(item)
            for key, item in mapped.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_like(item) for item in value)
    return False


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    if value == "<redacted>":
        return False
    return any(term in lowered for term in _SECRET_TERMS)


def _value(mapping: Mapping[str, Any] | None, key: str) -> object:
    if mapping is None:
        return None
    return mapping.get(key)


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()
