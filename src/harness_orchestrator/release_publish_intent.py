"""Pure Harness release publish intent binding."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


_FORMAT = "harness-release-publish-intent-v1"
_READY_FORMAT = "harness-release-publish-readiness-v1"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_READY_KEYS = frozenset(
    {
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "canonical_digest_prefix",
        "status",
        "ready",
        "blockers",
        "summary",
    }
)
_READY_SUMMARY_KEYS = frozenset(
    {
        "format",
        "run_id",
        "work_id",
        "dependency_id",
        "event_id",
        "canonical_digest_prefix",
        "task_count",
        "require_finished_tasks",
    }
)
_TARGET_TYPES = frozenset({"local-dry-run", "manual-release-placeholder"})
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
    " launcher",
    " runner",
    " shell",
    " webhook",
)


@dataclass(frozen=True)
class ReleasePublishIntent:
    """Frozen caller-supplied publish intent metadata."""

    run_id: str
    work_id: str
    release_binding_digest: str
    publish_target: Mapping[str, Any]
    publish_payload: Mapping[str, Any]
    artifact: Mapping[str, Any]
    metadata: Mapping[str, Any]
    canonical_payload: Mapping[str, Any]
    intent_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "work_id": self.work_id,
            "release_binding_digest": self.release_binding_digest,
            "publish_target": _plain_copy(self.publish_target),
            "publish_payload": _plain_copy(self.publish_payload),
            "artifact": _plain_copy(self.artifact),
            "metadata": _plain_copy(self.metadata),
            "canonical_payload": _plain_copy(self.canonical_payload),
            "intent_digest": self.intent_digest,
        }


@dataclass(frozen=True)
class ReleasePublishIntentResult:
    """Plain result from binding readiness to caller-supplied publish intent."""

    passed: bool = False
    blockers: tuple[str, ...] = ()
    intent: ReleasePublishIntent | None = None
    summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": self.blockers,
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "summary": _plain_copy(self.summary),
        }


def build_release_publish_intent(
    readiness: object,
    *,
    run_id: object,
    work_id: object,
    release_binding_digest: object,
    publish_target: object,
    publish_payload: object,
    artifact: object,
    metadata: object = None,
) -> ReleasePublishIntentResult:
    """Bind exposed readiness fields to deterministic caller publish metadata."""

    blockers: list[str] = []
    expected_run_id = _required_text(run_id, "run-id", blockers)
    expected_work_id = _required_text(work_id, "work-id", blockers)
    ready = _readiness_mapping(readiness, blockers)
    if ready is None:
        ready = {}

    _validate_readiness(ready, expected_run_id, expected_work_id, blockers)
    readiness_prefix = _digest_prefix_text(
        ready.get("canonical_digest_prefix"),
        "readiness-canonical-digest-prefix",
        blockers,
    )
    binding_digest = _digest_text(
        release_binding_digest,
        "release-binding-digest",
        blockers,
    )
    if binding_digest and readiness_prefix and binding_digest[:12] != readiness_prefix:
        blockers.append("release-binding-digest-prefix-mismatch")

    target = _publish_target_mapping(publish_target, blockers)
    payload = _publish_payload_mapping(publish_payload, blockers)
    artifact_data = _artifact_mapping(artifact, blockers)
    metadata_data = _metadata_mapping(metadata, blockers)

    summary = _freeze_value(
        _summary(
            expected_run_id,
            expected_work_id,
            readiness_prefix,
            binding_digest,
            target,
            payload,
            artifact_data,
            metadata_data,
        )
    )
    deduped = tuple(dict.fromkeys(blockers))
    if deduped:
        return ReleasePublishIntentResult(
            passed=False,
            blockers=deduped,
            intent=None,
            summary=summary,
        )

    canonical_payload = _canonical_payload(
        expected_run_id,
        expected_work_id,
        binding_digest,
        readiness_prefix,
        target,
        payload,
        artifact_data,
        metadata_data,
    )
    intent = ReleasePublishIntent(
        run_id=expected_run_id,
        work_id=expected_work_id,
        release_binding_digest=binding_digest,
        publish_target=_freeze_value(target),
        publish_payload=_freeze_value(payload),
        artifact=_freeze_value(artifact_data),
        metadata=_freeze_value(metadata_data),
        canonical_payload=_freeze_value(canonical_payload),
        intent_digest=_sha256_payload(canonical_payload),
    )
    return ReleasePublishIntentResult(
        passed=True,
        blockers=(),
        intent=intent,
        summary=summary,
    )


def _readiness_mapping(
    value: object,
    blockers: list[str],
) -> dict[str, Any] | None:
    mapped = _mapping_from_value(value, blockers, "readiness", allow_attrs=True)
    if mapped is None:
        blockers.append("malformed-readiness")
        return None
    for key in mapped:
        if key not in _READY_KEYS:
            blockers.append("unsafe-readiness-schema")
            break
    return mapped


def _mapping_from_value(
    value: object,
    blockers: list[str],
    name: str,
    *,
    allow_attrs: bool = False,
) -> dict[str, Any] | None:
    source: object = None
    if isinstance(value, Mapping):
        source = value
    else:
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            source = to_dict()
        elif allow_attrs and any(hasattr(value, key) for key in _READY_KEYS):
            source = {
                key: getattr(value, key)
                for key in _READY_KEYS
                if hasattr(value, key)
            }
    if not isinstance(source, Mapping):
        return None
    plain: dict[str, Any] = {}
    seen: set[str] = set()
    for key, item in source.items():
        if not isinstance(key, str):
            blockers.append(f"non-string-{name}-key")
            continue
        if key in seen:
            blockers.append(f"duplicate-{name}-key")
            _plain_value(item, blockers, name)
            continue
        seen.add(key)
        plain[key] = _plain_value(item, blockers, name)
    return plain


def _plain_value(value: object, blockers: list[str], name: str) -> object:
    if isinstance(value, Mapping):
        plain: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                blockers.append(f"non-string-{name}-key")
                continue
            plain[key] = _plain_value(item, blockers, name)
        return plain
    if isinstance(value, (list, tuple)):
        return tuple(_plain_value(item, blockers, name) for item in value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    blockers.append(f"unsupported-{name}-object")
    return None


def _validate_readiness(
    readiness: Mapping[str, Any],
    expected_run_id: str,
    expected_work_id: str,
    blockers: list[str],
) -> None:
    if readiness.get("ready") is not True:
        blockers.append("readiness-not-ready")
    if readiness.get("status") != "ready":
        blockers.append("readiness-status-not-ready")
    source_blockers = _blocker_tuple(readiness.get("blockers"))
    if source_blockers:
        blockers.append("readiness-blockers-present")
        blockers.extend(f"readiness-{item}" for item in source_blockers)
    readiness_run_id = _required_text(
        readiness.get("run_id"),
        "readiness-run-id",
        blockers,
    )
    readiness_work_id = _required_text(
        readiness.get("work_id"),
        "readiness-work-id",
        blockers,
    )
    _match_text(readiness_run_id, expected_run_id, "readiness-run-id", blockers)
    _match_text(readiness_work_id, expected_work_id, "readiness-work-id", blockers)
    _validate_readiness_summary(readiness.get("summary"), blockers)
    _check_secret_or_action("readiness", readiness, blockers)


def _validate_readiness_summary(value: object, blockers: list[str]) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, Mapping):
        blockers.append("malformed-readiness-summary")
        return
    for key in value:
        if key not in _READY_SUMMARY_KEYS:
            blockers.append("unsafe-readiness-summary-schema")
            break
    if value.get("format") not in (None, _READY_FORMAT):
        blockers.append("readiness-summary-format-mismatch")


def _publish_target_mapping(
    value: object,
    blockers: list[str],
) -> dict[str, str]:
    mapped = _exact_mapping(value, {"target_type", "target_id"}, "publish-target", blockers)
    target_type = mapped.get("target_type")
    if target_type not in _TARGET_TYPES:
        blockers.append("unsupported-publish-target-type")
        target_type = ""
    target_id = _safe_text(mapped.get("target_id"), "publish-target-id", blockers)
    if target_id and _looks_like_locator(target_id):
        blockers.append("unsafe-publish-target-id")
    return {"target_id": target_id, "target_type": target_type}


def _publish_payload_mapping(
    value: object,
    blockers: list[str],
) -> dict[str, str]:
    mapped = _mapping_from_value(value, blockers, "publish-payload")
    if mapped is None:
        blockers.append("malformed-publish-payload")
        mapped = {}
    keys = set(mapped)
    if keys - {"payload_digest", "payload_label"} or "payload_digest" not in keys:
        blockers.append("unsafe-publish-payload-schema")
    payload_digest = _digest_text(
        mapped.get("payload_digest"),
        "publish-payload-digest",
        blockers,
    )
    payload_label = ""
    if "payload_label" in mapped:
        payload_label = _safe_text(
            mapped.get("payload_label"),
            "publish-payload-label",
            blockers,
            required=False,
        )
    data = {"payload_digest": payload_digest}
    if "payload_label" in mapped:
        data["payload_label"] = payload_label
    return data


def _artifact_mapping(value: object, blockers: list[str]) -> dict[str, str]:
    mapped = _exact_mapping(value, {"artifact_id"}, "artifact", blockers)
    return {"artifact_id": _safe_text(mapped.get("artifact_id"), "artifact-id", blockers)}


def _metadata_mapping(value: object, blockers: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    mapped = _mapping_from_value(value, blockers, "metadata")
    if mapped is None:
        blockers.append("malformed-metadata")
        return {}
    safe: dict[str, Any] = {}
    for key, item in mapped.items():
        normalized = key.lower()
        if normalized in _RESERVED_METADATA_KEYS:
            blockers.append("reserved-metadata-key")
            continue
        if not _SAFE_TEXT.fullmatch(key) or _looks_like_locator(key):
            blockers.append("unsafe-metadata-key")
            continue
        if _is_secret_like(key):
            blockers.append("secret-like-metadata")
            continue
        if _is_action_key(key) or _has_action_text(key):
            blockers.append("action-intent-metadata")
            continue
        if isinstance(item, Mapping) or isinstance(item, tuple):
            blockers.append("nested-metadata")
            continue
        if isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int):
            safe[key] = item
        elif isinstance(item, str):
            if _is_secret_like(item):
                blockers.append("secret-like-metadata")
                continue
            if _has_action_text(item):
                blockers.append("action-intent-metadata")
                continue
            text = _safe_text(
                item,
                "metadata-value",
                blockers,
                required=False,
            )
            safe[key] = text
        else:
            blockers.append("invalid-metadata-value")
    return _sorted_mapping(safe)


def _exact_mapping(
    value: object,
    keys: set[str],
    name: str,
    blockers: list[str],
) -> dict[str, Any]:
    mapped = _mapping_from_value(value, blockers, name)
    if mapped is None:
        blockers.append(f"malformed-{name}")
        return {}
    if set(mapped) != keys:
        blockers.append(f"unsafe-{name}-schema")
    return mapped


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    return _safe_text(value, name, blockers, required=True)


def _safe_text(
    value: object,
    name: str,
    blockers: list[str],
    *,
    required: bool = True,
) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}" if required else f"invalid-{name}")
        return ""
    text = value.strip()
    if len(text) > 128 or not _SAFE_TEXT.fullmatch(text):
        blockers.append(f"unsafe-{name}")
        return ""
    if _is_secret_like(text):
        blockers.append(f"secret-like-{name}")
        return ""
    if _has_action_text(text):
        blockers.append(f"action-intent-{name}")
        return ""
    return text


def _digest_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    if not _SHA256_HEX.fullmatch(value):
        blockers.append(f"invalid-{name}")
        return ""
    return value


def _digest_prefix_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    if not _DIGEST_PREFIX.fullmatch(value):
        blockers.append(f"invalid-{name}")
        return ""
    return value


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


def _match_text(actual: str, expected: str, name: str, blockers: list[str]) -> None:
    if actual and expected and actual != expected:
        blockers.append(f"{name}-mismatch")


def _check_secret_or_action(
    name: str,
    value: object,
    blockers: list[str],
) -> None:
    if _contains_secret_like(value):
        blockers.append(f"secret-like-{name}")
    if _contains_action_intent(value):
        blockers.append(f"action-intent-{name}")


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
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)


def _is_action_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = "".join(
        character.lower() if character.isalnum() else "_"
        for character in value
    )
    return normalized in _ACTION_KEYS or "execute" in normalized


def _has_action_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = f" {value.strip().lower()} "
    return any(term in lowered for term in _ACTION_TEXT)


def _looks_like_locator(value: str) -> bool:
    lowered = value.lower()
    return (
        "://" in lowered
        or "/" in value
        or "\\" in value
        or value.startswith("@")
        or lowered.startswith("www.")
        or lowered.startswith("mailto:")
        or lowered.startswith("http:")
        or lowered.startswith("https:")
    )


def _canonical_payload(
    run_id: str,
    work_id: str,
    release_binding_digest: str,
    readiness_prefix: str,
    publish_target: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "readiness_binding": {
            "run_id": run_id,
            "work_id": work_id,
            "canonical_digest_prefix": readiness_prefix,
            "release_binding_digest": release_binding_digest,
            "source": "cr-har-030-exposed-readiness-fields-only",
        },
        "caller_supplied_intent_metadata": {
            "publish_target": _sorted_mapping(publish_target),
            "publish_payload": _sorted_mapping(publish_payload),
            "artifact": _sorted_mapping(artifact),
            "metadata": _sorted_mapping(metadata),
            "verification_status": "caller-supplied-not-cr-har-030-identity",
        },
    }


def _summary(
    run_id: str,
    work_id: str,
    readiness_prefix: str,
    release_binding_digest: str,
    publish_target: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "run_id": run_id,
        "work_id": work_id,
        "canonical_digest_prefix": readiness_prefix,
        "release_binding_digest_prefix": release_binding_digest[:12],
        "target_type": publish_target.get("target_type", ""),
        "target_id": publish_target.get("target_id", ""),
        "payload_digest_prefix": str(publish_payload.get("payload_digest", ""))[:12],
        "artifact_id": artifact.get("artifact_id", ""),
        "metadata_keys": tuple(sorted(metadata)),
        "intent_metadata_status": "caller-supplied-not-independently-verified",
    }


def _sorted_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _sorted_value(value[key]) for key in sorted(value)}


def _sorted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _sorted_mapping(value)
    if isinstance(value, tuple):
        return tuple(_sorted_value(item) for item in value)
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


__all__ = [
    "ReleasePublishIntent",
    "ReleasePublishIntentResult",
    "build_release_publish_intent",
]
