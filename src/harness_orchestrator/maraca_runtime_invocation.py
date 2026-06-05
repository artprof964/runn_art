"""Plain invocation envelope for a future MARACA runtime executor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping


REDACTED = "<redacted>"
_SECRET_TERMS = ("key", "token", "secret", "password")
_EXECUTION_FLAG_NAMES = (
    "execute",
    "run",
    "start",
    "invoke",
    "call",
    "dispatch",
    "submit",
    "schedule",
    "publish",
    "persist",
    "watch",
    "network",
    "sub" + "process",
)


@dataclass(frozen=True)
class MaracaRuntimeInvocationRequirements:
    """Requirements for preparing a caller-supplied runtime invocation."""

    require_preflight_ready: bool = True
    require_maraca_readiness_ready: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "require_preflight_ready": self.require_preflight_ready,
            "require_maraca_readiness_ready": self.require_maraca_readiness_ready,
        }


@dataclass(frozen=True)
class MaracaRuntimeInvocationRequest:
    """Frozen plain-data request for a future runtime invocation."""

    work_id: str
    run_id: str
    operation: str
    payload: Mapping[str, object]
    preflight: Mapping[str, object]
    maraca_readiness: Mapping[str, object]
    runtime_settings: Mapping[str, object]
    runtime_config: Mapping[str, object]
    metadata: Mapping[str, object]
    requirements: MaracaRuntimeInvocationRequirements

    def to_dict(self) -> dict[str, object]:
        return {
            "work_id": self.work_id,
            "run_id": self.run_id,
            "operation": self.operation,
            "payload": _redact(self.payload),
            "preflight": _redact(self.preflight),
            "maraca_readiness": _redact(self.maraca_readiness),
            "runtime_settings": _redact(self.runtime_settings),
            "runtime_config": _redact(self.runtime_config),
            "metadata": _redact(self.metadata),
            "requirements": self.requirements.to_dict(),
        }


@dataclass(frozen=True)
class MaracaRuntimeInvocationResult:
    """Plain preparation result containing only a redacted envelope."""

    ready: bool
    status: str
    blockers: tuple[str, ...]
    requirements: MaracaRuntimeInvocationRequirements
    envelope: MaracaRuntimeInvocationRequest | None

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "requirements": self.requirements.to_dict(),
            "envelope": self.envelope.to_dict() if self.envelope else None,
        }


def prepare_maraca_runtime_invocation(
    *,
    work_id: object,
    run_id: object,
    operation: object,
    payload: object,
    preflight: object,
    maraca_readiness: object,
    runtime_settings: object | None = None,
    runtime_config: object | None = None,
    metadata: object | None = None,
    requirements: MaracaRuntimeInvocationRequirements | None = None,
) -> MaracaRuntimeInvocationResult:
    """Validate explicit caller data and return a non-executing invocation envelope."""

    resolved = requirements or MaracaRuntimeInvocationRequirements()
    blockers: list[str] = []

    resolved_work_id = _required_text(work_id, "work-id", blockers)
    resolved_run_id = _required_text(run_id, "run-id", blockers)
    resolved_operation = _required_text(operation, "operation", blockers)

    payload_map = _required_mapping(payload, "payload", blockers)
    preflight_map = _required_mapping(preflight, "preflight", blockers)
    readiness_map = _required_mapping(maraca_readiness, "maraca-readiness", blockers)
    settings_map = _optional_mapping(runtime_settings, "runtime-settings", blockers)
    config_map = _optional_mapping(runtime_config, "runtime-config", blockers)
    metadata_map = _optional_mapping(metadata, "metadata", blockers)

    if resolved_work_id and resolved_run_id and resolved_work_id != resolved_run_id:
        blockers.append("work-id-run-id-mismatch")

    _check_ready_snapshot(
        preflight_map,
        "preflight",
        resolved.require_preflight_ready,
        blockers,
    )
    _check_ready_snapshot(
        readiness_map,
        "maraca-readiness",
        resolved.require_maraca_readiness_ready,
        blockers,
    )
    _check_work_run_identity(preflight_map, resolved_work_id, resolved_run_id, "preflight", blockers)
    _check_work_run_identity(
        readiness_map,
        resolved_work_id,
        resolved_run_id,
        "maraca-readiness",
        blockers,
    )

    if payload_map is not None and not payload_map:
        blockers.append("missing-payload")

    for kind, value in (
        ("payload", payload_map),
        ("runtime-settings", settings_map),
        ("runtime-config", config_map),
        ("metadata", metadata_map),
    ):
        _check_secret_like(kind, value, blockers)
        _check_execution_flags(kind, value, blockers)

    deduped_blockers = tuple(dict.fromkeys(blockers))
    ready = not deduped_blockers
    envelope = None
    if (
        resolved_work_id
        and resolved_run_id
        and resolved_operation
        and payload_map is not None
        and preflight_map is not None
        and readiness_map is not None
    ):
        envelope = MaracaRuntimeInvocationRequest(
            work_id=resolved_work_id,
            run_id=resolved_run_id,
            operation=resolved_operation,
            payload=payload_map or {},
            preflight=preflight_map or {},
            maraca_readiness=readiness_map or {},
            runtime_settings=settings_map or {},
            runtime_config=config_map or {},
            metadata=metadata_map or {},
            requirements=resolved,
        )

    return MaracaRuntimeInvocationResult(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped_blockers,
        requirements=resolved,
        envelope=envelope,
    )


def _plain_mapping(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return dict(mapped)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        if isinstance(mapped, Mapping):
            return dict(mapped)
    return None


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    if _is_secret_like(value):
        blockers.append(f"redacted-{name}")
        return ""
    return value.strip()


def _required_mapping(value: object, name: str, blockers: list[str]) -> dict[str, Any] | None:
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"missing-{name}")
    return mapped


def _optional_mapping(value: object | None, name: str, blockers: list[str]) -> dict[str, Any] | None:
    if value is None:
        return {}
    mapped = _plain_mapping(value)
    if mapped is None:
        blockers.append(f"malformed-{name}")
    return mapped


def _check_ready_snapshot(
    snapshot: Mapping[str, Any] | None,
    name: str,
    required: bool,
    blockers: list[str],
) -> None:
    if snapshot is None:
        return
    if not required:
        return
    if not bool(snapshot.get("ready")):
        blockers.append(f"blocked-{name}")


def _check_work_run_identity(
    snapshot: Mapping[str, Any] | None,
    work_id: str,
    run_id: str,
    name: str,
    blockers: list[str],
) -> None:
    if snapshot is None:
        return
    snapshot_work_id = _text(snapshot.get("work_id"))
    snapshot_run_id = _text(snapshot.get("run_id"))
    summary = _plain_mapping(snapshot.get("summary"))
    if summary is not None:
        snapshot_work_id = snapshot_work_id or _text(summary.get("work_id"))
        snapshot_run_id = snapshot_run_id or _text(summary.get("run_id"))

    if work_id and snapshot_work_id and snapshot_work_id != work_id:
        blockers.append(f"{name}-work-id-mismatch")
    if run_id and snapshot_run_id and snapshot_run_id != run_id:
        blockers.append(f"{name}-run-id-mismatch")
    if snapshot_work_id and snapshot_run_id and snapshot_work_id != snapshot_run_id:
        blockers.append(f"{name}-work-id-run-id-mismatch")


def _check_secret_like(
    kind: str,
    value: Mapping[str, Any] | None,
    blockers: list[str],
) -> None:
    if value is None:
        return
    for key, item in value.items():
        if _is_secret_like(key):
            blockers.append(f"redacted-{kind}-name")
        if _is_secret_like(item):
            blockers.append(f"redacted-{kind}-value")
        child = _plain_mapping(item)
        if child is not None:
            _check_secret_like(kind, child, blockers)
        elif isinstance(item, (list, tuple)):
            for child_item in item:
                child_map = _plain_mapping(child_item)
                if child_map is not None:
                    _check_secret_like(kind, child_map, blockers)
                elif _is_secret_like(child_item):
                    blockers.append(f"redacted-{kind}-value")


def _check_execution_flags(
    kind: str,
    value: Mapping[str, Any] | None,
    blockers: list[str],
) -> None:
    if value is None:
        return
    for key, item in value.items():
        if _is_execution_flag(key) and bool(item):
            blockers.append(f"execution-flag:{kind}")
        child = _plain_mapping(item)
        if child is not None:
            _check_execution_flags(kind, child, blockers)
        elif isinstance(item, (list, tuple)):
            for child_item in item:
                child_map = _plain_mapping(child_item)
                if child_map is not None:
                    _check_execution_flags(kind, child_map, blockers)


def _is_execution_flag(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower().replace("-", "_")
    return lowered in _EXECUTION_FLAG_NAMES or lowered.startswith("should_")


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            if _is_secret_like(key):
                redacted[REDACTED] = REDACTED
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, list):
        return tuple(_redact(item) for item in value)
    mapped = _plain_mapping(value)
    if mapped is not None:
        return _redact(mapped)
    if _is_secret_like(value):
        return REDACTED
    return value


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)
