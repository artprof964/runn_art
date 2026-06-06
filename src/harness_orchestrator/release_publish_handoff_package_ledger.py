"""Record release publish handoff packages into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.release_publish_handoff_package import (
    ReleasePublishHandoffPackage,
)
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
    TaskStatus,
)


_FORMAT = "harness-release-publish-handoff-package-ledger-v1"
_PACKAGE_FORMAT = "harness-release-publish-handoff-package-v1"
_DEPENDENCY_TYPE = "release-publish-handoff-package"
_EVENT_TYPE = "release-publish-handoff-package-ledger-record"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_TARGET_TYPES = frozenset({"local-dry-run", "manual-release-placeholder"})
_PACKAGE_RESULT_KEYS = frozenset(
    {
        "ready",
        "status",
        "blockers",
        "run_id",
        "work_id",
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
    }
)
_PACKAGE_DATA_KEYS = frozenset(
    {
        "format",
        "run_id",
        "work_id",
        "readiness_dependency_id",
        "readiness_event_id",
        "handoff_readiness_digest",
        "intent_dependency_id",
        "intent_event_id",
        "intent_digest",
        "release_binding_digest",
        "publish_target",
        "publish_payload",
        "artifact",
        "metadata",
    }
)
_PUBLISH_TARGET_KEYS = frozenset({"target_id", "target_type"})
_PUBLISH_PAYLOAD_ALLOWED_KEYS = frozenset({"payload_digest", "payload_label"})
_ARTIFACT_KEYS = frozenset({"artifact_id"})
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
        "publish_target",
        "publish_payload",
        "artifact",
        "metadata",
        "intent_metadata",
        "summary",
        "credentials",
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
_MALFORMED_BLOCKER = "malformed-release-publish-handoff-package-ledger-data"


@dataclass(frozen=True)
class ReleasePublishHandoffPackageLedgerResult:
    """Plain result from recording handoff package data."""

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


def record_release_publish_handoff_packages(
    packages: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishHandoffPackageLedgerResult:
    """Record already-built handoff package data into an injected run ledger."""

    if not isinstance(ledger, RunLedger):
        return ReleasePublishHandoffPackageLedgerResult(blockers=("ledger-missing",))

    items = _normalize_items(packages)
    if not items:
        return ReleasePublishHandoffPackageLedgerResult(
            blockers=("release-publish-handoff-packages-empty",),
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    snapshot = ledger.snapshot()
    ledger_blockers = _hostile_snapshot_blockers(snapshot)
    if ledger_blockers:
        return ReleasePublishHandoffPackageLedgerResult(
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
    existing_package_digests = _existing_package_digests(snapshot)

    for item in items:
        package, item_blockers = _package_from_item(item)
        dependency: DependencyRecord | None = None
        event: AuditEvent | None = None
        package_digest = ""
        if package is not None:
            package_digest = str(package["package_digest"])
            if ledger.run_id != package["run_id"]:
                item_blockers.append("ledger-run-id-mismatch")
            item_blockers.extend(_source_record_blockers(package, snapshot))
            if not item_blockers:
                dependency, event = _ledger_records(package)
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
        return ReleasePublishHandoffPackageLedgerResult(
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

    return ReleasePublishHandoffPackageLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        recorded_dependency_ids=tuple(recorded_dependency_ids),
        ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
    )


def record_release_publish_handoff_package(
    package: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishHandoffPackageLedgerResult:
    """Compatibility wrapper for recording one handoff package result."""

    return record_release_publish_handoff_packages(package, ledger=ledger)


def _normalize_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, ReleasePublishHandoffPackage):
        return (value,)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _package_from_item(item: object) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    mapped = _plain_mapping(item, blockers)
    if mapped is None:
        return None, ["release-publish-handoff-package-wrong-type"]

    if set(mapped) != _PACKAGE_RESULT_KEYS:
        blockers.append("unsafe-release-publish-handoff-package-schema")
    if mapped.get("ready") is not True:
        blockers.append("release-publish-handoff-package-not-ready")
    if mapped.get("status") != "ready":
        blockers.append("release-publish-handoff-package-status-not-ready")
    source_blockers = _blocker_tuple(mapped.get("blockers"))
    if source_blockers:
        blockers.append("release-publish-handoff-package-blockers-present")
        blockers.extend(f"release-publish-handoff-package-{blocker}" for blocker in source_blockers)

    run_id = _required_text(mapped.get("run_id"), "run-id", blockers)
    work_id = _required_text(mapped.get("work_id"), "work-id", blockers)
    readiness_dependency_id = _required_text(
        mapped.get("readiness_dependency_id"),
        "readiness-dependency-id",
        blockers,
    )
    readiness_event_id = _required_text(
        mapped.get("readiness_event_id"),
        "readiness-event-id",
        blockers,
    )
    intent_dependency_id = _required_text(
        mapped.get("intent_dependency_id"),
        "intent-dependency-id",
        blockers,
    )
    intent_event_id = _required_text(
        mapped.get("intent_event_id"),
        "intent-event-id",
        blockers,
    )
    intent_digest_prefix = _digest_prefix(
        mapped.get("intent_digest_prefix"),
        "intent-digest-prefix",
        blockers,
    )
    release_binding_digest_prefix = _digest_prefix(
        mapped.get("release_binding_digest_prefix"),
        "release-binding-digest-prefix",
        blockers,
    )
    handoff_readiness_digest_prefix = _digest_prefix(
        mapped.get("handoff_readiness_digest_prefix"),
        "handoff-readiness-digest-prefix",
        blockers,
    )
    package_digest = _digest_text(mapped.get("package_digest"), "package-digest", blockers)
    package_digest_prefix = _digest_prefix(
        mapped.get("package_digest_prefix"),
        "package-digest-prefix",
        blockers,
    )
    package_data = _required_mapping(mapped.get("package_data"), "package-data", blockers)

    if package_data:
        _validate_package_data(
            package_data=package_data,
            run_id=run_id,
            work_id=work_id,
            readiness_dependency_id=readiness_dependency_id,
            readiness_event_id=readiness_event_id,
            intent_dependency_id=intent_dependency_id,
            intent_event_id=intent_event_id,
            intent_digest_prefix=intent_digest_prefix,
            release_binding_digest_prefix=release_binding_digest_prefix,
            handoff_readiness_digest_prefix=handoff_readiness_digest_prefix,
            package_digest=package_digest,
            package_digest_prefix=package_digest_prefix,
            blockers=blockers,
        )

    if _contains_secret_like(package_data):
        blockers.append("secret-like-release-publish-handoff-package-ledger-data")
    if _contains_action_intent(package_data):
        blockers.append("action-intent-release-publish-handoff-package-ledger-data")

    package = {
        "run_id": run_id,
        "work_id": work_id,
        "readiness_dependency_id": readiness_dependency_id,
        "readiness_event_id": readiness_event_id,
        "intent_dependency_id": intent_dependency_id,
        "intent_event_id": intent_event_id,
        "intent_digest_prefix": intent_digest_prefix,
        "release_binding_digest_prefix": release_binding_digest_prefix,
        "handoff_readiness_digest_prefix": handoff_readiness_digest_prefix,
        "package_digest": package_digest,
        "package_digest_prefix": package_digest_prefix,
        "package_data": _sorted_value(package_data),
    }
    if blockers:
        return package, blockers
    return package, []


def _validate_package_data(
    *,
    package_data: Mapping[str, Any],
    run_id: str,
    work_id: str,
    readiness_dependency_id: str,
    readiness_event_id: str,
    intent_dependency_id: str,
    intent_event_id: str,
    intent_digest_prefix: str,
    release_binding_digest_prefix: str,
    handoff_readiness_digest_prefix: str,
    package_digest: str,
    package_digest_prefix: str,
    blockers: list[str],
) -> None:
    if set(package_data) != _PACKAGE_DATA_KEYS:
        blockers.append("unsafe-package-data-schema")
    _match(package_data.get("format"), _PACKAGE_FORMAT, "package-data-format", blockers)
    _match(package_data.get("run_id"), run_id, "package-data-run-id", blockers)
    _match(package_data.get("work_id"), work_id, "package-data-work-id", blockers)
    _match(
        package_data.get("readiness_dependency_id"),
        readiness_dependency_id,
        "package-data-readiness-dependency-id",
        blockers,
    )
    _match(
        package_data.get("readiness_event_id"),
        readiness_event_id,
        "package-data-readiness-event-id",
        blockers,
    )
    _match(
        package_data.get("intent_dependency_id"),
        intent_dependency_id,
        "package-data-intent-dependency-id",
        blockers,
    )
    _match(
        package_data.get("intent_event_id"),
        intent_event_id,
        "package-data-intent-event-id",
        blockers,
    )
    handoff_digest = _digest_text(
        package_data.get("handoff_readiness_digest"),
        "handoff-readiness-digest",
        blockers,
    )
    intent_digest = _digest_text(package_data.get("intent_digest"), "intent-digest", blockers)
    release_binding_digest = _digest_text(
        package_data.get("release_binding_digest"),
        "release-binding-digest",
        blockers,
    )
    _match(intent_digest[:12], intent_digest_prefix, "intent-digest-prefix", blockers)
    _match(
        release_binding_digest[:12],
        release_binding_digest_prefix,
        "release-binding-digest-prefix",
        blockers,
    )
    _match(
        handoff_digest[:12],
        handoff_readiness_digest_prefix,
        "handoff-readiness-digest-prefix",
        blockers,
    )
    _validate_publish_target(
        _required_mapping(package_data.get("publish_target"), "publish-target", blockers),
        blockers,
    )
    _validate_publish_payload(
        _required_mapping(package_data.get("publish_payload"), "publish-payload", blockers),
        blockers,
    )
    _validate_artifact(
        _required_mapping(package_data.get("artifact"), "artifact", blockers),
        blockers,
    )
    _validate_metadata_values(
        _required_mapping(package_data.get("metadata"), "metadata", blockers, required=False),
        blockers,
    )
    if package_digest:
        recomputed = _sha256_payload(package_data)
        if recomputed != package_digest:
            blockers.append("release-publish-handoff-package-digest-mismatch")
        _match(package_digest[:12], package_digest_prefix, "package-digest-prefix", blockers)


def _ledger_records(package: Mapping[str, Any]) -> tuple[DependencyRecord, AuditEvent]:
    package_digest = str(package["package_digest"])
    suffix = package_digest[:16]
    run_id = str(package["run_id"])
    work_id = str(package["work_id"])
    dependency_id = f"release-publish-handoff-package:{work_id}:{suffix}"
    event_id = f"release-publish-handoff-package-recorded:{work_id}:{suffix}"
    metadata = {
        "run_id": run_id,
        "readiness_dependency_id": package["readiness_dependency_id"],
        "readiness_event_id": package["readiness_event_id"],
        "intent_dependency_id": package["intent_dependency_id"],
        "intent_event_id": package["intent_event_id"],
        "intent_digest_prefix": package["intent_digest_prefix"],
        "release_binding_digest_prefix": package["release_binding_digest_prefix"],
        "handoff_readiness_digest_prefix": package["handoff_readiness_digest_prefix"],
        "package_digest": package_digest,
        "package_digest_prefix": package["package_digest_prefix"],
        "package_data": package["package_data"],
        "canonical_payload": {
            "format": _FORMAT,
            "handoff_package": package["package_data"],
        },
    }
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"release-publish-handoff-package:{run_id}:{work_id}:{suffix}",
        order=105,
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
        message="Release publish handoff package recorded in ledger.",
        actor="harness",
        metadata={
            "dependency_id": dependency_id,
            **metadata,
        },
    )
    return dependency, event


def _source_record_blockers(
    package: Mapping[str, Any],
    snapshot: RunLedgerSnapshot,
) -> tuple[str, ...]:
    blockers: list[str] = []
    readiness_dependency_id = package.get("readiness_dependency_id")
    readiness_event_id = package.get("readiness_event_id")
    intent_dependency_id = package.get("intent_dependency_id")
    intent_event_id = package.get("intent_event_id")
    run_id = package.get("run_id")
    work_id = package.get("work_id")
    readiness_dependencies = [
        record for record in snapshot.dependencies if record.dependency_id == readiness_dependency_id
    ]
    readiness_events = [
        event for event in snapshot.audit_events if event.event_id == readiness_event_id
    ]
    intent_dependencies = [
        record for record in snapshot.dependencies if record.dependency_id == intent_dependency_id
    ]
    intent_events = [
        event for event in snapshot.audit_events if event.event_id == intent_event_id
    ]
    if len(readiness_dependencies) != 1:
        blockers.append("source-readiness-dependency-missing" if not readiness_dependencies else "source-readiness-dependency-ambiguous")
    if len(readiness_events) != 1:
        blockers.append("source-readiness-event-missing" if not readiness_events else "source-readiness-event-ambiguous")
    if len(intent_dependencies) != 1:
        blockers.append("source-intent-dependency-missing" if not intent_dependencies else "source-intent-dependency-ambiguous")
    if len(intent_events) != 1:
        blockers.append("source-intent-event-missing" if not intent_events else "source-intent-event-ambiguous")
    if len(readiness_dependencies) == 1:
        record = readiness_dependencies[0]
        if record.dependency_type != "release-publish-handoff-readiness":
            blockers.append("source-readiness-dependency-type-mismatch")
        if record.work_id != work_id:
            blockers.append("source-readiness-dependency-work-id-mismatch")
        if record.metadata.get("run_id") != run_id:
            blockers.append("source-readiness-dependency-run-id-mismatch")
        if record.metadata.get("handoff_readiness_digest_prefix") != package.get(
            "handoff_readiness_digest_prefix"
        ):
            blockers.append("source-readiness-dependency-digest-prefix-mismatch")
    if len(readiness_events) == 1:
        event = readiness_events[0]
        if event.event_type != "release-publish-handoff-readiness-ledger-record":
            blockers.append("source-readiness-event-type-mismatch")
        if event.work_id != work_id:
            blockers.append("source-readiness-event-work-id-mismatch")
        if event.metadata.get("run_id") != run_id:
            blockers.append("source-readiness-event-run-id-mismatch")
        if event.metadata.get("dependency_id") != readiness_dependency_id:
            blockers.append("source-readiness-event-dependency-id-mismatch")
    if len(intent_dependencies) == 1:
        record = intent_dependencies[0]
        if record.dependency_type != "release-publish-intent":
            blockers.append("source-intent-dependency-type-mismatch")
        if record.work_id != work_id:
            blockers.append("source-intent-dependency-work-id-mismatch")
        if record.metadata.get("run_id") != run_id:
            blockers.append("source-intent-dependency-run-id-mismatch")
        if record.metadata.get("intent_digest_prefix") != package.get("intent_digest_prefix"):
            blockers.append("source-intent-dependency-digest-prefix-mismatch")
        if record.metadata.get("release_binding_digest_prefix") != package.get(
            "release_binding_digest_prefix"
        ):
            blockers.append("source-intent-dependency-release-binding-prefix-mismatch")
    if len(intent_events) == 1:
        event = intent_events[0]
        if event.event_type != "release-publish-intent-ledger-record":
            blockers.append("source-intent-event-type-mismatch")
        if event.work_id != work_id:
            blockers.append("source-intent-event-work-id-mismatch")
        if event.metadata.get("run_id") != run_id:
            blockers.append("source-intent-event-run-id-mismatch")
        if event.metadata.get("dependency_id") != intent_dependency_id:
            blockers.append("source-intent-event-dependency-id-mismatch")
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
        blockers.append("release-publish-handoff-package-dependency-id-duplicate")
    elif dependency.dependency_id in existing_dependency_ids:
        blockers.append("release-publish-handoff-package-dependency-id-already-recorded")
    if event.event_id in seen_event_ids:
        blockers.append("release-publish-handoff-package-event-id-duplicate")
    elif event.event_id in existing_event_ids:
        blockers.append("release-publish-handoff-package-event-id-already-recorded")
    if package_digest in seen_package_digests:
        blockers.append("release-publish-handoff-package-digest-duplicate")
    elif package_digest in existing_package_digests:
        blockers.append("release-publish-handoff-package-digest-already-recorded")
    return tuple(blockers)


def _existing_package_digests(snapshot: RunLedgerSnapshot) -> set[str]:
    values: set[str] = set()
    for record in (*snapshot.dependencies, *snapshot.audit_events):
        digest = record.metadata.get("package_digest")
        if isinstance(digest, str):
            values.add(digest)
    return values


def _plain_mapping(
    value: object,
    blockers: list[str] | None = None,
) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return _validated_plain_mapping(value, blockers)
    if isinstance(value, ReleasePublishHandoffPackage):
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
                blockers.append("non-string-key-release-publish-handoff-package-ledger-data")
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
        blockers.append("unsupported-object-release-publish-handoff-package-ledger-data")
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


def _validate_publish_target(value: Mapping[str, Any], blockers: list[str]) -> None:
    if (
        set(value) != _PUBLISH_TARGET_KEYS
        or value.get("target_type") not in _TARGET_TYPES
        or not _safe_text_value(value.get("target_id"))
    ):
        blockers.append("unsafe-publish-target-schema")


def _validate_publish_payload(value: Mapping[str, Any], blockers: list[str]) -> None:
    keys = set(value)
    if (
        "payload_digest" not in keys
        or not keys.issubset(_PUBLISH_PAYLOAD_ALLOWED_KEYS)
        or not _valid_digest(value.get("payload_digest"))
    ):
        blockers.append("unsafe-publish-payload-schema")
        return
    if "payload_label" in value and not _safe_text_value(value.get("payload_label"), required=False):
        blockers.append("unsafe-publish-payload-schema")


def _validate_artifact(value: Mapping[str, Any], blockers: list[str]) -> None:
    if set(value) != _ARTIFACT_KEYS or not _safe_text_value(value.get("artifact_id")):
        blockers.append("unsafe-artifact-schema")


def _validate_metadata_values(value: Mapping[str, Any], blockers: list[str]) -> None:
    for key, item in value.items():
        if not _safe_metadata_key(key):
            blockers.append("unsafe-package-metadata-schema")
            continue
        if isinstance(item, bool) or isinstance(item, int):
            continue
        if isinstance(item, str) and _safe_text_value(item, required=False) and not _is_secret_like(item) and not _has_action_text(item):
            continue
        blockers.append("unsafe-package-metadata-schema")


def _safe_text_value(value: object, *, required: bool = True) -> bool:
    if value in (None, "") and not required:
        return True
    return isinstance(value, str) and _SAFE_TEXT.fullmatch(value) is not None


def _safe_metadata_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and _safe_text_value(value)
        and value.lower() not in _RESERVED_METADATA_KEYS
        and not _is_secret_like(value)
        and not _is_action_key(value)
        and not _has_action_text(value)
    )


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


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
        blockers.append(f"release-publish-handoff-package-{name}-mismatch")


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
    if isinstance(value, tuple):
        return tuple(_sorted_value(item) for item in value)
    if isinstance(value, list):
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
    "ReleasePublishHandoffPackageLedgerResult",
    "record_release_publish_handoff_package",
    "record_release_publish_handoff_packages",
]
