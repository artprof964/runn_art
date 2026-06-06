"""Record release publish handoff readiness into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.release_publish_handoff_readiness import (
    ReleasePublishHandoffReadiness,
)
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
    TaskStatus,
)


_FORMAT = "harness-release-publish-handoff-readiness-ledger-v1"
_READINESS_FORMAT = "harness-release-publish-handoff-readiness-v1"
_DEPENDENCY_TYPE = "release-publish-handoff-readiness"
_EVENT_TYPE = "release-publish-handoff-readiness-ledger-record"
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_SUMMARY_KEYS = frozenset(
    {
        "format",
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "intent_digest_prefix",
        "release_binding_digest_prefix",
        "target_type",
        "target_id",
        "payload_digest_prefix",
        "artifact_id",
        "metadata_keys",
        "task_count",
        "require_finished_tasks",
    }
)
_TARGET_TYPES = frozenset({"local-dry-run", "manual-release-placeholder"})
_RESERVED_METADATA_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "canonical_digest",
        "canonical_payload",
        "handoff_readiness_digest",
        "handoff_readiness_digest_prefix",
        "intent_digest",
        "intent_digest_prefix",
        "release_binding_digest",
        "release_binding_digest_prefix",
        "secret",
        "token",
        "key",
        "password",
        "credential",
        "endpoint",
        "url",
        "uri",
        "webhook",
        "callback",
        "command",
        "cmd",
        "exec",
        "execute",
        "execution",
        "runner",
        "launcher",
        "shell",
    }
)
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
_MALFORMED_BLOCKER = "malformed-release-publish-handoff-readiness-ledger-data"


@dataclass(frozen=True)
class ReleasePublishHandoffReadinessLedgerResult:
    """Plain result from recording handoff readiness data."""

    recorded_event_ids: tuple[str, ...] = ()
    recorded_dependency_ids: tuple[str, ...] = ()
    skipped_event_ids: tuple[str, ...] = ()
    skipped_dependency_ids: tuple[str, ...] = ()
    skipped_handoff_readiness_digests: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_event_ids": self.recorded_event_ids,
            "recorded_dependency_ids": self.recorded_dependency_ids,
            "skipped_event_ids": self.skipped_event_ids,
            "skipped_dependency_ids": self.skipped_dependency_ids,
            "skipped_handoff_readiness_digests": self.skipped_handoff_readiness_digests,
            "blockers": self.blockers,
            "ledger_snapshot": (
                _snapshot_to_dict(self.ledger_snapshot)
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_release_publish_handoff_readinesses(
    readinesses: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishHandoffReadinessLedgerResult:
    """Record already-evaluated handoff readiness into an injected run ledger."""

    if not isinstance(ledger, RunLedger):
        return ReleasePublishHandoffReadinessLedgerResult(blockers=("ledger-missing",))

    items = _normalize_items(readinesses)
    if not items:
        return ReleasePublishHandoffReadinessLedgerResult(
            blockers=("release-publish-handoff-readiness-empty",),
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    snapshot = ledger.snapshot()
    ledger_blockers = _hostile_snapshot_blockers(snapshot)
    if ledger_blockers:
        return ReleasePublishHandoffReadinessLedgerResult(
            blockers=ledger_blockers,
            ledger_snapshot=_immutable_snapshot(snapshot),
        )

    blockers: list[str] = []
    staged: list[tuple[DependencyRecord, AuditEvent, str]] = []
    skipped_event_ids: list[str] = []
    skipped_dependency_ids: list[str] = []
    skipped_digests: list[str] = []
    seen_dependency_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    seen_digests: set[str] = set()
    existing_dependency_ids = {record.dependency_id for record in snapshot.dependencies}
    existing_event_ids = {event.event_id for event in snapshot.audit_events}
    existing_digests = _existing_handoff_readiness_digests(snapshot)

    for item in items:
        readiness, item_blockers = _readiness_from_item(item)
        dependency: DependencyRecord | None = None
        event: AuditEvent | None = None
        digest = ""
        if readiness is not None:
            if ledger.run_id != readiness["run_id"]:
                item_blockers.append("ledger-run-id-mismatch")
            item_blockers.extend(_source_record_blockers(readiness, snapshot))
            if not item_blockers:
                dependency, event, digest = _ledger_records(readiness)
                item_blockers.extend(
                    _duplicate_blockers(
                        dependency=dependency,
                        event=event,
                        handoff_readiness_digest=digest,
                        existing_dependency_ids=existing_dependency_ids,
                        existing_event_ids=existing_event_ids,
                        existing_digests=existing_digests,
                        seen_dependency_ids=seen_dependency_ids,
                        seen_event_ids=seen_event_ids,
                        seen_digests=seen_digests,
                    )
                )

        if item_blockers:
            blockers.extend(item_blockers)
            if dependency is not None:
                skipped_dependency_ids.append(dependency.dependency_id)
            if event is not None:
                skipped_event_ids.append(event.event_id)
            if digest:
                skipped_digests.append(digest)
            continue

        if dependency is not None and event is not None and digest:
            staged.append((dependency, event, digest))
            seen_dependency_ids.add(dependency.dependency_id)
            seen_event_ids.add(event.event_id)
            seen_digests.add(digest)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    if deduped_blockers or not staged:
        return ReleasePublishHandoffReadinessLedgerResult(
            skipped_event_ids=tuple(skipped_event_ids),
            skipped_dependency_ids=tuple(skipped_dependency_ids),
            skipped_handoff_readiness_digests=tuple(skipped_digests),
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

    return ReleasePublishHandoffReadinessLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        recorded_dependency_ids=tuple(recorded_dependency_ids),
        ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
    )


def record_release_publish_handoff_readiness(
    readiness: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishHandoffReadinessLedgerResult:
    """Compatibility wrapper for recording one handoff readiness result."""

    return record_release_publish_handoff_readinesses(readiness, ledger=ledger)


def _normalize_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, ReleasePublishHandoffReadiness):
        return (value,)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _readiness_from_item(item: object) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    mapped = _plain_mapping(item, blockers)
    if mapped is None:
        return None, ["release-publish-handoff-readiness-wrong-type"]

    if mapped.get("ready") is not True:
        blockers.append("release-publish-handoff-readiness-not-ready")
    if mapped.get("status") != "ready":
        blockers.append("release-publish-handoff-readiness-status-not-ready")
    source_blockers = _blocker_tuple(mapped.get("blockers"))
    if source_blockers:
        blockers.append("release-publish-handoff-readiness-blockers-present")
        blockers.extend(
            f"release-publish-handoff-readiness-{blocker}"
            for blocker in source_blockers
        )
    if _contains_secret_like(mapped):
        blockers.append("secret-like-release-publish-handoff-readiness-ledger-data")
    if _contains_action_intent(mapped):
        blockers.append("action-intent-release-publish-handoff-readiness-ledger-data")

    run_id = _required_text(mapped.get("run_id"), "run-id", blockers)
    work_id = _required_text(mapped.get("work_id"), "work-id", blockers)
    dependency_id = _required_text(mapped.get("dependency_id"), "dependency-id", blockers)
    event_id = _required_text(mapped.get("event_id"), "event-id", blockers)
    intent_prefix = _digest_prefix(
        mapped.get("intent_digest_prefix"),
        "intent-digest-prefix",
        blockers,
    )
    binding_prefix = _digest_prefix(
        mapped.get("release_binding_digest_prefix"),
        "release-binding-digest-prefix",
        blockers,
    )
    summary = _required_mapping(mapped.get("summary"), "summary", blockers)
    if summary:
        _validate_summary(
            summary=summary,
            run_id=run_id,
            work_id=work_id,
            dependency_id=dependency_id,
            event_id=event_id,
            intent_digest_prefix=intent_prefix,
            release_binding_digest_prefix=binding_prefix,
            blockers=blockers,
        )

    readiness = {
        "run_id": run_id,
        "work_id": work_id,
        "dependency_id": dependency_id,
        "event_id": event_id,
        "intent_digest_prefix": intent_prefix,
        "release_binding_digest_prefix": binding_prefix,
        "summary": _sorted_value(summary),
    }
    if blockers:
        return readiness, blockers
    return readiness, []


def _validate_summary(
    *,
    summary: Mapping[str, Any],
    run_id: str,
    work_id: str,
    dependency_id: str,
    event_id: str,
    intent_digest_prefix: str,
    release_binding_digest_prefix: str,
    blockers: list[str],
) -> None:
    if set(summary) != _SUMMARY_KEYS:
        blockers.append("unsafe-handoff-readiness-summary-schema")
    _match(summary.get("format"), _READINESS_FORMAT, "summary-format", blockers)
    _match(summary.get("run_id"), run_id, "summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "summary-work-id", blockers)
    _match(
        summary.get("dependency_id"),
        dependency_id,
        "summary-dependency-id",
        blockers,
    )
    _match(summary.get("event_id"), event_id, "summary-event-id", blockers)
    _match(
        summary.get("intent_digest_prefix"),
        intent_digest_prefix,
        "summary-intent-digest-prefix",
        blockers,
    )
    _match(
        summary.get("release_binding_digest_prefix"),
        release_binding_digest_prefix,
        "summary-release-binding-digest-prefix",
        blockers,
    )
    _validate_summary_text(summary.get("target_id"), "target-id", blockers)
    if summary.get("target_type") not in _TARGET_TYPES:
        blockers.append("unsafe-summary-target-type")
    _digest_prefix(summary.get("payload_digest_prefix"), "payload-digest-prefix", blockers)
    _validate_summary_text(summary.get("artifact_id"), "artifact-id", blockers)
    metadata_keys = summary.get("metadata_keys")
    if not isinstance(metadata_keys, tuple):
        blockers.append("unsafe-summary-metadata-keys")
    else:
        if tuple(sorted(metadata_keys)) != metadata_keys:
            blockers.append("unsafe-summary-metadata-keys")
        for key in metadata_keys:
            if (
                not isinstance(key, str)
                or not _safe_text_value(key)
                or key.lower() in _RESERVED_METADATA_KEYS
                or _is_secret_like(key)
                or _is_action_key(key)
                or _has_action_text(key)
            ):
                blockers.append("unsafe-summary-metadata-keys")
                break
    task_count = summary.get("task_count")
    if not isinstance(task_count, int) or isinstance(task_count, bool) or task_count < 0:
        blockers.append("unsafe-summary-task-count")
    if not isinstance(summary.get("require_finished_tasks"), bool):
        blockers.append("unsafe-summary-require-finished-tasks")


def _ledger_records(readiness: Mapping[str, Any]) -> tuple[DependencyRecord, AuditEvent, str]:
    canonical_payload = _canonical_payload(readiness)
    digest = _sha256_payload(canonical_payload)
    suffix = digest[:16]
    run_id = str(readiness["run_id"])
    work_id = str(readiness["work_id"])
    dependency_id = f"release-publish-handoff-readiness:{work_id}:{suffix}"
    event_id = f"release-publish-handoff-readiness-recorded:{work_id}:{suffix}"
    metadata = {
        "run_id": run_id,
        "source_dependency_id": readiness["dependency_id"],
        "source_event_id": readiness["event_id"],
        "intent_digest_prefix": readiness["intent_digest_prefix"],
        "release_binding_digest_prefix": readiness["release_binding_digest_prefix"],
        "handoff_readiness_digest": digest,
        "handoff_readiness_digest_prefix": digest[:12],
        "handoff_readiness_summary": readiness["summary"],
        "canonical_payload": canonical_payload,
    }
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"release-publish-handoff-readiness:{run_id}:{work_id}:{suffix}",
        order=100,
        dependency_type=_DEPENDENCY_TYPE,
        required=True,
        status="ready",
        metadata=metadata,
    )
    event = AuditEvent(
        event_id=event_id,
        work_id=work_id,
        event_type=_EVENT_TYPE,
        status="ready",
        message="Release publish handoff readiness recorded in ledger.",
        actor="harness",
        metadata={
            "dependency_id": dependency_id,
            **metadata,
        },
    )
    return dependency, event, digest


def _source_record_blockers(
    readiness: Mapping[str, Any],
    snapshot: RunLedgerSnapshot,
) -> tuple[str, ...]:
    blockers: list[str] = []
    dependency_id = readiness.get("dependency_id")
    event_id = readiness.get("event_id")
    run_id = readiness.get("run_id")
    work_id = readiness.get("work_id")
    dependencies = [
        record for record in snapshot.dependencies if record.dependency_id == dependency_id
    ]
    events = [event for event in snapshot.audit_events if event.event_id == event_id]
    if len(dependencies) != 1:
        blockers.append(
            "source-dependency-missing"
            if not dependencies
            else "source-dependency-ambiguous"
        )
    if len(events) != 1:
        blockers.append("source-event-missing" if not events else "source-event-ambiguous")
    if len(dependencies) == 1:
        dependency = dependencies[0]
        if dependency.dependency_type != "release-publish-intent":
            blockers.append("source-dependency-type-mismatch")
        if dependency.work_id != work_id:
            blockers.append("source-dependency-work-id-mismatch")
        if dependency.metadata.get("run_id") != run_id:
            blockers.append("source-dependency-run-id-mismatch")
        if dependency.metadata.get("intent_digest_prefix") != readiness.get(
            "intent_digest_prefix"
        ):
            blockers.append("source-dependency-intent-digest-prefix-mismatch")
        if dependency.metadata.get("release_binding_digest_prefix") != readiness.get(
            "release_binding_digest_prefix"
        ):
            blockers.append("source-dependency-release-binding-digest-prefix-mismatch")
    if len(events) == 1:
        event = events[0]
        if event.event_type != "release-publish-intent-ledger-record":
            blockers.append("source-event-type-mismatch")
        if event.work_id != work_id:
            blockers.append("source-event-work-id-mismatch")
        if event.metadata.get("run_id") != run_id:
            blockers.append("source-event-run-id-mismatch")
        if event.metadata.get("dependency_id") != dependency_id:
            blockers.append("source-event-dependency-id-mismatch")
        if event.metadata.get("intent_digest_prefix") != readiness.get(
            "intent_digest_prefix"
        ):
            blockers.append("source-event-intent-digest-prefix-mismatch")
        if event.metadata.get("release_binding_digest_prefix") != readiness.get(
            "release_binding_digest_prefix"
        ):
            blockers.append("source-event-release-binding-digest-prefix-mismatch")
    return tuple(blockers)


def _canonical_payload(readiness: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "handoff_readiness": {
            "run_id": readiness["run_id"],
            "work_id": readiness["work_id"],
            "dependency_id": readiness["dependency_id"],
            "event_id": readiness["event_id"],
            "intent_digest_prefix": readiness["intent_digest_prefix"],
            "release_binding_digest_prefix": readiness[
                "release_binding_digest_prefix"
            ],
            "summary": readiness["summary"],
        },
    }


def _duplicate_blockers(
    *,
    dependency: DependencyRecord,
    event: AuditEvent,
    handoff_readiness_digest: str,
    existing_dependency_ids: set[str],
    existing_event_ids: set[str],
    existing_digests: set[str],
    seen_dependency_ids: set[str],
    seen_event_ids: set[str],
    seen_digests: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if dependency.dependency_id in seen_dependency_ids:
        blockers.append("release-publish-handoff-readiness-dependency-id-duplicate")
    elif dependency.dependency_id in existing_dependency_ids:
        blockers.append(
            "release-publish-handoff-readiness-dependency-id-already-recorded"
        )
    if event.event_id in seen_event_ids:
        blockers.append("release-publish-handoff-readiness-event-id-duplicate")
    elif event.event_id in existing_event_ids:
        blockers.append("release-publish-handoff-readiness-event-id-already-recorded")
    if handoff_readiness_digest in seen_digests:
        blockers.append("release-publish-handoff-readiness-digest-duplicate")
    elif handoff_readiness_digest in existing_digests:
        blockers.append("release-publish-handoff-readiness-digest-already-recorded")
    return tuple(blockers)


def _existing_handoff_readiness_digests(snapshot: RunLedgerSnapshot) -> set[str]:
    values: set[str] = set()
    for record in (*snapshot.dependencies, *snapshot.audit_events):
        digest = record.metadata.get("handoff_readiness_digest")
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
            if blockers is not None:
                blockers.append(
                    "non-string-key-release-publish-handoff-readiness-ledger-data"
                )
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
        blockers.append("unsupported-object-release-publish-handoff-readiness-ledger-data")
    return None


def _required_mapping(
    value: object,
    name: str,
    blockers: list[str],
) -> dict[str, Any]:
    mapped = _plain_mapping(value, blockers)
    if mapped is None:
        blockers.append(f"missing-{name}")
        return {}
    if not mapped:
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


def _digest_prefix(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value:
        blockers.append(f"missing-{name}")
        return ""
    if value != value.strip() or not _DIGEST_PREFIX.fullmatch(value):
        blockers.append(f"invalid-{name}")
        return ""
    return value


def _validate_summary_text(value: object, name: str, blockers: list[str]) -> None:
    if not _safe_text_value(value):
        blockers.append(f"unsafe-summary-{name}")


def _safe_text_value(value: object, *, required: bool = True) -> bool:
    if value in (None, "") and not required:
        return True
    return isinstance(value, str) and _SAFE_TEXT.fullmatch(value) is not None


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
        blockers.append(f"release-publish-handoff-readiness-{name}-mismatch")


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


def _contains_action_intent(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            _is_action_key(key) or _contains_action_intent(item)
            for key, item in mapped.items()
        )
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
    normalized = "".join(
        character.lower() if character.isalnum() else "_"
        for character in value
    )
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
    return {
        key: _freeze_snapshot_value(item)
        for key, item in value.items()
        if isinstance(key, str)
    }


def _plain_snapshot_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _plain_snapshot_value(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        return tuple(_plain_snapshot_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return "<unsupported>"


def _append_malformed(blockers: list[str] | None) -> None:
    if blockers is not None:
        blockers.append(_MALFORMED_BLOCKER)


__all__ = [
    "ReleasePublishHandoffReadinessLedgerResult",
    "record_release_publish_handoff_readiness",
    "record_release_publish_handoff_readinesses",
]
