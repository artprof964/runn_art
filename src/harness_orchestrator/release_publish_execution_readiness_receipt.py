"""Pure receipt verification for Harness release publish execution readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping


_FORMAT = "harness-release-publish-execution-readiness-ledger-v1"
_DEPENDENCY_TYPE = "release-publish-execution-readiness"
_EVENT_TYPE = "release-publish-execution-readiness-ledger-record"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_RESULT_KEYS = frozenset(
    {
        "recorded_event_ids",
        "recorded_dependency_ids",
        "skipped_event_ids",
        "skipped_dependency_ids",
        "skipped_package_digests",
        "blockers",
        "ledger_snapshot",
    }
)
_SNAPSHOT_KEYS = frozenset(
    {"run_id", "gate_decisions", "dependencies", "audit_events", "tasks", "metadata"}
)
_METADATA_KEYS = frozenset(
    {
        "run_id",
        "source_dependency_id",
        "source_event_id",
        "completion_source_dependency_id",
        "completion_source_event_id",
        "acceptance_source_dependency_id",
        "acceptance_source_event_id",
        "final_authorization_dependency_id",
        "final_authorization_event_id",
        "package_digest",
        "package_digest_prefix",
        "readiness_summary",
        "receipt_summary",
        "canonical_payload",
    }
)
_READINESS_SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "package_digest_prefix",
        "source_dependency_id",
        "source_event_id",
        "completion_source_dependency_id",
        "completion_source_event_id",
        "acceptance_source_dependency_id",
        "acceptance_source_event_id",
        "final_authorization_dependency_id",
        "final_authorization_event_id",
        "ready",
    }
)
_RECEIPT_SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "package_digest_prefix",
        "source_dependency_id",
        "source_event_id",
        "completion_source_dependency_id",
        "completion_source_event_id",
        "acceptance_source_dependency_id",
        "acceptance_source_event_id",
        "source_blocker_count",
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
_MALFORMED = "malformed-release-publish-execution-readiness-receipt"


@dataclass(frozen=True)
class ReleasePublishExecutionReadinessReceiptVerification:
    """Frozen, JSON-safe release publish execution readiness verification result."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    work_id: str = ""
    dependency_id: str = ""
    event_id: str = ""
    package_digest: str = ""
    package_digest_prefix: str = ""
    source_dependency_id: str = ""
    source_event_id: str = ""
    completion_source_dependency_id: str = ""
    completion_source_event_id: str = ""
    acceptance_source_dependency_id: str = ""
    acceptance_source_event_id: str = ""
    final_authorization_dependency_id: str = ""
    final_authorization_event_id: str = ""
    receipt_summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "run_id": self.run_id,
            "work_id": self.work_id,
            "dependency_id": self.dependency_id,
            "event_id": self.event_id,
            "package_digest": self.package_digest,
            "package_digest_prefix": self.package_digest_prefix,
            "source_dependency_id": self.source_dependency_id,
            "source_event_id": self.source_event_id,
            "completion_source_dependency_id": self.completion_source_dependency_id,
            "completion_source_event_id": self.completion_source_event_id,
            "acceptance_source_dependency_id": self.acceptance_source_dependency_id,
            "acceptance_source_event_id": self.acceptance_source_event_id,
            "final_authorization_dependency_id": self.final_authorization_dependency_id,
            "final_authorization_event_id": self.final_authorization_event_id,
            "receipt_summary": _plain_copy(self.receipt_summary),
        }


def verify_release_publish_execution_readiness_receipt(
    ledger_source: object,
    *,
    run_id: object,
    work_id: object,
    expected_dependency_id: object = None,
    expected_event_id: object = None,
    expected_package_digest: object = None,
    expected_source_dependency_id: object = None,
    expected_source_event_id: object = None,
    expected_final_authorization_dependency_id: object = None,
    expected_final_authorization_event_id: object = None,
) -> ReleasePublishExecutionReadinessReceiptVerification:
    """Validate explicit in-memory CR-HAR-048 ledger result or snapshot data."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _required_text(work_id, "work-id", blockers)
    source = _plain_mapping(ledger_source, blockers)
    if source is None:
        blockers.append("missing-release-publish-execution-readiness-receipt")
        source = {}

    snapshot = _snapshot_from_source(source, blockers)
    _check_secret_or_action("receipt", source, blockers)

    receipt_run_id = _required_text(snapshot.get("run_id"), "receipt-run-id", blockers)
    _match(receipt_run_id, expected_run_id, "receipt-run-id", blockers)
    if set(snapshot) != _SNAPSHOT_KEYS:
        blockers.append("unsafe-release-publish-execution-readiness-snapshot-schema")

    dependencies = _record_sequence(snapshot.get("dependencies"), "dependencies", blockers)
    events = _record_sequence(snapshot.get("audit_events"), "audit-events", blockers)
    matching_dependencies = tuple(
        record for record in dependencies if record.get("dependency_type") == _DEPENDENCY_TYPE
    )
    matching_events = tuple(event for event in events if event.get("event_type") == _EVENT_TYPE)
    if len(matching_dependencies) != 1:
        blockers.append(
            "release-publish-execution-readiness-dependency-missing"
            if not matching_dependencies
            else "release-publish-execution-readiness-dependency-ambiguous"
        )
    if len(matching_events) != 1:
        blockers.append(
            "release-publish-execution-readiness-event-missing"
            if not matching_events
            else "release-publish-execution-readiness-event-ambiguous"
        )

    dependency = matching_dependencies[0] if len(matching_dependencies) == 1 else {}
    event = matching_events[0] if len(matching_events) == 1 else {}
    dependency_id = ""
    event_id = ""
    package_digest = ""
    source_dependency_id = ""
    source_event_id = ""
    completion_source_dependency_id = ""
    completion_source_event_id = ""
    acceptance_source_dependency_id = ""
    acceptance_source_event_id = ""
    final_authorization_dependency_id = ""
    final_authorization_event_id = ""
    receipt_summary: Mapping[str, Any] | None = None
    if dependency and event:
        values = _validate_pair(
            dependency=dependency,
            event=event,
            run_id=expected_run_id,
            work_id=expected_work_id,
            blockers=blockers,
        )
        (
            dependency_id,
            event_id,
            package_digest,
            source_dependency_id,
            source_event_id,
            completion_source_dependency_id,
            completion_source_event_id,
            acceptance_source_dependency_id,
            acceptance_source_event_id,
            final_authorization_dependency_id,
            final_authorization_event_id,
            receipt_summary,
        ) = values

    _validate_recorded_ids(
        source=source,
        dependency_id=dependency_id,
        event_id=event_id,
        blockers=blockers,
    )
    _expected_match(dependency_id, expected_dependency_id, "expected-dependency-id", blockers)
    _expected_match(event_id, expected_event_id, "expected-event-id", blockers)
    _expected_digest_match(package_digest, expected_package_digest, blockers)
    _expected_match(
        source_dependency_id,
        expected_source_dependency_id,
        "expected-source-dependency-id",
        blockers,
    )
    _expected_match(source_event_id, expected_source_event_id, "expected-source-event-id", blockers)
    _expected_match(
        final_authorization_dependency_id,
        expected_final_authorization_dependency_id,
        "expected-final-authorization-dependency-id",
        blockers,
    )
    _expected_match(
        final_authorization_event_id,
        expected_final_authorization_event_id,
        "expected-final-authorization-event-id",
        blockers,
    )

    deduped = tuple(dict.fromkeys(blockers))
    summary = _freeze_value(
        {
            "run_id": receipt_run_id or expected_run_id,
            "work_id": expected_work_id,
            "dependency_id": dependency_id,
            "event_id": event_id,
            "package_digest_prefix": package_digest[:12],
            "source_dependency_id": source_dependency_id,
            "source_event_id": source_event_id,
            "completion_source_dependency_id": completion_source_dependency_id,
            "completion_source_event_id": completion_source_event_id,
            "acceptance_source_dependency_id": acceptance_source_dependency_id,
            "acceptance_source_event_id": acceptance_source_event_id,
            "final_authorization_dependency_id": final_authorization_dependency_id,
            "final_authorization_event_id": final_authorization_event_id,
            "source_blocker_count": 0,
        }
    )
    return ReleasePublishExecutionReadinessReceiptVerification(
        passed=not deduped,
        blockers=deduped,
        run_id=receipt_run_id or expected_run_id,
        work_id=expected_work_id,
        dependency_id=dependency_id,
        event_id=event_id,
        package_digest=package_digest,
        package_digest_prefix=package_digest[:12],
        source_dependency_id=source_dependency_id,
        source_event_id=source_event_id,
        completion_source_dependency_id=completion_source_dependency_id,
        completion_source_event_id=completion_source_event_id,
        acceptance_source_dependency_id=acceptance_source_dependency_id,
        acceptance_source_event_id=acceptance_source_event_id,
        final_authorization_dependency_id=final_authorization_dependency_id,
        final_authorization_event_id=final_authorization_event_id,
        receipt_summary=summary if isinstance(summary, Mapping) else receipt_summary,
    )


def _snapshot_from_source(source: Mapping[str, Any], blockers: list[str]) -> dict[str, Any]:
    if "ledger_snapshot" not in source:
        return dict(source)
    if set(source) != _RESULT_KEYS:
        blockers.append("unsafe-release-publish-execution-readiness-result-schema")
    source_blockers = _blocker_tuple(source.get("blockers"))
    if source_blockers:
        blockers.append("source-blockers-present")
        blockers.extend(f"source-{blocker}" for blocker in source_blockers)
    for key, blocker in (
        ("skipped_event_ids", "skipped-event-ids-present"),
        ("skipped_dependency_ids", "skipped-dependency-ids-present"),
        ("skipped_package_digests", "skipped-package-digests-present"),
    ):
        values = _string_tuple(source.get(key), key, blockers)
        if values:
            blockers.append(blocker)
    snapshot = _plain_mapping(source.get("ledger_snapshot"), blockers)
    if snapshot is None:
        blockers.append("missing-ledger-snapshot")
        return {}
    return snapshot


def _validate_pair(
    *,
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    run_id: str,
    work_id: str,
    blockers: list[str],
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    Mapping[str, Any] | None,
]:
    if dependency.get("dependency_type") != _DEPENDENCY_TYPE:
        blockers.append("release-publish-execution-readiness-dependency-type-mismatch")
    if event.get("event_type") != _EVENT_TYPE:
        blockers.append("release-publish-execution-readiness-event-type-mismatch")
    if dependency.get("required") is not True:
        blockers.append("release-publish-execution-readiness-dependency-not-required")
    if dependency.get("status") != "ready":
        blockers.append("release-publish-execution-readiness-dependency-status-not-ready")
    if event.get("status") != "ready":
        blockers.append("release-publish-execution-readiness-event-status-not-ready")
    if event.get("actor") != "harness":
        blockers.append("release-publish-execution-readiness-event-actor-mismatch")
    _match(dependency.get("work_id"), work_id, "dependency-work-id", blockers)
    _match(event.get("work_id"), work_id, "event-work-id", blockers)

    dependency_id = _required_text(dependency.get("dependency_id"), "dependency-id", blockers)
    event_id = _required_text(event.get("event_id"), "event-id", blockers)
    dependency_metadata = _required_mapping(
        dependency.get("metadata"),
        "dependency-metadata",
        blockers,
    )
    event_metadata = _required_mapping(event.get("metadata"), "event-metadata", blockers)
    if set(dependency_metadata) != _METADATA_KEYS:
        blockers.append("unsafe-dependency-metadata-schema")
    if set(event_metadata) != (_METADATA_KEYS | {"dependency_id"}):
        blockers.append("unsafe-event-metadata-schema")
    _match(event_metadata.get("dependency_id"), dependency_id, "event-dependency-id", blockers)
    for key in _METADATA_KEYS:
        _match(
            event_metadata.get(key),
            dependency_metadata.get(key),
            f"event-{_label(key)}",
            blockers,
        )

    _match(dependency_metadata.get("run_id"), run_id, "dependency-run-id", blockers)
    _match(event_metadata.get("run_id"), run_id, "event-run-id", blockers)
    package_digest = _digest_text(
        dependency_metadata.get("package_digest"),
        "package-digest",
        blockers,
    )
    package_digest_prefix = _digest_prefix(
        dependency_metadata.get("package_digest_prefix"),
        "package-digest-prefix",
        blockers,
    )
    _match(package_digest[:12], package_digest_prefix, "package-digest-prefix", blockers)
    if package_digest:
        suffix = package_digest[:16]
        _match(
            dependency_id,
            f"release-publish-execution-readiness:{work_id}:{suffix}",
            "dependency-id",
            blockers,
        )
        _match(
            event_id,
            f"release-publish-execution-readiness-recorded:{work_id}:{suffix}",
            "event-id",
            blockers,
        )

    readiness_summary = _required_mapping(
        dependency_metadata.get("readiness_summary"),
        "readiness-summary",
        blockers,
    )
    receipt_summary = _required_mapping(
        dependency_metadata.get("receipt_summary"),
        "receipt-summary",
        blockers,
    )
    canonical = _required_mapping(
        dependency_metadata.get("canonical_payload"),
        "canonical-payload",
        blockers,
    )
    _validate_readiness_summary(
        readiness_summary,
        dependency_metadata,
        dependency_id,
        event_id,
        work_id,
        package_digest,
        blockers,
    )
    _validate_receipt_summary(
        receipt_summary,
        dependency_metadata,
        work_id,
        package_digest,
        blockers,
    )
    if set(canonical) != {"format", "execution_readiness"}:
        blockers.append("unsafe-canonical-payload-schema")
    _match(canonical.get("format"), _FORMAT, "canonical-payload-format", blockers)
    _match(
        canonical.get("execution_readiness"),
        readiness_summary,
        "canonical-payload-execution-readiness",
        blockers,
    )
    return (
        dependency_id,
        event_id,
        package_digest,
        str(dependency_metadata.get("source_dependency_id", "")),
        str(dependency_metadata.get("source_event_id", "")),
        str(dependency_metadata.get("completion_source_dependency_id", "")),
        str(dependency_metadata.get("completion_source_event_id", "")),
        str(dependency_metadata.get("acceptance_source_dependency_id", "")),
        str(dependency_metadata.get("acceptance_source_event_id", "")),
        str(dependency_metadata.get("final_authorization_dependency_id", "")),
        str(dependency_metadata.get("final_authorization_event_id", "")),
        receipt_summary,
    )


def _validate_readiness_summary(
    summary: Mapping[str, Any],
    metadata: Mapping[str, Any],
    dependency_id: str,
    event_id: str,
    work_id: str,
    package_digest: str,
    blockers: list[str],
) -> None:
    suffix = package_digest[:16]
    handoff_dependency_id = f"release-publish-handoff-completion:{work_id}:{suffix}"
    handoff_event_id = f"release-publish-handoff-completion-recorded:{work_id}:{suffix}"
    if set(summary) != _READINESS_SUMMARY_KEYS:
        blockers.append("unsafe-readiness-summary-schema")
    _match(summary.get("run_id"), metadata.get("run_id"), "readiness-summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "readiness-summary-work-id", blockers)
    _match(summary.get("dependency_id"), metadata.get("final_authorization_dependency_id"), "readiness-summary-dependency-id", blockers)
    _match(summary.get("event_id"), metadata.get("final_authorization_event_id"), "readiness-summary-event-id", blockers)
    _match(summary.get("package_digest_prefix"), metadata.get("package_digest_prefix"), "readiness-summary-package-digest-prefix", blockers)
    _match(summary.get("source_dependency_id"), handoff_dependency_id, "readiness-summary-source-dependency-id", blockers)
    _match(summary.get("source_event_id"), handoff_event_id, "readiness-summary-source-event-id", blockers)
    for key in (
        "completion_source_dependency_id",
        "completion_source_event_id",
        "acceptance_source_dependency_id",
        "acceptance_source_event_id",
        "final_authorization_dependency_id",
        "final_authorization_event_id",
    ):
        _match(summary.get(key), metadata.get(key), f"readiness-summary-{_label(key)}", blockers)
    _match(summary.get("ready"), True, "readiness-summary-ready", blockers)
    _match(metadata.get("final_authorization_dependency_id"), metadata.get("source_dependency_id"), "final-authorization-dependency-id", blockers)
    _match(metadata.get("final_authorization_event_id"), metadata.get("source_event_id"), "final-authorization-event-id", blockers)
    _match(dependency_id, f"{_DEPENDENCY_TYPE}:{work_id}:{suffix}", "readiness-ledger-dependency-id", blockers)
    _match(event_id, f"release-publish-execution-readiness-recorded:{work_id}:{suffix}", "readiness-ledger-event-id", blockers)


def _validate_receipt_summary(
    summary: Mapping[str, Any],
    metadata: Mapping[str, Any],
    work_id: str,
    package_digest: str,
    blockers: list[str],
) -> None:
    suffix = package_digest[:16]
    handoff_dependency_id = f"release-publish-handoff-completion:{work_id}:{suffix}"
    handoff_event_id = f"release-publish-handoff-completion-recorded:{work_id}:{suffix}"
    if set(summary) != _RECEIPT_SUMMARY_KEYS:
        blockers.append("unsafe-receipt-summary-schema")
    _match(summary.get("run_id"), metadata.get("run_id"), "receipt-summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "receipt-summary-work-id", blockers)
    _match(summary.get("dependency_id"), metadata.get("final_authorization_dependency_id"), "receipt-summary-dependency-id", blockers)
    _match(summary.get("event_id"), metadata.get("final_authorization_event_id"), "receipt-summary-event-id", blockers)
    _match(summary.get("package_digest_prefix"), metadata.get("package_digest_prefix"), "receipt-summary-package-digest-prefix", blockers)
    _match(summary.get("source_dependency_id"), handoff_dependency_id, "receipt-summary-source-dependency-id", blockers)
    _match(summary.get("source_event_id"), handoff_event_id, "receipt-summary-source-event-id", blockers)
    for key in (
        "completion_source_dependency_id",
        "completion_source_event_id",
        "acceptance_source_dependency_id",
        "acceptance_source_event_id",
    ):
        _match(summary.get(key), metadata.get(key), f"receipt-summary-{_label(key)}", blockers)
    source_blocker_count = summary.get("source_blocker_count")
    if not (type(source_blocker_count) is int and source_blocker_count == 0):
        blockers.append("release-publish-execution-readiness-receipt-summary-source-blocker-count-mismatch")


def _validate_recorded_ids(
    *,
    source: Mapping[str, Any],
    dependency_id: str,
    event_id: str,
    blockers: list[str],
) -> None:
    if "ledger_snapshot" not in source:
        return
    dependency_ids = _string_tuple(source.get("recorded_dependency_ids"), "recorded-dependency-ids", blockers)
    event_ids = _string_tuple(source.get("recorded_event_ids"), "recorded-event-ids", blockers)
    if len(dependency_ids) != 1:
        blockers.append(
            "release-publish-execution-readiness-recorded-dependency-id-missing"
            if not dependency_ids
            else "release-publish-execution-readiness-recorded-dependency-id-ambiguous"
        )
    elif dependency_id and dependency_ids[0] != dependency_id:
        blockers.append("release-publish-execution-readiness-recorded-dependency-id-mismatch")
    if len(event_ids) != 1:
        blockers.append(
            "release-publish-execution-readiness-recorded-event-id-missing"
            if not event_ids
            else "release-publish-execution-readiness-recorded-event-id-ambiguous"
        )
    elif event_id and event_ids[0] != event_id:
        blockers.append("release-publish-execution-readiness-recorded-event-id-mismatch")


def _plain_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            mapped = to_dict()
        except Exception:
            blockers.append(_MALFORMED)
            return None
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
        blockers.append(_MALFORMED)
        return None
    if is_dataclass(value) and not isinstance(value, type):
        try:
            mapped = asdict(value)
        except Exception:
            blockers.append(_MALFORMED)
            return None
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
    return None


def _validated_mapping(value: Mapping[Any, Any], blockers: list[str]) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            blockers.append("non-string-key-release-publish-execution-readiness-receipt")
            continue
        plain[key] = _plain_value(item, blockers)
    return plain


def _plain_value(value: object, blockers: list[str]) -> object:
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            mapped = to_dict()
        except Exception:
            blockers.append("unsupported-object-release-publish-execution-readiness-receipt")
            return None
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
        blockers.append("unsupported-object-release-publish-execution-readiness-receipt")
        return None
    if is_dataclass(value) and not isinstance(value, type):
        try:
            mapped = asdict(value)
        except Exception:
            blockers.append("unsupported-object-release-publish-execution-readiness-receipt")
            return None
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
    if isinstance(value, list):
        return tuple(_plain_value(item, blockers) for item in value)
    if isinstance(value, tuple):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    blockers.append("unsupported-object-release-publish-execution-readiness-receipt")
    return None


def _record_sequence(value: object, name: str, blockers: list[str]) -> tuple[dict[str, Any], ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, tuple):
        blockers.append(f"malformed-{name}")
        return ()
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            blockers.append(f"malformed-{name}-record")
            continue
        records.append(dict(item))
    return tuple(records)


def _required_mapping(value: object, name: str, blockers: list[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        blockers.append(f"missing-{name}")
        return {}
    if not value:
        blockers.append(f"missing-{name}")
    return dict(value)


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
    if isinstance(value, tuple):
        if not all(isinstance(item, str) for item in value):
            return ("malformed-source-blockers",)
        return tuple(item.strip() for item in value if item.strip())
    return ("malformed-source-blockers",)


def _string_tuple(value: object, name: str, blockers: list[str]) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if not isinstance(value, tuple) or not all(isinstance(item, str) and item for item in value):
        blockers.append(f"malformed-{name}")
        return ()
    return value


def _expected_match(actual: str, expected: object, name: str, blockers: list[str]) -> None:
    if expected is None:
        return
    expected_text = _required_text(expected, name, blockers)
    if actual and expected_text and actual != expected_text:
        blockers.append(f"{name}-mismatch")


def _expected_digest_match(actual: str, expected: object, blockers: list[str]) -> None:
    if expected is None:
        return
    expected_digest = _digest_text(expected, "expected-package-digest", blockers)
    if actual and expected_digest and actual != expected_digest:
        blockers.append("expected-package-digest-mismatch")


def _match(actual: object, expected: object, name: str, blockers: list[str]) -> None:
    if actual != expected:
        blockers.append(f"release-publish-execution-readiness-receipt-{name}-mismatch")


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
    if normalized == "execution_readiness":
        return False
    return any(fragment in normalized for fragment in _ACTION_KEYS)


def _has_action_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = f" {value.strip().lower()} "
    return any(term in text for term in _ACTION_TEXT)


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(value[key]) for key in sorted(value)})
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


def _label(value: str) -> str:
    return value.replace("_", "-")


__all__ = [
    "ReleasePublishExecutionReadinessReceiptVerification",
    "verify_release_publish_execution_readiness_receipt",
]
