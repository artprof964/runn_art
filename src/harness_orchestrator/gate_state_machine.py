"""Pure release gate state machine for Harness media."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from harness_orchestrator.contracts import (
    EvidenceBundle,
    GateDecision,
    MediaReleaseRequest,
)


@dataclass(frozen=True)
class GateStateMachineConfig:
    """Configuration for the final release gate."""

    default_required_gates: tuple[str, ...] = (
        "evidence",
        "ai-art-safety",
        "provenance",
        "human-review",
    )
    require_evidence: bool = True
    require_evidence_items: bool = True
    require_source_ids: bool = True
    require_media_items: bool = True
    require_target_channels: bool = True


class GateStateMachine:
    """Combine Harness gate decisions into one deterministic release decision."""

    gate_name = "release-state-machine"

    def __init__(self, config: GateStateMachineConfig | None = None) -> None:
        self.config = config or GateStateMachineConfig()

    def evaluate(
        self,
        media_request: MediaReleaseRequest,
        gate_decisions: Iterable[GateDecision] = (),
        evidence_bundle: EvidenceBundle | None = None,
        config: GateStateMachineConfig | None = None,
    ) -> GateDecision:
        """Evaluate whether a media request may be released."""

        active_config = config or self.config
        required_gates = self._required_gates(media_request, active_config)
        decisions = tuple(gate_decisions)
        matching_decisions = self._matching_decisions(media_request, decisions)

        blockers: list[str] = []
        blockers.extend(self._request_blockers(media_request, active_config))
        blockers.extend(
            self._evidence_blockers(
                media_request=media_request,
                evidence_bundle=evidence_bundle,
                config=active_config,
            )
        )
        blockers.extend(
            self._gate_blockers(
                media_request=media_request,
                decisions=decisions,
                matching_decisions=matching_decisions,
                required_gates=required_gates,
            )
        )

        passed = not blockers
        status = "passed" if passed else "blocked"
        passed_gates = tuple(
            sorted(
                decision.gate_name
                for decision in matching_decisions
                if decision.passed and decision.gate_name in required_gates
            )
        )

        return GateDecision(
            decision_id=f"{self.gate_name}:{media_request.request_id}",
            work_id=media_request.work_id,
            gate_name=self.gate_name,
            passed=passed,
            reason=self._reason(passed),
            blockers=tuple(blockers),
            evidence_bundle_id=(
                evidence_bundle.bundle_id
                if evidence_bundle is not None
                else media_request.evidence_bundle_id
            ),
            metadata={
                "request_id": media_request.request_id,
                "status": status,
                "required_gates": required_gates,
                "passed_gates": passed_gates,
            },
        )

    def _request_blockers(
        self,
        media_request: MediaReleaseRequest,
        config: GateStateMachineConfig,
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        if config.require_media_items and not media_request.media_items:
            blockers.append("missing-media-items")
        if config.require_target_channels and not media_request.target_channels:
            blockers.append("missing-target-channels")
        if config.require_evidence and not media_request.evidence_bundle_id:
            blockers.append("missing-media-release-evidence")
        return tuple(blockers)

    def _evidence_blockers(
        self,
        *,
        media_request: MediaReleaseRequest,
        evidence_bundle: EvidenceBundle | None,
        config: GateStateMachineConfig,
    ) -> tuple[str, ...]:
        if not config.require_evidence:
            return ()
        if evidence_bundle is None:
            return ("missing-evidence-bundle",)
        if (
            evidence_bundle.work_id != media_request.work_id
            or evidence_bundle.bundle_id != media_request.evidence_bundle_id
        ):
            return ("evidence-bundle-mismatch",)
        blockers: list[str] = []
        if config.require_evidence_items and not evidence_bundle.evidence_items:
            blockers.append("missing-evidence-items")
        if config.require_source_ids and not evidence_bundle.source_ids:
            blockers.append("missing-source-ids")
        if blockers:
            return tuple(blockers)
        return ()

    def _gate_blockers(
        self,
        *,
        media_request: MediaReleaseRequest,
        decisions: tuple[GateDecision, ...],
        matching_decisions: tuple[GateDecision, ...],
        required_gates: tuple[str, ...],
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        mismatched_gates = self._mismatched_gates(media_request, decisions, required_gates)

        for gate_name in required_gates:
            gate_decisions = tuple(
                decision
                for decision in matching_decisions
                if decision.gate_name == gate_name
            )
            if gate_name in mismatched_gates and not gate_decisions:
                blockers.append(f"gate-work-mismatch:{gate_name}")
                continue

            if not gate_decisions:
                blockers.append(f"missing-gate:{gate_name}")
            elif any(not decision.passed for decision in gate_decisions):
                blockers.append(f"failed-gate:{gate_name}")

        return tuple(blockers)

    def _matching_decisions(
        self,
        media_request: MediaReleaseRequest,
        decisions: tuple[GateDecision, ...],
    ) -> tuple[GateDecision, ...]:
        return tuple(decision for decision in decisions if decision.work_id == media_request.work_id)

    def _mismatched_gates(
        self,
        media_request: MediaReleaseRequest,
        decisions: tuple[GateDecision, ...],
        required_gates: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    decision.gate_name
                    for decision in decisions
                    if decision.work_id != media_request.work_id
                    and decision.gate_name in required_gates
                }
            )
        )

    def _required_gates(
        self,
        media_request: MediaReleaseRequest,
        config: GateStateMachineConfig,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    gate_name
                    for gate_name in (
                        tuple(config.default_required_gates)
                        + tuple(media_request.required_gates)
                    )
                    if gate_name
                }
            )
        )

    def _reason(self, passed: bool) -> str:
        if passed:
            return "All required gates passed."
        return "Release blocked by required gates."


def evaluate(
    media_request: MediaReleaseRequest,
    gate_decisions: Iterable[GateDecision] = (),
    evidence_bundle: EvidenceBundle | None = None,
    config: GateStateMachineConfig | None = None,
) -> GateDecision:
    """Convenience wrapper for one-shot release evaluation."""

    return GateStateMachine(config=config).evaluate(
        media_request=media_request,
        gate_decisions=gate_decisions,
        evidence_bundle=evidence_bundle,
    )
