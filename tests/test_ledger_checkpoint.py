import inspect
import json
from pathlib import Path
import tempfile
import unittest

from harness_orchestrator.ledger_checkpoint import (
    LedgerCheckpointResult,
    checkpoint_ledger_snapshot,
    write_ledger_checkpoint,
)
from harness_orchestrator.run_ledger import RunLedger


class LedgerCheckpointTests(unittest.TestCase):
    def test_writes_deterministic_checkpoint_for_explicit_snapshot(self) -> None:
        ledger = self._ledger()
        snapshot = ledger.snapshot()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            result = checkpoint_ledger_snapshot(
                snapshot,
                checkpoint_path=path,
                run_id="run-001",
            )
            written = json.loads(path.read_text(encoding="utf-8"))

        self.assertIsInstance(result, LedgerCheckpointResult)
        self.assertEqual(result.blockers, ())
        self.assertEqual(result.run_id, "run-001")
        self.assertEqual(written["checkpoint"]["run_id"], "run-001")
        self.assertEqual(
            written["checkpoint"]["snapshot_digest"],
            result.checkpoint_digest,
        )
        self.assertEqual(written["ledger_snapshot"], result.ledger_snapshot)

    def test_checkpoint_digest_is_stable_for_same_snapshot(self) -> None:
        snapshot = self._ledger().snapshot()

        with tempfile.TemporaryDirectory() as directory:
            first = checkpoint_ledger_snapshot(
                snapshot,
                checkpoint_path=Path(directory) / "first.json",
                run_id="run-001",
            )
            second = checkpoint_ledger_snapshot(
                snapshot.to_dict(),
                checkpoint_path=Path(directory) / "second.json",
                run_id="run-001",
            )

        self.assertEqual(first.checkpoint_digest, second.checkpoint_digest)

    def test_accepts_result_object_containing_ledger_snapshot(self) -> None:
        class ResultObject:
            def __init__(self, snapshot: object) -> None:
                self._snapshot = snapshot

            def to_dict(self) -> dict[str, object]:
                return {"ledger_snapshot": self._snapshot}

        with tempfile.TemporaryDirectory() as directory:
            result = write_ledger_checkpoint(
                ResultObject(self._ledger().snapshot()),
                checkpoint_path=Path(directory) / "checkpoint.json",
                run_id="run-001",
            )

        self.assertEqual(result.blockers, ())
        self.assertEqual(result.run_id, "run-001")

    def test_missing_path_fails_closed_without_writing(self) -> None:
        result = checkpoint_ledger_snapshot(
            self._ledger().snapshot(),
            checkpoint_path=None,
            run_id="run-001",
        )

        self.assertIn("checkpoint-path-missing", result.blockers)
        self.assertEqual(result.checkpoint_digest, "")

    def test_run_mismatch_fails_closed_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            result = checkpoint_ledger_snapshot(
                self._ledger().snapshot(),
                checkpoint_path=path,
                run_id="run-other",
            )

            self.assertFalse(path.exists())

        self.assertIn("checkpoint-run-id-mismatch", result.blockers)

    def test_existing_checkpoint_path_fails_closed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text("keep", encoding="utf-8")
            result = checkpoint_ledger_snapshot(
                self._ledger().snapshot(),
                checkpoint_path=path,
                run_id="run-001",
            )
            text = path.read_text(encoding="utf-8")

        self.assertIn("checkpoint-path-already-exists", result.blockers)
        self.assertEqual(text, "keep")

    def test_unfinished_tasks_and_blockers_fail_closed(self) -> None:
        ledger = RunLedger(run_id="run-001")
        ledger.record_task(
            task_id="task-001",
            work_id="work-001",
            title="Waiting",
            status="open",
            blockers=("approval",),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            result = checkpoint_ledger_snapshot(
                ledger.snapshot(),
                checkpoint_path=path,
                run_id="run-001",
            )

            self.assertFalse(path.exists())

        self.assertIn("unfinished-ledger-tasks", result.blockers)
        self.assertIn("unfinished-validation-blockers", result.blockers)

    def test_minimal_or_malformed_snapshot_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            minimal_path = Path(directory) / "minimal.json"
            minimal = checkpoint_ledger_snapshot(
                {"run_id": "run-001"},
                checkpoint_path=minimal_path,
                run_id="run-001",
            )
            malformed_path = Path(directory) / "malformed.json"
            malformed = checkpoint_ledger_snapshot(
                {
                    "run_id": "run-001",
                    "gate_decisions": (),
                    "dependencies": ("not-a-record",),
                    "audit_events": (),
                    "tasks": (),
                    "metadata": {},
                },
                checkpoint_path=malformed_path,
                run_id="run-001",
            )

            self.assertFalse(minimal_path.exists())
            self.assertFalse(malformed_path.exists())

        self.assertIn("missing-ledger-gate_decisions", minimal.blockers)
        self.assertIn("missing-ledger-dependencies", minimal.blockers)
        self.assertIn("missing-ledger-audit_events", minimal.blockers)
        self.assertIn("missing-ledger-tasks", minimal.blockers)
        self.assertIn("malformed-ledger-dependencies", malformed.blockers)

    def test_secret_like_data_and_path_fail_closed(self) -> None:
        ledger = RunLedger(
            run_id="run-001",
            metadata={"api_key": "value"},
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret-token-checkpoint.json"
            result = checkpoint_ledger_snapshot(
                ledger.snapshot(),
                checkpoint_path=path,
                run_id="run-001",
            )

            self.assertFalse(path.exists())

        self.assertIn("secret-like-checkpoint-data", result.blockers)
        self.assertIn("secret-like-checkpoint-path", result.blockers)

    def test_duplicate_checkpoint_metadata_and_unsafe_path_fail_closed(self) -> None:
        ledger = RunLedger(
            run_id="run-001",
            metadata={"checkpoint_digest": "abc"},
        )
        result = checkpoint_ledger_snapshot(
            ledger.snapshot(),
            checkpoint_path="..\\checkpoint.json",
            run_id="run-001",
        )

        self.assertIn("duplicate-checkpoint-metadata", result.blockers)
        self.assertIn("checkpoint-path-unsafe", result.blockers)

    def test_caller_snapshot_mapping_is_not_mutated(self) -> None:
        snapshot = self._ledger().snapshot().to_dict()
        before = json.dumps(snapshot, sort_keys=True, default=str)

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_ledger_snapshot(
                snapshot,
                checkpoint_path=Path(directory) / "checkpoint.json",
                run_id="run-001",
            )

        after = json.dumps(snapshot, sort_keys=True, default=str)
        self.assertEqual(after, before)
        self.assertNotIn("checkpoint_digest", snapshot.get("metadata", {}))

    def test_source_scan_has_no_runtime_or_service_behavior(self) -> None:
        import harness_orchestrator.ledger_checkpoint as module

        source = inspect.getsource(module)
        forbidden_tokens = (
            "requests",
            "urllib",
            "http.client",
            "socket",
            "subprocess",
            "os.environ",
            "importlib",
            "pkg_resources",
            "scheduler",
            "watch",
            "publish",
            "ai_art",
            "maraca",
            "random",
            "datetime",
        )

        for token in forbidden_tokens:
            self.assertNotIn(token, source)

    def _ledger(self) -> RunLedger:
        ledger = RunLedger(run_id="run-001", metadata={"owner": "tester"})
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
            status="complete",
        )
        ledger.record_task(
            task_id="task-001",
            work_id="work-001",
            title="Done",
            status="completed",
        )
        return ledger


if __name__ == "__main__":
    unittest.main()
