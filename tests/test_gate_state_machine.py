import inspect
import unittest

from harness_orchestrator.contracts import (
    EvidenceBundle,
    GateDecision,
    MediaReleaseRequest,
)
from harness_orchestrator.gate_state_machine import (
    GateStateMachine,
    GateStateMachineConfig,
)


DEFAULT_GATES = (
    "evidence",
    "ai-art-safety",
    "provenance",
    "human-review",
)


class GateStateMachineTests(unittest.TestCase):
    def test_all_required_gates_passing_allows_release(self) -> None:
        request = self._request()
        bundle = self._bundle()
        decisions = self._passing_decisions(DEFAULT_GATES)

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=decisions,
            evidence_bundle=bundle,
        )

        self.assertTrue(decision.passed)
        self.assertEqual(decision.gate_name, "release-state-machine")
        self.assertEqual(decision.blockers, ())
        self.assertEqual(decision.evidence_bundle_id, "bundle-001")
        self.assertEqual(decision.metadata["status"], "passed")
        self.assertEqual(decision.metadata["required_gates"], tuple(sorted(DEFAULT_GATES)))
        self.assertEqual(decision.metadata["passed_gates"], tuple(sorted(DEFAULT_GATES)))

    def test_any_failed_gate_blocks(self) -> None:
        request = self._request()
        decisions = self._passing_decisions(
            ("evidence", "ai-art-safety", "provenance")
        ) + (
            self._gate("human-review", passed=False),
        )

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=decisions,
            evidence_bundle=self._bundle(),
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("failed-gate:human-review",))
        self.assertEqual(decision.metadata["status"], "blocked")

    def test_missing_required_gate_blocks(self) -> None:
        request = self._request()
        decisions = self._passing_decisions(
            ("evidence", "ai-art-safety", "human-review")
        )

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=decisions,
            evidence_bundle=self._bundle(),
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("missing-gate:provenance",))

    def test_missing_evidence_bundle_blocks_when_required(self) -> None:
        request = self._request()

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=self._passing_decisions(DEFAULT_GATES),
        )

        self.assertFalse(decision.passed)
        self.assertIn("missing-evidence-bundle", decision.blockers)

    def test_missing_media_release_evidence_blocks_when_required(self) -> None:
        request = self._request(evidence_bundle_id=None)

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=self._passing_decisions(DEFAULT_GATES),
            evidence_bundle=self._bundle(),
        )

        self.assertFalse(decision.passed)
        self.assertIn("missing-media-release-evidence", decision.blockers)
        self.assertIn("evidence-bundle-mismatch", decision.blockers)

    def test_evidence_bundle_work_or_id_mismatch_blocks(self) -> None:
        request = self._request()
        bundle = self._bundle(bundle_id="bundle-other", work_id="work-other")

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=self._passing_decisions(DEFAULT_GATES),
            evidence_bundle=bundle,
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("evidence-bundle-mismatch",))

    def test_empty_evidence_bundle_items_or_sources_block_when_required(self) -> None:
        request = self._request()
        bundle = EvidenceBundle(
            bundle_id="bundle-001",
            request_id="evidence-001",
            work_id="work-001",
        )

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=self._passing_decisions(DEFAULT_GATES),
            evidence_bundle=bundle,
        )

        self.assertFalse(decision.passed)
        self.assertEqual(
            decision.blockers,
            ("missing-evidence-items", "missing-source-ids"),
        )

    def test_empty_evidence_bundle_detail_requirements_can_be_disabled(self) -> None:
        request = self._request()
        bundle = EvidenceBundle(
            bundle_id="bundle-001",
            request_id="evidence-001",
            work_id="work-001",
        )
        config = GateStateMachineConfig(
            require_evidence_items=False,
            require_source_ids=False,
        )

        decision = GateStateMachine(config=config).evaluate(
            media_request=request,
            gate_decisions=self._passing_decisions(DEFAULT_GATES),
            evidence_bundle=bundle,
        )

        self.assertTrue(decision.passed)

    def test_gate_decision_work_mismatch_blocks(self) -> None:
        request = self._request()
        decisions = self._passing_decisions(
            ("evidence", "ai-art-safety", "human-review")
        ) + (
            self._gate("provenance", work_id="work-other"),
        )

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=decisions,
            evidence_bundle=self._bundle(),
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("gate-work-mismatch:provenance",))

    def test_request_specific_required_gates_combine_with_config_gates(self) -> None:
        request = self._request(required_gates=("custom-approval", "human-review"))
        config = GateStateMachineConfig(default_required_gates=("human-review",))
        decisions = self._passing_decisions(("human-review", "custom-approval"))

        decision = GateStateMachine(config=config).evaluate(
            media_request=request,
            gate_decisions=decisions,
            evidence_bundle=self._bundle(),
        )

        self.assertTrue(decision.passed)
        self.assertEqual(
            decision.metadata["required_gates"],
            ("custom-approval", "human-review"),
        )

    def test_empty_media_items_and_target_channels_block(self) -> None:
        request = self._request(media_items=(), target_channels=())

        decision = GateStateMachine().evaluate(
            media_request=request,
            gate_decisions=self._passing_decisions(DEFAULT_GATES),
            evidence_bundle=self._bundle(),
        )

        self.assertFalse(decision.passed)
        self.assertEqual(
            decision.blockers[:2],
            ("missing-media-items", "missing-target-channels"),
        )

    def test_source_has_no_disallowed_terms(self) -> None:
        blocked_terms = (
            "re" + "quests",
            "ht" + "tpx",
            "so" + "cket",
            "sub" + "process",
            "pub" + "lish",
            "soc" + "ial",
            "sched" + "uler",
            "scr" + "ape",
            "ser" + "vice",
            "file" + "system",
        )
        sources = [
            inspect.getsource(
                __import__(
                    "harness_orchestrator.gate_state_machine",
                    fromlist=["unused"],
                )
            ),
            inspect.getsource(GateStateMachineTests),
        ]

        for source in sources:
            lowered = source.lower()
            for term in blocked_terms:
                self.assertNotIn(term, lowered)

    def _request(
        self,
        *,
        media_items=({"artifact_id": "asset-001", "kind": "image"},),
        target_channels=("review-feed",),
        required_gates=(),
        evidence_bundle_id="bundle-001",
    ) -> MediaReleaseRequest:
        return MediaReleaseRequest(
            request_id="media-001",
            work_id="work-001",
            media_items=media_items,
            target_channels=target_channels,
            required_gates=required_gates,
            evidence_bundle_id=evidence_bundle_id,
        )

    def _bundle(
        self,
        *,
        bundle_id="bundle-001",
        work_id="work-001",
    ) -> EvidenceBundle:
        return EvidenceBundle(
            bundle_id=bundle_id,
            request_id="evidence-001",
            work_id=work_id,
            evidence_items=({"source_id": "source-001"},),
            source_ids=("source-001",),
        )

    def _passing_decisions(self, gate_names) -> tuple[GateDecision, ...]:
        return tuple(self._gate(gate_name) for gate_name in gate_names)

    def _gate(
        self,
        gate_name: str,
        *,
        passed: bool = True,
        work_id: str = "work-001",
    ) -> GateDecision:
        return GateDecision(
            decision_id=f"{gate_name}:media-001",
            work_id=work_id,
            gate_name=gate_name,
            passed=passed,
            reason="Gate passed." if passed else "Gate blocked.",
            blockers=() if passed else ("policy",),
            evidence_bundle_id="bundle-001",
            metadata={"status": "passed" if passed else "blocked"},
        )


if __name__ == "__main__":
    unittest.main()
