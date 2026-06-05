from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.approval_inbox as approval_inbox
from harness_orchestrator.approval_decisions import (
    ApprovalDecision,
    ApprovalDecisionRequest,
)
from harness_orchestrator.approval_inbox import (
    ApprovalInbox,
    ApprovalInboxItem,
    ApprovalInboxResult,
    approval_inbox_item,
    resolve_approval_inbox,
)


class ApprovalInboxTests(unittest.TestCase):
    def test_records_are_frozen_dataclasses(self):
        for record_type in (ApprovalInboxItem, ApprovalInboxResult, ApprovalInbox):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        with self.assertRaises(FrozenInstanceError):
            approval_inbox_item(self._request()).metadata = {"changed": True}

    def test_missing_decision_fails_closed_as_pending(self):
        result = resolve_approval_inbox(self._request())

        self.assertFalse(result.gate_decision.passed)
        self.assertEqual("blocked", result.status)
        self.assertIn("approval-pending", result.blockers)
        self.assertIsNone(result.matched_decision)

    def test_single_exact_decision_approves(self):
        request = self._request(required_reviewer="reviewer-1")
        decision = self._decision(reviewer="reviewer-1")

        result = resolve_approval_inbox(
            request,
            decisions=(decision,),
            metadata={"source": "manual"},
        )

        self.assertTrue(result.gate_decision.passed)
        self.assertEqual("approved", result.status)
        self.assertEqual(decision, result.matched_decision)
        self.assertEqual("manual", result.metadata["source"])

    def test_mismatched_decision_fails_closed_without_selecting_it(self):
        result = resolve_approval_inbox(
            self._request(),
            decisions=(self._decision(request_id="other", work_id="work-1"),),
        )

        self.assertFalse(result.gate_decision.passed)
        self.assertIn("approval-decision-missing-match", result.blockers)
        self.assertIsNone(result.matched_decision)

    def test_multiple_exact_decisions_are_ambiguous_and_blocked(self):
        request = self._request()
        first = self._decision(decision_id="decision-1")
        second = self._decision(decision_id="decision-2")

        result = resolve_approval_inbox(request, decisions=(first, second))

        self.assertFalse(result.gate_decision.passed)
        self.assertIn("approval-decision-ambiguous", result.blockers)
        self.assertIsNone(result.matched_decision)

    def test_inbox_uses_item_decisions_and_lists_pending_requests(self):
        request = self._request(required_reviewer="reviewer-1")
        inbox = ApprovalInbox(
            items=(
                approval_inbox_item(
                    request,
                    decisions=(self._decision(reviewer="reviewer-1"),),
                    metadata={"queue": "local"},
                ),
            )
        )

        result = inbox.resolve(request)

        self.assertEqual((request,), inbox.pending_requests())
        self.assertTrue(result.gate_decision.passed)
        self.assertEqual("local", result.metadata["queue"])

    def test_duplicate_inbox_items_fail_closed_as_ambiguous(self):
        request = self._request()
        inbox = ApprovalInbox(
            items=(
                approval_inbox_item(request, decisions=(self._decision(),)),
                approval_inbox_item(request, decisions=(self._decision(),)),
            )
        )

        result = inbox.resolve(request)

        self.assertFalse(result.gate_decision.passed)
        self.assertIn("approval-inbox-item-ambiguous", result.blockers)
        self.assertIsNone(result.matched_decision)

    def test_custom_gate_name_keeps_boundary_replaceable(self):
        result = resolve_approval_inbox(
            self._request(),
            decisions=(self._decision(),),
            gate_name="editorial-approval",
        )

        self.assertTrue(result.gate_decision.passed)
        self.assertEqual("editorial-approval", result.gate_decision.gate_name)

    def test_source_has_no_disallowed_integration_terms(self):
        source = inspect.getsource(approval_inbox)
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
        required_reviewer=None,
    ):
        return ApprovalDecisionRequest(
            request_id=request_id,
            work_id=work_id,
            objective="Review draft media.",
            required_reviewer=required_reviewer,
            evidence_bundle_id="bundle-1",
        )

    def _decision(
        self,
        *,
        decision_id="decision-1",
        request_id="approval-1",
        work_id="work-1",
        reviewer="reviewer",
    ):
        return ApprovalDecision(
            decision_id=decision_id,
            request_id=request_id,
            work_id=work_id,
            approved=True,
            reviewer=reviewer,
            evidence_bundle_id="bundle-1",
        )


if __name__ == "__main__":
    unittest.main()
