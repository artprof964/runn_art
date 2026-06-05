"""Pure approval inbox composition over explicit local approval records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from harness_orchestrator.approval_decisions import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    approval_gate_decision,
)
from harness_orchestrator.contracts import GateDecision


Metadata = Mapping[str, object]


@dataclass(frozen=True)
class ApprovalInboxItem:
    """Local view of an approval request and its explicit candidate decisions."""

    request: ApprovalDecisionRequest
    decisions: tuple[ApprovalDecision, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "request": self.request.to_dict(),
            "decisions": tuple(decision.to_dict() for decision in self.decisions),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ApprovalInboxResult:
    """Resolution result for one approval inbox item."""

    request: ApprovalDecisionRequest
    gate_decision: GateDecision
    matched_decision: ApprovalDecision | None = None
    status: str = "blocked"
    blockers: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "request": self.request.to_dict(),
            "gate_decision": self.gate_decision.to_dict(),
            "matched_decision": (
                self.matched_decision.to_dict() if self.matched_decision else None
            ),
            "status": self.status,
            "blockers": self.blockers,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ApprovalInbox:
    """In-memory approval inbox backed only by caller-supplied records."""

    items: tuple[ApprovalInboxItem, ...] = ()
    decisions: tuple[ApprovalDecision, ...] = ()

    def pending_requests(self) -> tuple[ApprovalDecisionRequest, ...]:
        return tuple(item.request for item in self.items)

    def resolve(
        self,
        request: ApprovalDecisionRequest,
        *,
        gate_name: str = "human-review",
    ) -> ApprovalInboxResult:
        matching_items = _matching_items(request, self.items)
        if len(matching_items) > 1:
            gate_decision = _gate_with_blockers(
                approval_gate_decision(request, gate_name=gate_name),
                ["approval-inbox-item-ambiguous"],
            )
            return _result(request=request, gate_decision=gate_decision)

        item = matching_items[0] if matching_items else None
        item_decisions = item.decisions if item else ()
        return resolve_approval_inbox(
            request,
            decisions=(*self.decisions, *item_decisions),
            gate_name=gate_name,
            metadata=item.metadata if item else {},
        )


def approval_inbox_item(
    request: ApprovalDecisionRequest,
    *,
    decisions: Iterable[ApprovalDecision] = (),
    metadata: Metadata | None = None,
) -> ApprovalInboxItem:
    """Build one inert approval inbox item from explicit local records."""

    return ApprovalInboxItem(
        request=request,
        decisions=tuple(decisions),
        metadata=dict(metadata or {}),
    )


def resolve_approval_inbox(
    request: ApprovalDecisionRequest,
    *,
    decisions: Iterable[ApprovalDecision] = (),
    gate_name: str = "human-review",
    metadata: Metadata | None = None,
) -> ApprovalInboxResult:
    """Resolve a request against explicit decisions and fail closed by default."""

    candidates = tuple(decisions)
    exact_matches = tuple(_exact_matches(request, candidates))
    if len(exact_matches) == 1:
        gate_decision = approval_gate_decision(
            request,
            exact_matches[0],
            gate_name=gate_name,
        )
        return _result(
            request=request,
            gate_decision=gate_decision,
            matched_decision=exact_matches[0],
            metadata=metadata,
        )

    gate_decision = approval_gate_decision(request, gate_name=gate_name)
    blockers = list(gate_decision.blockers)
    if len(exact_matches) > 1:
        blockers.append("approval-decision-ambiguous")
    elif candidates:
        blockers.append("approval-decision-missing-match")

    if blockers != list(gate_decision.blockers):
        gate_decision = _gate_with_blockers(gate_decision, blockers)

    return _result(
        request=request,
        gate_decision=gate_decision,
        metadata=metadata,
    )


def _exact_matches(
    request: ApprovalDecisionRequest,
    decisions: tuple[ApprovalDecision, ...],
) -> tuple[ApprovalDecision, ...]:
    return tuple(
        decision
        for decision in decisions
        if decision.request_id == request.request_id
        and decision.work_id == request.work_id
        and (
            not request.evidence_bundle_id
            or decision.evidence_bundle_id == request.evidence_bundle_id
        )
    )


def _matching_items(
    request: ApprovalDecisionRequest,
    items: tuple[ApprovalInboxItem, ...],
) -> tuple[ApprovalInboxItem, ...]:
    return tuple(
        item
        for item in items
        if item.request.request_id == request.request_id
        and item.request.work_id == request.work_id
    )


def _gate_with_blockers(
    gate_decision: GateDecision,
    blockers: list[str],
) -> GateDecision:
    unique_blockers = tuple(dict.fromkeys(blockers))
    return GateDecision(
        decision_id=gate_decision.decision_id,
        work_id=gate_decision.work_id,
        gate_name=gate_decision.gate_name,
        passed=False,
        reason=gate_decision.reason,
        blockers=unique_blockers,
        evidence_bundle_id=gate_decision.evidence_bundle_id,
        reviewer=gate_decision.reviewer,
        metadata={**dict(gate_decision.metadata), "status": "blocked"},
    )


def _result(
    *,
    request: ApprovalDecisionRequest,
    gate_decision: GateDecision,
    matched_decision: ApprovalDecision | None = None,
    metadata: Metadata | None = None,
) -> ApprovalInboxResult:
    status = "approved" if gate_decision.passed else "blocked"
    return ApprovalInboxResult(
        request=request,
        gate_decision=gate_decision,
        matched_decision=matched_decision,
        status=status,
        blockers=gate_decision.blockers,
        metadata=dict(metadata or {}),
    )
