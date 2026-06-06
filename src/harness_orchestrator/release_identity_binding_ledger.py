"""Record release identity bindings into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.release_identity_binding import ReleaseIdentityBindingResult
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
    TaskStatus,
)


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SECRET_TERMS = ("key", "token", "secret", "password", "credential")
_EXECUTION_KEYS = frozenset(
    {
        "callback",
        "cmd",
        "command",
        "endpoint",
        "exec",
        "hook",
        "launcher",
        "runner",
        "shell",
    }
)
_EXECUTION_TEXT = (
    "$(",
    "`",
    " callback",
    " command",
    " endpoint",
    " execute",
    " exec ",
    " hook",
    " launcher",
    " runner",
    " shell",
)
_MALFORMED_BLOCKER = "malformed-release-identity-binding-ledger-data"


@dataclass(frozen=True)
class ReleaseIdentityBindingLedgerResult:
    """Plain result from recording release identity binding data."""

    recorded_event_ids: tuple[str, ...] = ()
    recorded_dependency_ids: tuple[str, ...] = ()
    skipped_event_ids: tuple[str, ...] = ()
    skipped_dependency_ids: tuple[str, ...] = ()
    skipped_canonical_digests: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_event_ids": self.recorded_event_ids,
            "recorded_dependency_ids": self.recorded_dependency_ids,
            "skipped_event_ids": self.skipped_event_ids,
            "skipped_dependency_ids": self.skipped_dependency_ids,
            "skipped_canonical_digests": self.skipped_canonical_digests,
            "blockers": self.blockers,
            "ledger_snapshot": (
                _snapshot_to_dict(self.ledger_snapshot)
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_release_identity_bindings(
    bindings: object,
    *,
    ledger: RunLedger | None,
) -> ReleaseIdentityBindingLedgerResult:
    """Record already-built release identity bindings into an injected ledger."""

    if not isinstance(ledger, RunLedger):
        return ReleaseIdentityBindingLedgerResult(blockers=("ledger-missing",))

    items = _normalize_items(bindings)
    if not items:
        return ReleaseIdentityBindingLedgerResult(
            blockers=("release-identity-bindings-empty",),
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    blockers: list[str] = []
    staged: list[tuple[DependencyRecord, AuditEvent, str]] = []
    skipped_event_ids: list[str] = []
    skipped_dependency_ids: list[str] = []
    skipped_canonical_digests: list[str] = []
    seen_dependency_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    seen_canonical_digests: set[str] = set()
    snapshot = ledger.snapshot()
    existing_dependency_ids = {record.dependency_id for record in snapshot.dependencies}
    existing_event_ids = {event.event_id for event in snapshot.audit_events}
    existing_canonical_digests = _existing_canonical_digests(snapshot)

    for item in items:
        binding, item_blockers = _binding_from_item(item)
        dependency: DependencyRecord | None = None
        event: AuditEvent | None = None
        canonical_digest = ""
        if binding is not None:
            canonical_digest = str(binding["canonical_digest"])
            dependency, event = _ledger_records(binding)
            item_blockers.extend(
                _duplicate_blockers(
                    dependency=dependency,
                    event=event,
                    canonical_digest=canonical_digest,
                    existing_dependency_ids=existing_dependency_ids,
                    existing_event_ids=existing_event_ids,
                    existing_canonical_digests=existing_canonical_digests,
                    seen_dependency_ids=seen_dependency_ids,
                    seen_event_ids=seen_event_ids,
                    seen_canonical_digests=seen_canonical_digests,
                )
            )

        if item_blockers:
            blockers.extend(item_blockers)
            if dependency is not None:
                skipped_dependency_ids.append(dependency.dependency_id)
            if event is not None:
                skipped_event_ids.append(event.event_id)
            if canonical_digest:
                skipped_canonical_digests.append(canonical_digest)
            continue

        if dependency is not None and event is not None:
            staged.append((dependency, event, canonical_digest))
            seen_dependency_ids.add(dependency.dependency_id)
            seen_event_ids.add(event.event_id)
            seen_canonical_digests.add(canonical_digest)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    if deduped_blockers or not staged:
        return ReleaseIdentityBindingLedgerResult(
            skipped_event_ids=tuple(skipped_event_ids),
            skipped_dependency_ids=tuple(skipped_dependency_ids),
            skipped_canonical_digests=tuple(skipped_canonical_digests),
            blockers=deduped_blockers,
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    recorded_dependency_ids: list[str] = []
    recorded_event_ids: list[str] = []
    for dependency, event, _canonical_digest in staged:
        ledger.record_dependency(dependency=dependency)
        ledger.record_audit_event(event=event)
        recorded_dependency_ids.append(dependency.dependency_id)
        recorded_event_ids.append(event.event_id)

    return ReleaseIdentityBindingLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        recorded_dependency_ids=tuple(recorded_dependency_ids),
        ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
    )


def record_release_identity_binding(
    binding: object,
    *,
    ledger: RunLedger | None,
) -> ReleaseIdentityBindingLedgerResult:
    """Compatibility wrapper for recording one release identity binding."""

    return record_release_identity_bindings(binding, ledger=ledger)


def _normalize_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, ReleaseIdentityBindingResult):
        return (value,)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _binding_from_item(item: object) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    mapped = _plain_mapping(item, blockers)
    if mapped is None:
        return None, ["release-identity-binding-wrong-type"]

    if mapped.get("passed") is not True:
        blockers.append("release-identity-binding-not-passed")
    source_blockers = _blocker_tuple(mapped.get("blockers"))
    if source_blockers:
        blockers.append("release-identity-binding-blockers-present")
        blockers.extend(f"release-identity-binding-{blocker}" for blocker in source_blockers)
    if _contains_secret_like(mapped):
        blockers.append("secret-like-release-identity-binding-ledger-data")
    if _contains_execution_intent(mapped):
        blockers.append("execution-intent-release-identity-binding-ledger-data")

    canonical_payload = _plain_mapping(mapped.get("canonical_payload"), blockers)
    if canonical_payload is None or not canonical_payload:
        blockers.append("release-identity-binding-canonical-payload-missing")
        canonical_payload = {}
    canonical_digest = _digest_text(
        mapped.get("canonical_digest"),
        "canonical-digest",
        blockers,
    )
    summary = _plain_mapping(mapped.get("summary"), blockers)
    if summary is None or not summary:
        blockers.append("release-identity-binding-summary-missing")
        summary = {}

    if canonical_digest and canonical_payload:
        recomputed = _sha256_payload(canonical_payload)
        if recomputed != canonical_digest:
            blockers.append("release-identity-binding-canonical-digest-mismatch")
    if canonical_digest and summary:
        prefix = summary.get("canonical_digest_prefix")
        if prefix != canonical_digest[:12]:
            blockers.append("release-identity-binding-summary-digest-prefix-mismatch")

    work_id = _work_id(canonical_payload, summary, blockers)
    _validate_structure(canonical_payload, summary, blockers)
    binding = {
        "work_id": work_id,
        "canonical_payload": _sorted_value(canonical_payload),
        "canonical_digest": canonical_digest,
        "summary": _sorted_value(summary),
    }
    if blockers:
        return binding, blockers
    return binding, []


def _validate_structure(
    canonical_payload: Mapping[str, Any],
    summary: Mapping[str, Any],
    blockers: list[str],
) -> None:
    expected_format = "harness-release-identity-binding-v1"
    if canonical_payload and canonical_payload.get("format") != (
        expected_format
    ):
        blockers.append("release-identity-binding-format-mismatch")
    expected = _plain_mapping(canonical_payload.get("expected"), blockers)
    if canonical_payload and expected is None:
        blockers.append("release-identity-binding-expected-missing")
        expected = {}
    if (
        canonical_payload
        and _plain_mapping(canonical_payload.get("gate_decision"), blockers) is None
    ):
        blockers.append("release-identity-binding-gate-decision-missing")
    if (
        canonical_payload
        and _plain_mapping(canonical_payload.get("identity_proof"), blockers) is None
    ):
        blockers.append("release-identity-binding-identity-proof-missing")
    if summary and summary.get("format") != expected_format:
        blockers.append("release-identity-binding-summary-format-mismatch")
    if expected is not None and summary:
        for key in (
            "work_id",
            "request_id",
            "evidence_bundle_id",
            "media_ids",
            "artifact_ids",
        ):
            if key in expected and key in summary and expected[key] != summary[key]:
                blockers.append(f"release-identity-binding-summary-{_label(key)}-mismatch")


def _work_id(
    canonical_payload: Mapping[str, Any],
    summary: Mapping[str, Any],
    blockers: list[str],
) -> str:
    expected = _plain_mapping(canonical_payload.get("expected"), blockers) or {}
    payload_work_id = expected.get("work_id")
    summary_work_id = summary.get("work_id")
    if not isinstance(payload_work_id, str) or not payload_work_id.strip():
        blockers.append("missing-work-id")
        return ""
    work_id = payload_work_id.strip()
    if summary_work_id != work_id:
        blockers.append("release-identity-binding-summary-work-id-mismatch")
    if _is_secret_like(work_id):
        blockers.append("secret-like-work-id")
        return ""
    if _has_execution_text(work_id):
        blockers.append("execution-intent-work-id")
        return ""
    return work_id


def _ledger_records(binding: Mapping[str, Any]) -> tuple[DependencyRecord, AuditEvent]:
    canonical_digest = str(binding["canonical_digest"])
    suffix = canonical_digest[:16]
    work_id = str(binding["work_id"])
    dependency_id = f"release-identity-binding:{work_id}:{suffix}"
    event_id = f"release-identity-binding-recorded:{work_id}:{suffix}"
    metadata = {
        "canonical_digest": canonical_digest,
        "canonical_digest_prefix": canonical_digest[:12],
        "canonical_payload": binding["canonical_payload"],
        "summary": binding["summary"],
    }
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"release-identity-binding:{work_id}:{suffix}",
        order=85,
        dependency_type="release-identity-binding",
        required=True,
        status="ready",
        metadata=metadata,
    )
    event = AuditEvent(
        event_id=event_id,
        work_id=work_id,
        event_type="release-identity-binding-ledger-record",
        status="ready",
        message="Release identity binding recorded in ledger.",
        actor="harness",
        metadata={
            "dependency_id": dependency_id,
            **metadata,
        },
    )
    return dependency, event


def _duplicate_blockers(
    *,
    dependency: DependencyRecord,
    event: AuditEvent,
    canonical_digest: str,
    existing_dependency_ids: set[str],
    existing_event_ids: set[str],
    existing_canonical_digests: set[str],
    seen_dependency_ids: set[str],
    seen_event_ids: set[str],
    seen_canonical_digests: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if dependency.dependency_id in seen_dependency_ids:
        blockers.append("release-identity-binding-dependency-id-duplicate")
    elif dependency.dependency_id in existing_dependency_ids:
        blockers.append("release-identity-binding-dependency-id-already-recorded")
    if event.event_id in seen_event_ids:
        blockers.append("release-identity-binding-event-id-duplicate")
    elif event.event_id in existing_event_ids:
        blockers.append("release-identity-binding-event-id-already-recorded")
    if canonical_digest in seen_canonical_digests:
        blockers.append("release-identity-binding-canonical-digest-duplicate")
    elif canonical_digest in existing_canonical_digests:
        blockers.append("release-identity-binding-canonical-digest-already-recorded")
    return tuple(blockers)


def _existing_canonical_digests(snapshot: RunLedgerSnapshot) -> set[str]:
    values: set[str] = set()
    for record in (*snapshot.dependencies, *snapshot.audit_events):
        digest = record.metadata.get("canonical_digest")
        if isinstance(digest, str):
            values.add(digest)
    return values


def _plain_mapping(
    value: object,
    blockers: list[str] | None = None,
) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return _validated_plain_mapping(value, blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return _validated_plain_mapping(mapped, blockers)
        _append_malformed(blockers)
        return None
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return _validated_plain_mapping(mapped, blockers)
    return None


def _validated_plain_mapping(
    value: Mapping[Any, Any],
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            _append_malformed(blockers)
            continue
        plain[key] = _plain_value(item, blockers)
    return plain


def _plain_value(value: object, blockers: list[str] | None = None) -> object:
    mapped = _plain_mapping(value, blockers)
    if mapped is not None:
        return {key: mapped[key] for key in sorted(mapped)}
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    _append_malformed(blockers)
    return None


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    text = value.strip().lower()
    if not _SHA256_HEX.fullmatch(text):
        blockers.append(f"invalid-{name}")
    return text


def _blocker_tuple(value: object) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            return ("malformed-blockers",)
        return tuple(item.strip() for item in value if item.strip())
    return ("malformed-blockers",)


def _contains_secret_like(value: object) -> bool:
    if _is_secret_like(value):
        return True
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            _is_secret_like(key) or _contains_secret_like(item)
            for key, item in mapped.items()
        )
    if isinstance(value, tuple):
        return any(_contains_secret_like(item) for item in value)
    return False


def _contains_execution_intent(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            _is_execution_key(str(key)) or _contains_execution_intent(item)
            for key, item in mapped.items()
        )
    if isinstance(value, tuple):
        return any(_contains_execution_intent(item) for item in value)
    return _has_execution_text(value)


def _is_execution_key(key: str) -> bool:
    normalized = "".join(
        character.lower() if character.isalnum() else "_"
        for character in key
    )
    return any(fragment in normalized for fragment in _EXECUTION_KEYS)


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str) or value == "<redacted>":
        return False
    lowered = value.lower()
    if lowered == "metadata_keys":
        return False
    return any(term in lowered for term in _SECRET_TERMS)


def _has_execution_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = f" {value.strip().lower()} "
    return any(term in text for term in _EXECUTION_TEXT)


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sorted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _sorted_value(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return tuple(_sorted_value(item) for item in value)
    return value


def _immutable_snapshot(snapshot: RunLedgerSnapshot) -> RunLedgerSnapshot:
    return RunLedgerSnapshot(
        run_id=snapshot.run_id,
        gate_decisions=tuple(
            GateDecision(
                decision_id=decision.decision_id,
                work_id=decision.work_id,
                gate_name=decision.gate_name,
                passed=decision.passed,
                reason=decision.reason,
                blockers=decision.blockers,
                evidence_bundle_id=decision.evidence_bundle_id,
                reviewer=decision.reviewer,
                metadata=_freeze_metadata(decision.metadata),
            )
            for decision in snapshot.gate_decisions
        ),
        dependencies=tuple(
            DependencyRecord(
                dependency_id=record.dependency_id,
                work_id=record.work_id,
                reference=record.reference,
                order=record.order,
                dependency_type=record.dependency_type,
                required=record.required,
                status=record.status,
                metadata=_freeze_metadata(record.metadata),
            )
            for record in snapshot.dependencies
        ),
        audit_events=tuple(
            AuditEvent(
                event_id=event.event_id,
                work_id=event.work_id,
                event_type=event.event_type,
                status=event.status,
                message=event.message,
                occurred_at=event.occurred_at,
                actor=event.actor,
                metadata=_freeze_metadata(event.metadata),
            )
            for event in snapshot.audit_events
        ),
        tasks=tuple(
            TaskStatus(
                task_id=task.task_id,
                work_id=task.work_id,
                title=task.title,
                status=task.status,
                blockers=task.blockers,
                assigned_to=task.assigned_to,
                metadata=_freeze_metadata(task.metadata),
            )
            for task in snapshot.tasks
        ),
        metadata=_freeze_metadata(snapshot.metadata),
    )


def _snapshot_to_dict(snapshot: RunLedgerSnapshot) -> dict[str, Any]:
    return {
        "run_id": snapshot.run_id,
        "gate_decisions": tuple(
            {
                "decision_id": decision.decision_id,
                "work_id": decision.work_id,
                "gate_name": decision.gate_name,
                "passed": decision.passed,
                "reason": decision.reason,
                "blockers": decision.blockers,
                "evidence_bundle_id": decision.evidence_bundle_id,
                "reviewer": decision.reviewer,
                "metadata": _plain_snapshot_value(decision.metadata),
            }
            for decision in snapshot.gate_decisions
        ),
        "dependencies": tuple(
            {
                "dependency_id": record.dependency_id,
                "work_id": record.work_id,
                "reference": record.reference,
                "order": record.order,
                "dependency_type": record.dependency_type,
                "required": record.required,
                "status": record.status,
                "metadata": _plain_snapshot_value(record.metadata),
            }
            for record in snapshot.dependencies
        ),
        "audit_events": tuple(
            {
                "event_id": event.event_id,
                "work_id": event.work_id,
                "event_type": event.event_type,
                "status": event.status,
                "message": event.message,
                "occurred_at": event.occurred_at,
                "actor": event.actor,
                "metadata": _plain_snapshot_value(event.metadata),
            }
            for event in snapshot.audit_events
        ),
        "tasks": tuple(
            {
                "task_id": task.task_id,
                "work_id": task.work_id,
                "title": task.title,
                "status": task.status,
                "blockers": task.blockers,
                "assigned_to": task.assigned_to,
                "metadata": _plain_snapshot_value(task.metadata),
            }
            for task in snapshot.tasks
        ),
        "metadata": _plain_snapshot_value(snapshot.metadata),
    }


def _freeze_metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(_frozen_snapshot_mapping(value))


def _freeze_snapshot_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(_frozen_snapshot_mapping(value))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_snapshot_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return "<unsupported>"


def _frozen_snapshot_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        key: _freeze_snapshot_value(item)
        for key, item in value.items()
        if isinstance(key, str)
    }


def _plain_snapshot_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        key: _plain_snapshot_value(item)
        for key, item in value.items()
        if isinstance(key, str)
    }


def _plain_snapshot_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _plain_snapshot_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_plain_snapshot_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return "<unsupported>"


def _append_malformed(blockers: list[str] | None) -> None:
    if blockers is not None:
        blockers.append(_MALFORMED_BLOCKER)


def _label(key: str) -> str:
    return key.replace("_", "-")


__all__ = [
    "ReleaseIdentityBindingLedgerResult",
    "record_release_identity_binding",
    "record_release_identity_bindings",
]
