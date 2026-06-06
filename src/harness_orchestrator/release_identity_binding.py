"""Pure Harness release identity binding boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
from types import MappingProxyType
import re
from typing import Any, Mapping

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.identity_proof import IdentityProofResult


_FORMAT = "harness-release-identity-binding-v1"
_RELEASE_GATE = "ai-art-media-release"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SECRET_TERMS = ("key", "token", "secret", "password", "credential")
_SCALAR_IDENTITY_KEYS = frozenset(
    {
        "work_id",
        "request_id",
        "evidence_bundle_id",
        "payload_digest",
        "checkpoint_digest",
        "promotion_intent_digest",
    }
)
_SET_IDENTITY_KEYS = frozenset({"media_ids", "artifact_ids"})
_SINGULAR_SET_KEYS = {"media_id": "media_ids", "artifact_id": "artifact_ids"}
_IDENTITY_KEYS = _SCALAR_IDENTITY_KEYS | _SET_IDENTITY_KEYS | frozenset(
    _SINGULAR_SET_KEYS
)
_DIGEST_KEYS = frozenset(
    {"payload_digest", "checkpoint_digest", "promotion_intent_digest"}
)
_INTENT_KEYS = frozenset(
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
_INTENT_FLAG_KEYS = frozenset({"execute", "execution", "execution_intent"})
_INTENT_TEXT = (
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
class ReleaseIdentityBindingResult:
    """Frozen result for a deterministic release identity binding."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    canonical_payload: Mapping[str, Any] | None = None
    canonical_digest: str = ""
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "canonical_payload": _plain_copy(self.canonical_payload),
            "canonical_digest": self.canonical_digest,
            "summary": _plain_copy(self.summary),
        }


def build_release_identity_binding(
    gate_decision: object,
    identity_proof: object,
    *,
    work_id: object,
    request_id: object = None,
    evidence_bundle_id: object = None,
    media_ids: object = (),
    artifact_ids: object = (),
    payload_digest: object = None,
    checkpoint_digest: object = None,
    promotion_intent_digest: object = None,
) -> ReleaseIdentityBindingResult:
    """Bind a passed release gate to a passed identity proof from explicit inputs."""

    blockers: list[str] = []
    expected = {
        "work_id": _required_text(work_id, "work-id", blockers),
        "request_id": _optional_text(request_id, "request-id", blockers),
        "evidence_bundle_id": _optional_text(
            evidence_bundle_id, "evidence-bundle-id", blockers
        ),
        "media_ids": _text_tuple(media_ids, "media-ids", blockers),
        "artifact_ids": _text_tuple(artifact_ids, "artifact-ids", blockers),
        "payload_digest": _optional_digest(payload_digest, "payload-digest", blockers),
        "checkpoint_digest": _optional_digest(
            checkpoint_digest, "checkpoint-digest", blockers
        ),
        "promotion_intent_digest": _optional_digest(
            promotion_intent_digest, "promotion-intent-digest", blockers
        ),
    }

    decision = _plain_mapping(gate_decision, blockers, "gate-decision")
    proof = _plain_mapping(identity_proof, blockers, "identity-proof")
    _check_secret_or_intent("expected-identity", expected, blockers)
    _check_secret_or_intent("gate-decision", decision, blockers)
    _check_secret_or_intent("identity-proof", proof, blockers)
    _check_nested_identity_metadata(
        "gate-decision",
        decision,
        blockers,
        allow_root_metadata=True,
    )
    _check_nested_identity_metadata("identity-proof", proof, blockers)

    _validate_decision(decision, blockers)
    _validate_proof(proof, blockers)

    decision_seen = _observed_identity(decision)
    proof_seen = _observed_identity(proof)
    _check_conflicts("gate", decision_seen, blockers)
    _check_conflicts("proof", proof_seen, blockers)
    _require_expected_matches("gate", decision_seen, expected, blockers)
    _require_expected_matches("proof", proof_seen, expected, blockers)

    canonical_payload = _canonical_payload(expected, decision, proof)
    digest = _sha256_payload(canonical_payload)
    deduped = tuple(dict.fromkeys(blockers))
    summary = _summary(expected, digest)
    return ReleaseIdentityBindingResult(
        passed=not deduped,
        blockers=deduped,
        canonical_payload=_freeze_value(canonical_payload),
        canonical_digest=digest,
        summary=_freeze_value(summary),
    )


def _plain_mapping(value: object, blockers: list[str], name: str) -> dict[str, Any]:
    mapped: object
    if isinstance(value, Mapping):
        mapped = value
    elif isinstance(value, (GateDecision, IdentityProofResult)):
        mapped = asdict(value)
    elif is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
    else:
        to_dict = getattr(value, "to_dict", None)
        mapped = to_dict() if callable(to_dict) else None
    if not isinstance(mapped, Mapping):
        blockers.append(f"malformed-{name}")
        return {}
    return {str(key): _plain_value(item, blockers, name) for key, item in mapped.items()}


def _plain_value(value: object, blockers: list[str], name: str) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_value(item, blockers, name) for key, item in value.items()
        }
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return {
                str(key): _plain_value(item, blockers, name)
                for key, item in mapped.items()
            }
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return {
                str(key): _plain_value(item, blockers, name)
                for key, item in mapped.items()
            }
        blockers.append(f"malformed-{name}-value")
        return None
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item, blockers, name) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    blockers.append(f"malformed-{name}-value")
    return None


def _validate_decision(decision: Mapping[str, Any], blockers: list[str]) -> None:
    if not decision:
        return
    if decision.get("gate_name") != _RELEASE_GATE:
        blockers.append("release-gate-mismatch")
    if decision.get("passed") is not True:
        blockers.append("gate-not-passed")
    gate_blockers = _blocker_tuple(decision.get("blockers"))
    if gate_blockers:
        blockers.append("gate-blockers-present")
        blockers.extend(f"gate-{item}" for item in gate_blockers)


def _validate_proof(proof: Mapping[str, Any], blockers: list[str]) -> None:
    if not proof:
        return
    if proof.get("passed") is not True:
        blockers.append("proof-not-passed")
    proof_blockers = _blocker_tuple(proof.get("blockers"))
    if proof_blockers:
        blockers.append("proof-blockers-present")
        blockers.extend(f"proof-{item}" for item in proof_blockers)
    digest = proof.get("canonical_digest")
    if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest.strip().lower()):
        blockers.append("missing-proof-canonical-digest")


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
    if _has_intent_text(text):
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


def _observed_identity(value: object) -> dict[str, tuple[str, ...]]:
    observed: dict[str, set[str]] = {key: set() for key in _SCALAR_IDENTITY_KEYS}
    observed.update({key: set() for key in _SET_IDENTITY_KEYS})
    _collect_identity(value, observed)
    return {key: tuple(sorted(values)) for key, values in observed.items()}


def _collect_identity(value: object, observed: dict[str, set[str]]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            target = _SINGULAR_SET_KEYS.get(lowered, lowered)
            if target in observed:
                _collect_identity_value(target, item, observed)
            _collect_identity(item, observed)
    elif isinstance(value, tuple):
        for item in value:
            _collect_identity(item, observed)


def _collect_identity_value(
    key: str,
    value: object,
    observed: dict[str, set[str]],
) -> None:
    if isinstance(value, str) and value.strip():
        observed[key].add(_identity_text(key, value))
    elif isinstance(value, tuple):
        for item in value:
            if isinstance(item, str) and item.strip():
                observed[key].add(_identity_text(key, item))


def _identity_text(key: str, value: str) -> str:
    text = value.strip()
    if key in _DIGEST_KEYS:
        return text.lower()
    return text


def _check_conflicts(
    source: str,
    observed: Mapping[str, tuple[str, ...]],
    blockers: list[str],
) -> None:
    for key, values in observed.items():
        if key in _SET_IDENTITY_KEYS:
            continue
        if len(values) > 1:
            blockers.append(f"conflicting-{source}-{_label(key)}")


def _require_expected_matches(
    source: str,
    observed: Mapping[str, tuple[str, ...]],
    expected: Mapping[str, Any],
    blockers: list[str],
) -> None:
    for key in sorted(_SCALAR_IDENTITY_KEYS):
        expected_value = expected[key]
        if not expected_value:
            continue
        values = observed.get(key, ())
        if not values:
            blockers.append(f"missing-{source}-{_label(key)}")
        elif expected_value not in values:
            blockers.append(f"{source}-{_label(key)}-mismatch")
    for key in sorted(_SET_IDENTITY_KEYS):
        expected_values = expected[key]
        if not expected_values:
            continue
        values = set(observed.get(key, ()))
        if not values:
            blockers.append(f"missing-{source}-{_label(key)}")
        elif values != set(expected_values):
            blockers.append(f"{source}-{_label(key)}-mismatch")


def _check_nested_identity_metadata(
    name: str,
    value: object,
    blockers: list[str],
    *,
    allow_root_metadata: bool = False,
) -> None:
    if _has_nested_identity_metadata(
        value,
        inside_metadata=False,
        allow_root_metadata=allow_root_metadata,
        depth=0,
    ):
        blockers.append(f"ambiguous-{name}-identity-metadata")


def _has_nested_identity_metadata(
    value: object,
    *,
    inside_metadata: bool,
    allow_root_metadata: bool,
    depth: int,
) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if inside_metadata and lowered in _IDENTITY_KEYS:
                return True
            next_inside = inside_metadata or lowered == "metadata"
            if allow_root_metadata and depth == 0 and lowered == "metadata":
                next_inside = False
            if _has_nested_identity_metadata(
                item,
                inside_metadata=next_inside,
                allow_root_metadata=allow_root_metadata,
                depth=depth + 1,
            ):
                return True
    elif isinstance(value, tuple):
        return any(
            _has_nested_identity_metadata(
                item,
                inside_metadata=inside_metadata,
                allow_root_metadata=allow_root_metadata,
                depth=depth,
            )
            for item in value
        )
    return False


def _blocker_tuple(value: object) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ("malformed-blockers",)


def _check_secret_or_intent(
    name: str,
    value: object,
    blockers: list[str],
) -> None:
    if _contains_secret_like(value):
        blockers.append(f"secret-like-{name}")
    if _contains_intent(value):
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


def _contains_intent(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_intent_key(str(key), item) or _contains_intent(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple):
        return any(_contains_intent(item) for item in value)
    return _has_intent_text(value)


def _is_intent_key(key: str, value: object) -> bool:
    lowered = key.lower()
    if lowered in _INTENT_KEYS:
        return True
    if lowered in _INTENT_FLAG_KEYS or "execute" in lowered or "execution" in lowered:
        return True
    return False


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str) or value == "<redacted>":
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)


def _has_intent_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = f" {value.strip().lower()} "
    return any(term in lowered for term in _INTENT_TEXT)


def _canonical_payload(
    expected: Mapping[str, Any],
    decision: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "expected": _sorted_mapping(expected),
        "gate_decision": _redacted_value(_sorted_mapping(decision)),
        "identity_proof": _redacted_value(_sorted_mapping(proof)),
    }


def _sorted_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _sorted_value(value[key]) for key in sorted(value)}


def _sorted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _sorted_mapping(value)
    if isinstance(value, tuple):
        return tuple(sorted((_sorted_value(item) for item in value), key=_sort_key))
    return value


def _sort_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _redacted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            _redacted_text(str(key)): _redacted_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_redacted_value(item) for item in value)
    if _is_secret_like(value):
        return "<redacted>"
    return value


def _redacted_text(value: str) -> str:
    if _is_secret_like(value):
        return "<redacted>"
    return value


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
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


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summary(expected: Mapping[str, Any], digest: str) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "work_id": expected["work_id"],
        "request_id": expected["request_id"],
        "evidence_bundle_id": expected["evidence_bundle_id"],
        "media_ids": expected["media_ids"],
        "artifact_ids": expected["artifact_ids"],
        "payload_digest_prefix": expected["payload_digest"][:12],
        "checkpoint_digest_prefix": expected["checkpoint_digest"][:12],
        "promotion_intent_digest_prefix": expected["promotion_intent_digest"][:12],
        "canonical_digest_prefix": digest[:12],
    }


def _label(key: str) -> str:
    return key.replace("_", "-")


__all__ = [
    "ReleaseIdentityBindingResult",
    "build_release_identity_binding",
]
