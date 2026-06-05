from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.run_ledger import (
    AuditEvent,
    DependencyRecord,
    RunLedger,
    RunLedgerSnapshot,
    TaskStatus,
)


class RunLedgerTests(unittest.TestCase):
    def test_records_are_frozen_dataclasses(self) -> None:
        for record_type in (
            DependencyRecord,
            AuditEvent,
            TaskStatus,
            RunLedgerSnapshot,
        ):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        with self.assertRaises(FrozenInstanceError):
            DependencyRecord(
                dependency_id="dep-001",
                work_id="work-001",
                reference="bundle-001",
                order=1,
            ).status = "ready"

    def test_in_memory_ledger_is_empty_and_side_effect_free_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before = set(Path(directory).iterdir())
            ledger = RunLedger(run_id="run-001")

            self.assertEqual(ledger.to_dict()["run_id"], "run-001")
            self.assertEqual(ledger.snapshot().gate_decisions, ())
            self.assertEqual(ledger.unfinished_tasks(), ())
            self.assertEqual(before, set(Path(directory).iterdir()))

    def test_records_gate_decisions(self) -> None:
        ledger = RunLedger(run_id="run-001")

        decision = ledger.record_gate_decision(
            decision_id="gate-001",
            work_id="work-001",
            gate_name="human-review",
            passed=False,
            reason="Reviewer approval is missing.",
            blockers=("approval",),
        )

        self.assertIsInstance(decision, GateDecision)
        self.assertFalse(ledger.snapshot().gate_decisions[0].passed)
        self.assertEqual(
            ledger.to_dict()["gate_decisions"][0]["blockers"],
            ("approval",),
        )

    def test_records_dependencies_in_deterministic_order(self) -> None:
        ledger = RunLedger(run_id="run-001")

        ledger.record_dependency(
            dependency_id="dep-late",
            work_id="work-001",
            reference="evidence:late",
            order=20,
        )
        ledger.record_dependency(
            dependency_id="dep-early-b",
            work_id="work-001",
            reference="evidence:early-b",
            order=10,
        )
        ledger.record_dependency(
            dependency_id="dep-early-a",
            work_id="work-001",
            reference="evidence:early-a",
            order=10,
        )

        self.assertEqual(
            tuple(record.dependency_id for record in ledger.snapshot().dependencies),
            ("dep-early-a", "dep-early-b", "dep-late"),
        )

    def test_records_audit_status_events(self) -> None:
        ledger = RunLedger(run_id="run-001")

        event = ledger.record_audit_event(
            event_id="audit-001",
            work_id="work-001",
            event_type="status",
            status="blocked",
            message="Waiting on reviewer.",
            occurred_at="2026-06-03T12:00:00+02:00",
            actor="tester",
        )

        self.assertEqual(event.status, "blocked")
        self.assertEqual(ledger.to_dict()["audit_events"][0]["actor"], "tester")

    def test_tracks_unfinished_and_open_tasks(self) -> None:
        ledger = RunLedger(run_id="run-001")

        ledger.record_task(
            task_id="task-001",
            work_id="work-001",
            title="Attach evidence bundle.",
            status="open",
            blockers=("evidence",),
        )
        ledger.record_task(
            task_id="task-002",
            work_id="work-001",
            title="Draft release notes.",
            status="completed",
        )

        unfinished = ledger.unfinished_tasks()

        self.assertEqual(len(unfinished), 1)
        self.assertEqual(unfinished[0].task_id, "task-001")
        self.assertEqual(unfinished[0].blockers, ("evidence",))

    def test_serialization_is_deterministic(self) -> None:
        ledger = self._populated_ledger()

        first = ledger.to_json()
        second = ledger.to_json()

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["run_id"], "run-001")

    def test_save_and_load_use_explicit_local_path(self) -> None:
        ledger = self._populated_ledger()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            ledger.save(path)
            loaded = RunLedger.load(path)

        self.assertEqual(loaded.to_dict(), ledger.to_dict())
        self.assertEqual(loaded.unfinished_tasks()[0].task_id, "task-001")

    def test_source_guards_keep_ledger_inert_and_local(self) -> None:
        source = inspect.getsource(__import__("harness_orchestrator.run_ledger").run_ledger)
        forbidden_tokens = (
            "requests",
            "urllib",
            "http.client",
            "socket",
            "subprocess",
            "threading",
            "sched",
            "scheduler",
            "ai_art",
            "maraca",
            "os.environ",
        )

        for token in forbidden_tokens:
            self.assertNotIn(token, source)

    def _populated_ledger(self) -> RunLedger:
        ledger = RunLedger(run_id="run-001", metadata={"owner": "tester"})
        ledger.record_gate_decision(
            decision_id="gate-001",
            work_id="work-001",
            gate_name="provenance",
            passed=True,
            reason="Evidence bundle is attached.",
            evidence_bundle_id="bundle-001",
        )
        ledger.record_dependency(
            dependency_id="dep-001",
            work_id="work-001",
            reference="bundle-001",
            order=1,
            dependency_type="evidence",
            status="ready",
        )
        ledger.record_audit_event(
            event_id="audit-001",
            work_id="work-001",
            event_type="status",
            status="running",
        )
        ledger.record_task(
            task_id="task-001",
            work_id="work-001",
            title="Collect reviewer approval.",
            status="open",
        )
        return ledger


if __name__ == "__main__":
    unittest.main()
