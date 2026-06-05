"""Record checkpoint promotion intents into an explicit Harness run ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from typing import Any, Iterable, Mapping

from harness_orchestrator.ledger_checkpoint_promotion_intent import (
    LedgerCheckpointPromotionIntent,
    LedgerCheckpointPromotionIntentResult,
)
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
)


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SECRET_TERMS = ("key", "token", "secret", "password")
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


@dataclass(frozen=True)
class LedgerCheckpointPromotionLedgerResult:
    """Plain result from recording checkpoint promotion intent data."""

    recorded_event_ids: tuple[str, ...] = ()
    recorded_dependency_ids: tuple[str, ...] = ()
    skipped_promotion_ids: tuple[str, ...] = ()
    skipped_intent_digests: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    ledger_snapshot: RunLedgerSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_event_ids": self.recorded_event_ids,
            "recorded_dependency_ids": self.recorded_dependency_ids,
            "skipped_promotion_ids": self.skipped_promotion_ids,
            "skipped_intent_digests": self.skipped_intent_digests,
            "blockers": self.blockers,
            "ledger_snapshot": (
                self.ledger_snapshot.to_dict()
                if self.ledger_snapshot is not None
                else None
            ),
        }


def record_ledger_checkpoint_promotion_intents(
    promotion_intents: object,
    *,
    ledger: RunLedger | None,
) -> LedgerCheckpointPromotionLedgerResult:
    """Record already-built checkpoint promotion intents into an injected ledger."""

    if not isinstance(ledger, RunLedger):
        return LedgerCheckpointPromotionLedgerResult(blockers=("ledger-missing",))

    items = _normalize_items(promotion_intents)
    if not items:
        return LedgerCheckpointPromotionLedgerResult(
            blockers=("checkpoint-promotion-intents-empty",),
            ledger_snapshot=ledger.snapshot(),
        )

    blockers: list[str] = []
    staged: list[tuple[DependencyRecord, AuditEvent, str, str]] = []
    skipped_promotion_ids: list[str] = []
    skipped_intent_digests: list[str] = []
    seen_dependency_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    seen_intent_digests: set[str] = set()
    snapshot = ledger.snapshot()
    existing_dependency_ids = {record.dependency_id for record in snapshot.dependencies}
    existing_event_ids = {event.event_id for event in snapshot.audit_events}
    existing_intent_digests = _existing_intent_digests(snapshot)

    for item in items:
        intent, item_blockers = _intent_from_item(item)
        if intent is None:
            blockers.extend(item_blockers)
            continue
        if intent["run_id"] and ledger.run_id != intent["run_id"]:
            item_blockers.append("ledger-run-id-mismatch")

        dependency, event = _ledger_records(intent)
        item_blockers.extend(
            _duplicate_blockers(
                dependency=dependency,
                event=event,
                intent_digest=str(intent["intent_digest"]),
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
            skipped_promotion_ids.append(str(intent["promotion_id"]))
            skipped_intent_digests.append(str(intent["intent_digest"]))
            continue
        staged.append(
            (
                dependency,
                event,
                str(intent["promotion_id"]),
                str(intent["intent_digest"]),
            )
        )
        seen_dependency_ids.add(dependency.dependency_id)
        seen_event_ids.add(event.event_id)
        seen_intent_digests.add(str(intent["intent_digest"]))

    deduped_blockers = tuple(dict.fromkeys(blockers))
    if deduped_blockers or not staged:
        return LedgerCheckpointPromotionLedgerResult(
            skipped_promotion_ids=tuple(skipped_promotion_ids),
            skipped_intent_digests=tuple(skipped_intent_digests),
            blockers=deduped_blockers,
            ledger_snapshot=ledger.snapshot(),
        )

    recorded_dependency_ids: list[str] = []
    recorded_event_ids: list[str] = []
    for dependency, event, _promotion_id, _intent_digest in staged:
        ledger.record_dependency(dependency=dependency)
        ledger.record_audit_event(event=event)
        recorded_dependency_ids.append(dependency.dependency_id)
        recorded_event_ids.append(event.event_id)

    return LedgerCheckpointPromotionLedgerResult(
        recorded_event_ids=tuple(recorded_event_ids),
        recorded_dependency_ids=tuple(recorded_dependency_ids),
        ledger_snapshot=ledger.snapshot(),
    )


def record_ledger_checkpoint_promotion_intent(
    promotion_intent: object,
    *,
    ledger: RunLedger | None,
) -> LedgerCheckpointPromotionLedgerResult:
    """Compatibility wrapper for recording one checkpoint promotion intent."""

    return record_ledger_checkpoint_promotion_intents(promotion_intent, ledger=ledger)


def _normalize_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(
        value,
        (LedgerCheckpointPromotionIntent, LedgerCheckpointPromotionIntentResult),
    ):
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
    mapped = _plain_mapping(item)
    if mapped is None:
        return None, ["checkpoint-promotion-intent-wrong-type"]

    result_like = "intent" in mapped or "passed" in mapped or "blockers" in mapped
    source = mapped
    if result_like:
        if not bool(mapped.get("passed")):
            blockers.append("checkpoint-promotion-intent-not-passed")
        source_blockers = _blocker_tuple(mapped.get("blockers"))
        if source_blockers:
            blockers.append("checkpoint-promotion-intent-blockers-present")
            blockers.extend(f"checkpoint-promotion-intent-{item}" for item in source_blockers)
        intent_value = mapped.get("intent")
        source = _plain_mapping(intent_value) or {}
        if not source:
            blockers.append("checkpoint-promotion-intent-missing")

    if _contains_secret_like(mapped):
        blockers.append("secret-like-checkpoint-promotion-ledger-data")
    if _contains_execution_intent(mapped):
        blockers.append("execution-intent-checkpoint-promotion-ledger-data")

    intent = _validated_intent(source, blockers)
    if blockers:
        return intent, blockers
    return intent, []


def _validated_intent(
    value: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Any] | None:
    if not value:
        return None
    work_id = _required_text(value.get("work_id"), "work-id", blockers)
    run_id = _required_text(value.get("run_id"), "run-id", blockers)
    promotion_id = _required_text(value.get("promotion_id"), "promotion-id", blockers)
    requested_by = _required_text(value.get("requested_by"), "requested-by", blockers)
    target_ledger_id = _required_text(
        value.get("target_ledger_id"), "target-ledger-id", blockers
    )
    checkpoint_path = _checkpoint_path(value.get("checkpoint_path"), blockers)
    checkpoint_digest = _digest_text(
        value.get("checkpoint_digest"), "checkpoint-digest", blockers
    )
    payload_digest = _digest_text(value.get("payload_digest"), "payload-digest", blockers)
    intent_digest = _digest_text(value.get("intent_digest"), "intent-digest", blockers)
    checkpoint_size_bytes = _positive_int(
        value.get("checkpoint_size_bytes"), "checkpoint-size-bytes", blockers
    )
    metadata = _optional_mapping(value.get("metadata"), "metadata", blockers)

    return {
        "work_id": work_id,
        "run_id": run_id,
        "promotion_id": promotion_id,
        "requested_by": requested_by,
        "target_ledger_id": target_ledger_id,
        "checkpoint_path": checkpoint_path,
        "checkpoint_digest": checkpoint_digest,
        "payload_digest": payload_digest,
        "checkpoint_size_bytes": checkpoint_size_bytes,
        "metadata": metadata,
        "intent_digest": intent_digest,
    }


def _ledger_records(intent: Mapping[str, Any]) -> tuple[DependencyRecord, AuditEvent]:
    suffix = str(intent["intent_digest"])[:16]
    promotion_id = str(intent["promotion_id"])
    work_id = str(intent["work_id"])
    run_id = str(intent["run_id"])
    dependency_id = f"checkpoint-promotion-intent:{work_id}:{promotion_id}:{suffix}"
    event_id = f"checkpoint-promotion-intent-recorded:{work_id}:{promotion_id}:{suffix}"
    metadata = {
        "run_id": run_id,
        "promotion_id": promotion_id,
        "requested_by": intent["requested_by"],
        "target_ledger_id": intent["target_ledger_id"],
        "checkpoint_path": intent["checkpoint_path"],
        "checkpoint_digest": intent["checkpoint_digest"],
        "payload_digest": intent["payload_digest"],
        "checkpoint_size_bytes": intent["checkpoint_size_bytes"],
        "intent_digest": intent["intent_digest"],
        "intent_metadata": dict(intent["metadata"]),
    }
    dependency = DependencyRecord(
        dependency_id=dependency_id,
        work_id=work_id,
        reference=f"checkpoint-promotion-intent:{run_id}:{promotion_id}",
        order=90,
        dependency_type="checkpoint-promotion-intent",
        required=True,
        status="ready",
        metadata=metadata,
    )
    event = AuditEvent(
        event_id=event_id,
        work_id=work_id,
        event_type="checkpoint-promotion-intent-ledger-record",
        status="ready",
        message="Checkpoint promotion intent recorded in ledger.",
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
        blockers.append("checkpoint-promotion-dependency-id-duplicate")
    elif dependency.dependency_id in existing_dependency_ids:
        blockers.append("checkpoint-promotion-dependency-id-already-recorded")
    if event.event_id in seen_event_ids:
        blockers.append("checkpoint-promotion-event-id-duplicate")
    elif event.event_id in existing_event_ids:
        blockers.append("checkpoint-promotion-event-id-already-recorded")
    if intent_digest in seen_intent_digests:
        blockers.append("checkpoint-promotion-intent-digest-duplicate")
    elif intent_digest in existing_intent_digests:
        blockers.append("checkpoint-promotion-intent-digest-already-recorded")
    return tuple(blockers)


def _existing_intent_digests(snapshot: RunLedgerSnapshot) -> set[str]:
    values: set[str] = set()
    for record in (*snapshot.dependencies, *snapshot.audit_events):
        digest = record.metadata.get("intent_digest")
        if isinstance(digest, str):
            values.add(digest)
    return values


def _plain_mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return {str(key): _plain_value(item) for key, item in mapped.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return {str(key): _plain_value(item) for key, item in mapped.items()}
    return None


def _plain_value(value: object) -> object:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return {key: mapped[key] for key in sorted(mapped)}
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    text = value.strip()
    if _is_secret_like(text):
        blockers.append(f"secret-like-{name}")
        return ""
    if _has_execution_text(text):
        blockers.append(f"execution-intent-{name}")
        return ""
    return text


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    text = value.strip().lower()
    if not _SHA256_HEX.fullmatch(text):
        blockers.append(f"invalid-{name}")
    return text


def _checkpoint_path(value: object, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append("checkpoint-path-missing")
        return ""
    text = value.strip().replace("\\", "/")
    lowered = text.lower()
    if (
        "://" in text
        or text.startswith("/")
        or text.startswith("//")
        or text.startswith("../")
        or text.endswith("/..")
        or "/../" in text
        or (len(text) > 1 and text[1] == ":")
        or "\x00" in text
        or lowered.startswith("~")
    ):
        blockers.append("checkpoint-path-unsafe")
    if "/" not in text:
        blockers.append("checkpoint-path-not-explicit")
    if _is_secret_like(text):
        blockers.append("secret-like-checkpoint-path")
    if _has_execution_text(text):
        blockers.append("execution-intent-checkpoint-path")
    return text


def _positive_int(value: object, name: str, blockers: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        blockers.append(f"invalid-{name}")
        return 0
    if value <= 0:
        blockers.append(f"nonpositive-{name}")
    return value


def _optional_mapping(value: object, name: str, blockers: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"malformed-{name}")
        return {}
    return {key: mapped[key] for key in sorted(mapped)}


def _blocker_tuple(value: object) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ("malformed-blockers",)


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


def _contains_execution_intent(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            key.lower() in _EXECUTION_KEYS or _contains_execution_intent(item)
            for key, item in mapped.items()
        )
    if isinstance(value, tuple):
        return any(_contains_execution_intent(item) for item in value)
    return _has_execution_text(value)


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str) or value == "<redacted>":
        return False
    lowered = value.lower()
    if lowered == "metadata_keys":
        return False
    return any(term in lowered for term in _SECRET_TERMS)


def _has_execution_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = f" {value.strip().lower()} "
    return any(term in text for term in _EXECUTION_TEXT)


__all__ = [
    "LedgerCheckpointPromotionLedgerResult",
    "record_ledger_checkpoint_promotion_intent",
    "record_ledger_checkpoint_promotion_intents",
]
