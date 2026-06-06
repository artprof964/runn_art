"""Pure Harness-only release publish handoff package boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.run_ledger import RunLedgerSnapshot


_FORMAT = "harness-release-publish-handoff-package-v1"
_READINESS_LEDGER_FORMAT = "harness-release-publish-handoff-readiness-ledger-v1"
_READINESS_FORMAT = "harness-release-publish-handoff-readiness-v1"
_INTENT_FORMAT = "harness-release-publish-intent-v1"
_READINESS_DEPENDENCY_TYPE = "release-publish-handoff-readiness"
_READINESS_EVENT_TYPE = "release-publish-handoff-readiness-ledger-record"
_INTENT_DEPENDENCY_TYPE = "release-publish-intent"
_INTENT_EVENT_TYPE = "release-publish-intent-ledger-record"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_TARGET_TYPES = frozenset({"local-dry-run", "manual-release-placeholder"})
_PUBLISH_TARGET_KEYS = frozenset({"target_id", "target_type"})
_PUBLISH_PAYLOAD_ALLOWED_KEYS = frozenset({"payload_digest", "payload_label"})
_ARTIFACT_KEYS = frozenset({"artifact_id"})
_READINESS_SUMMARY_KEYS = frozenset(
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
_READINESS_CANONICAL_KEYS = frozenset({"format", "handoff_readiness"})
_READINESS_CANONICAL_RECORD_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "intent_digest_prefix",
        "release_binding_digest_prefix",
        "summary",
    }
)
_INTENT_CANONICAL_KEYS = frozenset(
    {"format", "readiness_binding", "caller_supplied_intent_metadata"}
)
_INTENT_READINESS_BINDING_KEYS = frozenset(
    {"run_id", "work_id", "canonical_digest_prefix", "release_binding_digest", "source"}
)
_INTENT_CALLER_METADATA_KEYS = frozenset(
    {"publish_target", "publish_payload", "artifact", "metadata", "verification_status"}
)
_PACKAGE_KEYS = frozenset(
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


@dataclass(frozen=True)
class ReleasePublishHandoffPackage:
    """Frozen, JSON-safe release publish handoff package result."""

    ready: bool = False
    status: str = "blocked"
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    work_id: str = ""
    readiness_dependency_id: str = ""
    readiness_event_id: str = ""
    intent_dependency_id: str = ""
    intent_event_id: str = ""
    intent_digest_prefix: str = ""
    release_binding_digest_prefix: str = ""
    handoff_readiness_digest_prefix: str = ""
    package_digest: str = ""
    package_digest_prefix: str = ""
    package_data: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "run_id": self.run_id,
            "work_id": self.work_id,
            "readiness_dependency_id": self.readiness_dependency_id,
            "readiness_event_id": self.readiness_event_id,
            "intent_dependency_id": self.intent_dependency_id,
            "intent_event_id": self.intent_event_id,
            "intent_digest_prefix": self.intent_digest_prefix,
            "release_binding_digest_prefix": self.release_binding_digest_prefix,
            "handoff_readiness_digest_prefix": self.handoff_readiness_digest_prefix,
            "package_digest": self.package_digest,
            "package_digest_prefix": self.package_digest_prefix,
            "package_data": _plain_copy(self.package_data),
        }


def build_release_publish_handoff_package(
    ledger_snapshot: object,
    *,
    run_id: object,
    work_id: object,
) -> ReleasePublishHandoffPackage:
    """Build a fail-closed package from explicit snapshot-shaped data."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _required_text(work_id, "work-id", blockers)
    snapshot = _snapshot_mapping(ledger_snapshot, blockers)
    if snapshot is None:
        blockers.append("missing-ledger-snapshot")
        snapshot = {}

    snapshot_run_id = _required_text(snapshot.get("run_id"), "snapshot-run-id", blockers)
    _match_text(snapshot_run_id, expected_run_id, "snapshot-run-id", blockers)
    _check_secret_or_action("snapshot", snapshot, blockers)

    dependencies = _record_sequence(snapshot.get("dependencies"), "dependencies", blockers)
    audit_events = _record_sequence(snapshot.get("audit_events"), "audit-events", blockers)
    readiness_dependencies = _matching_records(
        dependencies,
        "dependency_type",
        _READINESS_DEPENDENCY_TYPE,
        expected_work_id,
        blockers,
    )
    readiness_events = _matching_records(
        audit_events,
        "event_type",
        _READINESS_EVENT_TYPE,
        expected_work_id,
        blockers,
    )
    if len(readiness_dependencies) != 1:
        blockers.append(
            "release-publish-handoff-readiness-dependency-missing"
            if not readiness_dependencies
            else "release-publish-handoff-readiness-dependency-ambiguous"
        )
    if len(readiness_events) != 1:
        blockers.append(
            "release-publish-handoff-readiness-event-missing"
            if not readiness_events
            else "release-publish-handoff-readiness-event-ambiguous"
        )

    readiness_dependency = readiness_dependencies[0] if len(readiness_dependencies) == 1 else {}
    readiness_event = readiness_events[0] if len(readiness_events) == 1 else {}
    package_data: Mapping[str, Any] = {}
    if readiness_dependency and readiness_event:
        package_data = _validate_chain_and_package(
            readiness_dependency=readiness_dependency,
            readiness_event=readiness_event,
            dependencies=dependencies,
            audit_events=audit_events,
            run_id=expected_run_id,
            work_id=expected_work_id,
            blockers=blockers,
        )

    deduped = tuple(dict.fromkeys(blockers))
    ready = not deduped
    package_digest = _sha256_payload(package_data) if ready else ""
    return ReleasePublishHandoffPackage(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped,
        run_id=expected_run_id or snapshot_run_id,
        work_id=expected_work_id,
        readiness_dependency_id=str(readiness_dependency.get("dependency_id", "")),
        readiness_event_id=str(readiness_event.get("event_id", "")),
        intent_dependency_id=str(package_data.get("intent_dependency_id", "")),
        intent_event_id=str(package_data.get("intent_event_id", "")),
        intent_digest_prefix=_digest_or_empty(package_data.get("intent_digest"))[:12],
        release_binding_digest_prefix=_digest_or_empty(
            package_data.get("release_binding_digest")
        )[:12],
        handoff_readiness_digest_prefix=_digest_or_empty(
            package_data.get("handoff_readiness_digest")
        )[:12],
        package_digest=package_digest,
        package_digest_prefix=package_digest[:12],
        package_data=_freeze_value(package_data) if ready else None,
    )


def evaluate_release_publish_handoff_package(
    ledger_snapshot: object,
    *,
    run_id: object,
    work_id: object,
) -> ReleasePublishHandoffPackage:
    """Compatibility alias for package evaluation."""

    return build_release_publish_handoff_package(
        ledger_snapshot,
        run_id=run_id,
        work_id=work_id,
    )


def _validate_chain_and_package(
    *,
    readiness_dependency: Mapping[str, Any],
    readiness_event: Mapping[str, Any],
    dependencies: tuple[dict[str, Any], ...],
    audit_events: tuple[dict[str, Any], ...],
    run_id: str,
    work_id: str,
    blockers: list[str],
) -> Mapping[str, Any]:
    _validate_readiness_record(readiness_dependency, "dependency", work_id, blockers)
    _validate_readiness_record(readiness_event, "event", work_id, blockers)
    readiness_metadata = _metadata(readiness_dependency, "readiness-dependency", blockers)
    event_metadata = _metadata(readiness_event, "readiness-event", blockers)
    _match_text(
        _required_text(event_metadata.get("dependency_id"), "readiness-event-dependency-id", blockers),
        _required_text(readiness_dependency.get("dependency_id"), "readiness-dependency-id", blockers),
        "readiness-event-dependency-id",
        blockers,
    )
    for key in (
        "run_id",
        "source_dependency_id",
        "source_event_id",
        "intent_digest_prefix",
        "release_binding_digest_prefix",
        "handoff_readiness_digest",
        "handoff_readiness_digest_prefix",
        "handoff_readiness_summary",
        "canonical_payload",
    ):
        if readiness_metadata.get(key) != event_metadata.get(key):
            blockers.append(f"release-publish-handoff-readiness-event-{_label(key)}-mismatch")

    _match_text(
        _required_text(readiness_metadata.get("run_id"), "readiness-run-id", blockers),
        run_id,
        "readiness-run-id",
        blockers,
    )
    source_dependency_id = _required_text(
        readiness_metadata.get("source_dependency_id"),
        "source-dependency-id",
        blockers,
    )
    source_event_id = _required_text(
        readiness_metadata.get("source_event_id"),
        "source-event-id",
        blockers,
    )
    handoff_digest = _digest_text(
        readiness_metadata.get("handoff_readiness_digest"),
        "handoff-readiness-digest",
        blockers,
    )
    _match_text(
        _required_text(readiness_dependency.get("dependency_id"), "readiness-dependency-id", blockers),
        f"release-publish-handoff-readiness:{work_id}:{handoff_digest[:16]}",
        "release-publish-handoff-readiness-dependency-id",
        blockers,
    )
    _match_text(
        _required_text(readiness_event.get("event_id"), "readiness-event-id", blockers),
        f"release-publish-handoff-readiness-recorded:{work_id}:{handoff_digest[:16]}",
        "release-publish-handoff-readiness-event-id",
        blockers,
    )
    _match_text(
        _digest_prefix(
            readiness_metadata.get("handoff_readiness_digest_prefix"),
            "handoff-readiness-digest-prefix",
            blockers,
        ),
        handoff_digest[:12],
        "handoff-readiness-digest-prefix",
        blockers,
    )
    summary = _metadata_mapping(
        readiness_metadata.get("handoff_readiness_summary"),
        "handoff-readiness-summary",
        blockers,
    )
    canonical = _metadata_mapping(
        readiness_metadata.get("canonical_payload"),
        "handoff-readiness-canonical-payload",
        blockers,
    )
    _validate_readiness_summary(
        summary=summary,
        run_id=run_id,
        work_id=work_id,
        source_dependency_id=source_dependency_id,
        source_event_id=source_event_id,
        intent_digest_prefix=_digest_prefix(
            readiness_metadata.get("intent_digest_prefix"),
            "intent-digest-prefix",
            blockers,
        ),
        release_binding_digest_prefix=_digest_prefix(
            readiness_metadata.get("release_binding_digest_prefix"),
            "release-binding-digest-prefix",
            blockers,
        ),
        blockers=blockers,
    )
    _validate_readiness_canonical(
        canonical=canonical,
        summary=summary,
        run_id=run_id,
        work_id=work_id,
        source_dependency_id=source_dependency_id,
        source_event_id=source_event_id,
        handoff_digest=handoff_digest,
        blockers=blockers,
    )

    source_dependencies = tuple(
        record for record in dependencies if record.get("dependency_id") == source_dependency_id
    )
    source_events = tuple(event for event in audit_events if event.get("event_id") == source_event_id)
    if len(source_dependencies) != 1:
        blockers.append("source-release-publish-intent-dependency-missing" if not source_dependencies else "source-release-publish-intent-dependency-ambiguous")
    if len(source_events) != 1:
        blockers.append("source-release-publish-intent-event-missing" if not source_events else "source-release-publish-intent-event-ambiguous")
    if len(source_dependencies) != 1 or len(source_events) != 1:
        return {}

    intent = _validate_intent_pair(
        dependency=source_dependencies[0],
        event=source_events[0],
        run_id=run_id,
        work_id=work_id,
        expected_intent_prefix=str(readiness_metadata.get("intent_digest_prefix", "")),
        expected_binding_prefix=str(readiness_metadata.get("release_binding_digest_prefix", "")),
        blockers=blockers,
    )
    if not intent:
        return {}
    return _sorted_value(
        {
            "format": _FORMAT,
            "run_id": run_id,
            "work_id": work_id,
            "readiness_dependency_id": readiness_dependency.get("dependency_id"),
            "readiness_event_id": readiness_event.get("event_id"),
            "handoff_readiness_digest": handoff_digest,
            "intent_dependency_id": source_dependency_id,
            "intent_event_id": source_event_id,
            "intent_digest": intent["intent_digest"],
            "release_binding_digest": intent["release_binding_digest"],
            "publish_target": intent["publish_target"],
            "publish_payload": intent["publish_payload"],
            "artifact": intent["artifact"],
            "metadata": intent["metadata"],
        }
    )


def _validate_readiness_record(
    record: Mapping[str, Any],
    name: str,
    work_id: str,
    blockers: list[str],
) -> None:
    if record.get("work_id") != work_id:
        blockers.append(f"release-publish-handoff-readiness-{name}-work-id-mismatch")
    if record.get("status") != "ready":
        blockers.append(f"release-publish-handoff-readiness-{name}-status-not-ready")
    if name == "dependency":
        if record.get("dependency_type") != _READINESS_DEPENDENCY_TYPE:
            blockers.append("release-publish-handoff-readiness-dependency-type-mismatch")
        if record.get("required") is not True:
            blockers.append("release-publish-handoff-readiness-dependency-not-required")
        _required_text(record.get("dependency_id"), "readiness-dependency-id", blockers)
    else:
        if record.get("event_type") != _READINESS_EVENT_TYPE:
            blockers.append("release-publish-handoff-readiness-event-type-mismatch")
        _required_text(record.get("event_id"), "readiness-event-id", blockers)


def _validate_readiness_summary(
    *,
    summary: Mapping[str, Any],
    run_id: str,
    work_id: str,
    source_dependency_id: str,
    source_event_id: str,
    intent_digest_prefix: str,
    release_binding_digest_prefix: str,
    blockers: list[str],
) -> None:
    if set(summary) != _READINESS_SUMMARY_KEYS:
        blockers.append("unsafe-handoff-readiness-summary-schema")
    _match_obj(summary.get("format"), _READINESS_FORMAT, "handoff-readiness-summary-format", blockers)
    _match_obj(summary.get("run_id"), run_id, "handoff-readiness-summary-run-id", blockers)
    _match_obj(summary.get("work_id"), work_id, "handoff-readiness-summary-work-id", blockers)
    _match_obj(summary.get("dependency_id"), source_dependency_id, "handoff-readiness-summary-dependency-id", blockers)
    _match_obj(summary.get("event_id"), source_event_id, "handoff-readiness-summary-event-id", blockers)
    _match_obj(summary.get("intent_digest_prefix"), intent_digest_prefix, "handoff-readiness-summary-intent-digest-prefix", blockers)
    _match_obj(summary.get("release_binding_digest_prefix"), release_binding_digest_prefix, "handoff-readiness-summary-release-binding-digest-prefix", blockers)
    if summary.get("target_type") not in _TARGET_TYPES:
        blockers.append("unsafe-summary-target-type")
    _validate_safe_text(summary.get("target_id"), "summary-target-id", blockers)
    _digest_prefix(summary.get("payload_digest_prefix"), "payload-digest-prefix", blockers)
    _validate_safe_text(summary.get("artifact_id"), "summary-artifact-id", blockers)
    metadata_keys = summary.get("metadata_keys")
    if not isinstance(metadata_keys, tuple) or tuple(sorted(metadata_keys)) != metadata_keys:
        blockers.append("unsafe-summary-metadata-keys")
    else:
        for key in metadata_keys:
            if not _safe_metadata_key(key):
                blockers.append("unsafe-summary-metadata-keys")
                break
    if not isinstance(summary.get("task_count"), int) or isinstance(summary.get("task_count"), bool) or summary.get("task_count") < 0:
        blockers.append("unsafe-summary-task-count")
    if not isinstance(summary.get("require_finished_tasks"), bool):
        blockers.append("unsafe-summary-require-finished-tasks")


def _validate_readiness_canonical(
    *,
    canonical: Mapping[str, Any],
    summary: Mapping[str, Any],
    run_id: str,
    work_id: str,
    source_dependency_id: str,
    source_event_id: str,
    handoff_digest: str,
    blockers: list[str],
) -> None:
    if set(canonical) != _READINESS_CANONICAL_KEYS:
        blockers.append("unsafe-handoff-readiness-canonical-payload-schema")
    if canonical.get("format") != _READINESS_LEDGER_FORMAT:
        blockers.append("handoff-readiness-canonical-format-mismatch")
    record = _metadata_mapping(
        canonical.get("handoff_readiness"),
        "handoff-readiness-canonical-record",
        blockers,
    )
    if record and set(record) != _READINESS_CANONICAL_RECORD_KEYS:
        blockers.append("unsafe-handoff-readiness-canonical-record-schema")
    _match_obj(record.get("run_id"), run_id, "handoff-readiness-canonical-run-id", blockers)
    _match_obj(record.get("work_id"), work_id, "handoff-readiness-canonical-work-id", blockers)
    _match_obj(record.get("dependency_id"), source_dependency_id, "handoff-readiness-canonical-dependency-id", blockers)
    _match_obj(record.get("event_id"), source_event_id, "handoff-readiness-canonical-event-id", blockers)
    _match_obj(record.get("summary"), summary, "handoff-readiness-canonical-summary", blockers)
    if canonical and handoff_digest and _sha256_payload(canonical) != handoff_digest:
        blockers.append("release-publish-handoff-readiness-digest-mismatch")


def _validate_intent_pair(
    *,
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    run_id: str,
    work_id: str,
    expected_intent_prefix: str,
    expected_binding_prefix: str,
    blockers: list[str],
) -> Mapping[str, Any]:
    if dependency.get("dependency_type") != _INTENT_DEPENDENCY_TYPE:
        blockers.append("source-release-publish-intent-dependency-type-mismatch")
    if dependency.get("required") is not True:
        blockers.append("source-release-publish-intent-dependency-not-required")
    if dependency.get("status") != "ready":
        blockers.append("source-release-publish-intent-dependency-status-not-ready")
    if dependency.get("work_id") != work_id:
        blockers.append("source-release-publish-intent-dependency-work-id-mismatch")
    if event.get("event_type") != _INTENT_EVENT_TYPE:
        blockers.append("source-release-publish-intent-event-type-mismatch")
    if event.get("status") != "ready":
        blockers.append("source-release-publish-intent-event-status-not-ready")
    if event.get("work_id") != work_id:
        blockers.append("source-release-publish-intent-event-work-id-mismatch")

    dependency_id = _required_text(dependency.get("dependency_id"), "source-intent-dependency-id", blockers)
    event_id = _required_text(event.get("event_id"), "source-intent-event-id", blockers)
    dependency_metadata = _metadata(dependency, "source-intent-dependency", blockers)
    event_metadata = _metadata(event, "source-intent-event", blockers)
    _match_text(
        _required_text(event_metadata.get("dependency_id"), "source-intent-event-dependency-id", blockers),
        dependency_id,
        "source-intent-event-dependency-id",
        blockers,
    )
    _match_text(
        _required_text(dependency_metadata.get("run_id"), "source-intent-dependency-run-id", blockers),
        run_id,
        "source-intent-dependency-run-id",
        blockers,
    )
    _match_text(
        _required_text(event_metadata.get("run_id"), "source-intent-event-run-id", blockers),
        run_id,
        "source-intent-event-run-id",
        blockers,
    )
    for key in (
        "release_binding_digest",
        "release_binding_digest_prefix",
        "intent_digest",
        "intent_digest_prefix",
        "publish_target",
        "publish_payload",
        "artifact",
        "intent_metadata",
        "canonical_payload",
    ):
        if dependency_metadata.get(key) != event_metadata.get(key):
            blockers.append(f"source-release-publish-intent-event-{_label(key)}-mismatch")

    release_binding_digest = _digest_text(
        dependency_metadata.get("release_binding_digest"),
        "release-binding-digest",
        blockers,
    )
    intent_digest = _digest_text(dependency_metadata.get("intent_digest"), "intent-digest", blockers)
    _match_text(
        _digest_prefix(dependency_metadata.get("release_binding_digest_prefix"), "release-binding-digest-prefix", blockers),
        release_binding_digest[:12],
        "release-binding-digest-prefix",
        blockers,
    )
    _match_text(
        _digest_prefix(dependency_metadata.get("intent_digest_prefix"), "intent-digest-prefix", blockers),
        intent_digest[:12],
        "intent-digest-prefix",
        blockers,
    )
    _match_text(intent_digest[:12], expected_intent_prefix, "readiness-intent-digest-prefix", blockers)
    _match_text(release_binding_digest[:12], expected_binding_prefix, "readiness-release-binding-digest-prefix", blockers)
    if intent_digest:
        _match_text(dependency_id, f"release-publish-intent:{work_id}:{intent_digest[:16]}", "source-intent-dependency-id", blockers)
        _match_text(event_id, f"release-publish-intent-recorded:{work_id}:{intent_digest[:16]}", "source-intent-event-id", blockers)

    publish_target = _metadata_mapping(dependency_metadata.get("publish_target"), "publish-target", blockers)
    publish_payload = _metadata_mapping(dependency_metadata.get("publish_payload"), "publish-payload", blockers)
    artifact = _metadata_mapping(dependency_metadata.get("artifact"), "artifact", blockers)
    metadata = _metadata_mapping(
        dependency_metadata.get("intent_metadata"),
        "intent-metadata",
        blockers,
        required=False,
    )
    canonical = _metadata_mapping(dependency_metadata.get("canonical_payload"), "canonical-payload", blockers)
    _validate_publish_target(publish_target, blockers)
    _validate_publish_payload(publish_payload, blockers)
    _validate_artifact(artifact, blockers)
    _validate_metadata_values(metadata, blockers)
    _validate_intent_canonical(
        canonical=canonical,
        run_id=run_id,
        work_id=work_id,
        release_binding_digest=release_binding_digest,
        intent_digest=intent_digest,
        publish_target=publish_target,
        publish_payload=publish_payload,
        artifact=artifact,
        metadata=metadata,
        blockers=blockers,
    )
    return {
        "intent_digest": intent_digest,
        "release_binding_digest": release_binding_digest,
        "publish_target": _sorted_value(publish_target),
        "publish_payload": _sorted_value(publish_payload),
        "artifact": _sorted_value(artifact),
        "metadata": _sorted_value(metadata),
    }


def _validate_intent_canonical(
    *,
    canonical: Mapping[str, Any],
    run_id: str,
    work_id: str,
    release_binding_digest: str,
    intent_digest: str,
    publish_target: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    metadata: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if set(canonical) != _INTENT_CANONICAL_KEYS:
        blockers.append("unsafe-canonical-payload-schema")
    if canonical.get("format") != _INTENT_FORMAT:
        blockers.append("release-publish-intent-format-mismatch")
    readiness = _metadata_mapping(canonical.get("readiness_binding"), "canonical-readiness-binding", blockers)
    caller = _metadata_mapping(canonical.get("caller_supplied_intent_metadata"), "canonical-caller-metadata", blockers)
    if readiness and set(readiness) != _INTENT_READINESS_BINDING_KEYS:
        blockers.append("unsafe-readiness-binding-schema")
    if caller and set(caller) != _INTENT_CALLER_METADATA_KEYS:
        blockers.append("unsafe-caller-intent-metadata-schema")
    _match_obj(readiness.get("run_id"), run_id, "canonical-run-id", blockers)
    _match_obj(readiness.get("work_id"), work_id, "canonical-work-id", blockers)
    _match_obj(readiness.get("release_binding_digest"), release_binding_digest, "canonical-release-binding-digest", blockers)
    prefix = _digest_prefix(readiness.get("canonical_digest_prefix"), "canonical-digest-prefix", blockers)
    _match_text(prefix, release_binding_digest[:12], "canonical-digest-prefix", blockers)
    _match_obj(readiness.get("source"), "cr-har-030-exposed-readiness-fields-only", "canonical-readiness-source", blockers)
    _match_mapping(caller.get("publish_target"), publish_target, "canonical-publish-target", blockers)
    _match_mapping(caller.get("publish_payload"), publish_payload, "canonical-publish-payload", blockers)
    _match_mapping(caller.get("artifact"), artifact, "canonical-artifact", blockers)
    _match_mapping(caller.get("metadata"), metadata, "canonical-metadata", blockers)
    _match_obj(caller.get("verification_status"), "caller-supplied-not-cr-har-030-identity", "canonical-verification-status", blockers)
    if canonical and intent_digest and _sha256_payload(canonical) != intent_digest:
        blockers.append("release-publish-intent-digest-mismatch")


def _snapshot_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, RunLedgerSnapshot):
        return _validated_mapping(value.to_dict(), blockers)
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
    blockers.append("malformed-ledger-snapshot")
    return None


def _validated_mapping(value: Mapping[Any, Any], blockers: list[str]) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            blockers.append("non-string-mapping-key")
            continue
        plain[key] = _plain_value(item, blockers)
    return plain


def _plain_value(value: object, blockers: list[str]) -> object:
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    blockers.append("unsupported-nested-object")
    return None


def _record_sequence(value: object, name: str, blockers: list[str]) -> tuple[dict[str, Any], ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        blockers.append(f"malformed-{name}")
        return ()
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            blockers.append(f"malformed-{name}-record")
            continue
        records.append(dict(item))
    return tuple(records)


def _matching_records(
    records: tuple[dict[str, Any], ...],
    kind_key: str,
    kind: str,
    work_id: str,
    blockers: list[str],
) -> tuple[dict[str, Any], ...]:
    matches: list[dict[str, Any]] = []
    for record in records:
        if record.get(kind_key) != kind:
            continue
        if record.get("work_id") == work_id:
            matches.append(record)
        elif record.get("work_id") not in (None, ""):
            continue
        else:
            blockers.append(f"{kind}-work-id-missing")
    return tuple(matches)


def _metadata(record: Mapping[str, Any], name: str, blockers: list[str]) -> Mapping[str, Any]:
    value = record.get("metadata")
    if not isinstance(value, Mapping):
        blockers.append(f"{name}-metadata-missing")
        return {}
    return value


def _metadata_mapping(
    value: object,
    name: str,
    blockers: list[str],
    *,
    required: bool = True,
) -> Mapping[str, Any]:
    if value is None and not required:
        return {}
    if not isinstance(value, Mapping):
        blockers.append(f"missing-{name}" if required else f"malformed-{name}")
        return {}
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
            blockers.append("unsafe-intent-metadata-schema")
            continue
        if isinstance(item, bool) or isinstance(item, int):
            continue
        if isinstance(item, str) and _safe_text_value(item, required=False) and not _is_secret_like(item) and not _has_action_text(item):
            continue
        blockers.append("unsafe-intent-metadata-schema")


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    text = value.strip()
    if text != value or not _SAFE_IDENTIFIER.fullmatch(text):
        blockers.append(f"unsafe-{name}")
        return ""
    if _is_secret_like(text):
        blockers.append(f"secret-like-{name}")
        return ""
    if _has_action_text(text):
        blockers.append(f"action-intent-{name}")
        return ""
    return text


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


def _digest_or_empty(value: object) -> str:
    if isinstance(value, str) and _SHA256_HEX.fullmatch(value):
        return value
    return ""


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


def _validate_safe_text(value: object, name: str, blockers: list[str]) -> None:
    if not _safe_text_value(value):
        blockers.append(f"unsafe-{name}")


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


def _match_text(actual: str, expected: str, name: str, blockers: list[str]) -> None:
    if actual and expected and actual != expected:
        blockers.append(f"{name}-mismatch")


def _match_obj(actual: object, expected: object, name: str, blockers: list[str]) -> None:
    if actual != expected:
        blockers.append(f"{name}-mismatch")


def _match_mapping(actual: object, expected: Mapping[str, Any], name: str, blockers: list[str]) -> None:
    if not isinstance(actual, Mapping):
        blockers.append(f"release-publish-intent-{name}-missing")
        return
    if _sorted_value(actual) != _sorted_value(expected):
        blockers.append(f"release-publish-intent-{name}-mismatch")


def _check_secret_or_action(name: str, value: object, blockers: list[str]) -> None:
    if _contains_secret_like(value):
        blockers.append(f"secret-like-{name}-data")
    if _contains_action_intent(value):
        blockers.append(f"action-intent-{name}-data")


def _contains_secret_like(value: object) -> bool:
    if _is_secret_like(value):
        return True
    if isinstance(value, Mapping):
        return any(_is_secret_like(key) or _contains_secret_like(item) for key, item in value.items())
    if isinstance(value, tuple):
        return any(_contains_secret_like(item) for item in value)
    return False


def _contains_action_intent(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_is_action_key(key) or _contains_action_intent(item) for key, item in value.items())
    if isinstance(value, tuple):
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
    return value


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _plain_copy(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {str(key): _plain_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain_copy(item) for item in value)
    return value


def _label(key: str) -> str:
    return key.replace("_", "-")


__all__ = [
    "ReleasePublishHandoffPackage",
    "build_release_publish_handoff_package",
    "evaluate_release_publish_handoff_package",
]
