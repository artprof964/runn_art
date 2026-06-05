from dataclasses import FrozenInstanceError, is_dataclass, replace
import inspect
import json
import unittest

import harness_orchestrator.approval_audit_ledger as approval_audit_ledger
from harness_orchestrator.approval_audit_binding import (
    ApprovalAuditBinding,
    ApprovalAuditEvent,
)
from harness_orchestrator.approval_audit_ledger import (
    ApprovalAuditLedgerResult,
    record_approval_audit_bindings,
)
from harness_orchestrator.run_ledger import AuditEvent, RunLedger


class ApprovalAuditLedgerTests(unittest.TestCase):
    def test_happy_path_records_passed_binding_into_injected_ledger(self) -> None:
        ledger = RunLedger(run_id="run-001")
        binding = self._binding()

        result = record_approval_audit_bindings((binding,), ledger=ledger)

        self.assertEqual(("approval-audit:approval-1:digest-1",), result.recorded_event_ids)
        self.assertEqual((), result.blockers)
        self.assertEqual(result.ledger_snapshot, ledger.snapshot())
        recorded = ledger.snapshot().audit_events[0]
        self.assertIsInstance(recorded, AuditEvent)
        self.assertEqual(binding.audit_event.event_id, recorded.event_id)
        self.assertEqual(binding.audit_event.work_id, recorded.work_id)
        self.assertEqual(binding.audit_event.event_type, recorded.event_type)
        self.assertEqual(binding.audit_event.status, recorded.status)
        self.assertEqual(binding.audit_event.message, recorded.message)
        self.assertEqual(binding.audit_event.occurred_at, recorded.occurred_at)
        self.assertEqual(binding.audit_event.actor, recorded.actor)
        self.assertEqual(dict(binding.audit_event.metadata), dict(recorded.metadata))

    def test_blocked_or_unpassed_binding_is_not_recorded(self) -> None:
        ledger = RunLedger(run_id="run-001")
        binding = self._binding(
            passed=False,
            status="blocked",
            blockers=("approval-pending",),
        )

        result = record_approval_audit_bindings((binding,), ledger=ledger)

        self.assertEqual((), result.recorded_event_ids)
        self.assertIn("approval-audit-binding-blocked", result.blockers)
        self.assertEqual((binding.binding_id,), result.skipped_binding_ids)
        self.assertEqual((binding.payload_digest,), result.skipped_payload_digests)
        self.assertEqual((binding.audit_event.event_id,), result.skipped_event_ids)
        self.assertEqual((), ledger.snapshot().audit_events)

    def test_duplicate_payload_digest_or_event_id_is_not_recorded_twice(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._binding(binding_id="binding-1", payload_digest="digest-shared")
        duplicate_digest = self._binding(
            binding_id="binding-2",
            payload_digest="digest-shared",
            event_id="approval-audit:approval-2:digest-shared",
            request_id="approval-2",
        )
        duplicate_event_id = self._binding(
            binding_id="binding-3",
            payload_digest="digest-3",
            event_id=first.audit_event.event_id,
            request_id="approval-3",
        )

        result = record_approval_audit_bindings(
            (first, duplicate_digest, duplicate_event_id),
            ledger=ledger,
        )

        self.assertEqual((first.audit_event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            (first.audit_event.event_id,),
            tuple(event.event_id for event in ledger.snapshot().audit_events),
        )
        self.assertIn("approval-audit-payload-digest-duplicate", result.blockers)
        self.assertIn("approval-audit-event-id-duplicate", result.blockers)
        self.assertEqual(("binding-2", "binding-3"), result.skipped_binding_ids)
        self.assertEqual(("digest-shared", "digest-3"), result.skipped_payload_digests)
        self.assertEqual(
            (duplicate_digest.audit_event.event_id, duplicate_event_id.audit_event.event_id),
            result.skipped_event_ids,
        )

    def test_event_id_already_present_in_ledger_is_not_recorded_again(self) -> None:
        existing = self._binding()
        ledger = RunLedger(run_id="run-001")
        ledger.record_audit_event(event=AuditEvent(**existing.audit_event.to_dict()))

        result = record_approval_audit_bindings((existing,), ledger=ledger)

        self.assertEqual((), result.recorded_event_ids)
        self.assertIn("approval-audit-event-id-already-recorded", result.blockers)
        self.assertEqual((existing.audit_event.event_id,), result.skipped_event_ids)
        self.assertEqual(1, len(ledger.snapshot().audit_events))

    def test_payload_digest_already_present_in_ledger_is_not_recorded_again(self) -> None:
        existing = self._binding()
        duplicate_digest = self._binding(
            binding_id="binding-existing-digest",
            event_id="approval-audit:approval-2:digest-1",
            request_id="approval-2",
            payload_digest=existing.payload_digest,
        )
        ledger = RunLedger(run_id="run-001")
        ledger.record_audit_event(event=AuditEvent(**existing.audit_event.to_dict()))

        result = record_approval_audit_bindings((duplicate_digest,), ledger=ledger)

        self.assertEqual((), result.recorded_event_ids)
        self.assertIn("approval-audit-payload-digest-already-recorded", result.blockers)
        self.assertEqual((duplicate_digest.binding_id,), result.skipped_binding_ids)
        self.assertEqual((duplicate_digest.payload_digest,), result.skipped_payload_digests)
        self.assertEqual(1, len(ledger.snapshot().audit_events))

    def test_missing_ledger_fails_closed_without_side_effects(self) -> None:
        result = record_approval_audit_bindings((self._binding(),), ledger=None)

        self.assertEqual((), result.recorded_event_ids)
        self.assertEqual(("ledger-missing",), result.blockers)
        self.assertIsNone(result.ledger_snapshot)

    def test_empty_binding_input_fails_closed(self) -> None:
        ledger = RunLedger(run_id="run-001")

        result = record_approval_audit_bindings((), ledger=ledger)

        self.assertEqual((), result.recorded_event_ids)
        self.assertEqual(("approval-audit-bindings-empty",), result.blockers)
        self.assertEqual((), ledger.snapshot().audit_events)
        self.assertEqual(result.ledger_snapshot, ledger.snapshot())

    def test_wrong_type_input_fails_closed_without_recording(self) -> None:
        ledger = RunLedger(run_id="run-001")

        result = record_approval_audit_bindings(("not-a-binding",), ledger=ledger)

        self.assertEqual((), result.recorded_event_ids)
        self.assertEqual(("approval-audit-binding-wrong-type",), result.blockers)
        self.assertEqual((), ledger.snapshot().audit_events)

    def test_missing_audit_event_or_event_id_is_skipped(self) -> None:
        ledger = RunLedger(run_id="run-001")
        missing_event = replace(self._binding(binding_id="binding-missing"), audit_event=None)
        missing_event_id = self._binding(binding_id="binding-no-id", event_id="")

        result = record_approval_audit_bindings(
            (missing_event, missing_event_id),
            ledger=ledger,
        )

        self.assertIn("approval-audit-event-missing", result.blockers)
        self.assertIn("approval-audit-event-id-missing", result.blockers)
        self.assertEqual(("binding-missing", "binding-no-id"), result.skipped_binding_ids)
        self.assertEqual((), ledger.snapshot().audit_events)

    def test_result_is_frozen_dataclass_and_to_dict_is_plain(self) -> None:
        self.assertTrue(is_dataclass(ApprovalAuditLedgerResult))
        self.assertTrue(ApprovalAuditLedgerResult.__dataclass_params__.frozen)
        result = record_approval_audit_bindings((self._binding(),), ledger=RunLedger("run-001"))

        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)

        payload = result.to_dict()
        self.assertEqual(result.recorded_event_ids, payload["recorded_event_ids"])
        self.assertEqual("run-001", payload["ledger_snapshot"]["run_id"])
        json.dumps(payload, sort_keys=True)

    def test_source_guard_has_no_forbidden_side_effects_or_runtime_imports(self) -> None:
        source = inspect.getsource(approval_audit_ledger)
        forbidden = (
            "build_approval_audit",
            "ApprovalDecisionRequest",
            "HumanReviewGatePackage",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "subprocess",
            "threading",
            "sched",
            "scheduler",
            "watch_social",
            ".save(",
            ".load(",
            "Path(",
            "open(",
            "os.environ",
            "import maraca",
            "maraca.",
            "import ai_art",
            "ai_art.",
            "service",
            "credential",
            "publish",
            "random",
            "datetime",
        )

        for term in forbidden:
            self.assertNotIn(term, source)

    def _binding(
        self,
        *,
        binding_id="human-review:approval-1:digest-1",
        request_id="approval-1",
        work_id="work-1",
        passed=True,
        status="approved",
        blockers=(),
        payload_digest="digest-1",
        event_id="approval-audit:approval-1:digest-1",
    ) -> ApprovalAuditBinding:
        return ApprovalAuditBinding(
            binding_id=binding_id,
            request_id=request_id,
            work_id=work_id,
            gate_name="human-review",
            passed=passed,
            status=status,
            blockers=blockers,
            gate_decision_id="human-review:decision-1",
            reviewer="reviewer",
            evidence_bundle_id="bundle-1",
            media_ids=("media-1",),
            canonical_payload={
                "request_id": request_id,
                "work_id": work_id,
                "status": status,
            },
            payload_digest=payload_digest,
            audit_event=ApprovalAuditEvent(
                event_id=event_id,
                work_id=work_id,
                event_type="approval-audit-binding",
                status=status,
                message="Human-review approval evidence is bound for audit.",
                occurred_at="2026-06-03T12:00:00+02:00",
                actor="reviewer",
                metadata={
                    "request_id": request_id,
                    "payload_digest": payload_digest,
                    "blockers": blockers,
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
