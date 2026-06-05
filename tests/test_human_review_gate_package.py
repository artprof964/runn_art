from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.human_review_gate_package as human_review_gate_package
from harness_orchestrator.approval_decisions import (
    ApprovalDecision,
    ApprovalDecisionRequest,
)
from harness_orchestrator.approval_inbox import (
    ApprovalInboxResult,
    resolve_approval_inbox,
)
from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.human_review_gate_package import (
    HumanReviewGatePackage,
    build_human_review_gate_package,
    build_human_review_gate_packages,
)


class HumanReviewGatePackageTests(unittest.TestCase):
    def test_record_is_frozen_dataclass_and_serializes_plain_data(self):
        self.assertTrue(is_dataclass(HumanReviewGatePackage))
        self.assertTrue(HumanReviewGatePackage.__dataclass_params__.frozen)

        package = build_human_review_gate_package(
            self._request(),
            self._approved_result(),
            metadata={"package": "local"},
        )

        self.assertTrue(package.passed)
        self.assertEqual("approved", package.status)
        self.assertEqual(("media-1",), package.media_ids)
        self.assertEqual("human-review", package.gate_name)
        self.assertEqual("approval-1", package.to_dict()["request_id"])
        self.assertEqual("local", package.to_dict()["metadata"]["package"])

        with self.assertRaises(FrozenInstanceError):
            package.status = "changed"

    def test_happy_path_builds_package_from_single_passing_human_review_gate(self):
        package = build_human_review_gate_package(
            self._request(),
            (self._approved_result(),),
        )

        self.assertTrue(package.passed)
        self.assertEqual("approved", package.status)
        self.assertEqual((), package.blockers)
        self.assertEqual("human-review:decision-1", package.gate_decision_id)
        self.assertEqual("reviewer", package.reviewer)
        self.assertEqual("bundle-1", package.evidence_bundle_id)

    def test_missing_inbox_result_fails_closed(self):
        package = build_human_review_gate_package(self._request())

        self.assertFalse(package.passed)
        self.assertEqual("blocked", package.status)
        self.assertIn("human-review-inbox-result-missing", package.blockers)
        self.assertIsNone(package.gate_decision_id)

    def test_blocked_gate_fails_closed(self):
        package = build_human_review_gate_package(
            self._request(),
            resolve_approval_inbox(self._request()),
        )

        self.assertFalse(package.passed)
        self.assertIn("approval-pending", package.blockers)
        self.assertIn("human-review-inbox-result-not-approved", package.blockers)

    def test_wrong_gate_name_fails_closed(self):
        result = resolve_approval_inbox(
            self._request(),
            decisions=(self._approval_decision(),),
            gate_name="editorial-approval",
        )

        package = build_human_review_gate_package(self._request(), result)

        self.assertFalse(package.passed)
        self.assertIn("human-review-gate-name-mismatch", package.blockers)

    def test_mismatched_request_work_and_media_ids_fail_closed(self):
        request = self._request()
        gate_result = self._approved_result(
            request=self._request(
                request_id="approval-2",
                work_id="work-2",
                media_ids=("media-2",),
            ),
            decision=self._approval_decision(
                request_id="approval-2",
                work_id="work-2",
                media_ids=("media-2",),
            ),
        )

        package = build_human_review_gate_package(request, gate_result)

        self.assertFalse(package.passed)
        self.assertIn("human-review-result-request-mismatch", package.blockers)
        self.assertIn("human-review-result-work-mismatch", package.blockers)
        self.assertIn("human-review-result-media-mismatch", package.blockers)
        self.assertIn("human-review-work-mismatch", package.blockers)
        self.assertIn("human-review-request-mismatch", package.blockers)
        self.assertIn("human-review-media-mismatch", package.blockers)

    def test_inbox_request_media_mismatch_fails_even_when_gate_media_matches(self):
        caller_request = self._request(media_ids=("media-1",))
        inbox_request = self._request(media_ids=("media-2",))
        result = self._approved_result(
            request=inbox_request,
            decision=self._approval_decision(media_ids=("media-1",)),
        )

        package = build_human_review_gate_package(caller_request, result)

        self.assertFalse(package.passed)
        self.assertIn("human-review-result-media-mismatch", package.blockers)
        self.assertNotIn("human-review-media-mismatch", package.blockers)

    def test_missing_caller_media_fails_when_gate_has_unexpected_media(self):
        request = self._request(media_ids=())
        result = self._result_with_gate_media_ids(
            self._approved_result(
                request=request,
                decision=self._approval_decision(media_ids=()),
            ),
            media_ids=("media-1",),
        )

        package = build_human_review_gate_package(request, result)

        self.assertFalse(package.passed)
        self.assertIn("human-review-media-mismatch", package.blockers)

    def test_duplicate_matching_results_are_ambiguous_and_blocked(self):
        request = self._request()
        first = self._approved_result(
            decision=self._approval_decision(decision_id="decision-1")
        )
        second = self._approved_result(
            decision=self._approval_decision(decision_id="decision-2")
        )

        package = build_human_review_gate_package(request, (first, second))

        self.assertFalse(package.passed)
        self.assertIn("human-review-inbox-result-ambiguous", package.blockers)
        self.assertIn("human-review-package-ambiguous", package.blockers)

    def test_tuple_builder_returns_single_blocking_package_for_composers(self):
        packages = build_human_review_gate_packages(self._request(), ())

        self.assertEqual(1, len(packages))
        self.assertIsInstance(packages[0], HumanReviewGatePackage)
        self.assertFalse(packages[0].passed)

    def test_source_has_no_disallowed_integration_terms(self):
        source = inspect.getsource(human_review_gate_package)
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

    def _request(
        self,
        *,
        request_id="approval-1",
        work_id="work-1",
        media_ids=("media-1",),
    ):
        return ApprovalDecisionRequest(
            request_id=request_id,
            work_id=work_id,
            objective="Review draft media.",
            required_reviewer="reviewer",
            evidence_bundle_id="bundle-1",
            metadata={"media_ids": media_ids},
        )

    def _approval_decision(
        self,
        *,
        decision_id="decision-1",
        request_id="approval-1",
        work_id="work-1",
        media_ids=("media-1",),
    ):
        return ApprovalDecision(
            decision_id=decision_id,
            request_id=request_id,
            work_id=work_id,
            approved=True,
            reviewer="reviewer",
            evidence_bundle_id="bundle-1",
            metadata={"media_ids": media_ids},
        )

    def _approved_result(
        self,
        *,
        request=None,
        decision=None,
    ):
        resolved_request = request or self._request()
        return resolve_approval_inbox(
            resolved_request,
            decisions=(decision or self._approval_decision(),),
        )

    def _result_with_gate_media_ids(self, result, *, media_ids):
        gate = result.gate_decision
        updated_gate = GateDecision(
            decision_id=gate.decision_id,
            work_id=gate.work_id,
            gate_name=gate.gate_name,
            passed=gate.passed,
            reason=gate.reason,
            blockers=gate.blockers,
            evidence_bundle_id=gate.evidence_bundle_id,
            reviewer=gate.reviewer,
            metadata={**dict(gate.metadata), "media_ids": media_ids},
        )
        return ApprovalInboxResult(
            request=result.request,
            gate_decision=updated_gate,
            matched_decision=result.matched_decision,
            status=result.status,
            blockers=result.blockers,
            metadata=dict(result.metadata),
        )


if __name__ == "__main__":
    unittest.main()
