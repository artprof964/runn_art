from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.maraca_runtime_result_ledger as result_ledger
from harness_orchestrator.maraca_runtime_invocation import prepare_maraca_runtime_invocation
from harness_orchestrator.maraca_runtime_result_intake import (
    intake_maraca_runtime_result,
)
from harness_orchestrator.maraca_runtime_result_ledger import (
    MaracaRuntimeResultLedgerRecord,
    MaracaRuntimeResultLedgerResult,
    record_maraca_runtime_result_intake,
)
from harness_orchestrator.run_ledger import RunLedger


def _invocation():
    return prepare_maraca_runtime_invocation(
        work_id="work-1",
        run_id="work-1",
        operation="collect-evidence",
        payload={"topic": "boundary"},
        preflight={"ready": True, "summary": {"work_id": "work-1", "run_id": "work-1"}},
        maraca_readiness={"ready": True, "work_id": "work-1", "run_id": "work-1"},
    )


def _runtime_result(**overrides):
    value = {
        "work_id": "work-1",
        "run_id": "work-1",
        "operation": "collect-evidence",
        "status": "succeeded",
        "evidence_items": ({"source_id": "source-1", "claim": "plain"},),
        "output": {"summary": "complete"},
        "metadata": {"origin": "unit-test"},
    }
    value.update(overrides)
    return value


def _intake(**overrides):
    return intake_maraca_runtime_result(
        invocation=overrides.pop("invocation", _invocation()),
        runtime_result=overrides.pop("runtime_result", _runtime_result(**overrides)),
    )


def _summary(**overrides):
    value = {"evidence_count": 1, "source_ids": ("source-1",)}
    value.update(overrides)
    return value


class MaracaRuntimeResultLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger = RunLedger(run_id="work-1")

        result = record_maraca_runtime_result_intake(
            _intake(),
            ledger=ledger,
            evidence_summary=_summary(),
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(result.ledger_snapshot, ledger.snapshot())
        self.assertIsInstance(result.ledger_record, MaracaRuntimeResultLedgerRecord)
        self.assertEqual(1, len(ledger.snapshot().dependencies))
        self.assertEqual(1, len(ledger.snapshot().audit_events))
        dependency = ledger.snapshot().dependencies[0]
        event = ledger.snapshot().audit_events[0]
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual("maraca-runtime-result", dependency.dependency_type)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("maraca-runtime-result-ledger-record", event.event_type)
        self.assertEqual(result.ledger_record.payload_digest, event.metadata["payload_digest"])
        self.assertEqual(("source-1",), event.metadata["source_ids"])

    def test_missing_ledger_fails_closed(self) -> None:
        result = record_maraca_runtime_result_intake(
            _intake(),
            ledger=None,
            evidence_summary=_summary(),
        )

        self.assertEqual(("ledger-missing",), result.blockers)
        self.assertEqual((), result.recorded_event_ids)
        self.assertIsNone(result.ledger_snapshot)

    def test_blocked_or_malformed_intake_does_not_mutate_ledger(self) -> None:
        ledger = RunLedger(run_id="work-1")
        before = ledger.to_dict()
        blocked = _intake(status="running")

        result = record_maraca_runtime_result_intake(
            blocked,
            ledger=ledger,
            evidence_summary=_summary(),
        )

        self.assertIn("runtime-result-intake-blocked", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_run_work_and_evidence_summary_mismatch_fail_closed(self) -> None:
        ledger = RunLedger(run_id="other-run")
        intake = _intake(work_id="work-2", invocation=_invocation())
        before = ledger.to_dict()

        result = record_maraca_runtime_result_intake(
            intake,
            ledger=ledger,
            evidence_summary=_summary(source_ids=("source-2",)),
        )

        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertIn("evidence-summary-source-id-mismatch", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_missing_or_malformed_evidence_summary_fails_closed(self) -> None:
        ledger = RunLedger(run_id="work-1")

        result = record_maraca_runtime_result_intake(
            _intake(),
            ledger=ledger,
            evidence_summary={"source_ids": ()},
        )

        self.assertIn("missing-evidence-summary-count", result.blockers)
        self.assertIn("missing-evidence-summary-source-ids", result.blockers)
        self.assertEqual((), ledger.snapshot().audit_events)

    def test_duplicate_event_dependency_or_payload_digest_does_not_mutate(self) -> None:
        ledger = RunLedger(run_id="work-1")
        first = record_maraca_runtime_result_intake(
            _intake(),
            ledger=ledger,
            evidence_summary=_summary(),
        )
        before = ledger.to_dict()

        duplicate = record_maraca_runtime_result_intake(
            _intake(),
            ledger=ledger,
            evidence_summary=_summary(),
        )

        self.assertEqual((), duplicate.recorded_event_ids)
        self.assertIn(
            "maraca-runtime-result-dependency-id-already-recorded",
            duplicate.blockers,
        )
        self.assertIn(
            "maraca-runtime-result-event-id-already-recorded",
            duplicate.blockers,
        )
        self.assertIn(
            "maraca-runtime-result-payload-digest-already-recorded",
            duplicate.blockers,
        )
        self.assertEqual(first.ledger_record.payload_digest, duplicate.skipped_payload_digests[0])
        self.assertEqual(before, ledger.to_dict())

    def test_secret_like_summary_or_metadata_blocks_without_mutation(self) -> None:
        ledger = RunLedger(run_id="work-1")
        before = ledger.to_dict()

        result = record_maraca_runtime_result_intake(
            _intake(runtime_result=_runtime_result(metadata={"api_key": "raw-key-value"})),
            ledger=ledger,
            evidence_summary={"evidence_count": 1, "source_ids": ("source-1",), "token": "raw"},
        )

        self.assertIn("runtime-result-intake-blocked", result.blockers)
        self.assertIn("secret-like-runtime-result-ledger-data", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_result_records_are_frozen_and_plain_serializable(self) -> None:
        for record_type in (
            MaracaRuntimeResultLedgerRecord,
            MaracaRuntimeResultLedgerResult,
        ):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        result = record_maraca_runtime_result_intake(
            _intake(),
            ledger=RunLedger(run_id="work-1"),
            evidence_summary=_summary(),
        )
        payload = result.to_dict()

        self.assertEqual(result.recorded_event_ids, payload["recorded_event_ids"])
        self.assertEqual("work-1", payload["ledger_snapshot"]["run_id"])
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)

    def test_source_has_no_forbidden_runtime_or_service_behavior(self) -> None:
        source = inspect.getsource(result_ledger)
        forbidden = (
            "os.environ",
            "getenv",
            "importlib",
            "pkg_resources",
            "requests",
            "httpx",
            "socket",
            "urllib",
            "open(",
            "read_text(",
            "write_text(",
            ".save(",
            ".load(",
            "MARACA.",
            "import maraca",
            "from maraca",
            "AI-Art",
            "AI_Artist",
            "scheduler",
            "watch_social",
            "Client(",
            "Service(",
            "datetime",
            "random",
        )
        forbidden_process = "sub" + "process"
        self.assertNotIn(forbidden_process, source)
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
