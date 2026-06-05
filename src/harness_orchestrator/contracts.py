"""Foundational records shared by Harness orchestration boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping


Metadata = Mapping[str, object]
Record = Mapping[str, object]


@dataclass(frozen=True)
class GovernedWorkRequest:
    """Governed unit of work accepted by the Harness control plane."""

    work_id: str
    requested_by: str
    objective: str
    channel: str = "manual"
    priority: str = "normal"
    policy_scope: str = "default"
    service_targets: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRequest:
    """Request envelope for source-backed evidence collection."""

    request_id: str
    work_id: str
    query: str
    connector_name: str = "maraca"
    required_sources: tuple[str, ...] = ()
    excluded_sources: tuple[str, ...] = ()
    freshness: str = "current"
    max_items: int = 10
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceBundle:
    """Evidence returned by a replaceable evidence connector."""

    bundle_id: str
    request_id: str
    work_id: str
    connector_name: str = "maraca"
    evidence_items: tuple[Record, ...] = ()
    source_ids: tuple[str, ...] = ()
    validation_notes: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MediaReleaseRequest:
    """Media release envelope evaluated before any publish action exists."""

    request_id: str
    work_id: str
    media_items: tuple[Record, ...]
    target_channels: tuple[str, ...] = ()
    required_gates: tuple[str, ...] = ()
    evidence_bundle_id: str | None = None
    connector_name: str = "ai-art"
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GateDecision:
    """Result from a policy, evidence, review, or release gate."""

    decision_id: str
    work_id: str
    gate_name: str
    passed: bool
    reason: str
    blockers: tuple[str, ...] = ()
    evidence_bundle_id: str | None = None
    reviewer: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
