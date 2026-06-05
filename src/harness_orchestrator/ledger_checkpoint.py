"""Explicit local checkpoint boundary for Harness run ledger snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


_SECRET_TERMS = ("key", "token", "secret", "password")
_CHECKPOINT_METADATA_KEYS = (
    "checkpoint_digest",
    "checkpoint_path",
    "ledger_checkpoint",
)


@dataclass(frozen=True)
class LedgerCheckpointResult:
    """Plain result from attempting to write one explicit ledger checkpoint."""

    checkpoint_path: str = ""
    checkpoint_digest: str = ""
    payload_digest: str = ""
    checkpoint_size_bytes: int = 0
    run_id: str = ""
    blockers: tuple[str, ...] = ()
    ledger_snapshot: Mapping[str, Any] | None = None
    source_snapshot_summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_digest": self.checkpoint_digest,
            "payload_digest": self.payload_digest,
            "checkpoint_size_bytes": self.checkpoint_size_bytes,
            "run_id": self.run_id,
            "blockers": self.blockers,
            "ledger_snapshot": dict(self.ledger_snapshot)
            if self.ledger_snapshot is not None
            else None,
            "source_snapshot_summary": dict(self.source_snapshot_summary)
            if self.source_snapshot_summary is not None
            else None,
        }


def checkpoint_ledger_snapshot(
    snapshot_source: object,
    *,
    checkpoint_path: str | Path | None,
    run_id: str,
) -> LedgerCheckpointResult:
    """Write a deterministic checkpoint for an explicit ledger snapshot source."""

    blockers: list[str] = []
    snapshot = _snapshot_mapping(snapshot_source, blockers)
    expected_run_id = _required_text(run_id, "run-id", blockers)
    target = _checkpoint_path(checkpoint_path, blockers)

    snapshot_run_id = _required_text(
        snapshot.get("run_id") if snapshot is not None else None,
        "snapshot-run-id",
        blockers,
    )
    if expected_run_id and snapshot_run_id and expected_run_id != snapshot_run_id:
        blockers.append("checkpoint-run-id-mismatch")

    if snapshot is not None:
        blockers.extend(_snapshot_blockers(snapshot))
    if target is not None:
        blockers.extend(_path_blockers(target))
    if snapshot_source is not None and _contains_checkpoint_metadata(snapshot_source):
        blockers.append("duplicate-checkpoint-metadata")
    if snapshot_source is not None and _contains_secret_like(snapshot_source):
        blockers.append("secret-like-checkpoint-data")
    if checkpoint_path is not None and _is_secret_like(str(checkpoint_path)):
        blockers.append("secret-like-checkpoint-path")

    plain_snapshot = _plain_value(snapshot) if snapshot is not None else None
    if not isinstance(plain_snapshot, Mapping):
        blockers.append("malformed-ledger-snapshot")
        plain_snapshot = None

    deduped = tuple(dict.fromkeys(blockers))
    if deduped or target is None or plain_snapshot is None:
        return LedgerCheckpointResult(
            checkpoint_path=str(target) if target is not None else "",
            run_id=snapshot_run_id or expected_run_id,
            blockers=deduped,
            ledger_snapshot=plain_snapshot,
            source_snapshot_summary=_snapshot_summary(plain_snapshot),
        )

    snapshot_json = _canonical_json(plain_snapshot)
    digest = _digest_text(snapshot_json)
    checkpoint_document = {
        "checkpoint": {
            "format": "harness-ledger-checkpoint-v1",
            "run_id": snapshot_run_id,
            "snapshot_digest": digest,
        },
        "ledger_snapshot": plain_snapshot,
    }
    checkpoint_json = _canonical_json(checkpoint_document) + "\n"
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(checkpoint_json)

    return LedgerCheckpointResult(
        checkpoint_path=str(target),
        checkpoint_digest=digest,
        payload_digest=digest,
        checkpoint_size_bytes=len(checkpoint_json.encode("utf-8")),
        run_id=snapshot_run_id,
        ledger_snapshot=plain_snapshot,
        source_snapshot_summary=_snapshot_summary(plain_snapshot),
    )


def write_ledger_checkpoint(
    snapshot_source: object,
    *,
    checkpoint_path: str | Path | None,
    run_id: str,
) -> LedgerCheckpointResult:
    """Compatibility alias for the explicit checkpoint boundary."""

    return checkpoint_ledger_snapshot(
        snapshot_source,
        checkpoint_path=checkpoint_path,
        run_id=run_id,
    )


def _snapshot_mapping(source: object, blockers: list[str]) -> dict[str, Any] | None:
    mapped = _plain_mapping(source)
    if mapped is None:
        blockers.append("missing-ledger-snapshot")
        return None
    nested = mapped.get("ledger_snapshot")
    if nested is not None:
        mapped = _plain_mapping(nested)
        if mapped is None:
            blockers.append("malformed-ledger-snapshot")
            return None
    return mapped


def _snapshot_blockers(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    blockers: list[str] = []
    for section in ("gate_decisions", "dependencies", "audit_events", "tasks"):
        if section not in snapshot:
            blockers.append(f"missing-ledger-{section}")
            continue
        value = snapshot.get(section)
        if not isinstance(value, (list, tuple)):
            blockers.append(f"malformed-ledger-{section}")
            continue
        for item in value:
            if _plain_mapping(item) is None:
                blockers.append(f"malformed-ledger-{section}")
    for task in snapshot.get("tasks", ()):
        mapped = _plain_mapping(task)
        if mapped is None:
            blockers.append("malformed-ledger-tasks")
            continue
        status = str(mapped.get("status", "open"))
        if status not in {"done", "complete", "completed", "closed"}:
            blockers.append("unfinished-ledger-tasks")
        task_blockers = mapped.get("blockers", ())
        if task_blockers:
            blockers.append("unfinished-validation-blockers")
    metadata = _plain_mapping(snapshot.get("metadata", {}))
    if metadata is None:
        blockers.append("malformed-ledger-metadata")
    elif _contains_checkpoint_metadata(metadata):
        blockers.append("duplicate-checkpoint-metadata")
    return tuple(blockers)


def _checkpoint_path(
    checkpoint_path: str | Path | None,
    blockers: list[str],
) -> Path | None:
    if checkpoint_path is None:
        blockers.append("checkpoint-path-missing")
        return None
    if not isinstance(checkpoint_path, (str, Path)):
        blockers.append("checkpoint-path-malformed")
        return None
    text = str(checkpoint_path).strip()
    if not text:
        blockers.append("checkpoint-path-missing")
        return None
    if "://" in text or text.startswith("\\\\"):
        blockers.append("checkpoint-path-unsafe")
        return None
    target = Path(text)
    if any(part == ".." for part in target.parts):
        blockers.append("checkpoint-path-unsafe")
        return None
    return target


def _path_blockers(target: Path) -> tuple[str, ...]:
    blockers: list[str] = []
    if not target.parent.exists():
        blockers.append("checkpoint-parent-missing")
    elif not target.parent.is_dir():
        blockers.append("checkpoint-parent-not-directory")
    if target.exists():
        blockers.append("checkpoint-path-already-exists")
    if target.name in {"", ".", ".."}:
        blockers.append("checkpoint-path-unsafe")
    return tuple(blockers)


def _plain_mapping(value: object) -> dict[str, Any] | None:
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


def _plain_value(value: object) -> object:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return {str(key): _plain_value(item) for key, item in mapped.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _snapshot_summary(snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    tasks = snapshot.get("tasks", ())
    task_records = tasks if isinstance(tasks, (list, tuple)) else ()
    return {
        "run_id": str(snapshot.get("run_id", "")),
        "gate_decision_count": len(snapshot.get("gate_decisions", ()) or ()),
        "dependency_count": len(snapshot.get("dependencies", ()) or ()),
        "audit_event_count": len(snapshot.get("audit_events", ()) or ()),
        "task_count": len(task_records),
        "unfinished_task_count": sum(
            1 for task in task_records if _task_is_unfinished(task)
        ),
    }


def _task_is_unfinished(task: object) -> bool:
    mapped = _plain_mapping(task)
    if mapped is None:
        return True
    return str(mapped.get("status", "open")) not in {
        "done",
        "complete",
        "completed",
        "closed",
    }


def _required_text(value: object, name: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"missing-{name}")
        return ""
    if _is_secret_like(value):
        blockers.append(f"redacted-{name}")
        return ""
    return value.strip()


def _contains_checkpoint_metadata(value: object) -> bool:
    mapped = _plain_mapping(value)
    if mapped is not None:
        return any(
            str(key) in _CHECKPOINT_METADATA_KEYS
            or _contains_checkpoint_metadata(item)
            for key, item in mapped.items()
        )
    if isinstance(value, (list, tuple)):
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
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_like(item) for item in value)
    return False


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if value == "<redacted>":
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
