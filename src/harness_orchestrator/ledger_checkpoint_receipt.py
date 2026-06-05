"""Readless verification for explicit Harness ledger checkpoint receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from typing import Any, Mapping


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SECRET_TERMS = ("key", "token", "secret", "password")
_CHECKPOINT_KEYS = frozenset(
    {
        "checkpoint",
        "checkpoint_digest",
        "checkpoint_path",
        "checkpoint_size_bytes",
        "ledger_checkpoint",
        "payload_digest",
    }
)
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


@dataclass(frozen=True)
class LedgerCheckpointReceiptVerification:
    """Plain result from validating caller-supplied checkpoint receipt data."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    run_id: str = ""
    checkpoint_path: str = ""
    checkpoint_digest: str = ""
    payload_digest: str = ""
    checkpoint_size_bytes: int = 0
    receipt_summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "run_id": self.run_id,
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_digest": self.checkpoint_digest,
            "payload_digest": self.payload_digest,
            "checkpoint_size_bytes": self.checkpoint_size_bytes,
            "receipt_summary": dict(self.receipt_summary)
            if self.receipt_summary is not None
            else None,
        }


def verify_ledger_checkpoint_receipt(
    receipt_source: object,
    *,
    run_id: str,
    expected_checkpoint_digest: object = None,
    expected_payload_digest: object = None,
    expected_checkpoint_path: object = None,
) -> LedgerCheckpointReceiptVerification:
    """Validate explicit in-memory checkpoint receipt data without side effects."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    receipt = _plain_mapping(receipt_source)
    if receipt is None:
        blockers.append("missing-checkpoint-receipt")
        receipt = {}

    source_blockers = _source_blockers(receipt.get("blockers"))
    if source_blockers:
        blockers.append("source-blockers-present")
        blockers.extend(f"source-{blocker}" for blocker in source_blockers)

    source_run_id = _required_text(receipt.get("run_id"), "receipt-run-id", blockers)
    if expected_run_id and source_run_id and expected_run_id != source_run_id:
        blockers.append("checkpoint-run-id-mismatch")

    checkpoint_digest = _digest_text(
        receipt.get("checkpoint_digest"), "checkpoint-digest", blockers
    )
    payload_digest = _digest_text(
        receipt.get("payload_digest"), "payload-digest", blockers
    )
    if checkpoint_digest and payload_digest and checkpoint_digest != payload_digest:
        blockers.append("checkpoint-payload-digest-mismatch")

    checkpoint_path = _checkpoint_path_text(receipt.get("checkpoint_path"), blockers)
    checkpoint_size_bytes = _positive_int(
        receipt.get("checkpoint_size_bytes"), "checkpoint-size-bytes", blockers
    )

    _expected_match(
        checkpoint_digest,
        expected_checkpoint_digest,
        "expected-checkpoint-digest",
        blockers,
    )
    _expected_match(
        payload_digest,
        expected_payload_digest,
        "expected-payload-digest",
        blockers,
    )
    _expected_match(
        checkpoint_path,
        expected_checkpoint_path,
        "expected-checkpoint-path",
        blockers,
    )

    nested = {
        key: value
        for key, value in receipt.items()
        if str(key)
        not in {
            "blockers",
            "checkpoint_digest",
            "checkpoint_path",
            "checkpoint_size_bytes",
            "payload_digest",
            "run_id",
        }
    }
    if _contains_checkpoint_metadata(nested):
        blockers.append("duplicate-checkpoint-metadata")
    if _contains_secret_like(receipt) or _is_secret_like(checkpoint_path):
        blockers.append("secret-like-checkpoint-data")
    if _contains_execution_intent(receipt):
        blockers.append("execution-intent-checkpoint-data")

    deduped = tuple(dict.fromkeys(blockers))
    return LedgerCheckpointReceiptVerification(
        passed=not deduped,
        blockers=deduped,
        run_id=source_run_id or expected_run_id,
        checkpoint_path=checkpoint_path,
        checkpoint_digest=checkpoint_digest,
        payload_digest=payload_digest,
        checkpoint_size_bytes=checkpoint_size_bytes,
        receipt_summary=_receipt_summary(
            source_run_id or expected_run_id,
            checkpoint_path,
            checkpoint_digest,
            payload_digest,
            checkpoint_size_bytes,
            len(source_blockers),
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
    parts = [part for part in normalized.split("/") if part]
    if (
        not parts
        or "://" in normalized
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
    return text


def _positive_int(value: object, name: str, blockers: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        blockers.append(f"invalid-{name}")
        return 0
    if value <= 0:
        blockers.append(f"nonpositive-{name}")
    return value


def _expected_match(
    actual: str, expected: object, name: str, blockers: list[str]
) -> None:
    if expected is None:
        return
    if not isinstance(expected, str) or not expected.strip():
        blockers.append(f"invalid-{name}")
        return
    if actual != expected.strip().lower():
        blockers.append(f"{name}-mismatch")


def _source_blockers(value: object) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ("malformed-source-blockers",)


def _contains_checkpoint_metadata(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            str(key) in _CHECKPOINT_KEYS or _contains_checkpoint_metadata(item)
            for key, item in mapped.items()
        )
    if isinstance(value, tuple):
        return any(_contains_checkpoint_metadata(item) for item in value)
    return False


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
            str(key).lower() in _EXECUTION_KEYS or _contains_execution_intent(item)
            for key, item in mapped.items()
        )
    if isinstance(value, tuple):
        return any(_contains_execution_intent(item) for item in value)
    if isinstance(value, str):
        text = value.lower()
        return "$(" in text or "`" in text
    return False


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str) or value == "<redacted>":
        return False
    text = value.lower()
    return any(term in text for term in _SECRET_TERMS)


def _receipt_summary(
    run_id: str,
    checkpoint_path: str,
    checkpoint_digest: str,
    payload_digest: str,
    checkpoint_size_bytes: int,
    source_blocker_count: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "checkpoint_path": _redacted_path(checkpoint_path),
        "checkpoint_digest_prefix": checkpoint_digest[:12],
        "payload_digest_prefix": payload_digest[:12],
        "checkpoint_size_bytes": checkpoint_size_bytes,
        "source_blocker_count": source_blocker_count,
    }


def _redacted_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return value
    return f".../{parts[-1]}"
