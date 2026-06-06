"""Record release publish handoff acceptance into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.release_publish_handoff_acceptance import (
    ReleasePublishHandoffAcceptance,
)
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
    TaskStatus,
)


_FORMAT = "harness-release-publish-handoff-acceptance-ledger-v1"
_DEPENDENCY_TYPE = "release-publish-handoff-acceptance"
_EVENT_TYPE = "release-publish-handoff-acceptance-ledger-record"
_PACKAGE_DEPENDENCY_TYPE = "release-publish-handoff-package"
_PACKAGE_EVENT_TYPE = "release-publish-handoff-package-ledger-record"
_PACKAGE_LEDGER_FORMAT = "harness-release-publish-handoff-package-ledger-v1"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_ACCEPTANCE_RESULT_KEYS = frozenset(
    {
        "accepted",
        "status",
        "blockers",
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "package_digest",
        "package_digest_prefix",
        "acceptance_summary",
        "receipt_summary",
    }
)
_ACCEPTANCE_SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "package_digest_prefix",
        "accepted",
    }
)
_RECEIPT_SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "package_digest_prefix",
        "source_blocker_count",
    }
)
_PACKAGE_DEPENDENCY_METADATA_KEYS = frozenset(
    {
        "run_id",
        "readiness_dependency_id",
        "readiness_event_id",
        "intent_dependency_id",
        "intent_event_id",
        "intent_digest_prefix",
        "release_binding_digest_prefix",
        "handoff_readiness_digest_prefix",
        "package_digest",
        "package_digest_prefix",
        "package_data",
        "canonical_payload",
    }
)
_PACKAGE_EVENT_METADATA_KEYS = frozenset({"dependency_id", *_PACKAGE_DEPENDENCY_METADATA_KEYS})
_SECRET_TERMS = ("key", "token", "secret", "password", "credential")
_ACTION_KEYS = frozenset(
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
        "webhook",
    }
)
_ACTION_TEXT = (
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
    " webhook",
)
_MALFORMED_BLOCKER = "malformed-release-publish-handoff-acceptance-ledger-data"


@dataclass(frozen=True)
class ReleasePublishHandoffAcceptanceLedgerResult:
    """Plain result from recording handoff acceptance data."""

    recorded_event_ids: tuple[str, ...] = ()
    recorded_dependency_ids: tuple[str, ...] = ()
    skipped_event_ids: tuple[str, ...] = ()
    skipped_dependency_ids: tuple[str, ...] = ()
    skipped_package_digests: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_event_ids": self.recorded_event_ids,
            "recorded_dependency_ids": self.recorded_dependency_ids,
            "skipped_event_ids": self.skipped_event_ids,
            "skipped_dependency_ids": self.skipped_dependency_ids,
            "skipped_package_digests": self.skipped_package_digests,
            "blockers": self.blockers,
            "ledger_snapshot": (
                _snapshot_to_dict(self.ledger_snapshot)
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_release_publish_handoff_acceptances(
    acceptances: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishHandoffAcceptanceLedgerResult:
    """Record already-accepted handoff acceptance data into an injected run ledger."""

    if not isinstance(ledger, RunLedger):
        return ReleasePublishHandoffAcceptanceLedgerResult(blockers=("ledger-missing",))

    items = _normalize_items(acceptances)
    if not items:
        return ReleasePublishHandoffAcceptanceLedgerResult(
            blockers=("release-publish-handoff-acceptance-empty",),
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    snapshot = ledger.snapshot()
    ledger_blockers = _hostile_snapshot_blockers(snapshot)
    if ledger_blockers:
        return ReleasePublishHandoffAcceptanceLedgerResult(
            blockers=ledger_blockers,
            ledger_snapshot=_immutable_snapshot(snapshot),
        )

    blockers: list[str] = []
    staged: list[tuple[DependencyRecord, AuditEvent, str]] = []
    skipped_event_ids: list[str] = []
    skipped_dependency_ids: list[str] = []
    skipped_package_digests: list[str] = []
    seen_dependency_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    seen_package_digests: set[str] = set()
    existing_dependency_ids = {record.dependency_id for record in snapshot.dependencies}
    existing_event_ids = {event.event_id for event in snapshot.audit_events}
    existing_package_digests = _existing_acceptance_package_digests(snapshot)

    for item in items:
        acceptance, item_blockers = _acceptance_from_item(item)
        dependency: DependencyRecord | None = None
        event: AuditEvent | None = None
        package_digest = ""
        if acceptance is not None:
            package_digest = str(acceptance["package_digest"])
            if ledger.run_id != acceptance["run_id"]:
                item_blockers.append("ledger-run-id-mismatch")
            item_blockers.extend(_source_record_blockers(acceptance, snapshot))
            if not item_blockers:
                dependency, event = _ledger_records(acceptance)
                item_blockers.extend(
                    _duplicate_blockers(
                        dependency=dependency,
                        event=event,
                        package_digest=package_digest,
                        existing_dependency_ids=existing_dependency_ids,
                        existing_event_ids=existing_event_ids,
                        existing_package_digests=existing_package_digests,
                        seen_dependency_ids=seen_dependency_ids,
                        seen_event_ids=seen_event_ids,
                        seen_package_digests=seen_package_digests,
                    )
                )

        if item_blockers:
            blockers.extend(item_blockers)
            if dependency is not None:
                skipped_dependency_ids.append(dependency.dependency_id)
            if event is not None:
                skipped_event_ids.append(event.event_id)
            if package_digest:
                skipped_package_digests.append(package_digest)
            continue

        if dependency is not None and event is not None and package_digest:
            staged.append((dependency, event, package_digest))
            seen_dependency_ids.add(dependency.dependency_id)
            seen_event_ids.add(event.event_id)
            seen_package_digests.add(package_digest)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    if deduped_blockers or not staged:
        return ReleasePublishHandoffAcceptanceLedgerResult(
            skipped_event_ids=tuple(skipped_event_ids),
            skipped_dependency_ids=tuple(skipped_dependency_ids),
            skipped_package_digests=tuple(skipped_package_digests),
            blockers=deduped_blockers,
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    recorded_dependency_ids: list[str] = []
    recorded_event_ids: list[str] = []
    for dependency, event, _digest in staged:
        ledger.record_dependency(dependency=dependency)
        ledger.record_audit_event(event=event)
        recorded_dependency_ids.append(dependency.dependency_id)
        recorded_event_ids.append(event.event_id)

    return ReleasePublishHandoffAcceptanceLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        recorded_dependency_ids=tuple(recorded_dependency_ids),
        ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
    )


def record_release_publish_handoff_acceptance(
    acceptance: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishHandoffAcceptanceLedgerResult:
    """Compatibility wrapper for recording one handoff acceptance result."""

    return record_release_publish_handoff_acceptances(acceptance, ledger=ledger)


def _normalize_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, ReleasePublishHandoffAcceptance):
        return (value,)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _acceptance_from_item(item: object) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    mapped = _plain_mapping(item, blockers)
    if mapped is None:
        return None, ["release-publish-handoff-acceptance-wrong-type"]

    if set(mapped) != _ACCEPTANCE_RESULT_KEYS:
        blockers.append("unsafe-release-publish-handoff-acceptance-schema")
    if mapped.get("accepted") is not True:
        blockers.append("release-publish-handoff-acceptance-not-accepted")
    if mapped.get("status") != "accepted":
        blockers.append("release-publish-handoff-acceptance-status-not-accepted")
    source_blockers = _blocker_tuple(mapped.get("blockers"))
    if source_blockers:
        blockers.append("release-publish-handoff-acceptance-blockers-present")
        blockers.extend(
            f"release-publish-handoff-acceptance-{blocker}"
            for blocker in source_blockers
        )

    run_id = _required_text(mapped.get("run_id"), "run-id", blockers)
    work_id = _required_text(mapped.get("work_id"), "work-id", blockers)
    dependency_id = _required_text(mapped.get("dependency_id"), "dependency-id", blockers)
    event_id = _required_text(mapped.get("event_id"), "event-id", blockers)
    package_digest = _digest_text(mapped.get("package_digest"), "package-digest", blockers)
    package_digest_prefix = _digest_prefix(
        mapped.get("package_digest_prefix"),
        "package-digest-prefix",
        blockers,
    )
    acceptance_summary = _required_mapping(
        mapped.get("acceptance_summary"),
        "acceptance-summary",
        blockers,
    )
    receipt_summary = _required_mapping(
        mapped.get("receipt_summary"),
        "receipt-summary",
        blockers,
        required=False,
    )

    if package_digest and package_digest_prefix:
        _match(package_digest[:12], package_digest_prefix, "package-digest-prefix", blockers)
    if work_id and package_digest:
        suffix = package_digest[:16]
        _match(
            dependency_id,
            f"release-publish-handoff-package:{work_id}:{suffix}",
            "dependency-id",
            blockers,
        )
        _match(
            event_id,
            f"release-publish-handoff-package-recorded:{work_id}:{suffix}",
            "event-id",
            blockers,
        )
    if acceptance_summary:
        _validate_acceptance_summary(
            summary=acceptance_summary,
            run_id=run_id,
            work_id=work_id,
            dependency_id=dependency_id,
            event_id=event_id,
            package_digest_prefix=package_digest_prefix,
            blockers=blockers,
        )
    if receipt_summary:
        _validate_receipt_summary(
            summary=receipt_summary,
            run_id=run_id,
            work_id=work_id,
            dependency_id=dependency_id,
            event_id=event_id,
            package_digest_prefix=package_digest_prefix,
            blockers=blockers,
        )

    if _contains_secret_like(mapped):
        blockers.append("secret-like-release-publish-handoff-acceptance-ledger-data")
    if _contains_action_intent(mapped):
        blockers.append("action-intent-release-publish-handoff-acceptance-ledger-data")

    acceptance = {
        "run_id": run_id,
        "work_id": work_id,
        "dependency_id": dependency_id,
        "event_id": event_id,
        "package_digest": package_digest,
        "package_digest_prefix": package_digest_prefix,
        "acceptance_summary": _sorted_value(acceptance_summary),
        "receipt_summary": _sorted_value(receipt_summary),
    }
    if blockers:
        return acceptance, blockers
    return acceptance, []


def _validate_acceptance_summary(
    *,
    summary: Mapping[str, Any],
    run_id: str,
    work_id: str,
    dependency_id: str,
    event_id: str,
    package_digest_prefix: str,
    blockers: list[str],
) -> None:
    if set(summary) != _ACCEPTANCE_SUMMARY_KEYS:
        blockers.append("unsafe-acceptance-summary-schema")
    _match(summary.get("run_id"), run_id, "acceptance-summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "acceptance-summary-work-id", blockers)
    _match(
        summary.get("dependency_id"),
        dependency_id,
        "acceptance-summary-dependency-id",
        blockers,
    )
    _match(summary.get("event_id"), event_id, "acceptance-summary-event-id", blockers)
    _match(
        summary.get("package_digest_prefix"),
        package_digest_prefix,
        "acceptance-summary-package-digest-prefix",
        blockers,
    )
    _match(summary.get("accepted"), True, "acceptance-summary-accepted", blockers)


def _validate_receipt_summary(
    *,
    summary: Mapping[str, Any],
    run_id: str,
    work_id: str,
    dependency_id: str,
    event_id: str,
    package_digest_prefix: str,
    blockers: list[str],
) -> None:
    if set(summary) != _RECEIPT_SUMMARY_KEYS:
        blockers.append("unsafe-receipt-summary-schema")
    _match(summary.get("run_id"), run_id, "receipt-summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "receipt-summary-work-id", blockers)
    _match(summary.get("dependency_id"), dependency_id, "receipt-summary-dependency-id", blockers)
    _match(summary.get("event_id"), event_id, "receipt-summary-event-id", blockers)
    _match(
        summary.get("package_digest_prefix"),
        package_digest_prefix,
        "receipt-summary-package-digest-prefix",
        blockers,
    )
    _match(summary.get("source_blocker_count"), 0, "receipt-summary-source-blocker-count", blockers)


def _ledger_records(acceptance: Mapping[str, Any]) -> tuple[DependencyRecord, AuditEvent]:
    package_digest = str(acceptance["package_digest"])
    suffix = package_digest[:16]
    run_id = str(acceptance["run_id"])
    work_id = str(acceptance["work_id"])
    dependency_id = f"release-publish-handoff-acceptance:{work_id}:{suffix}"
    event_id = f"release-publish-handoff-acceptance-recorded:{work_id}:{suffix}"
    metadata = {
        "run_id": run_id,
        "source_dependency_id": acceptance["dependency_id"],
        "source_event_id": acceptance["event_id"],
        "package_digest": package_digest,
        "package_digest_prefix": acceptance["package_digest_prefix"],
        "acceptance_summary": acceptance["acceptance_summary"],
        "receipt_summary": acceptance["receipt_summary"],
        "canonical_payload": {
            "format": _FORMAT,
            "handoff_acceptance": {
                "run_id": run_id,
                "work_id": work_id,
                "dependency_id": acceptance["dependency_id"],
                "event_id": acceptance["event_id"],
                "package_digest_prefix": acceptance["package_digest_prefix"],
                "accepted": True,
            },
        },
    }
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"release-publish-handoff-acceptance:{run_id}:{work_id}:{suffix}",
        order=110,
        dependency_type=_DEPENDENCY_TYPE,
        required=True,
        status="accepted",
        metadata=metadata,
    )
    event = AuditEvent(
        event_id=event_id,
        work_id=work_id,
        event_type=_EVENT_TYPE,
        status="accepted",
        message="Release publish handoff acceptance recorded in ledger.",
        actor="harness",
        metadata={
            "dependency_id": dependency_id,
            **metadata,
        },
    )
    return dependency, event


def _source_record_blockers(
    acceptance: Mapping[str, Any],
    snapshot: RunLedgerSnapshot,
) -> tuple[str, ...]:
    blockers: list[str] = []
    dependency_id = acceptance.get("dependency_id")
    event_id = acceptance.get("event_id")
    run_id = acceptance.get("run_id")
    work_id = acceptance.get("work_id")
    package_digest = acceptance.get("package_digest")
    package_digest_prefix = acceptance.get("package_digest_prefix")
    dependencies = [
        record for record in snapshot.dependencies if record.dependency_id == dependency_id
    ]
    events = [event for event in snapshot.audit_events if event.event_id == event_id]
    if len(dependencies) != 1:
        blockers.append(
            "source-package-dependency-missing"
            if not dependencies
            else "source-package-dependency-ambiguous"
        )
    if len(events) != 1:
        blockers.append("source-package-event-missing" if not events else "source-package-event-ambiguous")
    if len(dependencies) == 1:
        record = dependencies[0]
        if record.dependency_type != _PACKAGE_DEPENDENCY_TYPE:
            blockers.append("source-package-dependency-type-mismatch")
        if record.required is not True:
            blockers.append("source-package-dependency-not-required")
        if record.status != "ready":
            blockers.append("source-package-dependency-status-not-ready")
        if record.work_id != work_id:
            blockers.append("source-package-dependency-work-id-mismatch")
        if set(record.metadata) != _PACKAGE_DEPENDENCY_METADATA_KEYS:
            blockers.append("source-package-dependency-metadata-schema-mismatch")
        if record.metadata.get("run_id") != run_id:
            blockers.append("source-package-dependency-run-id-mismatch")
        if record.metadata.get("package_digest") != package_digest:
            blockers.append("source-package-dependency-digest-mismatch")
        if record.metadata.get("package_digest_prefix") != package_digest_prefix:
            blockers.append("source-package-dependency-digest-prefix-mismatch")
        package_data = record.metadata.get("package_data")
        if isinstance(package_data, Mapping):
            if package_data.get("run_id") != run_id:
                blockers.append("source-package-dependency-package-data-run-id-mismatch")
            if package_data.get("work_id") != work_id:
                blockers.append("source-package-dependency-package-data-work-id-mismatch")
            if package_digest and _sha256_payload(package_data) != package_digest:
                blockers.append("source-package-dependency-package-data-digest-mismatch")
        else:
            blockers.append("source-package-dependency-package-data-missing")
        canonical_payload = record.metadata.get("canonical_payload")
        if isinstance(canonical_payload, Mapping):
            if canonical_payload.get("format") != _PACKAGE_LEDGER_FORMAT:
                blockers.append("source-package-dependency-canonical-payload-format-mismatch")
            if canonical_payload.get("handoff_package") != package_data:
                blockers.append("source-package-dependency-canonical-payload-package-mismatch")
        else:
            blockers.append("source-package-dependency-canonical-payload-missing")
    if len(events) == 1:
        event = events[0]
        if event.event_type != _PACKAGE_EVENT_TYPE:
            blockers.append("source-package-event-type-mismatch")
        if event.status != "ready":
            blockers.append("source-package-event-status-not-ready")
        if event.work_id != work_id:
            blockers.append("source-package-event-work-id-mismatch")
        if set(event.metadata) != _PACKAGE_EVENT_METADATA_KEYS:
            blockers.append("source-package-event-metadata-schema-mismatch")
        if event.metadata.get("dependency_id") != dependency_id:
            blockers.append("source-package-event-dependency-id-mismatch")
        if event.metadata.get("run_id") != run_id:
            blockers.append("source-package-event-run-id-mismatch")
        if event.metadata.get("package_digest") != package_digest:
            blockers.append("source-package-event-digest-mismatch")
        if event.metadata.get("package_digest_prefix") != package_digest_prefix:
            blockers.append("source-package-event-digest-prefix-mismatch")
    if len(dependencies) == 1 and len(events) == 1:
        record = dependencies[0]
        event = events[0]
        for key in _PACKAGE_DEPENDENCY_METADATA_KEYS:
            if event.metadata.get(key) != record.metadata.get(key):
                blockers.append(f"source-package-event-{key.replace('_', '-')}-parity-mismatch")
    return tuple(blockers)


def _duplicate_blockers(
    *,
    dependency: DependencyRecord,
    event: AuditEvent,
    package_digest: str,
    existing_dependency_ids: set[str],
    existing_event_ids: set[str],
    existing_package_digests: set[str],
    seen_dependency_ids: set[str],
    seen_event_ids: set[str],
    seen_package_digests: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if dependency.dependency_id in seen_dependency_ids:
        blockers.append("release-publish-handoff-acceptance-dependency-id-duplicate")
    elif dependency.dependency_id in existing_dependency_ids:
        blockers.append("release-publish-handoff-acceptance-dependency-id-already-recorded")
    if event.event_id in seen_event_ids:
        blockers.append("release-publish-handoff-acceptance-event-id-duplicate")
    elif event.event_id in existing_event_ids:
        blockers.append("release-publish-handoff-acceptance-event-id-already-recorded")
    if package_digest in seen_package_digests:
        blockers.append("release-publish-handoff-acceptance-package-digest-duplicate")
    elif package_digest in existing_package_digests:
        blockers.append("release-publish-handoff-acceptance-package-digest-already-recorded")
    return tuple(blockers)


def _existing_acceptance_package_digests(snapshot: RunLedgerSnapshot) -> set[str]:
    values: set[str] = set()
    for record in snapshot.dependencies:
        if record.dependency_type == _DEPENDENCY_TYPE and isinstance(record.metadata.get("package_digest"), str):
            values.add(record.metadata["package_digest"])
    for event in snapshot.audit_events:
        if event.event_type == _EVENT_TYPE and isinstance(event.metadata.get("package_digest"), str):
            values.add(event.metadata["package_digest"])
    return values


def _plain_mapping(
    value: object,
    blockers: list[str] | None = None,
) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return _validated_plain_mapping(value, blockers)
    if isinstance(value, ReleasePublishHandoffAcceptance):
        return _validated_plain_mapping(value.to_dict(), blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return _validated_plain_mapping(mapped, blockers)
        _append_malformed(blockers)
        return None
    return None


def _validated_plain_mapping(
    value: Mapping[Any, Any],
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            _append_malformed(blockers)
            if blockers is not None:
                blockers.append("non-string-key-release-publish-handoff-acceptance-ledger-data")
            continue
        plain[key] = _plain_value(item, blockers)
    return plain


def _plain_value(value: object, blockers: list[str] | None = None) -> object:
    mapped = _plain_mapping(value, blockers)
    if mapped is not None:
        return {key: mapped[key] for key in sorted(mapped)}
    if isinstance(value, list):
        return [_plain_value(item, blockers) for item in value]
    if isinstance(value, tuple):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    _append_malformed(blockers)
    if blockers is not None:
        blockers.append("unsupported-object-release-publish-handoff-acceptance-ledger-data")
    return None


def _required_mapping(
    value: object,
    name: str,
    blockers: list[str],
    *,
    required: bool = True,
) -> dict[str, Any]:
    if value is None and not required:
        return {}
    mapped = _plain_mapping(value, blockers)
    if mapped is None:
        blockers.append(f"missing-{name}" if required else f"malformed-{name}")
        return {}
    if required and not mapped:
        blockers.append(f"missing-{name}")
    return {key: mapped[key] for key in sorted(mapped)}


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value:
        blockers.append(f"missing-{name}")
        return ""
    if value != value.strip() or not _SAFE_IDENTIFIER.fullmatch(value):
        blockers.append(f"unsafe-{name}")
        return ""
    if _is_secret_like(value):
        blockers.append(f"secret-like-{name}")
        return ""
    if _has_action_text(value):
        blockers.append(f"action-intent-{name}")
        return ""
    return value


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value:
        blockers.append(f"missing-{name}")
        return ""
    if value != value.strip() or not _SHA256_HEX.fullmatch(value):
        blockers.append(f"invalid-{name}")
        return ""
    return value


def _digest_prefix(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value:
        blockers.append(f"missing-{name}")
        return ""
    if value != value.strip() or not _DIGEST_PREFIX.fullmatch(value):
        blockers.append(f"invalid-{name}")
        return ""
    return value


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


def _match(actual: object, expected: object, name: str, blockers: list[str]) -> None:
    if actual != expected:
        blockers.append(f"release-publish-handoff-acceptance-{name}-mismatch")


def _contains_secret_like(value: object) -> bool:
    if _is_secret_like(value):
        return True
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(_is_secret_like(key) or _contains_secret_like(item) for key, item in mapped.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_like(item) for item in value)
    return False


def _contains_action_intent(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(_is_action_key(key) or _contains_action_intent(item) for key, item in mapped.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_action_intent(item) for item in value)
    return _has_action_text(value)


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str) or value == "<redacted>":
        return False
    lowered = value.lower()
    if lowered == "metadata_keys":
        return False
    return any(term in lowered for term in _SECRET_TERMS)


def _is_action_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = "".join(character.lower() if character.isalnum() else "_" for character in value)
    return any(fragment in normalized for fragment in _ACTION_KEYS)


def _has_action_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = f" {value.strip().lower()} "
    return any(term in text for term in _ACTION_TEXT)


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sorted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _sorted_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return tuple(_sorted_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_sorted_value(item) for item in value)
    return value


def _hostile_snapshot_blockers(snapshot: RunLedgerSnapshot) -> tuple[str, ...]:
    blockers: list[str] = []
    metadata_values: list[object] = [snapshot.metadata]
    metadata_values.extend(decision.metadata for decision in snapshot.gate_decisions)
    metadata_values.extend(record.metadata for record in snapshot.dependencies)
    metadata_values.extend(event.metadata for event in snapshot.audit_events)
    metadata_values.extend(task.metadata for task in snapshot.tasks)
    for value in metadata_values:
        _validate_existing_metadata(value, blockers)
    return tuple(dict.fromkeys(blockers))


def _validate_existing_metadata(value: object, blockers: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                blockers.append(_MALFORMED_BLOCKER)
                continue
            if _is_secret_like(key) or _is_secret_like(item):
                blockers.append("secret-like-existing-ledger-metadata")
            if _is_action_key(key) or _has_action_text(item):
                blockers.append("action-intent-existing-ledger-metadata")
            _validate_existing_metadata(item, blockers)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_existing_metadata(item, blockers)
        return
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    blockers.append(_MALFORMED_BLOCKER)
    blockers.append("unsupported-object-existing-ledger-metadata")


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
    return {key: _freeze_snapshot_value(item) for key, item in value.items() if isinstance(key, str)}


def _plain_snapshot_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_snapshot_value(item) for key, item in value.items() if isinstance(key, str)}
    if isinstance(value, (list, tuple)):
        return tuple(_plain_snapshot_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return "<unsupported>"


def _append_malformed(blockers: list[str] | None) -> None:
    if blockers is not None:
        blockers.append(_MALFORMED_BLOCKER)


__all__ = [
    "ReleasePublishHandoffAcceptanceLedgerResult",
    "record_release_publish_handoff_acceptance",
    "record_release_publish_handoff_acceptances",
]
