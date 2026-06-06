"""Pure readiness boundary for release publishing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.run_ledger import RunLedgerSnapshot


_FORMAT = "harness-release-publish-readiness-v1"
_BINDING_FORMAT = "harness-release-identity-binding-v1"
_DEPENDENCY_TYPE = "release-identity-binding"
_EVENT_TYPE = "release-identity-binding-ledger-record"
_DONE_STATUSES = frozenset({"done", "complete", "completed", "closed"})
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
_EXECUTION_FLAG_FRAGMENTS = ("execute", "execution")
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
_EXPECTED_KEYS = (
    "work_id",
    "request_id",
    "evidence_bundle_id",
    "media_ids",
    "artifact_ids",
    "payload_digest",
    "checkpoint_digest",
    "promotion_intent_digest",
)
_RECORD_KEYS = frozenset(
    {
        "dependency_id",
        "work_id",
        "reference",
        "order",
        "dependency_type",
        "required",
        "status",
        "event_id",
        "event_type",
        "message",
        "occurred_at",
        "actor",
        "metadata",
    }
)


@dataclass(frozen=True)
class ReleasePublishReadiness:
    """Frozen read-only result for release publishing readiness."""

    ready: bool = False
    status: str = "blocked"
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    work_id: str = ""
    dependency_id: str = ""
    event_id: str = ""
    canonical_digest_prefix: str = ""
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "run_id": self.run_id,
            "work_id": self.work_id,
            "dependency_id": _redact_identifier(self.dependency_id),
            "event_id": _redact_identifier(self.event_id),
            "canonical_digest_prefix": self.canonical_digest_prefix,
            "summary": _plain_copy(self.summary),
        }


def evaluate_release_publish_readiness(
    ledger_snapshot: object,
    *,
    run_id: object,
    work_id: object,
    expected_request_id: object = None,
    expected_evidence_bundle_id: object = None,
    expected_media_ids: object = (),
    expected_artifact_ids: object = (),
    expected_payload_digest: object = None,
    expected_checkpoint_digest: object = None,
    expected_promotion_intent_digest: object = None,
    require_finished_tasks: bool = True,
) -> ReleasePublishReadiness:
    """Evaluate release publish readiness using only explicit snapshot-shaped data."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _required_text(work_id, "work-id", blockers)
    expected = {
        "work_id": expected_work_id,
        "request_id": _optional_text(expected_request_id, "request-id", blockers),
        "evidence_bundle_id": _optional_text(
            expected_evidence_bundle_id, "evidence-bundle-id", blockers
        ),
        "media_ids": _text_tuple(expected_media_ids, "media-ids", blockers),
        "artifact_ids": _text_tuple(expected_artifact_ids, "artifact-ids", blockers),
        "payload_digest": _optional_digest(
            expected_payload_digest, "payload-digest", blockers
        ),
        "checkpoint_digest": _optional_digest(
            expected_checkpoint_digest, "checkpoint-digest", blockers
        ),
        "promotion_intent_digest": _optional_digest(
            expected_promotion_intent_digest, "promotion-intent-digest", blockers
        ),
    }

    snapshot = _snapshot_mapping(ledger_snapshot, blockers)
    if snapshot is None:
        blockers.append("missing-ledger-snapshot")
        snapshot = {}

    snapshot_run_id = _required_text(snapshot.get("run_id"), "snapshot-run-id", blockers)
    _match_text(snapshot_run_id, expected_run_id, "snapshot-run-id", blockers)

    dependencies = _record_sequence(snapshot.get("dependencies"), "dependencies", blockers)
    audit_events = _record_sequence(snapshot.get("audit_events"), "audit-events", blockers)
    tasks = _record_sequence(snapshot.get("tasks"), "tasks", blockers)
    _check_secret_or_execution("snapshot", snapshot, blockers)
    if require_finished_tasks:
        _check_unfinished_tasks(tasks, expected_work_id, blockers)

    matching_dependencies = _matching_dependencies(
        dependencies, expected_work_id, blockers
    )
    matching_events = _matching_events(audit_events, expected_work_id, blockers)
    if len(matching_dependencies) != 1:
        blockers.append(
            "release-identity-binding-dependency-missing"
            if not matching_dependencies
            else "release-identity-binding-dependency-ambiguous"
        )
    if len(matching_events) != 1:
        blockers.append(
            "release-identity-binding-event-missing"
            if not matching_events
            else "release-identity-binding-event-ambiguous"
        )

    dependency = matching_dependencies[0] if len(matching_dependencies) == 1 else {}
    event = matching_events[0] if len(matching_events) == 1 else {}
    canonical_digest = ""
    if dependency and event:
        canonical_digest = _validate_binding_pair(
            dependency,
            event,
            expected,
            blockers,
        )

    deduped = tuple(dict.fromkeys(blockers))
    ready = not deduped
    return ReleasePublishReadiness(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped,
        run_id=expected_run_id or snapshot_run_id,
        work_id=expected_work_id,
        dependency_id=str(dependency.get("dependency_id", "")),
        event_id=str(event.get("event_id", "")),
        canonical_digest_prefix=canonical_digest[:12],
        summary=_freeze_value(
            _summary(
                expected_run_id or snapshot_run_id,
                expected_work_id,
                dependency,
                event,
                canonical_digest,
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


def _validated_mapping(
    value: Mapping[Any, Any],
    blockers: list[str],
) -> dict[str, Any]:
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
        _check_nested_record_identity(item, name, blockers)
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


def _validate_dependency(
    record: Mapping[str, Any],
    work_id: str,
    blockers: list[str],
) -> None:
    if record.get("work_id") != work_id:
        blockers.append("release-identity-binding-dependency-work-id-mismatch")
    if record.get("required") is not True:
        blockers.append("release-identity-binding-dependency-not-required")
    if not _status_ready(record.get("status")):
        blockers.append("release-identity-binding-dependency-status-not-ready")
    if not isinstance(record.get("dependency_id"), str) or not record.get("dependency_id"):
        blockers.append("release-identity-binding-dependency-id-missing")


def _validate_event(
    record: Mapping[str, Any],
    work_id: str,
    blockers: list[str],
) -> None:
    if record.get("work_id") != work_id:
        blockers.append("release-identity-binding-event-work-id-mismatch")
    if not _status_ready(record.get("status")):
        blockers.append("release-identity-binding-event-status-not-ready")
    if not isinstance(record.get("event_id"), str) or not record.get("event_id"):
        blockers.append("release-identity-binding-event-id-missing")


def _validate_binding_pair(
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    expected: Mapping[str, Any],
    blockers: list[str],
) -> str:
    dependency_id = _required_text(
        dependency.get("dependency_id"), "binding-dependency-id", blockers
    )
    event_metadata = _metadata(event, "event", blockers)
    dependency_metadata = _metadata(dependency, "dependency", blockers)
    event_dependency_id = _required_text(
        event_metadata.get("dependency_id"), "event-dependency-id", blockers
    )
    _match_text(event_dependency_id, dependency_id, "event-dependency-id", blockers)

    dependency_digest = _digest_text(
        dependency_metadata.get("canonical_digest"),
        "dependency-canonical-digest",
        blockers,
    )
    event_digest = _digest_text(
        event_metadata.get("canonical_digest"), "event-canonical-digest", blockers
    )
    _match_text(event_digest, dependency_digest, "event-canonical-digest", blockers)

    canonical_payload = _metadata_payload(dependency_metadata, blockers)
    event_payload = event_metadata.get("canonical_payload")
    if event_payload != canonical_payload:
        blockers.append("release-identity-binding-event-canonical-payload-mismatch")

    summary = _metadata_summary(dependency_metadata, blockers)
    event_summary = event_metadata.get("summary")
    if event_summary != summary:
        blockers.append("release-identity-binding-event-summary-mismatch")

    prefix = dependency_metadata.get("canonical_digest_prefix")
    if prefix != dependency_digest[:12]:
        blockers.append("release-identity-binding-dependency-digest-prefix-mismatch")
    event_prefix = event_metadata.get("canonical_digest_prefix")
    if event_prefix != event_digest[:12]:
        blockers.append("release-identity-binding-event-digest-prefix-mismatch")

    _validate_binding_payload(canonical_payload, summary, dependency_digest, expected, blockers)
    return dependency_digest


def _metadata(
    record: Mapping[str, Any],
    name: str,
    blockers: list[str],
) -> Mapping[str, Any]:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        blockers.append(f"release-identity-binding-{name}-metadata-missing")
        return {}
    return metadata


def _metadata_payload(
    metadata: Mapping[str, Any],
    blockers: list[str],
) -> Mapping[str, Any]:
    payload = metadata.get("canonical_payload")
    if not isinstance(payload, Mapping):
        blockers.append("release-identity-binding-canonical-payload-missing")
        return {}
    return payload


def _metadata_summary(
    metadata: Mapping[str, Any],
    blockers: list[str],
) -> Mapping[str, Any]:
    summary = metadata.get("summary")
    if not isinstance(summary, Mapping):
        blockers.append("release-identity-binding-summary-missing")
        return {}
    return summary


def _validate_binding_payload(
    canonical_payload: Mapping[str, Any],
    summary: Mapping[str, Any],
    canonical_digest: str,
    expected: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if canonical_payload.get("format") != _BINDING_FORMAT:
        blockers.append("release-identity-binding-format-mismatch")
    if not isinstance(canonical_payload.get("gate_decision"), Mapping):
        blockers.append("release-identity-binding-gate-decision-missing")
    if not isinstance(canonical_payload.get("identity_proof"), Mapping):
        blockers.append("release-identity-binding-identity-proof-missing")
    recomputed = _sha256_payload(canonical_payload) if canonical_payload else ""
    if canonical_digest and recomputed and recomputed != canonical_digest:
        blockers.append("release-identity-binding-canonical-digest-mismatch")
    if summary.get("format") != _BINDING_FORMAT:
        blockers.append("release-identity-binding-summary-format-mismatch")

    payload_expected = canonical_payload.get("expected")
    if not isinstance(payload_expected, Mapping):
        blockers.append("release-identity-binding-expected-missing")
        payload_expected = {}
    _validate_summary_identity(summary, payload_expected, canonical_digest, blockers)
    for key in _EXPECTED_KEYS:
        wanted = expected.get(key)
        if key in {"media_ids", "artifact_ids"}:
            wanted = tuple(wanted or ())
            actual = _text_tuple(payload_expected.get(key), key.replace("_", "-"), blockers)
        elif key in {
            "payload_digest",
            "checkpoint_digest",
            "promotion_intent_digest",
        }:
            actual = _optional_digest(
                payload_expected.get(key), key.replace("_", "-"), blockers
            )
        else:
            actual = _optional_text(
                payload_expected.get(key), key.replace("_", "-"), blockers
            )
        if wanted and actual != wanted:
            blockers.append(f"release-identity-binding-expected-{_label(key)}-mismatch")


def _validate_summary_identity(
    summary: Mapping[str, Any],
    payload_expected: Mapping[str, Any],
    canonical_digest: str,
    blockers: list[str],
) -> None:
    for key in (
        "work_id",
        "request_id",
        "evidence_bundle_id",
        "media_ids",
        "artifact_ids",
    ):
        if key not in summary:
            blockers.append(f"release-identity-binding-summary-{_label(key)}-missing")
            continue
        if key in {"media_ids", "artifact_ids"}:
            summary_value = _text_tuple(
                summary.get(key),
                f"summary-{key.replace('_', '-')}",
                blockers,
            )
            expected_value = _text_tuple(
                payload_expected.get(key),
                key.replace("_", "-"),
                blockers,
            )
        else:
            summary_value = _optional_text(
                summary.get(key),
                f"summary-{key.replace('_', '-')}",
                blockers,
            )
            expected_value = _optional_text(
                payload_expected.get(key),
                key.replace("_", "-"),
                blockers,
            )
        if summary_value != expected_value:
            blockers.append(f"release-identity-binding-summary-{_label(key)}-mismatch")

    prefix_expectations = {
        "canonical_digest_prefix": canonical_digest[:12],
        "payload_digest_prefix": _prefix(payload_expected.get("payload_digest")),
        "checkpoint_digest_prefix": _prefix(payload_expected.get("checkpoint_digest")),
        "promotion_intent_digest_prefix": _prefix(
            payload_expected.get("promotion_intent_digest")
        ),
    }
    for key, expected_prefix in prefix_expectations.items():
        label = key.replace("_", "-")
        if key not in summary:
            blockers.append(f"release-identity-binding-summary-{label}-missing")
            continue
        if summary.get(key) != expected_prefix:
            blockers.append(f"release-identity-binding-summary-{label}-mismatch")


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


def _check_nested_record_identity(
    record: Mapping[str, Any],
    name: str,
    blockers: list[str],
) -> None:
    for key, item in record.items():
        if key in _RECORD_KEYS:
            continue
        if _contains_identity_detail(item):
            blockers.append(f"ambiguous-{name}-record")


def _contains_identity_detail(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _EXPECTED_KEYS or str(key) in {
                "canonical_digest",
                "canonical_payload",
                "summary",
                "dependency_id",
            }:
                return True
            if _contains_identity_detail(item):
                return True
    if isinstance(value, tuple):
        return any(_contains_identity_detail(item) for item in value)
    return False


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    return _screened_text(value, name, blockers)


def _optional_text(value: object, name: str, blockers: list[str]) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"invalid-{name}")
        return ""
    return _screened_text(value, name, blockers)


def _screened_text(value: str, name: str, blockers: list[str]) -> str:
    text = value.strip()
    if _is_secret_like(text):
        blockers.append(f"secret-like-{name}")
        return ""
    if _has_execution_text(text):
        blockers.append(f"execution-intent-{name}")
        return ""
    return text


def _text_tuple(value: object, name: str, blockers: list[str]) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        blockers.append(f"invalid-{name}")
        return ()
    safe: list[str] = []
    for item in values:
        text = _optional_text(item, name.rstrip("s"), blockers)
        if text:
            safe.append(text)
    if len(safe) != len(set(safe)):
        blockers.append(f"duplicate-{name}")
    return tuple(sorted(set(safe)))


def _optional_digest(value: object, name: str, blockers: list[str]) -> str:
    if value in (None, ""):
        return ""
    return _digest_text(value, name, blockers)


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    text = _screened_text(value, name, blockers).lower()
    if text and not _SHA256_HEX.fullmatch(text):
        blockers.append(f"invalid-{name}")
    return text


def _match_text(actual: str, expected: str, name: str, blockers: list[str]) -> None:
    if actual and expected and actual != expected:
        blockers.append(f"{name}-mismatch")


def _status_ready(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() == "ready"


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


def _check_secret_or_execution(
    name: str,
    value: object,
    blockers: list[str],
) -> None:
    if _contains_secret_like(value):
        blockers.append(f"secret-like-{name}-data")
    if _contains_execution_intent(value):
        blockers.append(f"execution-intent-{name}-data")


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


def _contains_execution_intent(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_execution_key(str(key)) or _contains_execution_intent(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple):
        return any(_contains_execution_intent(item) for item in value)
    return _has_execution_text(value)


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str) or value == "<redacted>":
        return False
    lowered = value.lower()
    if lowered in {"metadata", "metadata_keys"}:
        return False
    return any(term in lowered for term in _SECRET_TERMS)


def _is_execution_key(key: str) -> bool:
    normalized = "".join(
        character.lower() if character.isalnum() else "_"
        for character in key
    )
    return any(fragment in normalized for fragment in _EXECUTION_KEYS) or any(
        fragment in normalized for fragment in _EXECUTION_FLAG_FRAGMENTS
    )


def _has_execution_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = f" {value.strip().lower()} "
    return any(term in lowered for term in _EXECUTION_TEXT)


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prefix(value: object) -> str:
    return value[:12] if isinstance(value, str) else ""


def _summary(
    run_id: str,
    work_id: str,
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    canonical_digest: str,
    task_count: int,
    require_finished_tasks: bool,
) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "run_id": run_id,
        "work_id": work_id,
        "dependency_id": _redact_identifier(str(dependency.get("dependency_id", ""))),
        "event_id": _redact_identifier(str(event.get("event_id", ""))),
        "canonical_digest_prefix": canonical_digest[:12],
        "task_count": task_count,
        "require_finished_tasks": require_finished_tasks,
    }


def _redact_identifier(value: str) -> str:
    if not value:
        return ""
    parts = value.split(":")
    if len(parts) >= 3 and _SHA256_HEX.match((parts[-1] + "0" * 48)[:64]):
        return ":".join((*parts[:-1], f"{parts[-1][:8]}..."))
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
    "ReleasePublishReadiness",
    "evaluate_release_publish_readiness",
]
