"""Pure handoff readiness boundary for Harness release publish execution."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping


_VERIFICATION_KEYS = frozenset(
    {
        "passed",
        "blockers",
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "package_digest",
        "package_digest_prefix",
        "source_dependency_id",
        "source_event_id",
        "completion_source_dependency_id",
        "completion_source_event_id",
        "acceptance_source_dependency_id",
        "acceptance_source_event_id",
        "final_authorization_dependency_id",
        "final_authorization_event_id",
        "authorization_summary",
        "receipt_summary",
    }
)
_AUTHORIZATION_SUMMARY_KEYS = frozenset(
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
        "authorized",
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
        "final_authorization_dependency_id",
        "final_authorization_event_id",
        "source_blocker_count",
    }
)
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
_ALLOWED_ACTION_KEYS = frozenset(
    {
        "release_publish_execution_authorization",
        "release_publish_execution_authorization_recorded",
        "release_publish_execution_handoff_readiness",
        "release_publish_execution_handoff_readiness_recorded",
        "release_publish_execution_readiness",
        "release_publish_execution_readiness_recorded",
        "release_publish_execution_readiness_acceptance",
        "release_publish_execution_readiness_acceptance_recorded",
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
class ReleasePublishExecutionHandoffReadiness:
    """Frozen, JSON-safe readiness data for a Harness-only execution handoff."""

    ready: bool = False
    status: str = "blocked"
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    work_id: str = ""
    dependency_id: str = ""
    event_id: str = ""
    package_digest: str = ""
    package_digest_prefix: str = ""
    source_dependency_id: str = ""
    source_event_id: str = ""
    authorization_dependency_id: str = ""
    authorization_event_id: str = ""
    readiness_summary: Mapping[str, Any] | None = None
    authorization_summary: Mapping[str, Any] | None = None
    receipt_summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "run_id": self.run_id,
            "work_id": self.work_id,
            "dependency_id": self.dependency_id,
            "event_id": self.event_id,
            "package_digest": self.package_digest,
            "package_digest_prefix": self.package_digest_prefix,
            "source_dependency_id": self.source_dependency_id,
            "source_event_id": self.source_event_id,
            "authorization_dependency_id": self.authorization_dependency_id,
            "authorization_event_id": self.authorization_event_id,
            "readiness_summary": _plain_copy(self.readiness_summary),
            "authorization_summary": _plain_copy(self.authorization_summary),
            "receipt_summary": _plain_copy(self.receipt_summary),
        }


def evaluate_release_publish_execution_handoff_readiness(
    readiness_source: object,
    *,
    run_id: object,
    work_id: object,
    expected_dependency_id: object = None,
    expected_event_id: object = None,
    expected_package_digest: object = None,
    expected_source_dependency_id: object = None,
    expected_source_event_id: object = None,
    expected_authorization_dependency_id: object = None,
    expected_authorization_event_id: object = None,
) -> ReleasePublishExecutionHandoffReadiness:
    """Accept only explicit, already-passed CR-HAR-055 verification data."""

    blockers: list[str] = []
    expected_run_id_text = _required_text(run_id, "run-id", blockers)
    expected_work_id_text = _required_text(work_id, "work-id", blockers)
    verification = _plain_mapping(readiness_source, blockers)
    if verification is None:
        blockers.append("missing-release-publish-execution-authorization-receipt-verification")
        verification = {}

    if set(verification) != _VERIFICATION_KEYS:
        blockers.append("unsafe-release-publish-execution-handoff-readiness-verification-schema")
    if verification.get("passed") is not True:
        blockers.append("release-publish-execution-authorization-receipt-verification-not-passed")

    source_blockers = _blocker_tuple(verification.get("blockers"))
    if source_blockers:
        blockers.append("source-blockers-present")
        blockers.extend(f"source-{blocker}" for blocker in source_blockers)

    verification_run_id = _required_text(verification.get("run_id"), "verification-run-id", blockers)
    verification_work_id = _required_text(verification.get("work_id"), "verification-work-id", blockers)
    authorization_dependency_id = _required_text(verification.get("dependency_id"), "authorization-dependency-id", blockers)
    authorization_event_id = _required_text(verification.get("event_id"), "authorization-event-id", blockers)
    package_digest = _digest_text(verification.get("package_digest"), "package-digest", blockers)
    package_digest_prefix = _digest_prefix(verification.get("package_digest_prefix"), "package-digest-prefix", blockers)
    readiness_source_dependency_id = _required_text(verification.get("source_dependency_id"), "readiness-source-dependency-id", blockers)
    readiness_source_event_id = _required_text(verification.get("source_event_id"), "readiness-source-event-id", blockers)
    completion_source_dependency_id = _required_text(verification.get("completion_source_dependency_id"), "completion-source-dependency-id", blockers)
    completion_source_event_id = _required_text(verification.get("completion_source_event_id"), "completion-source-event-id", blockers)
    acceptance_source_dependency_id = _required_text(verification.get("acceptance_source_dependency_id"), "acceptance-source-dependency-id", blockers)
    acceptance_source_event_id = _required_text(verification.get("acceptance_source_event_id"), "acceptance-source-event-id", blockers)
    final_authorization_dependency_id = _required_text(verification.get("final_authorization_dependency_id"), "final-authorization-dependency-id", blockers)
    final_authorization_event_id = _required_text(verification.get("final_authorization_event_id"), "final-authorization-event-id", blockers)

    _match(verification_run_id, expected_run_id_text, "verification-run-id", blockers)
    _match(verification_work_id, expected_work_id_text, "verification-work-id", blockers)
    if package_digest and package_digest_prefix:
        _match(package_digest[:12], package_digest_prefix, "package-digest-prefix", blockers)

    dependency_id = ""
    event_id = ""
    expected_ids = _deterministic_ids(verification_work_id, package_digest)
    if verification_work_id and package_digest:
        dependency_id = expected_ids["dependency_id"]
        event_id = expected_ids["event_id"]
        _match(authorization_dependency_id, expected_ids["authorization_dependency_id"], "authorization-dependency-id", blockers)
        _match(authorization_event_id, expected_ids["authorization_event_id"], "authorization-event-id", blockers)
        _match(readiness_source_dependency_id, expected_ids["readiness_dependency_id"], "readiness-source-dependency-id", blockers)
        _match(readiness_source_event_id, expected_ids["readiness_event_id"], "readiness-source-event-id", blockers)
        _match(completion_source_dependency_id, expected_ids["completion_dependency_id"], "completion-source-dependency-id", blockers)
        _match(completion_source_event_id, expected_ids["completion_event_id"], "completion-source-event-id", blockers)
        _match(acceptance_source_dependency_id, expected_ids["package_dependency_id"], "acceptance-source-dependency-id", blockers)
        _match(acceptance_source_event_id, expected_ids["package_event_id"], "acceptance-source-event-id", blockers)
        _match(final_authorization_dependency_id, expected_ids["final_authorization_dependency_id"], "final-authorization-dependency-id", blockers)
        _match(final_authorization_event_id, expected_ids["final_authorization_event_id"], "final-authorization-event-id", blockers)

    _expected_match(dependency_id, expected_dependency_id, "expected-dependency-id", blockers)
    _expected_match(event_id, expected_event_id, "expected-event-id", blockers)
    _expected_digest_match(package_digest, expected_package_digest, blockers)
    _expected_match(authorization_dependency_id, expected_source_dependency_id, "expected-source-dependency-id", blockers)
    _expected_match(authorization_event_id, expected_source_event_id, "expected-source-event-id", blockers)
    _expected_match(
        authorization_dependency_id,
        expected_authorization_dependency_id,
        "expected-authorization-dependency-id",
        blockers,
    )
    _expected_match(
        authorization_event_id,
        expected_authorization_event_id,
        "expected-authorization-event-id",
        blockers,
    )

    authorization_summary = _required_authorization_summary(
        verification.get("authorization_summary"),
        run_id=verification_run_id,
        work_id=verification_work_id,
        authorization_dependency_id=authorization_dependency_id,
        authorization_event_id=authorization_event_id,
        package_digest_prefix=package_digest_prefix,
        expected_ids=expected_ids,
        blockers=blockers,
    )
    receipt_summary = _required_receipt_summary(
        verification.get("receipt_summary"),
        authorization_summary=authorization_summary,
        run_id=verification_run_id,
        work_id=verification_work_id,
        authorization_dependency_id=authorization_dependency_id,
        authorization_event_id=authorization_event_id,
        package_digest_prefix=package_digest_prefix,
        expected_ids=expected_ids,
        blockers=blockers,
    )
    _check_secret_or_action("verification", verification, blockers)

    deduped = tuple(dict.fromkeys(blockers))
    ready = not deduped
    summary = _freeze_value(
        {
            "run_id": verification_run_id or expected_run_id_text,
            "work_id": verification_work_id or expected_work_id_text,
            "dependency_id": dependency_id,
            "event_id": event_id,
            "package_digest_prefix": package_digest_prefix,
            "source_dependency_id": authorization_dependency_id,
            "source_event_id": authorization_event_id,
            "authorization_dependency_id": authorization_dependency_id,
            "authorization_event_id": authorization_event_id,
            "ready": ready,
        }
    )
    frozen_authorization = _freeze_value(authorization_summary) if authorization_summary is not None else None
    frozen_receipt = _freeze_value(receipt_summary) if receipt_summary is not None else None
    return ReleasePublishExecutionHandoffReadiness(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped,
        run_id=verification_run_id or expected_run_id_text,
        work_id=verification_work_id or expected_work_id_text,
        dependency_id=dependency_id,
        event_id=event_id,
        package_digest=package_digest,
        package_digest_prefix=package_digest_prefix,
        source_dependency_id=authorization_dependency_id,
        source_event_id=authorization_event_id,
        authorization_dependency_id=authorization_dependency_id,
        authorization_event_id=authorization_event_id,
        readiness_summary=summary if isinstance(summary, Mapping) else None,
        authorization_summary=frozen_authorization if isinstance(frozen_authorization, Mapping) else None,
        receipt_summary=frozen_receipt if isinstance(frozen_receipt, Mapping) else None,
    )


def verify_release_publish_execution_handoff_readiness(
    readiness_source: object,
    *,
    run_id: object,
    work_id: object,
    expected_dependency_id: object = None,
    expected_event_id: object = None,
    expected_package_digest: object = None,
    expected_source_dependency_id: object = None,
    expected_source_event_id: object = None,
    expected_authorization_dependency_id: object = None,
    expected_authorization_event_id: object = None,
) -> ReleasePublishExecutionHandoffReadiness:
    """Compatibility wrapper for execution handoff readiness evaluation."""

    return evaluate_release_publish_execution_handoff_readiness(
        readiness_source,
        run_id=run_id,
        work_id=work_id,
        expected_dependency_id=expected_dependency_id,
        expected_event_id=expected_event_id,
        expected_package_digest=expected_package_digest,
        expected_source_dependency_id=expected_source_dependency_id,
        expected_source_event_id=expected_source_event_id,
        expected_authorization_dependency_id=expected_authorization_dependency_id,
        expected_authorization_event_id=expected_authorization_event_id,
    )


def _deterministic_ids(work_id: str, package_digest: str) -> dict[str, str]:
    suffix = package_digest[:16]
    return {
        "dependency_id": f"release-publish-execution-handoff-readiness:{work_id}:{suffix}",
        "event_id": f"release-publish-execution-handoff-readiness-recorded:{work_id}:{suffix}",
        "authorization_dependency_id": f"release-publish-execution-authorization:{work_id}:{suffix}",
        "authorization_event_id": f"release-publish-execution-authorization-recorded:{work_id}:{suffix}",
        "final_authorization_dependency_id": f"release-publish-final-authorization:{work_id}:{suffix}",
        "final_authorization_event_id": f"release-publish-final-authorization-recorded:{work_id}:{suffix}",
        "readiness_dependency_id": f"release-publish-execution-readiness:{work_id}:{suffix}",
        "readiness_event_id": f"release-publish-execution-readiness-recorded:{work_id}:{suffix}",
        "acceptance_dependency_id": f"release-publish-execution-readiness-acceptance:{work_id}:{suffix}",
        "acceptance_event_id": f"release-publish-execution-readiness-acceptance-recorded:{work_id}:{suffix}",
        "completion_dependency_id": f"release-publish-handoff-acceptance:{work_id}:{suffix}",
        "completion_event_id": f"release-publish-handoff-acceptance-recorded:{work_id}:{suffix}",
        "package_dependency_id": f"release-publish-handoff-package:{work_id}:{suffix}",
        "package_event_id": f"release-publish-handoff-package-recorded:{work_id}:{suffix}",
    }


def _required_authorization_summary(
    value: object,
    *,
    run_id: str,
    work_id: str,
    authorization_dependency_id: str,
    authorization_event_id: str,
    package_digest_prefix: str,
    expected_ids: Mapping[str, str],
    blockers: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        blockers.append("missing-authorization-summary")
        return None
    summary = dict(value)
    if set(summary) != _AUTHORIZATION_SUMMARY_KEYS:
        blockers.append("unsafe-authorization-summary-schema")
    _validate_source_chain_summary(
        summary,
        run_id=run_id,
        work_id=work_id,
        dependency_id=expected_ids["acceptance_dependency_id"],
        event_id=expected_ids["acceptance_event_id"],
        package_digest_prefix=package_digest_prefix,
        expected_ids=expected_ids,
        label="authorization-summary",
        blockers=blockers,
    )
    _match(summary.get("authorized"), True, "authorization-summary-authorized", blockers)
    return summary


def _required_receipt_summary(
    value: object,
    *,
    authorization_summary: Mapping[str, Any] | None,
    run_id: str,
    work_id: str,
    authorization_dependency_id: str,
    authorization_event_id: str,
    package_digest_prefix: str,
    expected_ids: Mapping[str, str],
    blockers: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        blockers.append("missing-receipt-summary")
        return None
    summary = dict(value)
    if set(summary) != _RECEIPT_SUMMARY_KEYS:
        blockers.append("unsafe-receipt-summary-schema")
    _match(summary.get("run_id"), run_id, "receipt-summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "receipt-summary-work-id", blockers)
    _match(summary.get("dependency_id"), authorization_summary.get("source_dependency_id") if authorization_summary else "", "receipt-summary-dependency-id", blockers)
    _match(summary.get("event_id"), authorization_summary.get("source_event_id") if authorization_summary else "", "receipt-summary-event-id", blockers)
    _match(summary.get("package_digest_prefix"), package_digest_prefix, "receipt-summary-package-digest-prefix", blockers)
    _match(summary.get("source_dependency_id"), expected_ids["final_authorization_dependency_id"], "receipt-summary-source-dependency-id", blockers)
    _match(summary.get("source_event_id"), expected_ids["final_authorization_event_id"], "receipt-summary-source-event-id", blockers)
    _match(summary.get("completion_source_dependency_id"), expected_ids["completion_dependency_id"], "receipt-summary-completion-source-dependency-id", blockers)
    _match(summary.get("completion_source_event_id"), expected_ids["completion_event_id"], "receipt-summary-completion-source-event-id", blockers)
    _match(summary.get("acceptance_source_dependency_id"), expected_ids["package_dependency_id"], "receipt-summary-acceptance-source-dependency-id", blockers)
    _match(summary.get("acceptance_source_event_id"), expected_ids["package_event_id"], "receipt-summary-acceptance-source-event-id", blockers)
    _match(summary.get("final_authorization_dependency_id"), expected_ids["final_authorization_dependency_id"], "receipt-summary-final-authorization-dependency-id", blockers)
    _match(summary.get("final_authorization_event_id"), expected_ids["final_authorization_event_id"], "receipt-summary-final-authorization-event-id", blockers)
    if authorization_summary is not None:
        for key in _RECEIPT_SUMMARY_KEYS - {"source_blocker_count"}:
            if key in {"dependency_id", "event_id", "source_dependency_id", "source_event_id"}:
                continue
            _match(summary.get(key), authorization_summary.get(key), f"receipt-summary-authorization-{_label(key)}", blockers)
        _match(summary.get("dependency_id"), authorization_summary.get("source_dependency_id"), "receipt-summary-authorization-source-dependency-id", blockers)
        _match(summary.get("event_id"), authorization_summary.get("source_event_id"), "receipt-summary-authorization-source-event-id", blockers)
    source_blocker_count = summary.get("source_blocker_count")
    if not (type(source_blocker_count) is int and source_blocker_count == 0):
        blockers.append("release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch")
    return summary


def _validate_source_chain_summary(
    summary: Mapping[str, Any],
    *,
    run_id: str,
    work_id: str,
    dependency_id: object,
    event_id: object,
    package_digest_prefix: str,
    expected_ids: Mapping[str, str],
    label: str,
    blockers: list[str],
) -> None:
    _match(summary.get("run_id"), run_id, f"{label}-run-id", blockers)
    _match(summary.get("work_id"), work_id, f"{label}-work-id", blockers)
    _match(summary.get("dependency_id"), dependency_id, f"{label}-dependency-id", blockers)
    _match(summary.get("event_id"), event_id, f"{label}-event-id", blockers)
    _match(summary.get("package_digest_prefix"), package_digest_prefix, f"{label}-package-digest-prefix", blockers)
    _match(summary.get("source_dependency_id"), expected_ids["readiness_dependency_id"], f"{label}-source-dependency-id", blockers)
    _match(summary.get("source_event_id"), expected_ids["readiness_event_id"], f"{label}-source-event-id", blockers)
    _match(summary.get("completion_source_dependency_id"), expected_ids["completion_dependency_id"], f"{label}-completion-source-dependency-id", blockers)
    _match(summary.get("completion_source_event_id"), expected_ids["completion_event_id"], f"{label}-completion-source-event-id", blockers)
    _match(summary.get("acceptance_source_dependency_id"), expected_ids["package_dependency_id"], f"{label}-acceptance-source-dependency-id", blockers)
    _match(summary.get("acceptance_source_event_id"), expected_ids["package_event_id"], f"{label}-acceptance-source-event-id", blockers)
    _match(summary.get("final_authorization_dependency_id"), expected_ids["final_authorization_dependency_id"], f"{label}-final-authorization-dependency-id", blockers)
    _match(summary.get("final_authorization_event_id"), expected_ids["final_authorization_event_id"], f"{label}-final-authorization-event-id", blockers)


def _plain_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return _validated_mapping(value, blockers)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            mapped = to_dict()
        except Exception:
            blockers.append("malformed-release-publish-execution-handoff-readiness")
            return None
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
        blockers.append("malformed-release-publish-execution-handoff-readiness")
        return None
    if is_dataclass(value) and not isinstance(value, type):
        try:
            return _validated_mapping(
                {
                    field.name: _plain_value(getattr(value, field.name), blockers)
                    for field in fields(value)
                },
                blockers,
            )
        except Exception:
            blockers.append("malformed-release-publish-execution-handoff-readiness")
            return None
    return None


def _validated_mapping(value: Mapping[Any, Any], blockers: list[str]) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            blockers.append("non-string-key-release-publish-execution-handoff-readiness")
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
            blockers.append("unsupported-object-release-publish-execution-handoff-readiness")
            return None
        if isinstance(mapped, Mapping):
            return _validated_mapping(mapped, blockers)
        blockers.append("unsupported-object-release-publish-execution-handoff-readiness")
        return None
    if is_dataclass(value) and not isinstance(value, type):
        try:
            return {
                field.name: _plain_value(getattr(value, field.name), blockers)
                for field in fields(value)
            }
        except Exception:
            blockers.append("unsupported-object-release-publish-execution-handoff-readiness")
            return None
    if isinstance(value, list):
        return tuple(_plain_value(item, blockers) for item in value)
    if isinstance(value, tuple):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    blockers.append("unsupported-object-release-publish-execution-handoff-readiness")
    return None


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
        blockers.append(f"release-publish-execution-handoff-readiness-{name}-mismatch")


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
    normalized = normalized.strip("_")
    if normalized in _ALLOWED_ACTION_KEYS:
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
    "ReleasePublishExecutionHandoffReadiness",
    "evaluate_release_publish_execution_handoff_readiness",
    "verify_release_publish_execution_handoff_readiness",
]
