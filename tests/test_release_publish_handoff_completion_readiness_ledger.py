from dataclasses import FrozenInstanceError, dataclass
import copy
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_completion_readiness_ledger as completion_ledger
from harness_orchestrator.release_publish_handoff_acceptance import (
    evaluate_release_publish_handoff_acceptance,
)
from harness_orchestrator.release_publish_handoff_acceptance_ledger import (
    record_release_publish_handoff_acceptance,
)
from harness_orchestrator.release_publish_handoff_acceptance_receipt import (
    verify_release_publish_handoff_acceptance_receipt,
)
from harness_orchestrator.release_publish_handoff_completion_readiness import (
    ReleasePublishHandoffCompletionReadiness,
    evaluate_release_publish_handoff_completion_readiness,
)
from harness_orchestrator.release_publish_handoff_completion_readiness_ledger import (
    ReleasePublishHandoffCompletionReadinessLedgerResult,
    record_release_publish_handoff_completion_readiness,
    record_release_publish_handoff_completion_readinesses,
)
from harness_orchestrator.release_publish_handoff_package import (
    build_release_publish_handoff_package,
)
from harness_orchestrator.release_publish_handoff_package_ledger import (
    record_release_publish_handoff_package,
)
from harness_orchestrator.release_publish_handoff_readiness import (
    evaluate_release_publish_handoff_readiness,
)
from harness_orchestrator.release_publish_handoff_readiness_ledger import (
    record_release_publish_handoff_readiness,
)
from harness_orchestrator.release_publish_handoff_receipt import (
    verify_release_publish_handoff_receipt,
)
from harness_orchestrator.release_publish_intent import build_release_publish_intent
from harness_orchestrator.release_publish_intent_ledger import record_release_publish_intent
from harness_orchestrator.release_publish_readiness import ReleasePublishReadiness
from harness_orchestrator.run_ledger import AuditEvent, DependencyRecord, RunLedger


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictReadiness:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class ReleasePublishHandoffCompletionReadinessLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, readiness = self._ledger_and_completion_readiness()

        result = record_release_publish_handoff_completion_readiness(
            readiness,
            ledger=ledger,
        )

        self.assertIsInstance(result, ReleasePublishHandoffCompletionReadinessLedgerResult)
        self.assertEqual((), result.blockers)
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-completion"
        )
        event = next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-handoff-completion-ledger-record"
        )
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-handoff-completion:work-001:{readiness.package_digest[:16]}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-handoff-completion-recorded:work-001:{readiness.package_digest[:16]}",
            event.event_id,
        )
        self.assertEqual("ready", dependency.status)
        self.assertEqual("ready", event.status)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(readiness.dependency_id, dependency.metadata["source_dependency_id"])
        self.assertEqual(readiness.event_id, dependency.metadata["source_event_id"])
        self.assertEqual(readiness.package_digest, dependency.metadata["package_digest"])
        self.assertEqual(
            readiness.to_dict()["readiness_summary"],
            dependency.metadata["readiness_summary"],
        )

    def test_mapping_dataclass_to_dict_inputs_and_batch_record(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._completion_readiness_into_ledger(ledger, work_id="work-001")
        second = self._completion_readiness_into_ledger(ledger, work_id="work-002")

        @dataclass(frozen=True)
        class ReadinessData:
            ready: bool
            status: str
            blockers: tuple[str, ...]
            run_id: str
            work_id: str
            dependency_id: str
            event_id: str
            package_digest: str
            package_digest_prefix: str
            source_dependency_id: str
            source_event_id: str
            readiness_summary: dict[str, object]
            receipt_summary: dict[str, object]

        dataclass_readiness = ReadinessData(**first.to_dict())
        result = record_release_publish_handoff_completion_readinesses(
            (dataclass_readiness, ToDictReadiness(second.to_dict())),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(2, len(result.recorded_dependency_ids))
        self.assertEqual(10, len(ledger.snapshot().dependencies))
        self.assertEqual(10, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_handoff_completion_readiness(
            self._completion_readiness(),
            ledger=None,
        )
        wrong_ledger = record_release_publish_handoff_completion_readiness(
            self._completion_readiness(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_handoff_completion_readiness("bad", ledger=ledger)
        empty = record_release_publish_handoff_completion_readinesses((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn(
            "release-publish-handoff-completion-readiness-wrong-type",
            wrong_input.blockers,
        )
        self.assertEqual(("release-publish-handoff-completion-readiness-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_not_ready_malformed_schema_prefix_and_summary_mismatch_do_not_mutate(self) -> None:
        cases = []
        blocked = self._completion_readiness().to_dict()
        blocked["ready"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-handoff-completion-readiness-not-ready"))
        extra = self._completion_readiness().to_dict()
        extra["extra"] = "bad"
        cases.append((extra, "unsafe-release-publish-handoff-completion-readiness-schema"))
        prefix = self._completion_readiness().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-handoff-completion-readiness-package-digest-prefix-mismatch"))
        summary = self._completion_readiness().to_dict()
        summary["readiness_summary"]["ready"] = False
        cases.append((summary, "release-publish-handoff-completion-readiness-readiness-summary-ready-mismatch"))
        receipt = self._completion_readiness().to_dict()
        receipt["receipt_summary"]["source_blocker_count"] = 1
        cases.append((receipt, "release-publish-handoff-completion-readiness-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_completion_readiness()
                before = ledger.to_dict()
                result = record_release_publish_handoff_completion_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_ledger_run_work_source_record_and_metadata_parity_fail_closed(self) -> None:
        run_mismatch = self._completion_readiness().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["readiness_summary"]["run_id"] = "run-999"
        run_mismatch["receipt_summary"]["run_id"] = "run-999"
        ledger, _readiness = self._ledger_and_completion_readiness()
        before = ledger.to_dict()
        result = record_release_publish_handoff_completion_readiness(run_mismatch, ledger=ledger)
        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertEqual(before, ledger.to_dict())

        ledger, readiness = self._ledger_and_completion_readiness()
        acceptance_dependency, _acceptance_event = self._acceptance_source_records(ledger)
        tampered = DependencyRecord(
            dependency_id=acceptance_dependency.dependency_id,
            work_id=acceptance_dependency.work_id,
            reference=acceptance_dependency.reference,
            order=acceptance_dependency.order,
            dependency_type=acceptance_dependency.dependency_type,
            required=acceptance_dependency.required,
            status=acceptance_dependency.status,
            metadata={**acceptance_dependency.metadata, "package_digest": "c" * 64},
        )
        bad_ledger = RunLedger(
            run_id=ledger.run_id,
            dependencies=tuple(
                tampered if record is acceptance_dependency else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=ledger.snapshot().audit_events,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_handoff_completion_readiness(readiness, ledger=bad_ledger)
        self.assertIn("source-acceptance-dependency-digest-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

        ledger, readiness = self._ledger_and_completion_readiness()
        _acceptance_dependency, acceptance_event = self._acceptance_source_records(ledger)
        event_metadata = dict(acceptance_event.metadata)
        event_metadata["package_digest_prefix"] = "0" * 12
        bad_ledger = self._ledger_with_acceptance_source_metadata(
            ledger,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_handoff_completion_readiness(readiness, ledger=bad_ledger)
        self.assertIn("source-acceptance-event-package-digest-prefix-parity-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_duplicates_secret_action_non_string_unsupported_and_hostile_existing_block(self) -> None:
        ledger, readiness = self._ledger_and_completion_readiness()
        first = record_release_publish_handoff_completion_readiness(readiness, ledger=ledger)
        before_existing = ledger.to_dict()
        duplicate_existing = record_release_publish_handoff_completion_readiness(
            readiness,
            ledger=ledger,
        )

        self.assertEqual((), first.blockers)
        self.assertIn(
            "release-publish-handoff-completion-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-completion-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-completion-package-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_readiness = self._ledger_and_completion_readiness()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_handoff_completion_readinesses(
            (batch_readiness, batch_readiness.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn("release-publish-handoff-completion-dependency-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-handoff-completion-event-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-handoff-completion-package-digest-duplicate", duplicate_batch.blockers)
        self.assertEqual(before_batch, batch_ledger.to_dict())

        cases = []
        secret = self._completion_readiness().to_dict()
        secret["receipt_summary"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-handoff-completion-readiness-ledger-data"))
        action = self._completion_readiness().to_dict()
        action["receipt_summary"]["runner"] = "manual"
        cases.append((action, "action-intent-release-publish-handoff-completion-readiness-ledger-data"))
        non_string = self._completion_readiness().to_dict()
        non_string["receipt_summary"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-handoff-completion-readiness-ledger-data"))
        unsupported = self._completion_readiness().to_dict()
        unsupported["receipt_summary"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-handoff-completion-readiness-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_completion_readiness()
                before = ledger.to_dict()
                result = record_release_publish_handoff_completion_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-handoff-completion-readiness-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-handoff-completion-readiness-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_handoff_completion_readiness(
                    self._completion_readiness(),
                    ledger=hostile,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        ledger, readiness = self._ledger_and_completion_readiness()
        payload = readiness.to_dict()
        original_summary = copy.deepcopy(payload["readiness_summary"])

        result = record_release_publish_handoff_completion_readiness(payload, ledger=ledger)
        payload["readiness_summary"]["dependency_id"] = "changed"

        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-completion"
        )
        self.assertEqual(original_summary, dependency.metadata["readiness_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-completion"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["readiness_summary"]["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan(self) -> None:
        source = inspect.getsource(completion_ledger)
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
            "read_text",
            "write_text",
            ".save(",
            ".load(",
            "Path(",
            "RunLedger(",
            "MARACA.",
            "import maraca",
            "from maraca",
            "AI-Art",
            "AI_Artist",
            "coordinator",
            "runtime",
            "scheduler",
            "watch",
            "Client(",
            "Service(",
            "datetime",
            "random",
        )
        forbidden_process = "sub" + "process"
        self.assertNotIn(forbidden_process, source)
        for token in forbidden:
            self.assertNotIn(token, source)

    def _ledger_and_completion_readiness(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishHandoffCompletionReadiness]:
        ledger = RunLedger(run_id=run_id)
        readiness = self._completion_readiness_into_ledger(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )
        return ledger, readiness

    def _completion_readiness(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffCompletionReadiness:
        ledger = RunLedger(run_id=run_id)
        return self._completion_readiness_into_ledger(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )

    def _completion_readiness_into_ledger(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffCompletionReadiness:
        intent = build_release_publish_intent(
            readiness=ReleasePublishReadiness(
                ready=True,
                status="ready",
                blockers=(),
                run_id=run_id,
                work_id=work_id,
                dependency_id=f"dependency-{work_id}",
                event_id=f"event-{work_id}",
                canonical_digest_prefix=_BINDING_DIGEST[:12],
                summary={"format": "harness-release-publish-readiness-v1"},
            ),
            run_id=run_id,
            work_id=work_id,
            release_binding_digest=_BINDING_DIGEST,
            publish_target={"target_type": "local-dry-run", "target_id": f"target-{work_id}"},
            publish_payload={"payload_digest": _PAYLOAD_DIGEST, "payload_label": "release"},
            artifact={"artifact_id": f"artifact-{work_id}"},
            metadata={"ticket": "HAR-042", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        intent_result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), intent_result.blockers)
        handoff_readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), handoff_readiness.blockers)
        handoff_result = record_release_publish_handoff_readiness(
            handoff_readiness,
            ledger=ledger,
        )
        self.assertEqual((), handoff_result.blockers)
        package = build_release_publish_handoff_package(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), package.blockers)
        package_result = record_release_publish_handoff_package(package, ledger=ledger)
        self.assertEqual((), package_result.blockers)
        package_verification = verify_release_publish_handoff_receipt(
            ledger.snapshot().to_dict(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(package_verification.passed)
        acceptance = evaluate_release_publish_handoff_acceptance(
            package_verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(acceptance.accepted)
        acceptance_result = record_release_publish_handoff_acceptance(
            acceptance,
            ledger=ledger,
        )
        self.assertEqual((), acceptance_result.blockers)
        acceptance_verification = verify_release_publish_handoff_acceptance_receipt(
            ledger.snapshot().to_dict(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(acceptance_verification.passed)
        readiness = evaluate_release_publish_handoff_completion_readiness(
            acceptance_verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(readiness.ready)
        return readiness

    def _acceptance_source_records(self, ledger: RunLedger) -> tuple[DependencyRecord, AuditEvent]:
        snapshot = ledger.snapshot()
        dependency = next(
            record
            for record in snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-acceptance"
        )
        event = next(
            record
            for record in snapshot.audit_events
            if record.event_type == "release-publish-handoff-acceptance-ledger-record"
        )
        return dependency, event

    def _ledger_with_acceptance_source_metadata(
        self,
        ledger: RunLedger,
        *,
        dependency_metadata: dict[str, object] | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> RunLedger:
        acceptance_dependency, acceptance_event = self._acceptance_source_records(ledger)
        replacement_dependency = DependencyRecord(
            dependency_id=acceptance_dependency.dependency_id,
            work_id=acceptance_dependency.work_id,
            reference=acceptance_dependency.reference,
            order=acceptance_dependency.order,
            dependency_type=acceptance_dependency.dependency_type,
            required=acceptance_dependency.required,
            status=acceptance_dependency.status,
            metadata=(
                dependency_metadata
                if dependency_metadata is not None
                else acceptance_dependency.metadata
            ),
        )
        replacement_event = AuditEvent(
            event_id=acceptance_event.event_id,
            work_id=acceptance_event.work_id,
            event_type=acceptance_event.event_type,
            status=acceptance_event.status,
            message=acceptance_event.message,
            occurred_at=acceptance_event.occurred_at,
            actor=acceptance_event.actor,
            metadata=event_metadata if event_metadata is not None else acceptance_event.metadata,
        )
        return RunLedger(
            run_id=ledger.run_id,
            gate_decisions=ledger.snapshot().gate_decisions,
            dependencies=tuple(
                replacement_dependency
                if record.dependency_id == acceptance_dependency.dependency_id
                else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=tuple(
                replacement_event if event.event_id == acceptance_event.event_id else event
                for event in ledger.snapshot().audit_events
            ),
            tasks=ledger.snapshot().tasks,
            metadata=ledger.snapshot().metadata,
        )


if __name__ == "__main__":
    unittest.main()
