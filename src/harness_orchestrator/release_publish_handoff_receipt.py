"""Pure receipt verification for Harness release publish handoff packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


_LEDGER_FORMAT = "harness-release-publish-handoff-package-ledger-v1"
_PACKAGE_FORMAT = "harness-release-publish-handoff-package-v1"
_DEPENDENCY_TYPE = "release-publish-handoff-package"
_EVENT_TYPE = "release-publish-handoff-package-ledger-record"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
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
_METADATA_KEYS = frozenset(
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


@dataclass(frozen=True)
class ReleasePublishHandoffReceiptVerification:
    """Frozen, JSON-safe release publish handoff receipt verification result."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    work_id: str = ""
    dependency_id: str = ""
    event_id: str = ""
    package_digest: str = ""
    package_digest_prefix: str = ""
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
            "receipt_summary": _plain_copy(self.receipt_summary),
        }


def verify_release_publish_handoff_receipt(
    receipt_source: object,
    *,
    run_id: object,
    work_id: object,
    expected_dependency_id: object = None,
    expected_event_id: object = None,
    expected_package_digest: object = None,
) -> ReleasePublishHandoffReceiptVerification:
    """Validate explicit in-memory handoff package receipt data without effects."""

    blockers: list[str] = []
    expected_run_id_text = _required_text(run_id, "run-id", blockers)
    expected_work_id_text = _required_text(work_id, "work-id", blockers)
    receipt = _plain_mapping(receipt_source, blockers)
    if receipt is None:
        blockers.append("missing-release-publish-handoff-receipt")
        receipt = {}

    source_blockers = _blocker_tuple(receipt.get("blockers"))
    if source_blockers:
        blockers.append("source-blockers-present")
        blockers.extend(f"source-{blocker}" for blocker in source_blockers)

    receipt_run_id = _required_text(receipt.get("run_id"), "receipt-run-id", blockers)
    _match(receipt_run_id, expected_run_id_text, "receipt-run-id", blockers)
    _check_secret_or_action("receipt", receipt, blockers)

    dependencies = _record_sequence(receipt.get("dependencies"), "dependencies", blockers)
    events = _record_sequence(receipt.get("audit_events"), "audit-events", blockers)
    matching_dependencies = _matching_records(
        dependencies,
        "dependency_type",
        _DEPENDENCY_TYPE,
        expected_work_id_text,
        blockers,
    )
    matching_events = _matching_records(
        events,
        "event_type",
        _EVENT_TYPE,
        expected_work_id_text,
        blockers,
    )
    if len(matching_dependencies) != 1:
        blockers.append(
            "release-publish-handoff-package-dependency-missing"
            if not matching_dependencies
            else "release-publish-handoff-package-dependency-ambiguous"
        )
    if len(matching_events) != 1:
        blockers.append(
            "release-publish-handoff-package-event-missing"
            if not matching_events
            else "release-publish-handoff-package-event-ambiguous"
        )

    dependency = matching_dependencies[0] if len(matching_dependencies) == 1 else {}
    event = matching_events[0] if len(matching_events) == 1 else {}
    package_digest = ""
    dependency_id = ""
    event_id = ""
    if dependency and event:
        dependency_id, event_id, package_digest = _validate_pair(
            dependency=dependency,
            event=event,
            run_id=expected_run_id_text,
            work_id=expected_work_id_text,
            blockers=blockers,
        )

    _expected_match(dependency_id, expected_dependency_id, "expected-dependency-id", blockers)
    _expected_match(event_id, expected_event_id, "expected-event-id", blockers)
    _expected_digest_match(package_digest, expected_package_digest, blockers)

    deduped = tuple(dict.fromkeys(blockers))
    summary = _freeze_value(
        {
            "run_id": receipt_run_id or expected_run_id_text,
            "work_id": expected_work_id_text,
            "dependency_id": dependency_id,
            "event_id": event_id,
            "package_digest_prefix": package_digest[:12],
            "source_blocker_count": len(source_blockers),
        }
    )
    return ReleasePublishHandoffReceiptVerification(
        passed=not deduped,
        blockers=deduped,
        run_id=receipt_run_id or expected_run_id_text,
        work_id=expected_work_id_text,
        dependency_id=dependency_id,
        event_id=event_id,
        package_digest=package_digest,
        package_digest_prefix=package_digest[:12],
        receipt_summary=summary if isinstance(summary, Mapping) else None,
    )


def _validate_pair(
    *,
    dependency: Mapping[str, Any],
    event: Mapping[str, Any],
    run_id: str,
    work_id: str,
    blockers: list[str],
) -> tuple[str, str, str]:
    if dependency.get("dependency_type") != _DEPENDENCY_TYPE:
        blockers.append("release-publish-handoff-package-dependency-type-mismatch")
    if event.get("event_type") != _EVENT_TYPE:
        blockers.append("release-publish-handoff-package-event-type-mismatch")
    if dependency.get("required") is not True:
        blockers.append("release-publish-handoff-package-dependency-not-required")
    if dependency.get("status") != "ready":
        blockers.append("release-publish-handoff-package-dependency-status-not-ready")
    if event.get("status") != "ready":
        blockers.append("release-publish-handoff-package-event-status-not-ready")
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

    package_data = _required_mapping(
        dependency_metadata.get("package_data"),
        "package-data",
        blockers,
    )
    canonical = _required_mapping(
        dependency_metadata.get("canonical_payload"),
        "canonical-payload",
        blockers,
    )
    _validate_package_data(
        package_data=package_data,
        run_id=run_id,
        work_id=work_id,
        dependency_metadata=dependency_metadata,
        dependency_id=dependency_id,
        event_id=event_id,
        package_digest=package_digest,
        blockers=blockers,
    )
    _validate_canonical_payload(
        canonical=canonical,
        package_data=package_data,
        blockers=blockers,
    )
    return dependency_id, event_id, package_digest


def _validate_package_data(
    *,
    package_data: Mapping[str, Any],
    run_id: str,
    work_id: str,
    dependency_metadata: Mapping[str, Any],
    dependency_id: str,
    event_id: str,
    package_digest: str,
    blockers: list[str],
) -> None:
    if set(package_data) != _PACKAGE_DATA_KEYS:
        blockers.append("unsafe-package-data-schema")
    _match(package_data.get("format"), _PACKAGE_FORMAT, "package-data-format", blockers)
    _match(package_data.get("run_id"), run_id, "package-data-run-id", blockers)
    _match(package_data.get("work_id"), work_id, "package-data-work-id", blockers)
    _match(
        package_data.get("readiness_dependency_id"),
        dependency_metadata.get("readiness_dependency_id"),
        "package-data-readiness-dependency-id",
        blockers,
    )
    _match(
        package_data.get("readiness_event_id"),
        dependency_metadata.get("readiness_event_id"),
        "package-data-readiness-event-id",
        blockers,
    )
    _match(
        package_data.get("intent_dependency_id"),
        dependency_metadata.get("intent_dependency_id"),
        "package-data-intent-dependency-id",
        blockers,
    )
    _match(
        package_data.get("intent_event_id"),
        dependency_metadata.get("intent_event_id"),
        "package-data-intent-event-id",
        blockers,
    )
    handoff_digest = _digest_text(
        package_data.get("handoff_readiness_digest"),
        "handoff-readiness-digest",
        blockers,
    )
    intent_digest = _digest_text(package_data.get("intent_digest"), "intent-digest", blockers)
    binding_digest = _digest_text(
        package_data.get("release_binding_digest"),
        "release-binding-digest",
        blockers,
    )
    _match(
        handoff_digest[:12],
        dependency_metadata.get("handoff_readiness_digest_prefix"),
        "handoff-readiness-digest-prefix",
        blockers,
    )
    _match(
        intent_digest[:12],
        dependency_metadata.get("intent_digest_prefix"),
        "intent-digest-prefix",
        blockers,
    )
    _match(
        binding_digest[:12],
        dependency_metadata.get("release_binding_digest_prefix"),
        "release-binding-digest-prefix",
        blockers,
    )
    if package_digest and _sha256_payload(package_data) != package_digest:
        blockers.append("release-publish-handoff-package-digest-mismatch")
    if dependency_id and package_digest:
        _match(
            dependency_id,
            f"release-publish-handoff-package:{work_id}:{package_digest[:16]}",
            "package-data-dependency-id",
            blockers,
        )
    if event_id and package_digest:
        _match(
            event_id,
            f"release-publish-handoff-package-recorded:{work_id}:{package_digest[:16]}",
            "package-data-event-id",
            blockers,
        )


def _validate_canonical_payload(
    *,
    canonical: Mapping[str, Any],
    package_data: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if set(canonical) != {"format", "handoff_package"}:
        blockers.append("unsafe-canonical-payload-schema")
    _match(canonical.get("format"), _LEDGER_FORMAT, "canonical-payload-format", blockers)
    _match(canonical.get("handoff_package"), package_data, "canonical-payload-package-data", blockers)


def _plain_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
        blockers.append("malformed-release-publish-handoff-receipt")
        return None
    return None


def _validated_mapping(value: Mapping[Any, Any], blockers: list[str]) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            blockers.append("non-string-key-release-publish-handoff-receipt")
            continue
        plain[key] = _plain_value(item, blockers)
    return plain


def _plain_value(value: object, blockers: list[str]) -> object:
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
        blockers.append("unsupported-object-release-publish-handoff-receipt")
        return None
    if isinstance(value, list):
        return tuple(_plain_value(item, blockers) for item in value)
    if isinstance(value, tuple):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    blockers.append("unsupported-object-release-publish-handoff-receipt")
    return None


def _record_sequence(
    value: object,
    name: str,
    blockers: list[str],
) -> tuple[dict[str, Any], ...]:
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
        elif record.get("work_id") in (None, ""):
            blockers.append(f"{kind}-work-id-missing")
    return tuple(matches)


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


def _expected_match(
    actual: str,
    expected: object,
    name: str,
    blockers: list[str],
) -> None:
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
        blockers.append(f"release-publish-handoff-receipt-{name}-mismatch")


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
    "ReleasePublishHandoffReceiptVerification",
    "verify_release_publish_handoff_receipt",
]
