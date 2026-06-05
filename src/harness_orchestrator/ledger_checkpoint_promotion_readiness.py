"""Pure readiness boundary for future ledger checkpoint promotion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from typing import Any, Mapping


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_READY_STATUSES = frozenset({"ready", "passed", "complete", "completed", "done"})
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
_IDENTITY_KEYS = frozenset(
    {
        "checkpoint",
        "checkpoint_digest",
        "checkpoint_metadata",
        "checkpoint_path",
        "checkpoint_size_bytes",
        "digests",
        "ids",
        "ledger_checkpoint",
        "payload_digest",
        "promotion_metadata",
        "work_id",
        "run_id",
    }
)
_TOP_LEVEL_RECEIPT_KEYS = frozenset(
    {
        "blockers",
        "checkpoint_digest",
        "checkpoint_path",
        "checkpoint_size_bytes",
        "passed",
        "payload_digest",
        "receipt_summary",
        "run_id",
        "status",
        "work_id",
    }
)
_TOP_LEVEL_SUMMARY_KEYS = frozenset(
    {
        "blockers",
        "checkpoint_digest",
        "checkpoint_path",
        "checkpoint_size_bytes",
        "payload_digest",
        "passed",
        "ready",
        "run_id",
        "status",
        "summary",
        "tasks",
        "unfinished_task_count",
        "unfinished_tasks",
        "work_id",
    }
)


@dataclass(frozen=True)
class LedgerCheckpointPromotionReadiness:
    """Plain result describing whether a verified checkpoint can be promoted."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    work_id: str = ""
    run_id: str = ""
    checkpoint_path: str = ""
    checkpoint_digest: str = ""
    payload_digest: str = ""
    checkpoint_size_bytes: int = 0
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "work_id": self.work_id,
            "run_id": self.run_id,
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_digest": self.checkpoint_digest,
            "payload_digest": self.payload_digest,
            "checkpoint_size_bytes": self.checkpoint_size_bytes,
            "summary": dict(self.summary) if self.summary is not None else None,
        }


def evaluate_ledger_checkpoint_promotion_readiness(
    receipt_verification: object,
    *,
    work_id: object = None,
    run_id: object,
    expected_checkpoint_digest: object = None,
    expected_payload_digest: object = None,
    expected_checkpoint_path: object = None,
    preflight_summary: object = None,
    result_ledger_record: object = None,
    checkpoint_result: object = None,
) -> LedgerCheckpointPromotionReadiness:
    """Evaluate caller-supplied plain data without durable side effects."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _optional_text(work_id, "work-id", blockers)

    receipt = _plain_mapping(receipt_verification)
    if receipt is None:
        blockers.append("missing-receipt-verification")
        receipt = {}

    if not bool(receipt.get("passed")):
        blockers.append("receipt-verification-not-passed")
    _check_source_blockers(receipt.get("blockers"), "receipt", blockers)

    receipt_run_id = _required_text(receipt.get("run_id"), "receipt-run-id", blockers)
    _match_text(receipt_run_id, expected_run_id, "receipt-run-id", blockers)

    receipt_work_id = _optional_text(receipt.get("work_id"), "receipt-work-id", blockers)
    if expected_work_id:
        if not receipt_work_id:
            blockers.append("missing-receipt-work-id")
        else:
            _match_text(receipt_work_id, expected_work_id, "receipt-work-id", blockers)

    checkpoint_digest = _digest_text(
        receipt.get("checkpoint_digest"), "checkpoint-digest", blockers
    )
    payload_digest = _digest_text(receipt.get("payload_digest"), "payload-digest", blockers)
    checkpoint_path = _checkpoint_path_text(receipt.get("checkpoint_path"), blockers)
    checkpoint_size_bytes = _positive_int(
        receipt.get("checkpoint_size_bytes"),
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

    _check_nested_identity(receipt, _TOP_LEVEL_RECEIPT_KEYS, "receipt", blockers)

    summaries = (
        ("preflight", preflight_summary),
        ("result-ledger", result_ledger_record),
        ("checkpoint-result", checkpoint_result),
    )
    summary_maps: list[tuple[str, Mapping[str, Any]]] = []
    for name, source in summaries:
        if source is None:
            continue
        mapped = _plain_mapping(source)
        if mapped is None:
            blockers.append(f"malformed-{name}")
            continue
        summary_maps.append((name, mapped))
        _check_ready_summary(
            mapped,
            name,
            expected_work_id,
            expected_run_id,
            checkpoint_path,
            checkpoint_digest,
            payload_digest,
            checkpoint_size_bytes,
            blockers,
        )

    all_sources: list[tuple[str, Mapping[str, Any]]] = [("receipt", receipt)]
    all_sources.extend(summary_maps)
    _check_secret_or_execution_intent(all_sources, blockers)

    deduped = tuple(dict.fromkeys(blockers))
    return LedgerCheckpointPromotionReadiness(
        passed=not deduped,
        blockers=deduped,
        work_id=expected_work_id or receipt_work_id,
        run_id=receipt_run_id or expected_run_id,
        checkpoint_path=checkpoint_path,
        checkpoint_digest=checkpoint_digest,
        payload_digest=payload_digest,
        checkpoint_size_bytes=checkpoint_size_bytes,
        summary=_readiness_summary(
            expected_work_id or receipt_work_id,
            receipt_run_id or expected_run_id,
            checkpoint_path,
            checkpoint_digest,
            payload_digest,
            checkpoint_size_bytes,
            summary_maps,
        ),
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
        return mapped
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
    text = value.strip()
    normalized = text.replace("\\", "/")
    lowered = normalized.lower()
    if (
        "://" in normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or normalized.startswith("../")
        or normalized.endswith("/..")
        or "/../" in normalized
        or (len(normalized) > 1 and normalized[1] == ":")
        or "\x00" in normalized
        or lowered.startswith("~")
    ):
        blockers.append("checkpoint-path-unsafe")
    if "/" not in normalized:
        blockers.append("checkpoint-path-not-explicit")
    if _is_secret_like(normalized):
        blockers.append("secret-like-checkpoint-path")
    if _has_execution_text(normalized):
        blockers.append("execution-intent-checkpoint-path")
    return text


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
    expected_text = _checkpoint_path_text(expected, blockers)
    if actual and expected_text and actual != expected_text:
        blockers.append(f"{name}-mismatch")


def _match_text(actual: str, expected: str, name: str, blockers: list[str]) -> None:
    if actual and expected and actual != expected:
        blockers.append(f"{name}-mismatch")


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


def _check_ready_summary(
    summary: Mapping[str, Any],
    name: str,
    work_id: str,
    run_id: str,
    checkpoint_path: str,
    checkpoint_digest: str,
    payload_digest: str,
    checkpoint_size_bytes: int,
    blockers: list[str],
) -> None:
    _check_source_blockers(summary.get("blockers"), name, blockers)
    if "passed" in summary and not bool(summary.get("passed")):
        blockers.append(f"{name}-not-passed")
    if "ready" in summary and not bool(summary.get("ready")):
        blockers.append(f"{name}-not-ready")
    if "status" in summary and not _status_ready(summary.get("status")):
        blockers.append(f"{name}-status-not-ready")
    if _unfinished_count(summary):
        blockers.append(f"{name}-unfinished-tasks")
    _check_summary_identity(summary, name, work_id, run_id, blockers)
    _check_optional_field_match(
        summary,
        "checkpoint_path",
        checkpoint_path,
        name,
        blockers,
    )
    _check_optional_digest_match(
        summary,
        "checkpoint_digest",
        checkpoint_digest,
        name,
        blockers,
    )
    _check_optional_digest_match(summary, "payload_digest", payload_digest, name, blockers)
    _check_optional_size_match(
        summary,
        checkpoint_size_bytes,
        name,
        blockers,
    )
    _check_nested_identity(summary, _TOP_LEVEL_SUMMARY_KEYS, name, blockers)


def _status_ready(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in _READY_STATUSES


def _unfinished_count(summary: Mapping[str, Any]) -> int:
    total = 0
    for key in ("unfinished_task_count", "unfinished_tasks"):
        value = summary.get(key)
        if isinstance(value, bool):
            total += int(value)
        elif isinstance(value, int):
            total += value
        elif isinstance(value, (list, tuple)):
            total += len(value)
    tasks = summary.get("tasks")
    if isinstance(tasks, (list, tuple)):
        for task in tasks:
            mapped = _plain_mapping(task)
            if mapped is None:
                total += 1
                continue
            status = str(mapped.get("status", "open")).strip().lower()
            if status not in _READY_STATUSES and status != "closed":
                total += 1
            if _blocker_tuple(mapped.get("blockers")):
                total += 1
    return total


def _check_summary_identity(
    summary: Mapping[str, Any],
    name: str,
    work_id: str,
    run_id: str,
    blockers: list[str],
) -> None:
    candidates = [summary]
    nested = _plain_mapping(summary.get("summary"))
    if nested is not None:
        candidates.append(nested)
    for candidate in candidates:
        source_work_id = _optional_text(candidate.get("work_id"), f"{name}-work-id", blockers)
        source_run_id = _optional_text(candidate.get("run_id"), f"{name}-run-id", blockers)
        if work_id and source_work_id:
            _match_text(source_work_id, work_id, f"{name}-work-id", blockers)
        if run_id and source_run_id:
            _match_text(source_run_id, run_id, f"{name}-run-id", blockers)


def _check_optional_field_match(
    summary: Mapping[str, Any],
    key: str,
    expected: str,
    name: str,
    blockers: list[str],
) -> None:
    values = _nested_values(summary, key)
    for value in values:
        if not isinstance(value, str) or not value.strip():
            blockers.append(f"invalid-{name}-{key.replace('_', '-')}")
            continue
        if expected and value.strip() != expected:
            blockers.append(f"{name}-{key.replace('_', '-')}-mismatch")


def _check_optional_digest_match(
    summary: Mapping[str, Any],
    key: str,
    expected: str,
    name: str,
    blockers: list[str],
) -> None:
    values = _nested_values(summary, key)
    for value in values:
        digest = _digest_text(value, f"{name}-{key.replace('_', '-')}", blockers)
        if expected and digest and digest != expected:
            blockers.append(f"{name}-{key.replace('_', '-')}-mismatch")


def _check_optional_size_match(
    summary: Mapping[str, Any],
    expected: int,
    name: str,
    blockers: list[str],
) -> None:
    values = _nested_values(summary, "checkpoint_size_bytes")
    for value in values:
        size = _positive_int(value, f"{name}-checkpoint-size-bytes", blockers)
        if expected and size and size != expected:
            blockers.append(f"{name}-checkpoint-size-bytes-mismatch")


def _nested_values(value: object, wanted_key: str) -> tuple[object, ...]:
    found: list[object] = []
    mapped = _plain_mapping(value)
    if mapped is not None:
        for key, item in mapped.items():
            if key == wanted_key:
                found.append(item)
            elif key in {"summary", "receipt_summary", "checkpoint_result"}:
                found.extend(_nested_values(item, wanted_key))
        return tuple(found)
    if isinstance(value, tuple):
        for item in value:
            found.extend(_nested_values(item, wanted_key))
    return tuple(found)


def _check_nested_identity(
    value: object,
    allowed_top_level: frozenset[str],
    prefix: str,
    blockers: list[str],
) -> None:
    mapped = _plain_mapping(value)
    if mapped is None:
        return
    for key, item in mapped.items():
        if key in allowed_top_level:
            if key in {"summary", "receipt_summary"}:
                _check_nested_identity(item, frozenset(), prefix, blockers)
            continue
        if key in _IDENTITY_KEYS or _contains_identity_key(item):
            blockers.append(f"duplicate-{prefix}-metadata")


def _contains_identity_key(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(key in _IDENTITY_KEYS or _contains_identity_key(item) for key, item in mapped.items())
    if isinstance(value, tuple):
        return any(_contains_identity_key(item) for item in value)
    return False


def _check_secret_or_execution_intent(
    sources: list[tuple[str, Mapping[str, Any]]],
    blockers: list[str],
) -> None:
    for name, source in sources:
        if _contains_secret_like(source):
            blockers.append(f"secret-like-{name}-data")
        if _contains_execution_intent(source):
            blockers.append(f"execution-intent-{name}-data")


def _contains_secret_like(value: object) -> bool:
    if _is_secret_like(value):
        return True
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(_is_secret_like(key) or _contains_secret_like(item) for key, item in mapped.items())
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
    text = value.lower()
    return "$(" in text or "`" in text


def _readiness_summary(
    work_id: str,
    run_id: str,
    checkpoint_path: str,
    checkpoint_digest: str,
    payload_digest: str,
    checkpoint_size_bytes: int,
    summaries: list[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "run_id": run_id,
        "checkpoint_path": _redacted_path(checkpoint_path),
        "checkpoint_digest_prefix": checkpoint_digest[:12],
        "payload_digest_prefix": payload_digest[:12],
        "checkpoint_size_bytes": checkpoint_size_bytes,
        "optional_summary_count": len(summaries),
        "optional_summary_names": tuple(name for name, _source in summaries),
    }


def _redacted_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return value
    return f".../{parts[-1]}"
