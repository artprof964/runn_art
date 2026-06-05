import inspect
import unittest

import harness_orchestrator.coordinator as coordinator
from harness_orchestrator.contracts import (
    EvidenceBundle,
    GateDecision,
    GovernedWorkRequest,
    MediaReleaseRequest,
)
from harness_orchestrator.coordinator import (
    ManualRunResult,
    coordinate_manual_run,
)


class CoordinatorTests(unittest.TestCase):
    def test_happy_path_with_fake_clients_and_supplemental_gates(self):
        policy = FakePolicyGateway(
            GateDecision(
                decision_id="policy:run-1",
                work_id="run-1",
                gate_name="policy",
                passed=True,
                reason="ok",
            )
        )
        evidence = FakeEvidenceGateway(
            EvidenceBundle(
                bundle_id="bundle-1",
                request_id="run-1:evidence",
                work_id="run-1",
                evidence_items=({"source_id": "src-1", "claim": "ok"},),
                source_ids=("src-1",),
                metadata={"status": "collected"},
            )
        )
        safety = FakeSafetyGateway(
            GateDecision(
                decision_id="ai-art-safety:run-1",
                work_id="run-1",
                gate_name="ai-art-safety",
                passed=True,
                reason="ok",
            )
        )

        result = coordinate_manual_run(
            work_request=self._work(),
            media_request=self._media(),
            supplemental_gate_decisions=(
                self._gate("provenance"),
                self._gate("human-review"),
            ),
            policy_gateway=policy,
            evidence_gateway=evidence,
            safety_gateway=safety,
        )

        self.assertIsInstance(result, ManualRunResult)
        self.assertTrue(result.final_decision.passed)
        self.assertEqual("bundle-1", result.media_request.evidence_bundle_id)
        self.assertEqual("bundle-1", result.evidence_bundle.bundle_id)
        self.assertEqual(
            (
                "policy",
                "evidence",
                "ai-art-safety",
                "provenance",
                "human-review",
                "release-state-machine",
            ),
            tuple(
                decision.gate_name
                for decision in result.ledger_snapshot.gate_decisions
            ),
        )
        self.assertEqual(1, policy.calls)
        self.assertEqual(1, evidence.calls)
        self.assertEqual(1, safety.calls)
        self.assertEqual(1, len(result.ledger_snapshot.dependencies))
        dependency = result.ledger_snapshot.dependencies[0]
        self.assertEqual("evidence:bundle-1", dependency.dependency_id)
        self.assertEqual("bundle-1", dependency.reference)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("passed", result.to_dict()["metadata"]["status"])

    def test_default_no_clients_blocks_inertly_and_returns_snapshot(self):
        result = coordinate_manual_run(
            work_request=self._work(),
            media_request=self._media(),
        )

        self.assertFalse(result.final_decision.passed)
        self.assertIsNone(result.evidence_bundle)
        self.assertEqual(("failed-gate:policy",), result.final_decision.blockers)
        self.assertEqual("policy", result.policy_decision.gate_name)
        self.assertEqual(
            "client-not-configured",
            result.policy_decision.blockers[0],
        )
        self.assertEqual(2, len(result.ledger_snapshot.gate_decisions))
        self.assertEqual(2, len(result.ledger_snapshot.audit_events))

    def test_policy_denial_avoids_later_gateway_calls(self):
        policy = FakePolicyGateway(
            GateDecision(
                decision_id="policy:run-1",
                work_id="run-1",
                gate_name="policy",
                passed=False,
                reason="denied",
                blockers=("policy-denied",),
            )
        )
        evidence = FakeEvidenceGateway(None)
        safety = FakeSafetyGateway(None)

        result = coordinate_manual_run(
            work_request=self._work(),
            media_request=self._media(),
            policy_gateway=policy,
            evidence_gateway=evidence,
            safety_gateway=safety,
        )

        self.assertFalse(result.final_decision.passed)
        self.assertEqual(1, policy.calls)
        self.assertEqual(0, evidence.calls)
        self.assertEqual(0, safety.calls)
        self.assertIsNone(result.evidence_decision)
        self.assertIsNone(result.safety_decision)

    def test_evidence_and_safety_errors_block_and_are_logged(self):
        result = coordinate_manual_run(
            work_request=self._work(),
            media_request=self._media(),
            supplemental_gate_decisions=(
                self._gate("provenance"),
                self._gate("human-review"),
            ),
            policy_gateway=FakePolicyGateway(
                GateDecision(
                    decision_id="policy:run-1",
                    work_id="run-1",
                    gate_name="policy",
                    passed=True,
                    reason="ok",
                )
            ),
            evidence_gateway=ExplodingEvidenceGateway(),
            safety_gateway=ExplodingSafetyGateway(),
        )

        self.assertFalse(result.final_decision.passed)
        self.assertIn("client-error", result.evidence_decision.blockers)
        self.assertIn("client-error", result.safety_decision.blockers)
        audit_messages = tuple(
            event.message for event in result.ledger_snapshot.audit_events
        )
        self.assertIn("Evidence collection blocked the run.", audit_messages)
        self.assertIn("AI-Art safety gateway client error.", audit_messages)
        self.assertEqual("blocked", result.ledger_snapshot.dependencies[0].status)

    def test_missing_supplemental_gates_keeps_final_decision_blocked(self):
        result = coordinate_manual_run(
            work_request=self._work(),
            media_request=self._media(),
            policy_gateway=FakePolicyGateway(
                GateDecision(
                    decision_id="policy:run-1",
                    work_id="run-1",
                    gate_name="policy",
                    passed=True,
                    reason="ok",
                )
            ),
            evidence_gateway=FakeEvidenceGateway(
                EvidenceBundle(
                    bundle_id="bundle-1",
                    request_id="run-1:evidence",
                    work_id="run-1",
                    evidence_items=({"source_id": "src-1"},),
                    source_ids=("src-1",),
                    metadata={"status": "collected"},
                )
            ),
            safety_gateway=FakeSafetyGateway(
                GateDecision(
                    decision_id="ai-art-safety:run-1",
                    work_id="run-1",
                    gate_name="ai-art-safety",
                    passed=True,
                    reason="ok",
                )
            ),
        )

        self.assertFalse(result.final_decision.passed)
        self.assertIn("missing-gate:provenance", result.final_decision.blockers)
        self.assertIn("missing-gate:human-review", result.final_decision.blockers)

    def test_source_guard_scan_for_forbidden_execution_terms(self):
        source = inspect.getsource(coordinator)
        forbidden = (
            "scheduler",
            "watch_social",
            "social",
            "socket",
            "subprocess",
            "thread",
            "timer",
            "http",
            "import maraca",
            "import ai_art",
            "RunLedger.save",
            "RunLedger.load",
            ".save(",
            ".load(",
        )

        for term in forbidden:
            self.assertNotIn(term, source)

    def _work(self):
        return GovernedWorkRequest(
            work_id="run-1",
            requested_by="tester",
            objective="Prepare a governed manual media run.",
        )

    def _media(self):
        return MediaReleaseRequest(
            request_id="media-1",
            work_id="run-1",
            media_items=({"id": "media-1"},),
            target_channels=("manual-review",),
        )

    def _gate(self, gate_name):
        return GateDecision(
            decision_id=f"{gate_name}:run-1",
            work_id="run-1",
            gate_name=gate_name,
            passed=True,
            reason="ok",
        )


class FakePolicyGateway:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def evaluate(self, *args, **kwargs):
        self.calls += 1
        return self.decision


class FakeEvidenceGateway:
    def __init__(self, bundle):
        self.bundle = bundle
        self.calls = 0

    def collect(self, request):
        self.calls += 1
        return self.bundle


class FakeSafetyGateway:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        return self.decision


class ExplodingEvidenceGateway:
    def collect(self, request):
        raise RuntimeError("evidence boom")


class ExplodingSafetyGateway:
    def evaluate(self, **kwargs):
        raise RuntimeError("safety boom")


if __name__ == "__main__":
    unittest.main()
