from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.approval_audit_binding as approval_audit_binding
from harness_orchestrator.approval_audit_binding import (
    ApprovalAuditBinding,
    ApprovalAuditEvent,
    build_approval_audit_binding,
    build_approval_audit_bindings,
)
from harness_orchestrator.approval_decisions import ApprovalDecisionRequest
from harness_orchestrator.human_review_gate_package import HumanReviewGatePackage


class ApprovalAuditBindingTests(unittest.TestCase):
    def test_records_are_frozen_dataclasses_and_serialize_plain_data(self):
        for record_type in (ApprovalAuditBinding, ApprovalAuditEvent):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        binding = build_approval_audit_binding(
            self._request(),
            self._passed_package(),
            metadata={"source": "local"},
        )

        self.assertTrue(binding.passed)
        self.assertEqual("approved", binding.status)
        self.assertEqual("local", binding.metadata["source"])
        self.assertEqual("approval-1", binding.to_dict()["request_id"])
        self.assertEqual(
            "approval-audit-binding",
            binding.to_dict()["audit_event"]["event_type"],
        )
        self.assertEqual(("media-1",), binding.to_dict()["media_ids"])

        with self.assertRaises(FrozenInstanceError):
            binding.status = "changed"

    def test_happy_path_builds_passed_binding_with_digest_and_event_mapping(self):
        binding = build_approval_audit_binding(self._request(), self._passed_package())

        self.assertTrue(binding.passed)
        self.assertEqual((), binding.blockers)
        self.assertEqual("human-review", binding.gate_name)
        self.assertEqual("human-review:decision-1", binding.gate_decision_id)
        self.assertEqual("reviewer", binding.reviewer)
        self.assertEqual(64, len(binding.payload_digest))
        self.assertIn(binding.payload_digest, binding.binding_id)
        self.assertEqual(binding.payload_digest, binding.audit_event.metadata["payload_digest"])
        self.assertEqual("", binding.audit_event.occurred_at)
        self.assertEqual("reviewer", binding.audit_event.actor)
        self.assertEqual("bundle-1", binding.canonical_payload["evidence_bundle_id"])
        self.assertEqual("human-review", binding.canonical_payload["expected_gate_name"])
        self.assertEqual("human-review", binding.canonical_payload["package_gate_name"])

    def test_blocked_or_missing_package_fails_closed(self):
        missing = build_approval_audit_binding(self._request(), None)
        blocked = build_approval_audit_binding(
            self._request(),
            self._passed_package(
                passed=False,
                status="blocked",
                blockers=("approval-pending",),
            ),
        )

        self.assertFalse(missing.passed)
        self.assertIn("human-review-package-missing", missing.blockers)
        self.assertFalse(blocked.passed)
        self.assertIn("human-review-package-blocked", blocked.blockers)
        self.assertIn("approval-pending", blocked.blockers)

    def test_request_work_evidence_and_media_mismatches_fail_closed(self):
        binding = build_approval_audit_binding(
            self._request(),
            self._passed_package(
                request_id="approval-2",
                work_id="work-2",
                evidence_bundle_id="bundle-2",
                media_ids=("media-2",),
            ),
        )

        self.assertFalse(binding.passed)
        self.assertIn("human-review-request-mismatch", binding.blockers)
        self.assertIn("human-review-work-mismatch", binding.blockers)
        self.assertIn("human-review-evidence-bundle-mismatch", binding.blockers)
        self.assertIn("human-review-media-mismatch", binding.blockers)

    def test_wrong_gate_name_fails_closed(self):
        binding = build_approval_audit_binding(
            self._request(),
            self._passed_package(gate_name="editorial-approval"),
        )

        self.assertFalse(binding.passed)
        self.assertIn("human-review-gate-name-mismatch", binding.blockers)

    def test_missing_gate_decision_id_or_reviewer_fails_closed(self):
        missing_decision = build_approval_audit_binding(
            self._request(),
            self._passed_package(gate_decision_id=None),
        )
        missing_reviewer = build_approval_audit_binding(
            self._request(),
            self._passed_package(reviewer=None),
        )

        self.assertFalse(missing_decision.passed)
        self.assertIn("human-review-gate-decision-missing", missing_decision.blockers)
        self.assertFalse(missing_reviewer.passed)
        self.assertIn("human-review-reviewer-missing", missing_reviewer.blockers)

    def test_repeated_builder_calls_are_deterministic(self):
        first = build_approval_audit_binding(self._request(), self._passed_package())
        second = build_approval_audit_binding(self._request(), self._passed_package())

        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.payload_digest, second.payload_digest)

    def test_tuple_builder_returns_single_binding_for_composers(self):
        bindings = build_approval_audit_bindings(self._request(), self._passed_package())

        self.assertEqual(1, len(bindings))
        self.assertIsInstance(bindings[0], ApprovalAuditBinding)
        self.assertTrue(bindings[0].passed)

    def test_source_has_no_disallowed_side_effect_terms(self):
        source = inspect.getsource(approval_audit_binding)
        forbidden = (
            "import requests",
            "requests.",
            "import httpx",
            "httpx.",
            "import urllib",
            "urllib.",
            "import socket",
            "socket.",
            "import subprocess",
            "subprocess.",
            "import threading",
            "threading.",
            "sched",
            "scheduler",
            "watch_social",
            "RunLedger",
            ".save(",
            ".load(",
            "os.environ",
            "import maraca",
            "maraca.",
            "import ai_art",
            "ai_art.",
            "publish",
        )

        for term in forbidden:
            self.assertNotIn(term, source)

    def _request(
        self,
        *,
        request_id="approval-1",
        work_id="work-1",
        evidence_bundle_id="bundle-1",
        media_ids=("media-1",),
    ):
        return ApprovalDecisionRequest(
            request_id=request_id,
            work_id=work_id,
            objective="Review draft media.",
            required_reviewer="reviewer",
            evidence_bundle_id=evidence_bundle_id,
            metadata={"media_ids": media_ids},
        )

    def _passed_package(
        self,
        *,
        request_id="approval-1",
        work_id="work-1",
        gate_name="human-review",
        gate_decision_id="human-review:decision-1",
        passed=True,
        status="approved",
        blockers=(),
        evidence_bundle_id="bundle-1",
        reviewer="reviewer",
        media_ids=("media-1",),
    ):
        return HumanReviewGatePackage(
            package_id=f"{gate_name}:{request_id}:{gate_decision_id or 'missing'}",
            request_id=request_id,
            work_id=work_id,
            gate_name=gate_name,
            gate_decision_id=gate_decision_id,
            passed=passed,
            status=status,
            blockers=blockers,
            evidence_bundle_id=evidence_bundle_id,
            reviewer=reviewer,
            media_ids=media_ids,
        )


if __name__ == "__main__":
    unittest.main()
