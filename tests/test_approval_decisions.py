from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.approval_decisions as approval_decisions
from harness_orchestrator.approval_decisions import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    approval_gate_decision,
    pending_approval_decision,
)
from harness_orchestrator.contracts import GateDecision


class ApprovalDecisionTests(unittest.TestCase):
    def test_records_are_frozen_dataclasses(self):
        for record_type in (ApprovalDecisionRequest, ApprovalDecision):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        with self.assertRaises(FrozenInstanceError):
            ApprovalDecisionRequest(
                request_id="approval-1",
                work_id="work-1",
                objective="Review draft media.",
            ).objective = "changed"

    def test_pending_approval_fails_closed(self):
        request = ApprovalDecisionRequest(
            request_id="approval-1",
            work_id="work-1",
            objective="Review draft media.",
            required_reviewer="human-1",
            evidence_bundle_id="bundle-1",
        )

        decision = pending_approval_decision(request)
        gate = approval_gate_decision(request)

        self.assertFalse(decision.approved)
        self.assertEqual(("approval-pending",), decision.blockers)
        self.assertFalse(gate.passed)
        self.assertEqual("human-review", gate.gate_name)
        self.assertIn("approval-pending", gate.blockers)
        self.assertEqual("human-1", gate.reviewer)
        self.assertEqual("bundle-1", gate.evidence_bundle_id)

    def test_explicit_approval_maps_to_passing_human_review_gate(self):
        request = ApprovalDecisionRequest(
            request_id="approval-1",
            work_id="work-1",
            objective="Review draft media.",
            required_reviewer="human-1",
            evidence_bundle_id="bundle-1",
            metadata={"channel": "manual"},
        )
        decision = ApprovalDecision(
            decision_id="decision-1",
            request_id="approval-1",
            work_id="work-1",
            approved=True,
            reviewer="human-1",
            reason="Approved after source check.",
            evidence_bundle_id="bundle-1",
            metadata={"ticket": "T-1"},
        )

        gate = approval_gate_decision(request, decision)

        self.assertIsInstance(gate, GateDecision)
        self.assertTrue(gate.passed)
        self.assertEqual("human-review:decision-1", gate.decision_id)
        self.assertEqual("Approved after source check.", gate.reason)
        self.assertEqual((), gate.blockers)
        self.assertEqual("T-1", gate.metadata["ticket"])

    def test_denial_and_missing_reviewer_block_release(self):
        request = ApprovalDecisionRequest(
            request_id="approval-1",
            work_id="work-1",
            objective="Review draft media.",
        )
        decision = ApprovalDecision(
            decision_id="decision-1",
            request_id="approval-1",
            work_id="work-1",
            approved=False,
        )

        gate = approval_gate_decision(request, decision)

        self.assertFalse(gate.passed)
        self.assertIn("missing-reviewer", gate.blockers)
        self.assertNotIn("approval-denied", gate.blockers)

    def test_request_mismatch_blocks_even_when_decision_approves(self):
        request = ApprovalDecisionRequest(
            request_id="approval-1",
            work_id="work-1",
            objective="Review draft media.",
            required_reviewer="human-1",
            evidence_bundle_id="bundle-1",
        )
        decision = ApprovalDecision(
            decision_id="decision-1",
            request_id="other-approval",
            work_id="work-2",
            approved=True,
            reviewer="human-2",
            evidence_bundle_id="bundle-2",
        )

        gate = approval_gate_decision(request, decision)

        self.assertFalse(gate.passed)
        self.assertIn("request-mismatch", gate.blockers)
        self.assertIn("work-mismatch", gate.blockers)
        self.assertIn("reviewer-mismatch", gate.blockers)
        self.assertIn("evidence-bundle-mismatch", gate.blockers)

    def test_custom_gate_name_keeps_boundary_replaceable(self):
        request = ApprovalDecisionRequest(
            request_id="approval-1",
            work_id="work-1",
            objective="Review draft media.",
        )
        decision = ApprovalDecision(
            decision_id="decision-1",
            request_id="approval-1",
            work_id="work-1",
            approved=True,
            reviewer="reviewer",
        )

        gate = approval_gate_decision(
            request,
            decision,
            gate_name="editorial-approval",
        )

        self.assertEqual("editorial-approval", gate.gate_name)
        self.assertTrue(gate.passed)

    def test_source_has_no_disallowed_integration_terms(self):
        source = inspect.getsource(approval_decisions)
        forbidden = (
            "requests",
            "httpx",
            "urllib",
            "socket",
            "subprocess",
            "threading",
            "scheduler",
            "watch_social",
            "RunLedger.save",
            "RunLedger.load",
            "os.environ",
            "import maraca",
            "import ai_art",
        )

        for term in forbidden:
            self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()
