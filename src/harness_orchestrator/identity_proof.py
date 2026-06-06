"""Pure identity proof boundary for explicit governed Harness artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import re
from typing import Any, Mapping


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SECRET_TERMS = ("key", "token", "secret", "password")
_IDENTITY_KEYS = frozenset(
    {
        "artifact_id",
        "artifact_ids",
        "checkpoint_digest",
        "checkpoint_path",
        "evidence_bundle_id",
        "intent_digest",
        "media_id",
        "media_ids",
        "payload_digest",
        "promotion_intent_digest",
        "request_id",
        "run_id",
        "work_id",
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
_EXECUTION_FLAG_KEYS = frozenset(
    {
        "execute",
        "execution",
        "execution_intent",
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
class IdentityProofResult:
    """Plain result from building a deterministic artifact identity proof."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    canonical_payload: Mapping[str, Any] | None = None
    canonical_digest: str = ""
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "canonical_payload": dict(self.canonical_payload)
            if self.canonical_payload is not None
            else None,
            "canonical_digest": self.canonical_digest,
            "summary": dict(self.summary) if self.summary is not None else None,
        }


def build_identity_proof(
    source_records: object,
    *,
    work_id: object,
    run_id: object = None,
    request_id: object = None,
    evidence_bundle_id: object = None,
    media_ids: object = (),
    artifact_ids: object = (),
    checkpoint_path: object = None,
    checkpoint_digest: object = None,
    payload_digest: object = None,
    promotion_intent_digest: object = None,
) -> IdentityProofResult:
    """Build a deterministic identity proof from explicit plain record data."""

    blockers: list[str] = []
    expected = {
        "work_id": _required_text(work_id, "work-id", blockers),
        "run_id": _optional_text(run_id, "run-id", blockers),
        "request_id": _optional_text(request_id, "request-id", blockers),
        "evidence_bundle_id": _optional_text(
            evidence_bundle_id, "evidence-bundle-id", blockers
        ),
        "media_ids": _text_tuple(media_ids, "media-ids", blockers),
        "artifact_ids": _text_tuple(artifact_ids, "artifact-ids", blockers),
        "checkpoint_path": _optional_checkpoint_path(checkpoint_path, blockers),
        "checkpoint_digest": _optional_digest(
            checkpoint_digest, "checkpoint-digest", blockers
        ),
        "payload_digest": _optional_digest(payload_digest, "payload-digest", blockers),
        "promotion_intent_digest": _optional_digest(
            promotion_intent_digest, "promotion-intent-digest", blockers
        ),
    }

    records = _record_tuple(source_records, blockers)
    _check_secret_or_execution("source-records", records, blockers)
    _check_nested_identity_metadata(records, blockers)
    _check_source_blockers(records, blockers)

    observed = _observed_identity(records)
    _check_observed_identity_conflicts(observed, blockers)
    _require_matching_scalar(observed, expected, "work_id", "work-id", blockers)
    for key, label in (
        ("run_id", "run-id"),
        ("request_id", "request-id"),
        ("evidence_bundle_id", "evidence-bundle-id"),
        ("checkpoint_path", "checkpoint-path"),
        ("checkpoint_digest", "checkpoint-digest"),
        ("payload_digest", "payload-digest"),
        ("promotion_intent_digest", "promotion-intent-digest"),
    ):
        if expected[key]:
            _require_matching_scalar(observed, expected, key, label, blockers)
    if expected["media_ids"]:
        _require_matching_set(observed, expected, "media_ids", "media-ids", blockers)
    if expected["artifact_ids"]:
        _require_matching_set(
            observed, expected, "artifact_ids", "artifact-ids", blockers
        )

    canonical_payload = _canonical_payload(expected, observed, records)
    canonical_digest = _sha256_payload(canonical_payload)
    deduped = tuple(dict.fromkeys(blockers))
    return IdentityProofResult(
        passed=not deduped,
        blockers=deduped,
        canonical_payload=canonical_payload,
        canonical_digest=canonical_digest,
        summary=_summary(canonical_payload),
    )


def _record_tuple(value: object, blockers: list[str]) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (list, tuple)):
        records = tuple(_plain_mapping(item, blockers) for item in value)
    else:
        records = (_plain_mapping(value, blockers),)
    if not records or any(record is None for record in records):
        blockers.append("malformed-source-records")
        return ()
    return tuple(record for record in records if record is not None)


def _plain_mapping(value: object, blockers: list[str]) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item, blockers) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return {
                str(key): _plain_value(item, blockers) for key, item in mapped.items()
            }
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return {
                str(key): _plain_value(item, blockers) for key, item in mapped.items()
            }
    return None


def _plain_value(value: object, blockers: list[str]) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item, blockers) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return {
                str(key): _plain_value(item, blockers) for key, item in mapped.items()
            }
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return {
                str(key): _plain_value(item, blockers) for key, item in mapped.items()
            }
        blockers.append("malformed-to-dict-record")
        return None
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item, blockers) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    blockers.append("malformed-source-value")
    return None


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    return _screened_text(value, name, blockers)


def _optional_text(value: object, name: str, blockers: list[str]) -> str:
    if value is None:
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
        text = _optional_text(item, name[:-1], blockers)
        if text:
            safe.append(text)
    if len(safe) != len(set(safe)):
        blockers.append(f"duplicate-{name}")
    return tuple(sorted(set(safe)))


def _optional_digest(value: object, name: str, blockers: list[str]) -> str:
    if value is None:
        return ""
    text = _optional_text(value, name, blockers).lower()
    if text and not _SHA256_HEX.fullmatch(text):
        blockers.append(f"invalid-{name}")
    return text


def _optional_checkpoint_path(value: object, blockers: list[str]) -> str:
    if value is None:
        return ""
    text = _optional_text(value, "checkpoint-path", blockers)
    if not text:
        return ""
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
    return normalized


def _observed_identity(records: tuple[dict[str, Any], ...]) -> dict[str, tuple[str, ...]]:
    observed: dict[str, set[str]] = {key: set() for key in _IDENTITY_KEYS}
    for record in records:
        _collect_identity(record, observed)
    return {key: tuple(sorted(values)) for key, values in observed.items()}


def _collect_identity(value: object, observed: dict[str, set[str]]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key)
            lowered = normalized.lower()
            if lowered in observed:
                _collect_identity_value(lowered, item, observed)
            if lowered != "metadata":
                _collect_identity(item, observed)
    elif isinstance(value, tuple):
        for item in value:
            _collect_identity(item, observed)


def _collect_identity_value(
    key: str,
    value: object,
    observed: dict[str, set[str]],
) -> None:
    target_key = key
    if key == "media_id":
        target_key = "media_ids"
    elif key == "artifact_id":
        target_key = "artifact_ids"
    elif key == "intent_digest":
        target_key = "promotion_intent_digest"
    if isinstance(value, str) and value.strip():
        observed[target_key].add(_identity_text(key, value))
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip():
                observed[target_key].add(_identity_text(key, item))


def _identity_text(key: str, value: str) -> str:
    text = value.strip()
    if "path" in key:
        return text.replace("\\", "/")
    if "digest" in key:
        return text.lower()
    return text


def _require_matching_scalar(
    observed: Mapping[str, tuple[str, ...]],
    expected: Mapping[str, Any],
    key: str,
    label: str,
    blockers: list[str],
) -> None:
    expected_text = expected[key]
    actual = observed.get(key, ())
    if not expected_text:
        return
    if not actual:
        blockers.append(f"missing-source-{label}")
        return
    if len(actual) > 1:
        blockers.append(f"ambiguous-source-{label}")
    if expected_text not in actual:
        blockers.append(f"{label}-mismatch")


def _require_matching_set(
    observed: Mapping[str, tuple[str, ...]],
    expected: Mapping[str, Any],
    key: str,
    label: str,
    blockers: list[str],
) -> None:
    actual = set(observed.get(key, ()))
    wanted = set(expected[key])
    if not actual:
        blockers.append(f"missing-source-{label}")
    if actual != wanted:
        blockers.append(f"{label}-mismatch")


def _check_observed_identity_conflicts(
    observed: Mapping[str, tuple[str, ...]],
    blockers: list[str],
) -> None:
    for key, values in observed.items():
        if key in {"artifact_ids", "media_ids"}:
            continue
        if len(values) > 1:
            blockers.append(f"conflicting-source-{_identity_label(key)}")


def _identity_label(key: str) -> str:
    return key.replace("_", "-")


def _check_nested_identity_metadata(
    records: tuple[dict[str, Any], ...],
    blockers: list[str],
) -> None:
    for record in records:
        _scan_nested_identity(record, blockers, inside_metadata=False)


def _scan_nested_identity(
    value: object,
    blockers: list[str],
    *,
    inside_metadata: bool,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if inside_metadata and lowered in _IDENTITY_KEYS:
                blockers.append("duplicate-identity-metadata")
            _scan_nested_identity(
                item,
                blockers,
                inside_metadata=inside_metadata or lowered == "metadata",
            )
    elif isinstance(value, tuple):
        for item in value:
            _scan_nested_identity(item, blockers, inside_metadata=inside_metadata)


def _check_source_blockers(
    records: tuple[dict[str, Any], ...],
    blockers: list[str],
) -> None:
    source_blockers = _nested_blockers(records)
    if source_blockers:
        blockers.append("source-blockers-present")
        blockers.extend(f"source-{item}" for item in source_blockers)


def _nested_blockers(value: object) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() == "blockers":
                found.extend(_blocker_tuple(item))
            else:
                found.extend(_nested_blockers(item))
    elif isinstance(value, tuple):
        for item in value:
            found.extend(_nested_blockers(item))
    return tuple(found)


def _blocker_tuple(value: object) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ("malformed-blockers",)


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
            _is_execution_key(str(key), item) or _contains_execution_intent(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple):
        return any(_contains_execution_intent(item) for item in value)
    return _has_execution_text(value)


def _is_execution_key(key: str, value: object) -> bool:
    lowered = key.lower()
    if lowered in _EXECUTION_KEYS:
        return True
    if lowered in _EXECUTION_FLAG_KEYS or "execute" in lowered or "execution" in lowered:
        return _truthy_execution_flag(value)
    return False


def _truthy_execution_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {
            "0",
            "false",
            "no",
            "none",
            "off",
        }
    if isinstance(value, (Mapping, tuple)):
        return True
    return value is not None


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


def _canonical_payload(
    expected: Mapping[str, Any],
    observed: Mapping[str, tuple[str, ...]],
    records: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    canonical_records = tuple(
        sorted(
            (_redacted_mapping(_sorted_mapping(record)) for record in records),
            key=_record_sort_key,
        )
    )
    return {
        "format": "harness-identity-proof-v1",
        "expected": _sorted_mapping(expected),
        "observed": _redacted_mapping(_sorted_mapping(observed)),
        "records": canonical_records,
    }


def _sorted_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _sorted_value(value[key]) for key in sorted(value)}


def _sorted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _sorted_mapping(value)
    if isinstance(value, tuple):
        return tuple(_sorted_value(item) for item in value)
    return value


def _redacted_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _redacted_payload_text(key): _redacted_value(item)
        for key, item in value.items()
    }


def _redacted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _redacted_mapping(value)
    if isinstance(value, tuple):
        return tuple(_redacted_value(item) for item in value)
    if _is_secret_like(value):
        return "<redacted>"
    return value


def _redacted_payload_text(value: str) -> str:
    if _is_secret_like(value):
        return "<redacted>"
    return value


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_sort_key(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = payload["expected"]
    observed = payload["observed"]
    return {
        "work_id": expected["work_id"],
        "run_id": expected["run_id"],
        "request_id": expected["request_id"],
        "evidence_bundle_id": expected["evidence_bundle_id"],
        "media_ids": expected["media_ids"],
        "artifact_ids": expected["artifact_ids"],
        "checkpoint_path": _redacted_path(expected["checkpoint_path"]),
        "checkpoint_digest_prefix": expected["checkpoint_digest"][:12],
        "payload_digest_prefix": expected["payload_digest"][:12],
        "promotion_intent_digest_prefix": expected["promotion_intent_digest"][:12],
        "source_record_count": len(payload["records"]),
        "observed_identity_keys": tuple(
            key for key, value in sorted(observed.items()) if value
        ),
    }


def _redacted_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return value
    return f".../{parts[-1]}"


__all__ = [
    "IdentityProofResult",
    "build_identity_proof",
]
