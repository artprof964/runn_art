"""Pure human-review gate package records for Harness release boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping

from harness_orchestrator.approval_decisions import ApprovalDecisionRequest
from harness_orchestrator.approval_inbox import ApprovalInboxResult
from harness_orchestrator.contracts import GateDecision


Metadata = Mapping[str, object]


@dataclass(frozen=True)
class HumanReviewGatePackage:
    """Frozen, caller-supplied data proving a human-review gate is usable."""

    package_id: str
    request_id: str
    work_id: str
    gate_name: str
    gate_decision_id: str | None = None
    passed: bool = False
    status: str = "blocked"
    blockers: tuple[str, ...] = ()
    evidence_bundle_id: str | None = None
    reviewer: str | None = None
    media_ids: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_human_review_gate_package(
    request: ApprovalDecisionRequest,
    inbox_results: ApprovalInboxResult | Iterable[ApprovalInboxResult] = (),
    *,
    gate_name: str = "human-review",
    metadata: Metadata | None = None,
) -> HumanReviewGatePackage:
    """Build one fail-closed package from explicit approval inbox result data."""

    results = _as_result_tuple(inbox_results)
    blockers = list(_result_collection_blockers(request, results))
    matches = tuple(_matching_passed_results(request, results, gate_name))

    if len(matches) == 1 and not blockers:
        gate_decision = matches[0].gate_decision
        return HumanReviewGatePackage(
            package_id=_package_id(request, gate_decision, gate_name),
            request_id=request.request_id,
            work_id=request.work_id,
            gate_name=gate_name,
            gate_decision_id=gate_decision.decision_id,
            passed=True,
            status="approved",
            blockers=(),
            evidence_bundle_id=gate_decision.evidence_bundle_id,
            reviewer=gate_decision.reviewer,
            media_ids=_request_media_ids(request),
            metadata={
                "source": "approval-inbox-result",
                **dict(gate_decision.metadata),
                **dict(metadata or {}),
            },
        )

    if len(matches) > 1:
        blockers.append("human-review-package-ambiguous")
    elif not matches:
        blockers.extend(_missing_match_blockers(request, results, gate_name))

    return _blocked_package(
        request,
        gate_name=gate_name,
        blockers=tuple(dict.fromkeys(blockers or ["human-review-package-blocked"])),
        metadata=metadata,
    )


def build_human_review_gate_packages(
    request: ApprovalDecisionRequest,
    inbox_results: Iterable[ApprovalInboxResult] = (),
    *,
    gate_name: str = "human-review",
    metadata: Metadata | None = None,
) -> tuple[HumanReviewGatePackage, ...]:
    """Return a single-package tuple for callers that compose tuple records."""

    return (
        build_human_review_gate_package(
            request,
            inbox_results,
            gate_name=gate_name,
            metadata=metadata,
        ),
    )


def _as_result_tuple(
    inbox_results: ApprovalInboxResult | Iterable[ApprovalInboxResult],
) -> tuple[ApprovalInboxResult, ...]:
    if isinstance(inbox_results, ApprovalInboxResult):
        return (inbox_results,)
    return tuple(inbox_results)


def _matching_passed_results(
    request: ApprovalDecisionRequest,
    results: tuple[ApprovalInboxResult, ...],
    gate_name: str,
) -> tuple[ApprovalInboxResult, ...]:
    return tuple(
        result
        for result in results
        if _result_matches_request(result, request)
        and _gate_matches_request(result.gate_decision, request, gate_name)
        and result.gate_decision.passed
        and result.status == "approved"
    )


def _result_collection_blockers(
    request: ApprovalDecisionRequest,
    results: tuple[ApprovalInboxResult, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not results:
        blockers.append("human-review-inbox-result-missing")
    matching_results = tuple(
        result for result in results if _result_matches_request(result, request)
    )
    if len(matching_results) > 1:
        blockers.append("human-review-inbox-result-ambiguous")
    return tuple(blockers)


def _missing_match_blockers(
    request: ApprovalDecisionRequest,
    results: tuple[ApprovalInboxResult, ...],
    gate_name: str,
) -> tuple[str, ...]:
    if not results:
        return ()

    blockers: list[str] = []
    for result in results:
        blockers.extend(_result_blockers(result, request, gate_name))
    return tuple(dict.fromkeys(blockers or ["human-review-gate-missing-match"]))


def _result_blockers(
    result: ApprovalInboxResult,
    request: ApprovalDecisionRequest,
    gate_name: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    blockers.extend(_result_request_blockers(result, request))
    blockers.extend(_gate_blockers(result.gate_decision, request, gate_name))
    if result.status != "approved":
        blockers.append("human-review-inbox-result-not-approved")
    return tuple(blockers)


def _result_matches_request(
    result: ApprovalInboxResult,
    request: ApprovalDecisionRequest,
) -> bool:
    return not _result_request_blockers(result, request)


def _result_request_blockers(
    result: ApprovalInboxResult,
    request: ApprovalDecisionRequest,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if result.request.request_id != request.request_id:
        blockers.append("human-review-result-request-mismatch")
    if result.request.work_id != request.work_id:
        blockers.append("human-review-result-work-mismatch")
    if result.request.evidence_bundle_id != request.evidence_bundle_id:
        blockers.append("human-review-result-evidence-bundle-mismatch")
    if _request_media_ids(result.request) != _request_media_ids(request):
        blockers.append("human-review-result-media-mismatch")
    return tuple(blockers)


def _gate_matches_request(
    gate_decision: GateDecision,
    request: ApprovalDecisionRequest,
    gate_name: str,
) -> bool:
    return not _gate_blockers(gate_decision, request, gate_name)


def _gate_blockers(
    gate_decision: GateDecision,
    request: ApprovalDecisionRequest,
    gate_name: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if gate_decision.gate_name != gate_name:
        blockers.append("human-review-gate-name-mismatch")
    if gate_decision.work_id != request.work_id:
        blockers.append("human-review-work-mismatch")
    if gate_decision.metadata.get("request_id") != request.request_id:
        blockers.append("human-review-request-mismatch")
    if request.evidence_bundle_id != gate_decision.evidence_bundle_id:
        blockers.append("human-review-evidence-bundle-mismatch")

    if _request_media_ids(request) != _gate_media_ids(gate_decision):
        blockers.append("human-review-media-mismatch")
    if not gate_decision.passed:
        blockers.extend(gate_decision.blockers or ("human-review-gate-blocked",))
    return tuple(blockers)


def _blocked_package(
    request: ApprovalDecisionRequest,
    *,
    gate_name: str,
    blockers: tuple[str, ...],
    metadata: Metadata | None,
) -> HumanReviewGatePackage:
    return HumanReviewGatePackage(
        package_id=f"{gate_name}:{request.request_id}:blocked",
        request_id=request.request_id,
        work_id=request.work_id,
        gate_name=gate_name,
        passed=False,
        status="blocked",
        blockers=blockers,
        evidence_bundle_id=request.evidence_bundle_id,
        media_ids=_request_media_ids(request),
        metadata=dict(metadata or {}),
    )


def _package_id(
    request: ApprovalDecisionRequest,
    gate_decision: GateDecision,
    gate_name: str,
) -> str:
    return f"{gate_name}:{request.request_id}:{gate_decision.decision_id}"


def _request_media_ids(request: ApprovalDecisionRequest) -> tuple[str, ...]:
    return _media_ids_from_metadata(request.metadata)


def _gate_media_ids(gate_decision: GateDecision) -> tuple[str, ...]:
    return _media_ids_from_metadata(gate_decision.metadata)


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
