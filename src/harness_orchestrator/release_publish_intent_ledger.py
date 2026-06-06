"""Record release publish intents into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.release_publish_intent import (
    ReleasePublishIntent,
    ReleasePublishIntentResult,
)
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
    TaskStatus,
)


_FORMAT = "harness-release-publish-intent-v1"
_CANONICAL_PAYLOAD_KEYS = frozenset(
    {"format", "readiness_binding", "caller_supplied_intent_metadata"}
)
_READINESS_BINDING_KEYS = frozenset(
    {"run_id", "work_id", "canonical_digest_prefix", "release_binding_digest", "source"}
)
_CALLER_INTENT_METADATA_KEYS = frozenset(
    {"publish_target", "publish_payload", "artifact", "metadata", "verification_status"}
)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PUBLISH_TARGET_KEYS = frozenset({"target_id", "target_type"})
_PUBLISH_PAYLOAD_ALLOWED_KEYS = frozenset({"payload_digest", "payload_label"})
_ARTIFACT_KEYS = frozenset({"artifact_id"})
_TARGET_TYPES = frozenset({"local-dry-run", "manual-release-placeholder"})
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_RESERVED_METADATA_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "canonical_digest",
        "canonical_digest_prefix",
        "release_binding_digest",
        "publish_target",
        "publish_payload",
        "artifact",
        "identity",
        "expected",
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
_MALFORMED_BLOCKER = "malformed-release-publish-intent-ledger-data"


@dataclass(frozen=True)
class ReleasePublishIntentLedgerResult:
    """Plain result from recording release publish intent data."""

    recorded_event_ids: tuple[str, ...] = ()
    recorded_dependency_ids: tuple[str, ...] = ()
    skipped_event_ids: tuple[str, ...] = ()
    skipped_dependency_ids: tuple[str, ...] = ()
    skipped_intent_digests: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_event_ids": self.recorded_event_ids,
            "recorded_dependency_ids": self.recorded_dependency_ids,
            "skipped_event_ids": self.skipped_event_ids,
            "skipped_dependency_ids": self.skipped_dependency_ids,
            "skipped_intent_digests": self.skipped_intent_digests,
            "blockers": self.blockers,
            "ledger_snapshot": (
                _snapshot_to_dict(self.ledger_snapshot)
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_release_publish_intents(
    intents: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishIntentLedgerResult:
    """Record already-built release publish intent data into an injected ledger."""

    if not isinstance(ledger, RunLedger):
        return ReleasePublishIntentLedgerResult(blockers=("ledger-missing",))

    items = _normalize_items(intents)
    if not items:
        return ReleasePublishIntentLedgerResult(
            blockers=("release-publish-intents-empty",),
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    snapshot = ledger.snapshot()
    ledger_blockers = _hostile_snapshot_blockers(snapshot)
    if ledger_blockers:
        return ReleasePublishIntentLedgerResult(
            blockers=ledger_blockers,
            ledger_snapshot=_immutable_snapshot(snapshot),
        )

    blockers: list[str] = []
    staged: list[tuple[DependencyRecord, AuditEvent, str]] = []
    skipped_event_ids: list[str] = []
    skipped_dependency_ids: list[str] = []
    skipped_intent_digests: list[str] = []
    seen_dependency_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    seen_intent_digests: set[str] = set()
    existing_dependency_ids = {record.dependency_id for record in snapshot.dependencies}
    existing_event_ids = {event.event_id for event in snapshot.audit_events}
    existing_intent_digests = _existing_intent_digests(snapshot)

    for item in items:
        intent, item_blockers = _intent_from_item(item)
        dependency: DependencyRecord | None = None
        event: AuditEvent | None = None
        intent_digest = ""
        if intent is not None:
            intent_digest = str(intent["intent_digest"])
            if ledger.run_id != intent["run_id"]:
                item_blockers.append("ledger-run-id-mismatch")
            if not item_blockers:
                dependency, event = _ledger_records(intent)
                item_blockers.extend(
                    _duplicate_blockers(
                        dependency=dependency,
                        event=event,
                        intent_digest=intent_digest,
                        existing_dependency_ids=existing_dependency_ids,
                        existing_event_ids=existing_event_ids,
                        existing_intent_digests=existing_intent_digests,
                        seen_dependency_ids=seen_dependency_ids,
                        seen_event_ids=seen_event_ids,
                        seen_intent_digests=seen_intent_digests,
                    )
                )

        if item_blockers:
            blockers.extend(item_blockers)
            if dependency is not None:
                skipped_dependency_ids.append(dependency.dependency_id)
            if event is not None:
                skipped_event_ids.append(event.event_id)
            if intent_digest:
                skipped_intent_digests.append(intent_digest)
            continue

        if dependency is not None and event is not None and intent_digest:
            staged.append((dependency, event, intent_digest))
            seen_dependency_ids.add(dependency.dependency_id)
            seen_event_ids.add(event.event_id)
            seen_intent_digests.add(intent_digest)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    if deduped_blockers or not staged:
        return ReleasePublishIntentLedgerResult(
            skipped_event_ids=tuple(skipped_event_ids),
            skipped_dependency_ids=tuple(skipped_dependency_ids),
            skipped_intent_digests=tuple(skipped_intent_digests),
            blockers=deduped_blockers,
            ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
        )

    recorded_dependency_ids: list[str] = []
    recorded_event_ids: list[str] = []
    for dependency, event, _intent_digest in staged:
        ledger.record_dependency(dependency=dependency)
        ledger.record_audit_event(event=event)
        recorded_dependency_ids.append(dependency.dependency_id)
        recorded_event_ids.append(event.event_id)

    return ReleasePublishIntentLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        recorded_dependency_ids=tuple(recorded_dependency_ids),
        ledger_snapshot=_immutable_snapshot(ledger.snapshot()),
    )


def record_release_publish_intent(
    intent: object,
    *,
    ledger: RunLedger | None,
) -> ReleasePublishIntentLedgerResult:
    """Compatibility wrapper for recording one release publish intent."""

    return record_release_publish_intents(intent, ledger=ledger)


def _normalize_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (ReleasePublishIntent, ReleasePublishIntentResult)):
        return (value,)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _intent_from_item(item: object) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    mapped = _plain_mapping(item, blockers)
    if mapped is None:
        return None, ["release-publish-intent-wrong-type"]

    result_like = "intent" in mapped or "passed" in mapped or "blockers" in mapped
    source = mapped
    summary: Mapping[str, Any] | None = None
    if result_like:
        if mapped.get("passed") is not True:
            blockers.append("release-publish-intent-not-passed")
        source_blockers = _blocker_tuple(mapped.get("blockers"))
        if source_blockers:
            blockers.append("release-publish-intent-blockers-present")
            blockers.extend(f"release-publish-intent-{blocker}" for blocker in source_blockers)
        source = _plain_mapping(mapped.get("intent"), blockers) or {}
        if not source:
            blockers.append("release-publish-intent-missing")
        summary = _plain_mapping(mapped.get("summary"), blockers)

    if _contains_secret_like(mapped):
        blockers.append("secret-like-release-publish-intent-ledger-data")
    if _contains_action_intent(mapped):
        blockers.append("action-intent-release-publish-intent-ledger-data")

    intent = _validated_intent(source, summary, blockers)
    if blockers:
        return intent, blockers
    return intent, []


def _validated_intent(
    value: Mapping[str, Any],
    summary: Mapping[str, Any] | None,
    blockers: list[str],
) -> dict[str, Any] | None:
    if not value:
        return None
    run_id = _required_text(value.get("run_id"), "run-id", blockers)
    work_id = _required_text(value.get("work_id"), "work-id", blockers)
    release_binding_digest = _digest_text(
        value.get("release_binding_digest"),
        "release-binding-digest",
        blockers,
    )
    intent_digest = _digest_text(value.get("intent_digest"), "intent-digest", blockers)
    publish_target = _required_mapping(value.get("publish_target"), "publish-target", blockers)
    publish_payload = _required_mapping(value.get("publish_payload"), "publish-payload", blockers)
    artifact = _required_mapping(value.get("artifact"), "artifact", blockers)
    metadata = _required_mapping(value.get("metadata"), "metadata", blockers, required=False)
    canonical_payload = _required_mapping(
        value.get("canonical_payload"),
        "canonical-payload",
        blockers,
    )

    if canonical_payload:
        _validate_canonical_payload(
            canonical_payload=canonical_payload,
            run_id=run_id,
            work_id=work_id,
            release_binding_digest=release_binding_digest,
            publish_target=publish_target,
            publish_payload=publish_payload,
            artifact=artifact,
            metadata=metadata,
            blockers=blockers,
        )
        if intent_digest and _sha256_payload(canonical_payload) != intent_digest:
            blockers.append("release-publish-intent-digest-mismatch")
    if summary:
        _validate_summary(
            summary=summary,
            run_id=run_id,
            work_id=work_id,
            release_binding_digest=release_binding_digest,
            publish_target=publish_target,
            publish_payload=publish_payload,
            artifact=artifact,
            metadata=metadata,
            blockers=blockers,
        )

    return {
        "run_id": run_id,
        "work_id": work_id,
        "release_binding_digest": release_binding_digest,
        "publish_target": _sorted_value(publish_target),
        "publish_payload": _sorted_value(publish_payload),
        "artifact": _sorted_value(artifact),
        "metadata": _sorted_value(metadata),
        "canonical_payload": _sorted_value(canonical_payload),
        "intent_digest": intent_digest,
    }


def _validate_canonical_payload(
    *,
    canonical_payload: Mapping[str, Any],
    run_id: str,
    work_id: str,
    release_binding_digest: str,
    publish_target: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    metadata: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if set(canonical_payload) != _CANONICAL_PAYLOAD_KEYS:
        blockers.append("unsafe-canonical-payload-schema")
    if canonical_payload.get("format") != _FORMAT:
        blockers.append("release-publish-intent-format-mismatch")
    readiness = _plain_mapping(canonical_payload.get("readiness_binding"), blockers)
    caller = _plain_mapping(
        canonical_payload.get("caller_supplied_intent_metadata"),
        blockers,
    )
    if readiness is None:
        blockers.append("release-publish-intent-readiness-binding-missing")
        readiness = {}
    if caller is None:
        blockers.append("release-publish-intent-caller-metadata-missing")
        caller = {}
    if readiness and set(readiness) != _READINESS_BINDING_KEYS:
        blockers.append("unsafe-readiness-binding-schema")
    if caller and set(caller) != _CALLER_INTENT_METADATA_KEYS:
        blockers.append("unsafe-caller-intent-metadata-schema")
    prefix = readiness.get("canonical_digest_prefix")
    if not isinstance(prefix, str) or not _DIGEST_PREFIX.fullmatch(prefix):
        blockers.append("invalid-canonical-digest-prefix")
    elif prefix != release_binding_digest[:12]:
        blockers.append("release-publish-intent-canonical-digest-prefix-mismatch")
    _match(readiness.get("run_id"), run_id, "canonical-run-id", blockers)
    _match(readiness.get("work_id"), work_id, "canonical-work-id", blockers)
    _match(
        readiness.get("release_binding_digest"),
        release_binding_digest,
        "canonical-release-binding-digest",
        blockers,
    )
    _match(
        readiness.get("source"),
        "cr-har-030-exposed-readiness-fields-only",
        "canonical-readiness-source",
        blockers,
    )
    _validate_canonical_publish_target(caller.get("publish_target"), blockers)
    _validate_canonical_publish_payload(caller.get("publish_payload"), blockers)
    _validate_canonical_artifact(caller.get("artifact"), blockers)
    _validate_canonical_metadata(caller.get("metadata"), blockers)
    _match_mapping(
        caller.get("publish_target"),
        publish_target,
        "canonical-publish-target",
        blockers,
    )
    _match_mapping(
        caller.get("publish_payload"),
        publish_payload,
        "canonical-publish-payload",
        blockers,
    )
    _match_mapping(caller.get("artifact"), artifact, "canonical-artifact", blockers)
    _match_mapping(caller.get("metadata"), metadata, "canonical-metadata", blockers)
    _match(
        caller.get("verification_status"),
        "caller-supplied-not-cr-har-030-identity",
        "canonical-verification-status",
        blockers,
    )


def _validate_canonical_publish_target(
    value: object,
    blockers: list[str],
) -> None:
    mapped = _plain_mapping(value, blockers)
    if (
        mapped is None
        or set(mapped) != _PUBLISH_TARGET_KEYS
        or mapped.get("target_type") not in _TARGET_TYPES
        or not _safe_text_value(mapped.get("target_id"))
    ):
        blockers.append("unsafe-canonical-publish-target-schema")


def _validate_canonical_publish_payload(
    value: object,
    blockers: list[str],
) -> None:
    mapped = _plain_mapping(value, blockers)
    if mapped is None:
        blockers.append("unsafe-canonical-publish-payload-schema")
        return
    keys = set(mapped)
    if (
        "payload_digest" not in keys
        or not keys.issubset(_PUBLISH_PAYLOAD_ALLOWED_KEYS)
        or not _valid_digest(mapped.get("payload_digest"))
    ):
        blockers.append("unsafe-canonical-publish-payload-schema")
        return
    if "payload_label" in mapped and not _safe_text_value(
        mapped.get("payload_label"),
        required=False,
    ):
        blockers.append("unsafe-canonical-publish-payload-schema")


def _validate_canonical_artifact(value: object, blockers: list[str]) -> None:
    mapped = _plain_mapping(value, blockers)
    if (
        mapped is None
        or set(mapped) != _ARTIFACT_KEYS
        or not _safe_text_value(mapped.get("artifact_id"))
    ):
        blockers.append("unsafe-canonical-artifact-schema")


def _validate_canonical_metadata(value: object, blockers: list[str]) -> None:
    mapped = _plain_mapping(value, blockers)
    if mapped is None:
        blockers.append("unsafe-canonical-metadata-schema")
        return
    for key, item in mapped.items():
        if not isinstance(key, str):
            blockers.append("unsafe-canonical-metadata-schema")
            continue
        if key.lower() in _RESERVED_METADATA_KEYS:
            blockers.append("unsafe-canonical-metadata-schema")
            continue
        if (
            not _safe_text_value(key)
            or _is_secret_like(key)
            or _is_action_key(key)
            or _has_action_text(key)
        ):
            blockers.append("unsafe-canonical-metadata-schema")
            continue
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            continue
        if isinstance(item, str):
            if (
                not _safe_text_value(item, required=False)
                or _is_secret_like(item)
                or _has_action_text(item)
            ):
                blockers.append("unsafe-canonical-metadata-schema")
            continue
        blockers.append("unsafe-canonical-metadata-schema")


def _validate_summary(
    *,
    summary: Mapping[str, Any],
    run_id: str,
    work_id: str,
    release_binding_digest: str,
    publish_target: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    metadata: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if summary.get("format") != _FORMAT:
        blockers.append("release-publish-intent-summary-format-mismatch")
    _match(summary.get("run_id"), run_id, "summary-run-id", blockers)
    _match(summary.get("work_id"), work_id, "summary-work-id", blockers)
    _match(
        summary.get("release_binding_digest_prefix"),
        release_binding_digest[:12],
        "summary-release-binding-digest-prefix",
        blockers,
    )
    _match(summary.get("target_type"), publish_target.get("target_type"), "summary-target-type", blockers)
    _match(summary.get("target_id"), publish_target.get("target_id"), "summary-target-id", blockers)
    _match(
        summary.get("payload_digest_prefix"),
        str(publish_payload.get("payload_digest", ""))[:12],
        "summary-payload-digest-prefix",
        blockers,
    )
    _match(summary.get("artifact_id"), artifact.get("artifact_id"), "summary-artifact-id", blockers)
    _match(summary.get("metadata_keys"), tuple(sorted(metadata)), "summary-metadata-keys", blockers)


def _ledger_records(intent: Mapping[str, Any]) -> tuple[DependencyRecord, AuditEvent]:
    intent_digest = str(intent["intent_digest"])
    suffix = intent_digest[:16]
    run_id = str(intent["run_id"])
    work_id = str(intent["work_id"])
    dependency_id = f"release-publish-intent:{work_id}:{suffix}"
    event_id = f"release-publish-intent-recorded:{work_id}:{suffix}"
    metadata = {
        "run_id": run_id,
        "release_binding_digest": intent["release_binding_digest"],
        "release_binding_digest_prefix": str(intent["release_binding_digest"])[:12],
        "intent_digest": intent_digest,
        "intent_digest_prefix": suffix[:12],
        "publish_target": intent["publish_target"],
        "publish_payload": intent["publish_payload"],
        "artifact": intent["artifact"],
        "intent_metadata": intent["metadata"],
        "canonical_payload": intent["canonical_payload"],
    }
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"release-publish-intent:{run_id}:{work_id}:{suffix}",
        order=95,
        dependency_type="release-publish-intent",
        required=True,
        status="ready",
        metadata=metadata,
    )
    event = AuditEvent(
        event_id=event_id,
        work_id=work_id,
        event_type="release-publish-intent-ledger-record",
        status="ready",
        message="Release publish intent recorded in ledger.",
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
    intent_digest: str,
    existing_dependency_ids: set[str],
    existing_event_ids: set[str],
    existing_intent_digests: set[str],
    seen_dependency_ids: set[str],
    seen_event_ids: set[str],
    seen_intent_digests: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if dependency.dependency_id in seen_dependency_ids:
        blockers.append("release-publish-intent-dependency-id-duplicate")
    elif dependency.dependency_id in existing_dependency_ids:
        blockers.append("release-publish-intent-dependency-id-already-recorded")
    if event.event_id in seen_event_ids:
        blockers.append("release-publish-intent-event-id-duplicate")
    elif event.event_id in existing_event_ids:
        blockers.append("release-publish-intent-event-id-already-recorded")
    if intent_digest in seen_intent_digests:
        blockers.append("release-publish-intent-digest-duplicate")
    elif intent_digest in existing_intent_digests:
        blockers.append("release-publish-intent-digest-already-recorded")
    return tuple(blockers)


def _existing_intent_digests(snapshot: RunLedgerSnapshot) -> set[str]:
    values: set[str] = set()
    for record in (*snapshot.dependencies, *snapshot.audit_events):
        digest = record.metadata.get("intent_digest")
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
    if value != value.strip():
        blockers.append(f"unsafe-{name}")
        return ""
    if not _SAFE_IDENTIFIER.fullmatch(value):
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


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


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
        blockers.append(f"release-publish-intent-{name}-mismatch")


def _match_mapping(
    actual: object,
    expected: Mapping[str, Any],
    name: str,
    blockers: list[str],
) -> None:
    mapped = _plain_mapping(actual, blockers)
    if mapped is None:
        blockers.append(f"release-publish-intent-{name}-missing")
        return
    if _sorted_value(mapped) != _sorted_value(expected):
        blockers.append(f"release-publish-intent-{name}-mismatch")


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


def _contains_action_intent(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            _is_action_key(key) or _contains_action_intent(item)
            for key, item in mapped.items()
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
    "ReleasePublishIntentLedgerResult",
    "record_release_publish_intent",
    "record_release_publish_intents",
]
