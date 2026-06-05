"""Record approval audit bindings into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from harness_orchestrator.approval_audit_binding import (
    ApprovalAuditBinding,
    ApprovalAuditEvent,
)
from harness_orchestrator.run_ledger import AuditEvent, RunLedger, RunLedgerSnapshot


@dataclass(frozen=True)
class ApprovalAuditLedgerResult:
    """Plain result from attempting to record approval audit events."""

    recorded_event_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    skipped_binding_ids: tuple[str, ...] = ()
    skipped_payload_digests: tuple[str, ...] = ()
    skipped_event_ids: tuple[str, ...] = ()
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_event_ids": self.recorded_event_ids,
            "blockers": self.blockers,
            "skipped_binding_ids": self.skipped_binding_ids,
            "skipped_payload_digests": self.skipped_payload_digests,
            "skipped_event_ids": self.skipped_event_ids,
            "ledger_snapshot": (
                self.ledger_snapshot.to_dict()
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_approval_audit_bindings(
    bindings: ApprovalAuditBinding | Iterable[ApprovalAuditBinding] | None,
    *,
    ledger: RunLedger | None,
) -> ApprovalAuditLedgerResult:
    """Record passed approval audit binding events into the provided ledger."""

    if not isinstance(ledger, RunLedger):
        return ApprovalAuditLedgerResult(blockers=("ledger-missing",))

    normalized = _normalize_bindings(bindings)
    if not normalized:
        return ApprovalAuditLedgerResult(
            blockers=("approval-audit-bindings-empty",),
            ledger_snapshot=ledger.snapshot(),
        )

    blockers: list[str] = []
    recorded_event_ids: list[str] = []
    skipped_binding_ids: list[str] = []
    skipped_payload_digests: list[str] = []
    skipped_event_ids: list[str] = []
    existing_audit_events = ledger.snapshot().audit_events
    existing_event_ids = {event.event_id for event in existing_audit_events}
    existing_payload_digests = {
        event.metadata.get("payload_digest")
        for event in existing_audit_events
        if isinstance(event.metadata.get("payload_digest"), str)
    }
    seen_payload_digests: set[str] = set()
    seen_event_ids: set[str] = set()

    for binding in normalized:
        binding_blockers = _binding_blockers(
            binding,
            existing_event_ids=existing_event_ids,
            existing_payload_digests=existing_payload_digests,
            seen_payload_digests=seen_payload_digests,
            seen_event_ids=seen_event_ids,
        )
        if binding_blockers:
            blockers.extend(binding_blockers)
            _skip_binding(
                binding,
                skipped_binding_ids=skipped_binding_ids,
                skipped_payload_digests=skipped_payload_digests,
                skipped_event_ids=skipped_event_ids,
            )
            continue

        assert isinstance(binding, ApprovalAuditBinding)
        event = _ledger_audit_event(binding.audit_event)
        ledger.record_audit_event(event=event)
        recorded_event_ids.append(event.event_id)
        existing_event_ids.add(event.event_id)
        existing_payload_digests.add(binding.payload_digest)
        seen_event_ids.add(event.event_id)
        seen_payload_digests.add(binding.payload_digest)

    return ApprovalAuditLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        blockers=tuple(dict.fromkeys(blockers)),
        skipped_binding_ids=tuple(skipped_binding_ids),
        skipped_payload_digests=tuple(skipped_payload_digests),
        skipped_event_ids=tuple(skipped_event_ids),
        ledger_snapshot=ledger.snapshot(),
    )


def record_approval_audit_bindings_in_ledger(
    bindings: ApprovalAuditBinding | Iterable[ApprovalAuditBinding] | None,
    *,
    ledger: RunLedger | None,
) -> ApprovalAuditLedgerResult:
    """Compatibility alias for explicit ledger recording."""

    return record_approval_audit_bindings(bindings, ledger=ledger)


def _normalize_bindings(
    bindings: ApprovalAuditBinding | Iterable[ApprovalAuditBinding] | None,
) -> tuple[object, ...]:
    if bindings is None:
        return (None,)
    if isinstance(bindings, ApprovalAuditBinding):
        return (bindings,)
    if isinstance(bindings, (str, bytes)):
        return (bindings,)
    try:
        return tuple(bindings)
    except TypeError:
        return (bindings,)


def _binding_blockers(
    binding: object,
    *,
    existing_event_ids: set[str],
    existing_payload_digests: set[str],
    seen_payload_digests: set[str],
    seen_event_ids: set[str],
) -> tuple[str, ...]:
    if binding is None:
        return ("approval-audit-binding-missing",)
    if not isinstance(binding, ApprovalAuditBinding):
        return ("approval-audit-binding-wrong-type",)
    if not binding.passed or binding.status != "approved" or binding.blockers:
        return ("approval-audit-binding-blocked",)
    if not isinstance(binding.audit_event, ApprovalAuditEvent):
        return ("approval-audit-event-missing",)
    if not binding.audit_event.event_id:
        return ("approval-audit-event-id-missing",)

    blockers: list[str] = []
    if binding.payload_digest in seen_payload_digests:
        blockers.append("approval-audit-payload-digest-duplicate")
    elif binding.payload_digest in existing_payload_digests:
        blockers.append("approval-audit-payload-digest-already-recorded")
    if binding.audit_event.event_id in seen_event_ids:
        blockers.append("approval-audit-event-id-duplicate")
    elif binding.audit_event.event_id in existing_event_ids:
        blockers.append("approval-audit-event-id-already-recorded")
    return tuple(blockers)


def _skip_binding(
    binding: object,
    *,
    skipped_binding_ids: list[str],
    skipped_payload_digests: list[str],
    skipped_event_ids: list[str],
) -> None:
    if not isinstance(binding, ApprovalAuditBinding):
        return
    skipped_binding_ids.append(binding.binding_id)
    skipped_payload_digests.append(binding.payload_digest)
    if isinstance(binding.audit_event, ApprovalAuditEvent):
        skipped_event_ids.append(binding.audit_event.event_id)


def _ledger_audit_event(event: ApprovalAuditEvent) -> AuditEvent:
    return AuditEvent(
        event_id=event.event_id,
        work_id=event.work_id,
        event_type=event.event_type,
        status=event.status,
        message=event.message,
        occurred_at=event.occurred_at,
        actor=event.actor,
        metadata=dict(event.metadata),
    )



