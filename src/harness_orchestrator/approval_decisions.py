"""Pure approval decision records for Harness human-review gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping

from harness_orchestrator.contracts import GateDecision


Metadata = Mapping[str, object]


@dataclass(frozen=True)
class ApprovalDecisionRequest:
    """Plain request data needed before a human-review gate can pass."""

    request_id: str
    work_id: str
    objective: str
    required_reviewer: str | None = None
    evidence_bundle_id: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalDecision:
    """Explicit reviewer decision captured without inbox or runtime side effects."""

    decision_id: str
    request_id: str
    work_id: str
    approved: bool
    reviewer: str | None = None
    reason: str = ""
    blockers: tuple[str, ...] = ()
    evidence_bundle_id: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_gate_decision(self, gate_name: str = "human-review") -> GateDecision:
        blockers = _approval_blockers(self)
        passed = self.approved and not blockers
        return GateDecision(
            decision_id=f"{gate_name}:{self.decision_id}",
            work_id=self.work_id,
            gate_name=gate_name,
            passed=passed,
            reason=_gate_reason(self, passed),
            blockers=blockers,
            evidence_bundle_id=self.evidence_bundle_id,
            reviewer=self.reviewer,
            metadata={
                "request_id": self.request_id,
                "approval_decision_id": self.decision_id,
                "approved": self.approved,
                "status": "approved" if passed else "blocked",
                **dict(self.metadata),
            },
        )


def pending_approval_decision(
    request: ApprovalDecisionRequest,
    *,
    reason: str = "Reviewer approval is pending.",
) -> ApprovalDecision:
    """Return a fail-closed approval decision for work awaiting review."""

    return ApprovalDecision(
        decision_id=f"{request.request_id}:pending",
        request_id=request.request_id,
        work_id=request.work_id,
        approved=False,
        reviewer=request.required_reviewer,
        reason=reason,
        blockers=("approval-pending",),
        evidence_bundle_id=request.evidence_bundle_id,
        metadata={
            "status": "pending",
            "objective": request.objective,
            **dict(request.metadata),
        },
    )


def approval_gate_decision(
    request: ApprovalDecisionRequest,
    decision: ApprovalDecision | None = None,
    *,
    gate_name: str = "human-review",
) -> GateDecision:
    """Convert an explicit approval decision into a fail-closed gate decision."""

    resolved = decision or pending_approval_decision(request)
    blockers = list(_request_blockers(request, resolved))
    if blockers:
        resolved = ApprovalDecision(
            decision_id=resolved.decision_id,
            request_id=resolved.request_id,
            work_id=resolved.work_id,
            approved=resolved.approved,
            reviewer=resolved.reviewer,
            reason=resolved.reason,
            blockers=tuple((*resolved.blockers, *blockers)),
            evidence_bundle_id=resolved.evidence_bundle_id,
            metadata=dict(resolved.metadata),
        )
    return resolved.to_gate_decision(gate_name=gate_name)


def _request_blockers(
    request: ApprovalDecisionRequest,
    decision: ApprovalDecision,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if decision.request_id != request.request_id:
        blockers.append("request-mismatch")
    if decision.work_id != request.work_id:
        blockers.append("work-mismatch")
    if (
        request.required_reviewer
        and decision.reviewer
        and request.required_reviewer != decision.reviewer
    ):
        blockers.append("reviewer-mismatch")
    if request.evidence_bundle_id and (
        decision.evidence_bundle_id != request.evidence_bundle_id
    ):
        blockers.append("evidence-bundle-mismatch")
    return tuple(blockers)


def _approval_blockers(decision: ApprovalDecision) -> tuple[str, ...]:
    blockers = list(decision.blockers)
    if not decision.reviewer:
        blockers.append("missing-reviewer")
    if not decision.approved and not blockers:
        blockers.append("approval-denied")
    return tuple(dict.fromkeys(blockers))


def _gate_reason(decision: ApprovalDecision, passed: bool) -> str:
    if decision.reason:
        return decision.reason
    if passed:
        return "Human review approved the work."
    return "Human review blocked the work."
