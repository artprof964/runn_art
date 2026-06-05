"""Pure promotion intent binding for verified Harness ledger checkpoints."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import re
from typing import Any, Mapping


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
_READY_TOP_LEVEL_KEYS = frozenset(
    {
        "blockers",
        "checkpoint_digest",
        "checkpoint_path",
        "checkpoint_size_bytes",
        "passed",
        "payload_digest",
        "ready",
        "run_id",
        "status",
        "summary",
        "work_id",
    }
)
_READY_SUMMARY_KEYS = frozenset(
    {
        "checkpoint_digest_prefix",
        "checkpoint_path",
        "checkpoint_size_bytes",
        "optional_summary_count",
        "optional_summary_names",
        "payload_digest_prefix",
        "run_id",
        "work_id",
    }
)
_DUPLICATE_METADATA_KEYS = frozenset(
    {
        "audit",
        "audits",
        "blockers",
        "checkpoint",
        "checkpoint_digest",
        "checkpoint_identity",
        "checkpoint_metadata",
        "checkpoint_path",
        "checkpoint_result",
        "checkpoint_size_bytes",
        "digest",
        "digests",
        "intent",
        "intent_digest",
        "ledger",
        "ledger_id",
        "payload_digest",
        "promotion",
        "promotion_id",
        "promotion_intent",
        "promotion_metadata",
        "readiness",
        "result",
        "results",
        "run_id",
        "target_ledger_id",
        "work_id",
    }
)


@dataclass(frozen=True)
class LedgerCheckpointPromotionIntent:
    """Digestable intent data for a future durable checkpoint promotion step."""

    work_id: str
    run_id: str
    promotion_id: str
    requested_by: str
    target_ledger_id: str
    checkpoint_path: str
    checkpoint_digest: str
    payload_digest: str
    checkpoint_size_bytes: int
    metadata: Mapping[str, Any]
    intent_payload: Mapping[str, Any]
    intent_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "run_id": self.run_id,
            "promotion_id": self.promotion_id,
            "requested_by": self.requested_by,
            "target_ledger_id": self.target_ledger_id,
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_digest": self.checkpoint_digest,
            "payload_digest": self.payload_digest,
            "checkpoint_size_bytes": self.checkpoint_size_bytes,
            "metadata": _sorted_plain_mapping(self.metadata),
            "intent_payload": _sorted_plain_mapping(self.intent_payload),
            "intent_digest": self.intent_digest,
        }


@dataclass(frozen=True)
class LedgerCheckpointPromotionIntentResult:
    """Plain result from binding explicit checkpoint readiness to promotion intent."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    intent: LedgerCheckpointPromotionIntent | None = None
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "summary": dict(self.summary) if self.summary is not None else None,
        }


def build_ledger_checkpoint_promotion_intent(
    readiness: object,
    *,
    promotion_id: object,
    requested_by: object,
    run_id: object,
    work_id: object = None,
    target_ledger_id: object = None,
    expected_checkpoint_digest: object = None,
    expected_payload_digest: object = None,
    expected_checkpoint_path: object = None,
    metadata: object = None,
) -> LedgerCheckpointPromotionIntentResult:
    """Bind caller-supplied readiness data to deterministic promotion intent data."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _optional_text(work_id, "work-id", blockers)
    safe_promotion_id = _required_text(promotion_id, "promotion-id", blockers)
    safe_requested_by = _required_text(requested_by, "requested-by", blockers)
    safe_target_ledger_id = _optional_text(
        target_ledger_id, "target-ledger-id", blockers
    )

    ready = _plain_mapping(readiness)
    if ready is None:
        blockers.append("missing-readiness")
        ready = {}

    if not _truthy_ready(ready):
        blockers.append("readiness-not-passed")
    _check_source_blockers(ready.get("blockers"), "readiness", blockers)

    readiness_run_id = _required_text(ready.get("run_id"), "readiness-run-id", blockers)
    _match_text(readiness_run_id, expected_run_id, "readiness-run-id", blockers)

    readiness_work_id = _optional_text(ready.get("work_id"), "readiness-work-id", blockers)
    if expected_work_id:
        if not readiness_work_id:
            blockers.append("missing-readiness-work-id")
        else:
            _match_text(
                readiness_work_id,
                expected_work_id,
                "readiness-work-id",
                blockers,
            )

    checkpoint_digest = _digest_text(
        ready.get("checkpoint_digest"), "checkpoint-digest", blockers
    )
    payload_digest = _digest_text(ready.get("payload_digest"), "payload-digest", blockers)
    checkpoint_path = _checkpoint_path_text(ready.get("checkpoint_path"), blockers)
    checkpoint_size_bytes = _positive_int(
        ready.get("checkpoint_size_bytes"),
        "checkpoint-size-bytes",
        blockers,
        required=True,
    )

    _expected_digest_match(
        checkpoint_digest,
        expected_checkpoint_digest,
        "expected-checkpoint-digest",
        blockers,
    )
    _expected_digest_match(
        payload_digest,
        expected_payload_digest,
        "expected-payload-digest",
        blockers,
    )
    _expected_path_match(
        checkpoint_path,
        expected_checkpoint_path,
        "expected-checkpoint-path",
        blockers,
    )

    _check_nested_duplicates(ready, _READY_TOP_LEVEL_KEYS, "readiness", blockers)
    _check_secret_or_execution("readiness", ready, blockers)

    safe_metadata = _metadata_mapping(metadata, blockers)
    if safe_metadata is not None:
        _check_nested_duplicates(safe_metadata, frozenset(), "metadata", blockers)
        _check_secret_or_execution("metadata", safe_metadata, blockers)
    else:
        safe_metadata = {}

    deduped = tuple(dict.fromkeys(blockers))
    summary = _summary(
        readiness_work_id or expected_work_id,
        readiness_run_id or expected_run_id,
        safe_promotion_id,
        safe_requested_by,
        safe_target_ledger_id,
        checkpoint_path,
        checkpoint_digest,
        payload_digest,
        checkpoint_size_bytes,
        safe_metadata,
    )
    if deduped:
        return LedgerCheckpointPromotionIntentResult(
            passed=False,
            blockers=deduped,
            intent=None,
            summary=summary,
        )

    intent_payload = _intent_payload(
        readiness_work_id or expected_work_id,
        readiness_run_id or expected_run_id,
        safe_promotion_id,
        safe_requested_by,
        safe_target_ledger_id,
        checkpoint_path,
        checkpoint_digest,
        payload_digest,
        checkpoint_size_bytes,
        safe_metadata,
    )
    intent = LedgerCheckpointPromotionIntent(
        work_id=readiness_work_id or expected_work_id,
        run_id=readiness_run_id or expected_run_id,
        promotion_id=safe_promotion_id,
        requested_by=safe_requested_by,
        target_ledger_id=safe_target_ledger_id,
        checkpoint_path=checkpoint_path,
        checkpoint_digest=checkpoint_digest,
        payload_digest=payload_digest,
        checkpoint_size_bytes=checkpoint_size_bytes,
        metadata=safe_metadata,
        intent_payload=intent_payload,
        intent_digest=_sha256_payload(intent_payload),
    )
    return LedgerCheckpointPromotionIntentResult(
        passed=True,
        blockers=(),
        intent=intent,
        summary=summary,
    )


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
        return _sorted_plain_mapping(mapped)
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _sorted_plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _plain_value(value[key]) for key in sorted(value)}


def _metadata_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    if value is None:
        return {}
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append("malformed-metadata")
        return None
    return _sorted_plain_mapping(mapped)


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


def _optional_text(value: object, name: str, blockers: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"invalid-{name}")
        return ""
    text = value.strip()
    if _is_secret_like(text):
        blockers.append(f"secret-like-{name}")
        return ""
    if _has_execution_text(text):
        blockers.append(f"execution-intent-{name}")
        return ""
    return text


def _truthy_ready(readiness: Mapping[str, Any]) -> bool:
    if "passed" in readiness:
        return bool(readiness.get("passed"))
    if "ready" in readiness:
        return bool(readiness.get("ready"))
    return False


def _check_source_blockers(value: object, prefix: str, blockers: list[str]) -> None:
    source_blockers = _blocker_tuple(value)
    if source_blockers:
        blockers.append(f"{prefix}-blockers-present")
        blockers.extend(f"{prefix}-{item}" for item in source_blockers)


def _blocker_tuple(value: object) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ("malformed-blockers",)


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    text = value.strip().lower()
    if not _SHA256_HEX.fullmatch(text):
        blockers.append(f"invalid-{name}")
    return text


def _checkpoint_path_text(value: object, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append("checkpoint-path-missing")
        return ""
    text = _normalize_path(value)
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


def _normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/")


def _positive_int(
    value: object,
    name: str,
    blockers: list[str],
    *,
    required: bool = False,
) -> int:
    if value is None:
        if required:
            blockers.append(f"missing-{name}")
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        blockers.append(f"invalid-{name}")
        return 0
    if value <= 0:
        blockers.append(f"nonpositive-{name}")
    return value


def _expected_digest_match(
    actual: str,
    expected: object,
    name: str,
    blockers: list[str],
) -> None:
    if expected is None:
        return
    expected_text = _digest_text(expected, name, blockers)
    if actual and expected_text and actual != expected_text:
        blockers.append(f"{name}-mismatch")


def _expected_path_match(
    actual: str,
    expected: object,
    name: str,
    blockers: list[str],
) -> None:
    if expected is None:
        return
    if not isinstance(expected, str) or not expected.strip():
        blockers.append(f"invalid-{name}")
        return
    expected_text = _checkpoint_path_text(expected, blockers)
    if actual and expected_text and actual != expected_text:
        blockers.append(f"{name}-mismatch")


def _match_text(actual: str, expected: str, name: str, blockers: list[str]) -> None:
    if actual and expected and actual != expected:
        blockers.append(f"{name}-mismatch")


def _check_nested_duplicates(
    value: object,
    allowed_top_level: frozenset[str],
    prefix: str,
    blockers: list[str],
) -> None:
    mapped = _plain_mapping(value)
    if mapped is None:
        return
    for key, item in mapped.items():
        normalized_key = key.lower()
        if normalized_key in allowed_top_level:
            if normalized_key == "summary":
                _check_nested_duplicates(item, _READY_SUMMARY_KEYS, prefix, blockers)
            continue
        if (
            normalized_key in _DUPLICATE_METADATA_KEYS
            or _contains_duplicate_metadata(item)
        ):
            if prefix == "metadata":
                blockers.append("duplicate-metadata")
            else:
                blockers.append(f"duplicate-{prefix}-metadata")


def _contains_duplicate_metadata(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            key.lower() in _DUPLICATE_METADATA_KEYS
            or _contains_duplicate_metadata(item)
            for key, item in mapped.items()
        )
    if isinstance(value, tuple):
        return any(_contains_duplicate_metadata(item) for item in value)
    return False


def _check_secret_or_execution(
    name: str,
    value: object,
    blockers: list[str],
) -> None:
    if _contains_secret_like(value):
        blockers.append(f"secret-like-{name}")
    if _contains_execution_intent(value):
        blockers.append(f"execution-intent-{name}")


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
    text = value.lower()
    return any(term in text for term in _SECRET_TERMS)


def _has_execution_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = f" {value.strip().lower()} "
    return any(term in text for term in _EXECUTION_TEXT)


def _intent_payload(
    work_id: str,
    run_id: str,
    promotion_id: str,
    requested_by: str,
    target_ledger_id: str,
    checkpoint_path: str,
    checkpoint_digest: str,
    payload_digest: str,
    checkpoint_size_bytes: int,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": "harness-ledger-checkpoint-promotion-intent-v1",
        "promotion": {
            "promotion_id": promotion_id,
            "requested_by": requested_by,
            "target_ledger_id": target_ledger_id,
        },
        "run": {
            "run_id": run_id,
            "work_id": work_id,
        },
        "checkpoint": {
            "checkpoint_path": checkpoint_path,
            "checkpoint_digest": checkpoint_digest,
            "payload_digest": payload_digest,
            "checkpoint_size_bytes": checkpoint_size_bytes,
        },
        "metadata": _sorted_plain_mapping(metadata),
    }


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summary(
    work_id: str,
    run_id: str,
    promotion_id: str,
    requested_by: str,
    target_ledger_id: str,
    checkpoint_path: str,
    checkpoint_digest: str,
    payload_digest: str,
    checkpoint_size_bytes: int,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "run_id": run_id,
        "promotion_id": _redacted_text(promotion_id),
        "requested_by": _redacted_text(requested_by),
        "target_ledger_id": _redacted_text(target_ledger_id),
        "checkpoint_path": _redacted_path(checkpoint_path),
        "checkpoint_digest_prefix": checkpoint_digest[:12],
        "payload_digest_prefix": payload_digest[:12],
        "checkpoint_size_bytes": checkpoint_size_bytes,
        "metadata_keys": tuple(_redacted_metadata_key(key) for key in sorted(metadata)),
    }


def _redacted_metadata_key(value: str) -> str:
    if _is_secret_like(value):
        return "<redacted>"
    return value


def _redacted_text(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-4:]}"


def _redacted_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return value
    return f".../{parts[-1]}"


__all__ = [
    "LedgerCheckpointPromotionIntent",
    "LedgerCheckpointPromotionIntentResult",
    "build_ledger_checkpoint_promotion_intent",
]
