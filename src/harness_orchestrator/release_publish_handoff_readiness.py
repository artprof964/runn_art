"""Pure handoff readiness boundary for release publish intent records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.run_ledger import RunLedgerSnapshot


_FORMAT = "harness-release-publish-handoff-readiness-v1"
_INTENT_FORMAT = "harness-release-publish-intent-v1"
_DEPENDENCY_TYPE = "release-publish-intent"
_EVENT_TYPE = "release-publish-intent-ledger-record"
_READINESS_SOURCE = "cr-har-030-exposed-readiness-fields-only"
_VERIFY_STATUS = "caller-supplied-not-cr-har-030-identity"
_DONE_STATUSES = frozenset({"done", "complete", "completed", "closed"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_TARGET_TYPES = frozenset({"local-dry-run", "manual-release-placeholder"})
_PUBLISH_TARGET_KEYS = frozenset({"target_id", "target_type"})
_PUBLISH_PAYLOAD_ALLOWED_KEYS = frozenset({"payload_digest", "payload_label"})
_ARTIFACT_KEYS = frozenset({"artifact_id"})
_CANONICAL_PAYLOAD_KEYS = frozenset(
    {"format", "readiness_binding", "caller_supplied_intent_metadata"}
)
_READINESS_BINDING_KEYS = frozenset(
    {"run_id", "work_id", "canonical_digest_prefix", "release_binding_digest", "source"}
)
_CALLER_INTENT_METADATA_KEYS = frozenset(
    {"publish_target", "publish_payload", "artifact", "metadata", "verification_status"}
)
_RESERVED_METADATA_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
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
        "canonical_payload",
        "credentials",
        "secret",
        "token",
        "key",
        "password",
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
class ReleasePublishHandoffReadiness:
    """Frozen read-only result for release publish handoff readiness."""

    ready: bool = False
    status: str = "blocked"
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    work_id: str = ""
    dependency_id: str = ""
    event_id: str = ""
    intent_digest_prefix: str = ""
    release_binding_digest_prefix: str = ""
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "run_id": self.run_id,
            "work_id": self.work_id,
            "dependency_id": self.dependency_id,
            "event_id": self.event_id,
            "intent_digest_prefix": self.intent_digest_prefix,
            "release_binding_digest_prefix": self.release_binding_digest_prefix,
            "summary": _plain_copy(self.summary),
        }


def evaluate_release_publish_handoff_readiness(
    ledger_snapshot: object,
    *,
    run_id: object,
    work_id: object,
    require_finished_tasks: bool = True,
) -> ReleasePublishHandoffReadiness:
    """Evaluate handoff readiness using only explicit snapshot-shaped data."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _required_text(work_id, "work-id", blockers)

    snapshot = _snapshot_mapping(ledger_snapshot, blockers)
    if snapshot is None:
        blockers.append("missing-ledger-snapshot")
        snapshot = {}

    snapshot_run_id = _required_text(snapshot.get("run_id"), "snapshot-run-id", blockers)
    _match_text(snapshot_run_id, expected_run_id, "snapshot-run-id", blockers)

    dependencies = _record_sequence(snapshot.get("dependencies"), "dependencies", blockers)
    audit_events = _record_sequence(snapshot.get("audit_events"), "audit-events", blockers)
    tasks = _record_sequence(snapshot.get("tasks"), "tasks", blockers)
    _check_secret_or_action("snapshot", snapshot, blockers)
    if require_finished_tasks:
        _check_unfinished_tasks(tasks, expected_work_id, blockers)

    matching_dependencies = _matching_dependencies(dependencies, expected_work_id, blockers)
    matching_events = _matching_events(audit_events, expected_work_id, blockers)
    if len(matching_dependencies) != 1:
        blockers.append(
            "release-publish-intent-dependency-missing"
            if not matching_dependencies
            else "release-publish-intent-dependency-ambiguous"
        )
    if len(matching_events) != 1:
        blockers.append(
            "release-publish-intent-event-missing"
            if not matching_events
            else "release-publish-intent-event-ambiguous"
        )

    dependency = matching_dependencies[0] if len(matching_dependencies) == 1 else {}
    event = matching_events[0] if len(matching_events) == 1 else {}
    metadata: Mapping[str, Any] = {}
    if dependency and event:
        metadata = _validate_intent_pair(dependency, event, expected_run_id, expected_work_id, blockers)

    intent_digest = _digest_or_empty(metadata.get("intent_digest"))
    release_binding_digest = _digest_or_empty(metadata.get("release_binding_digest"))
    deduped = tuple(dict.fromkeys(blockers))
    ready = not deduped
    return ReleasePublishHandoffReadiness(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped,
        run_id=expected_run_id or snapshot_run_id,
        work_id=expected_work_id,
        dependency_id=str(dependency.get("dependency_id", "")),
        event_id=str(event.get("event_id", "")),
        intent_digest_prefix=intent_digest[:12],
        release_binding_digest_prefix=release_binding_digest[:12],
        summary=_freeze_value(
            _summary(
                expected_run_id or snapshot_run_id,
                expected_work_id,
                dependency,
                event,
                metadata,
                len(tasks),
                require_finished_tasks,
            )
        ),
    )


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


def _record_sequence(
    value: object,
    name: str,
    blockers: list[str],
) -> tuple[dict[str, Any], ...]:
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


def _matching_dependencies(
    records: tuple[dict[str, Any], ...],
    work_id: str,
    blockers: list[str],
) -> tuple[dict[str, Any], ...]:
    matches: list[dict[str, Any]] = []
    for record in records:
        if record.get("dependency_type") != _DEPENDENCY_TYPE:
            continue
        if record.get("work_id") == work_id:
            _validate_dependency(record, work_id, blockers)
            matches.append(record)
    return tuple(matches)


def _matching_events(
    records: tuple[dict[str, Any], ...],
    work_id: str,
    blockers: list[str],
) -> tuple[dict[str, Any], ...]:
    matches: list[dict[str, Any]] = []
    for record in records:
        if record.get("event_type") != _EVENT_TYPE:
            continue
        if record.get("work_id") == work_id:
            _validate_event(record, work_id, blockers)
            matches.append(record)
    return tuple(matches)


def _validate_dependency(record: Mapping[str, Any], work_id: str, blockers: list[str]) -> None:
    if record.get("work_id") != work_id:
        blockers.append("release-publish-intent-dependency-work-id-mismatch")
    if record.get("required") is not True:
        blockers.append("release-publish-intent-dependency-not-required")
    if not _status_ready(record.get("status")):
        blockers.append("release-publish-intent-dependency-status-not-ready")
    if not _required_text(record.get("dependency_id"), "release-publish-intent-dependency-id", blockers):
        return


def _validate_event(record: Mapping[str, Any], work_id: str, blockers: list[str]) -> None:
    if record.get("work_id") != work_id:
        blockers.append("release-publish-intent-event-work-id-mismatch")
    if not _status_ready(record.get("status")):
        blockers.append("release-publish-intent-event-status-not-ready")
    if not _required_text(record.get("event_id"), "release-publish-intent-event-id", blockers):
        return


def _validate_intent_pair(
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    run_id: str,
    work_id: str,
    blockers: list[str],
) -> Mapping[str, Any]:
    dependency_id = _required_text(
        dependency.get("dependency_id"),
        "release-publish-intent-dependency-id",
        blockers,
    )
    event_id = _required_text(
        event.get("event_id"),
        "release-publish-intent-event-id",
        blockers,
    )
    event_metadata = _metadata(event, "event", blockers)
    dependency_metadata = _metadata(dependency, "dependency", blockers)
    event_dependency_id = _required_text(
        event_metadata.get("dependency_id"),
        "event-dependency-id",
        blockers,
    )
    _match_text(event_dependency_id, dependency_id, "event-dependency-id", blockers)

    _match_text(
        _required_text(event_metadata.get("run_id"), "event-run-id", blockers),
        run_id,
        "event-run-id",
        blockers,
    )
    _match_text(
        _required_text(dependency_metadata.get("run_id"), "dependency-run-id", blockers),
        run_id,
        "dependency-run-id",
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
        if event_metadata.get(key) != dependency_metadata.get(key):
            blockers.append(f"release-publish-intent-event-{_label(key)}-mismatch")

    intent_digest = _validate_metadata(
        dependency_metadata=dependency_metadata,
        event_metadata=event_metadata,
        run_id=run_id,
        work_id=work_id,
        blockers=blockers,
    )
    if intent_digest:
        expected_dependency_id = f"release-publish-intent:{work_id}:{intent_digest[:16]}"
        expected_event_id = f"release-publish-intent-recorded:{work_id}:{intent_digest[:16]}"
        if dependency_id and dependency_id != expected_dependency_id:
            blockers.append("release-publish-intent-dependency-id-mismatch")
        if event_id and event_id != expected_event_id:
            blockers.append("release-publish-intent-event-id-mismatch")
    return dependency_metadata


def _validate_metadata(
    *,
    dependency_metadata: Mapping[str, Any],
    event_metadata: Mapping[str, Any],
    run_id: str,
    work_id: str,
    blockers: list[str],
) -> str:
    release_binding_digest = _digest_text(
        dependency_metadata.get("release_binding_digest"),
        "release-binding-digest",
        blockers,
    )
    intent_digest = _digest_text(
        dependency_metadata.get("intent_digest"),
        "intent-digest",
        blockers,
    )
    _match_text(
        _digest_text(event_metadata.get("intent_digest"), "event-intent-digest", blockers),
        intent_digest,
        "event-intent-digest",
        blockers,
    )

    _match_text(
        _optional_text(
            dependency_metadata.get("release_binding_digest_prefix"),
            "release-binding-digest-prefix",
            blockers,
        ),
        release_binding_digest[:12],
        "release-binding-digest-prefix",
        blockers,
    )
    _match_text(
        _optional_text(dependency_metadata.get("intent_digest_prefix"), "intent-digest-prefix", blockers),
        intent_digest[:12],
        "intent-digest-prefix",
        blockers,
    )

    publish_target = _metadata_mapping(
        dependency_metadata.get("publish_target"),
        "publish-target",
        blockers,
    )
    publish_payload = _metadata_mapping(
        dependency_metadata.get("publish_payload"),
        "publish-payload",
        blockers,
    )
    artifact = _metadata_mapping(dependency_metadata.get("artifact"), "artifact", blockers)
    intent_metadata = _metadata_mapping(
        dependency_metadata.get("intent_metadata"),
        "intent-metadata",
        blockers,
        required=False,
    )
    canonical_payload = _metadata_mapping(
        dependency_metadata.get("canonical_payload"),
        "canonical-payload",
        blockers,
    )
    _validate_publish_target(publish_target, blockers)
    _validate_publish_payload(publish_payload, blockers)
    _validate_artifact(artifact, blockers)
    _validate_intent_metadata(intent_metadata, blockers)
    _validate_canonical_payload(
        canonical_payload=canonical_payload,
        run_id=run_id,
        work_id=work_id,
        release_binding_digest=release_binding_digest,
        intent_digest=intent_digest,
        publish_target=publish_target,
        publish_payload=publish_payload,
        artifact=artifact,
        intent_metadata=intent_metadata,
        blockers=blockers,
    )
    return intent_digest


def _validate_canonical_payload(
    *,
    canonical_payload: Mapping[str, Any],
    run_id: str,
    work_id: str,
    release_binding_digest: str,
    intent_digest: str,
    publish_target: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    intent_metadata: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if set(canonical_payload) != _CANONICAL_PAYLOAD_KEYS:
        blockers.append("unsafe-canonical-payload-schema")
    if canonical_payload.get("format") != _INTENT_FORMAT:
        blockers.append("release-publish-intent-format-mismatch")
    readiness = _metadata_mapping(
        canonical_payload.get("readiness_binding"),
        "canonical-readiness-binding",
        blockers,
    )
    caller = _metadata_mapping(
        canonical_payload.get("caller_supplied_intent_metadata"),
        "canonical-caller-metadata",
        blockers,
    )
    if readiness and set(readiness) != _READINESS_BINDING_KEYS:
        blockers.append("unsafe-readiness-binding-schema")
    if caller and set(caller) != _CALLER_INTENT_METADATA_KEYS:
        blockers.append("unsafe-caller-intent-metadata-schema")
    canonical_prefix = _optional_text(
        readiness.get("canonical_digest_prefix"),
        "canonical-digest-prefix",
        blockers,
    )
    if canonical_prefix and not _DIGEST_PREFIX.fullmatch(canonical_prefix):
        blockers.append("invalid-canonical-digest-prefix")
    elif canonical_prefix and release_binding_digest and canonical_prefix != release_binding_digest[:12]:
        blockers.append("release-publish-intent-canonical-digest-prefix-mismatch")
    _match_text(_optional_text(readiness.get("run_id"), "canonical-run-id", blockers), run_id, "canonical-run-id", blockers)
    _match_text(_optional_text(readiness.get("work_id"), "canonical-work-id", blockers), work_id, "canonical-work-id", blockers)
    _match_text(
        _digest_text(readiness.get("release_binding_digest"), "canonical-release-binding-digest", blockers),
        release_binding_digest,
        "canonical-release-binding-digest",
        blockers,
    )
    _match_text(
        _optional_text(readiness.get("source"), "canonical-readiness-source", blockers),
        _READINESS_SOURCE,
        "canonical-readiness-source",
        blockers,
    )
    _match_mapping(caller.get("publish_target"), publish_target, "canonical-publish-target", blockers)
    _match_mapping(caller.get("publish_payload"), publish_payload, "canonical-publish-payload", blockers)
    _match_mapping(caller.get("artifact"), artifact, "canonical-artifact", blockers)
    _match_mapping(caller.get("metadata"), intent_metadata, "canonical-metadata", blockers)
    _match_text(
        _optional_text(caller.get("verification_status"), "canonical-verification-status", blockers),
        _VERIFY_STATUS,
        "canonical-verification-status",
        blockers,
    )
    if canonical_payload and intent_digest and _sha256_payload(canonical_payload) != intent_digest:
        blockers.append("release-publish-intent-digest-mismatch")


def _metadata(record: Mapping[str, Any], name: str, blockers: list[str]) -> Mapping[str, Any]:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        blockers.append(f"release-publish-intent-{name}-metadata-missing")
        return {}
    return metadata


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


def _validate_intent_metadata(value: Mapping[str, Any], blockers: list[str]) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or key.lower() in _RESERVED_METADATA_KEYS:
            blockers.append("unsafe-intent-metadata-schema")
            continue
        if not _safe_text_value(key) or _is_secret_like(key) or _is_action_key(key) or _has_action_text(key):
            blockers.append("unsafe-intent-metadata-schema")
            continue
        if isinstance(item, bool) or isinstance(item, int):
            continue
        if isinstance(item, str) and _safe_text_value(item, required=False) and not _is_secret_like(item) and not _has_action_text(item):
            continue
        blockers.append("unsafe-intent-metadata-schema")


def _check_unfinished_tasks(
    tasks: tuple[dict[str, Any], ...],
    work_id: str,
    blockers: list[str],
) -> None:
    unfinished = 0
    for task in tasks:
        task_work_id = task.get("work_id")
        if task_work_id not in ("", None, work_id):
            continue
        status = str(task.get("status", "open")).strip().lower()
        if status not in _DONE_STATUSES:
            unfinished += 1
            continue
        if _blocker_tuple(task.get("blockers")):
            unfinished += 1
    if unfinished:
        blockers.append("unfinished-tasks-present")


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


def _optional_text(value: object, name: str, blockers: list[str]) -> str:
    if value in (None, ""):
        return ""
    return _required_text(value, name, blockers)


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value:
        blockers.append(f"missing-{name}")
        return ""
    if value != value.strip() or not _SHA256_HEX.fullmatch(value):
        blockers.append(f"invalid-{name}")
        return ""
    return value


def _digest_or_empty(value: object) -> str:
    if isinstance(value, str) and _SHA256_HEX.fullmatch(value):
        return value
    return ""


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


def _safe_text_value(value: object, *, required: bool = True) -> bool:
    if value in (None, "") and not required:
        return True
    return isinstance(value, str) and _SAFE_TEXT.fullmatch(value) is not None


def _match_text(actual: str, expected: str, name: str, blockers: list[str]) -> None:
    if actual and expected and actual != expected:
        blockers.append(f"{name}-mismatch")


def _match_mapping(
    actual: object,
    expected: Mapping[str, Any],
    name: str,
    blockers: list[str],
) -> None:
    if not isinstance(actual, Mapping):
        blockers.append(f"release-publish-intent-{name}-missing")
        return
    if _sorted_value(actual) != _sorted_value(expected):
        blockers.append(f"release-publish-intent-{name}-mismatch")


def _status_ready(value: object) -> bool:
    return isinstance(value, str) and value == "ready"


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


def _check_secret_or_action(name: str, value: object, blockers: list[str]) -> None:
    if _contains_secret_like(value):
        blockers.append(f"secret-like-{name}-data")
    if _contains_action_intent(value):
        blockers.append(f"action-intent-{name}-data")


def _contains_secret_like(value: object) -> bool:
    if _is_secret_like(value):
        return True
    if isinstance(value, Mapping):
        return any(
            _is_secret_like(key) or _contains_secret_like(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple):
        return any(_contains_secret_like(item) for item in value)
    return False


def _contains_action_intent(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_action_key(key) or _contains_action_intent(item)
            for key, item in value.items()
        )
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


def _summary(
    run_id: str,
    work_id: str,
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    metadata: Mapping[str, Any],
    task_count: int,
    require_finished_tasks: bool,
) -> dict[str, Any]:
    publish_target = metadata.get("publish_target")
    publish_payload = metadata.get("publish_payload")
    artifact = metadata.get("artifact")
    intent_metadata = metadata.get("intent_metadata")
    return {
        "format": _FORMAT,
        "run_id": run_id,
        "work_id": work_id,
        "dependency_id": str(dependency.get("dependency_id", "")),
        "event_id": str(event.get("event_id", "")),
        "intent_digest_prefix": _digest_or_empty(metadata.get("intent_digest"))[:12],
        "release_binding_digest_prefix": _digest_or_empty(
            metadata.get("release_binding_digest")
        )[:12],
        "target_type": publish_target.get("target_type", "") if isinstance(publish_target, Mapping) else "",
        "target_id": publish_target.get("target_id", "") if isinstance(publish_target, Mapping) else "",
        "payload_digest_prefix": _digest_or_empty(
            publish_payload.get("payload_digest") if isinstance(publish_payload, Mapping) else ""
        )[:12],
        "artifact_id": artifact.get("artifact_id", "") if isinstance(artifact, Mapping) else "",
        "metadata_keys": tuple(sorted(intent_metadata)) if isinstance(intent_metadata, Mapping) else (),
        "task_count": task_count,
        "require_finished_tasks": require_finished_tasks,
    }


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
    "ReleasePublishHandoffReadiness",
    "evaluate_release_publish_handoff_readiness",
]
