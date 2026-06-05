"""Audit-ready approval evidence for Harness human-review boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Iterable, Mapping

from harness_orchestrator.approval_decisions import ApprovalDecisionRequest
from harness_orchestrator.human_review_gate_package import HumanReviewGatePackage


Metadata = Mapping[str, object]


@dataclass(frozen=True)
class ApprovalAuditEvent:
    """Plain event data ready for a later audit recorder."""

    event_id: str
    work_id: str
    event_type: str
    status: str
    message: str = ""
    occurred_at: str = ""
    actor: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalAuditBinding:
    """Frozen proof that one human-review approval can be audited."""

    binding_id: str
    request_id: str
    work_id: str
    gate_name: str
    passed: bool
    status: str
    blockers: tuple[str, ...]
    gate_decision_id: str | None
    reviewer: str | None
    evidence_bundle_id: str | None
    media_ids: tuple[str, ...]
    canonical_payload: Metadata
    payload_digest: str
    audit_event: ApprovalAuditEvent
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_id": self.binding_id,
            "request_id": self.request_id,
            "work_id": self.work_id,
            "gate_name": self.gate_name,
            "passed": self.passed,
            "status": self.status,
            "blockers": self.blockers,
            "gate_decision_id": self.gate_decision_id,
            "reviewer": self.reviewer,
            "evidence_bundle_id": self.evidence_bundle_id,
            "media_ids": self.media_ids,
            "canonical_payload": dict(self.canonical_payload),
            "payload_digest": self.payload_digest,
            "audit_event": self.audit_event.to_dict(),
            "metadata": dict(self.metadata),
        }


def build_approval_audit_binding(
    request: ApprovalDecisionRequest,
    gate_package: HumanReviewGatePackage | None,
    *,
    gate_name: str = "human-review",
    metadata: Metadata | None = None,
) -> ApprovalAuditBinding:
    """Bind one approval request to one gate package, failing closed."""

    blockers = list(_binding_blockers(request, gate_package, gate_name))
    passed = not blockers
    status = "approved" if passed else "blocked"
    package = gate_package or _missing_package(request, gate_name)
    media_ids = _request_media_ids(request)
    canonical_payload = _canonical_payload(
        request,
        package,
        gate_name=gate_name,
        passed=passed,
        status=status,
        blockers=tuple(dict.fromkeys(blockers)),
    )
    payload_digest = _payload_digest(canonical_payload)
    binding_id = f"{gate_name}:{request.request_id}:{payload_digest}"

    return ApprovalAuditBinding(
        binding_id=binding_id,
        request_id=request.request_id,
        work_id=request.work_id,
        gate_name=gate_name,
        passed=passed,
        status=status,
        blockers=tuple(dict.fromkeys(blockers)),
        gate_decision_id=package.gate_decision_id,
        reviewer=package.reviewer,
        evidence_bundle_id=request.evidence_bundle_id,
        media_ids=media_ids,
        canonical_payload=canonical_payload,
        payload_digest=payload_digest,
        audit_event=_audit_event(
            request,
            gate_name=gate_name,
            status=status,
            blockers=tuple(dict.fromkeys(blockers)),
            payload_digest=payload_digest,
            package=package,
        ),
        metadata=dict(metadata or {}),
    )


def build_approval_audit_bindings(
    request: ApprovalDecisionRequest,
    gate_package: HumanReviewGatePackage | None,
    *,
    gate_name: str = "human-review",
    metadata: Metadata | None = None,
) -> tuple[ApprovalAuditBinding, ...]:
    """Return a single binding tuple for composers."""

    return (
        build_approval_audit_binding(
            request,
            gate_package,
            gate_name=gate_name,
            metadata=metadata,
        ),
    )


def _binding_blockers(
    request: ApprovalDecisionRequest,
    gate_package: HumanReviewGatePackage | None,
    gate_name: str,
) -> tuple[str, ...]:
    if gate_package is None:
        return ("human-review-package-missing",)

    blockers: list[str] = []
    if not gate_package.passed or gate_package.status != "approved":
        blockers.append("human-review-package-blocked")
    blockers.extend(gate_package.blockers)
    if gate_package.request_id != request.request_id:
        blockers.append("human-review-request-mismatch")
    if gate_package.work_id != request.work_id:
        blockers.append("human-review-work-mismatch")
    if gate_package.evidence_bundle_id != request.evidence_bundle_id:
        blockers.append("human-review-evidence-bundle-mismatch")
    if gate_package.media_ids != _request_media_ids(request):
        blockers.append("human-review-media-mismatch")
    if gate_package.gate_name != gate_name:
        blockers.append("human-review-gate-name-mismatch")
    if not gate_package.gate_decision_id:
        blockers.append("human-review-gate-decision-missing")
    if not gate_package.reviewer:
        blockers.append("human-review-reviewer-missing")
    return tuple(dict.fromkeys(blockers))


def _canonical_payload(
    request: ApprovalDecisionRequest,
    gate_package: HumanReviewGatePackage,
    *,
    gate_name: str,
    passed: bool,
    status: str,
    blockers: tuple[str, ...],
) -> dict[str, object]:
    return {
        "blockers": blockers,
        "evidence_bundle_id": request.evidence_bundle_id,
        "expected_gate_name": gate_name,
        "gate_decision_id": gate_package.gate_decision_id,
        "media_ids": _request_media_ids(request),
        "package_gate_name": gate_package.gate_name,
        "package_id": gate_package.package_id,
        "package_status": gate_package.status,
        "passed": passed,
        "request_id": request.request_id,
        "required_reviewer": request.required_reviewer,
        "reviewer": gate_package.reviewer,
        "status": status,
        "work_id": request.work_id,
    }


def _payload_digest(payload: Metadata) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _audit_event(
    request: ApprovalDecisionRequest,
    *,
    gate_name: str,
    status: str,
    blockers: tuple[str, ...],
    payload_digest: str,
    package: HumanReviewGatePackage,
) -> ApprovalAuditEvent:
    return ApprovalAuditEvent(
        event_id=f"approval-audit:{request.request_id}:{payload_digest}",
        work_id=request.work_id,
        event_type="approval-audit-binding",
        status=status,
        message=_audit_message(status),
        actor=package.reviewer,
        metadata={
            "request_id": request.request_id,
            "gate_name": gate_name,
            "package_gate_name": package.gate_name,
            "gate_decision_id": package.gate_decision_id,
            "evidence_bundle_id": request.evidence_bundle_id,
            "media_ids": _request_media_ids(request),
            "payload_digest": payload_digest,
            "blockers": blockers,
        },
    )


def _audit_message(status: str) -> str:
    if status == "approved":
        return "Human-review approval evidence is bound for audit."
    return "Human-review approval evidence is blocked for audit."


def _missing_package(
    request: ApprovalDecisionRequest,
    gate_name: str,
) -> HumanReviewGatePackage:
    return HumanReviewGatePackage(
        package_id=f"{gate_name}:{request.request_id}:missing",
        request_id=request.request_id,
        work_id=request.work_id,
        gate_name=gate_name,
        passed=False,
        status="blocked",
        blockers=("human-review-package-missing",),
        evidence_bundle_id=request.evidence_bundle_id,
        media_ids=_request_media_ids(request),
    )


def _request_media_ids(request: ApprovalDecisionRequest) -> tuple[str, ...]:
    return _media_ids_from_metadata(request.metadata)


def _media_ids_from_metadata(metadata: Metadata) -> tuple[str, ...]:
    if "media_ids" in metadata:
        value = metadata["media_ids"]
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Iterable):
            return tuple(str(item) for item in value)
    if "media_id" in metadata:
        return (str(metadata["media_id"]),)
    return ()
