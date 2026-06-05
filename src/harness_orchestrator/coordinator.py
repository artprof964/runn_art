"""Manual Harness-only coordinator for one governed work run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from harness_orchestrator.adapters.ai_art_safety_gateway import AIArtSafetyGateway
from harness_orchestrator.adapters.maraca_evidence_gateway import MaracaEvidenceGateway
from harness_orchestrator.adapters.policy_gateway import PolicyGateway
from harness_orchestrator.contracts import (
    EvidenceBundle,
    EvidenceRequest,
    GateDecision,
    GovernedWorkRequest,
    MediaReleaseRequest,
)
from harness_orchestrator.gate_state_machine import GateStateMachine
from harness_orchestrator.run_ledger import RunLedger, RunLedgerSnapshot


@dataclass(frozen=True)
class ManualRunResult:
    """Plain result for a single manual governed work run."""

    work_request: GovernedWorkRequest
    media_request: MediaReleaseRequest
    evidence_request: EvidenceRequest
    policy_decision: GateDecision
    evidence_bundle: EvidenceBundle | None
    evidence_decision: GateDecision | None
    safety_decision: GateDecision | None
    supplemental_gate_decisions: tuple[GateDecision, ...]
    final_decision: GateDecision
    ledger_snapshot: RunLedgerSnapshot
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def coordinate_manual_run(
    *,
    work_request: GovernedWorkRequest,
    media_request: MediaReleaseRequest,
    evidence_request: EvidenceRequest | None = None,
    supplemental_gate_decisions: Iterable[GateDecision] = (),
    policy_gateway: PolicyGateway | None = None,
    evidence_gateway: MaracaEvidenceGateway | None = None,
    safety_gateway: AIArtSafetyGateway | None = None,
    gate_state_machine: GateStateMachine | None = None,
    ledger: RunLedger | None = None,
) -> ManualRunResult:
    """Coordinate one manual run through injected Harness boundaries."""

    active_ledger = ledger or RunLedger(run_id=work_request.work_id)
    active_policy = policy_gateway or PolicyGateway()
    active_evidence = evidence_gateway or MaracaEvidenceGateway()
    active_safety = safety_gateway or AIArtSafetyGateway()
    active_state_machine = gate_state_machine or GateStateMachine()
    resolved_evidence_request = evidence_request or _default_evidence_request(work_request)
    supplemental = tuple(supplemental_gate_decisions)

    policy_decision = active_policy.evaluate(
        work_request,
        request_id=f"{work_request.work_id}:policy",
        operation="manual-run-policy",
    )
    _record_gate(active_ledger, policy_decision)

    if not policy_decision.passed:
        final_decision = _blocked_final(work_request, policy_decision)
        _record_gate(active_ledger, final_decision)
        return _result(
            work_request=work_request,
            media_request=media_request,
            evidence_request=resolved_evidence_request,
            policy_decision=policy_decision,
            evidence_bundle=None,
            evidence_decision=None,
            safety_decision=None,
            supplemental_gate_decisions=supplemental,
            final_decision=final_decision,
            ledger=active_ledger,
            status="policy_blocked",
        )

    try:
        evidence_bundle = active_evidence.collect(resolved_evidence_request)
    except Exception:
        evidence_bundle = _failed_evidence_bundle(resolved_evidence_request)
    evidence_decision = _evidence_decision(evidence_bundle)
    _record_gate(active_ledger, evidence_decision)
    active_ledger.record_dependency(
        dependency_id=f"evidence:{evidence_bundle.bundle_id}",
        work_id=evidence_bundle.work_id,
        reference=evidence_bundle.bundle_id,
        order=10,
        dependency_type="evidence",
        status="ready" if evidence_decision.passed else "blocked",
        metadata={
            "request_id": evidence_bundle.request_id,
            "connector_name": evidence_bundle.connector_name,
        },
    )

    try:
        safety_decision = active_safety.evaluate(
            request_id=f"{work_request.work_id}:safety",
            work_id=work_request.work_id,
            operation="manual-run-safety",
            payload={
                "media_items": media_request.media_items,
                "target_channels": media_request.target_channels,
                "evidence_bundle_id": evidence_bundle.bundle_id,
            },
        )
    except Exception:
        safety_decision = _failed_safety_decision(work_request)
    _record_gate(active_ledger, safety_decision)

    for decision in supplemental:
        _record_gate(active_ledger, decision)

    final_decision = active_state_machine.evaluate(
        media_request=_media_with_evidence(media_request, evidence_bundle),
        gate_decisions=(
            policy_decision,
            evidence_decision,
            safety_decision,
            *supplemental,
        ),
        evidence_bundle=evidence_bundle,
    )
    _record_gate(active_ledger, final_decision)

    return _result(
        work_request=work_request,
        media_request=_media_with_evidence(media_request, evidence_bundle),
        evidence_request=resolved_evidence_request,
        policy_decision=policy_decision,
        evidence_bundle=evidence_bundle,
        evidence_decision=evidence_decision,
        safety_decision=safety_decision,
        supplemental_gate_decisions=supplemental,
        final_decision=final_decision,
        ledger=active_ledger,
        status="passed" if final_decision.passed else "blocked",
    )


def run_manual_governed_work(**kwargs: Any) -> ManualRunResult:
    """Compatibility alias for the manual coordinator."""

    return coordinate_manual_run(**kwargs)


def _default_evidence_request(work_request: GovernedWorkRequest) -> EvidenceRequest:
    return EvidenceRequest(
        request_id=f"{work_request.work_id}:evidence",
        work_id=work_request.work_id,
        query=work_request.objective,
        metadata={"channel": work_request.channel},
    )


def _evidence_decision(evidence_bundle: EvidenceBundle) -> GateDecision:
    status = str(evidence_bundle.metadata.get("status", ""))
    blockers: list[str] = []
    if status in {"client_error", "not_configured"}:
        blockers.append(status.replace("_", "-"))
    if not evidence_bundle.evidence_items:
        blockers.append("missing-evidence-items")
    if not evidence_bundle.source_ids:
        blockers.append("missing-source-ids")

    passed = not blockers
    return GateDecision(
        decision_id=f"evidence:{evidence_bundle.request_id}",
        work_id=evidence_bundle.work_id,
        gate_name="evidence",
        passed=passed,
        reason=(
            "Evidence collection passed."
            if passed
            else "Evidence collection blocked the run."
        ),
        blockers=tuple(blockers),
        evidence_bundle_id=evidence_bundle.bundle_id,
        metadata={
            "request_id": evidence_bundle.request_id,
            "status": "passed" if passed else "blocked",
            "connector_status": status,
            "validation_notes": evidence_bundle.validation_notes,
        },
    )


def _failed_evidence_bundle(evidence_request: EvidenceRequest) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id=f"{evidence_request.connector_name}:{evidence_request.request_id}:empty",
        request_id=evidence_request.request_id,
        work_id=evidence_request.work_id,
        connector_name=evidence_request.connector_name,
        validation_notes=("Evidence gateway client error.",),
        metadata={"status": "client_error"},
    )


def _failed_safety_decision(work_request: GovernedWorkRequest) -> GateDecision:
    return GateDecision(
        decision_id=f"ai-art-safety:{work_request.work_id}:safety",
        work_id=work_request.work_id,
        gate_name="ai-art-safety",
        passed=False,
        reason="AI-Art safety gateway client error.",
        blockers=("client-error",),
        metadata={"status": "client_error"},
    )


def _media_with_evidence(
    media_request: MediaReleaseRequest,
    evidence_bundle: EvidenceBundle,
) -> MediaReleaseRequest:
    if media_request.evidence_bundle_id == evidence_bundle.bundle_id:
        return media_request
    return MediaReleaseRequest(
        request_id=media_request.request_id,
        work_id=media_request.work_id,
        media_items=tuple(media_request.media_items),
        target_channels=tuple(media_request.target_channels),
        required_gates=tuple(media_request.required_gates),
        evidence_bundle_id=evidence_bundle.bundle_id,
        connector_name=media_request.connector_name,
        metadata=dict(media_request.metadata),
    )


def _blocked_final(
    work_request: GovernedWorkRequest,
    blocking_decision: GateDecision,
) -> GateDecision:
    return GateDecision(
        decision_id=f"manual-run-final:{work_request.work_id}",
        work_id=work_request.work_id,
        gate_name="manual-run-final",
        passed=False,
        reason="Manual run blocked before final release gate.",
        blockers=(f"failed-gate:{blocking_decision.gate_name}",),
        metadata={
            "status": "blocked",
            "blocking_decision_id": blocking_decision.decision_id,
        },
    )


def _record_gate(ledger: RunLedger, decision: GateDecision) -> None:
    ledger.record_gate_decision(decision)
    status = "passed" if decision.passed else "blocked"
    ledger.record_audit_event(
        work_id=decision.work_id,
        event_id=f"{decision.decision_id}:audit",
        event_type=f"gate:{decision.gate_name}",
        status=status,
        message=decision.reason,
        metadata={
            "blockers": decision.blockers,
            "decision_id": decision.decision_id,
        },
    )


def _result(
    *,
    work_request: GovernedWorkRequest,
    media_request: MediaReleaseRequest,
    evidence_request: EvidenceRequest,
    policy_decision: GateDecision,
    evidence_bundle: EvidenceBundle | None,
    evidence_decision: GateDecision | None,
    safety_decision: GateDecision | None,
    supplemental_gate_decisions: tuple[GateDecision, ...],
    final_decision: GateDecision,
    ledger: RunLedger,
    status: str,
) -> ManualRunResult:
    return ManualRunResult(
        work_request=work_request,
        media_request=media_request,
        evidence_request=evidence_request,
        policy_decision=policy_decision,
        evidence_bundle=evidence_bundle,
        evidence_decision=evidence_decision,
        safety_decision=safety_decision,
        supplemental_gate_decisions=supplemental_gate_decisions,
        final_decision=final_decision,
        ledger_snapshot=ledger.snapshot(),
        metadata={"status": status},
    )
