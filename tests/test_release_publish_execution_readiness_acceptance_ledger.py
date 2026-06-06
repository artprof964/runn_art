import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_readiness_acceptance_ledger as ledger_module
from harness_orchestrator.release_publish_execution_readiness_acceptance import (
    ReleasePublishExecutionReadinessAcceptance,
    evaluate_release_publish_execution_readiness_acceptance,
)
from harness_orchestrator.release_publish_execution_readiness_acceptance_ledger import (
    ReleasePublishExecutionReadinessAcceptanceLedgerResult,
    record_release_publish_execution_readiness_acceptance,
    record_release_publish_execution_readiness_acceptances,
)
from harness_orchestrator.release_publish_execution_readiness_ledger import (
    record_release_publish_execution_readiness,
)
from harness_orchestrator.release_publish_execution_readiness_receipt import (
    verify_release_publish_execution_readiness_receipt,
)
from harness_orchestrator.run_ledger import AuditEvent, DependencyRecord, RunLedger
from tests.test_release_publish_execution_readiness_ledger import (
    ReleasePublishExecutionReadinessLedgerTests as _ReleasePublishExecutionReadinessLedgerTests,
)


class ToDictAcceptance:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


@dataclass(frozen=True)
class AcceptanceData:
    accepted: bool
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
    completion_source_dependency_id: str
    completion_source_event_id: str
    acceptance_source_dependency_id: str
    acceptance_source_event_id: str
    final_authorization_dependency_id: str
    final_authorization_event_id: str
    acceptance_summary: dict[str, object]
    receipt_summary: dict[str, object]


@dataclass(frozen=True)
class RaisingToDictAcceptanceData(AcceptanceData):
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictAcceptance:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> tuple[tuple[str, object], ...]:
        return tuple(self._data.items())


class RaisingToDictValue:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictValue:
    def to_dict(self) -> tuple[str, ...]:
        return ("bad",)


class HostileReleasePublishExecutionReadinessAcceptance(
    ReleasePublishExecutionReadinessAcceptance
):
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class ReleasePublishExecutionReadinessAcceptanceLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()

        result = record_release_publish_execution_readiness_acceptance(
            acceptance,
            ledger=ledger,
        )

        self.assertIsInstance(
            result,
            ReleasePublishExecutionReadinessAcceptanceLedgerResult,
        )
        self.assertEqual((), result.blockers)
        dependency = self._acceptance_dependency(ledger)
        event = self._acceptance_event(ledger)
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-execution-readiness-acceptance:work-001:{acceptance.package_digest[:16]}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-execution-readiness-acceptance-recorded:work-001:{acceptance.package_digest[:16]}",
            event.event_id,
        )
        self.assertEqual("release-publish-execution-readiness-acceptance", dependency.dependency_type)
        self.assertEqual("release-publish-execution-readiness-acceptance-ledger-record", event.event_type)
        self.assertEqual(130, dependency.order)
        self.assertTrue(dependency.required)
        self.assertEqual("accepted", dependency.status)
        self.assertEqual("accepted", event.status)
        self.assertEqual("harness", event.actor)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(acceptance.dependency_id, dependency.metadata["source_dependency_id"])
        self.assertEqual(acceptance.event_id, dependency.metadata["source_event_id"])
        self.assertEqual(acceptance.package_digest, dependency.metadata["package_digest"])
        self.assertEqual(
            acceptance.to_dict()["acceptance_summary"],
            dependency.metadata["acceptance_summary"],
        )
        self.assertEqual(
            "harness-release-publish-execution-readiness-acceptance-ledger-v1",
            dependency.metadata["canonical_payload"]["format"],
        )

    def test_mapping_dataclass_to_dict_inputs_and_batch_record(self) -> None:
        first_ledger, first = self._ledger_and_acceptance(work_id="work-001")
        second_ledger, second = self._ledger_and_acceptance(work_id="work-002")
        third_ledger, third = self._ledger_and_acceptance(work_id="work-003")
        ledger = RunLedger(
            run_id="run-001",
            dependencies=(
                *first_ledger.snapshot().dependencies,
                *second_ledger.snapshot().dependencies,
                *third_ledger.snapshot().dependencies,
            ),
            audit_events=(
                *first_ledger.snapshot().audit_events,
                *second_ledger.snapshot().audit_events,
                *third_ledger.snapshot().audit_events,
            ),
        )

        result = record_release_publish_execution_readiness_acceptances(
            (
                first.to_dict(),
                AcceptanceData(**second.to_dict()),
                ToDictAcceptance(third.to_dict()),
            ),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(3, len(result.recorded_dependency_ids))
        self.assertEqual(24, len(ledger.snapshot().dependencies))
        self.assertEqual(24, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_execution_readiness_acceptance(
            self._acceptance(),
            ledger=None,
        )
        wrong_ledger = record_release_publish_execution_readiness_acceptance(
            self._acceptance(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_execution_readiness_acceptance("bad", ledger=ledger)
        empty = record_release_publish_execution_readiness_acceptances((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn(
            "release-publish-execution-readiness-acceptance-wrong-type",
            wrong_input.blockers,
        )
        self.assertEqual(
            ("release-publish-execution-readiness-acceptance-empty",),
            empty.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_malformed_schema_digest_prefix_summary_and_count_no_mutation(self) -> None:
        cases = []
        blocked = self._acceptance().to_dict()
        blocked["accepted"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-execution-readiness-acceptance-not-accepted"))
        extra = self._acceptance().to_dict()
        extra["extra"] = "bad"
        cases.append((extra, "unsafe-release-publish-execution-readiness-acceptance-schema"))
        digest = self._acceptance().to_dict()
        digest["package_digest"] = "A" * 64
        cases.append((digest, "invalid-package-digest"))
        prefix = self._acceptance().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-execution-readiness-acceptance-package-digest-prefix-mismatch"))
        summary = self._acceptance().to_dict()
        summary["acceptance_summary"]["accepted"] = False
        cases.append((summary, "release-publish-execution-readiness-acceptance-acceptance-summary-accepted-mismatch"))
        receipt = self._acceptance().to_dict()
        receipt["receipt_summary"]["source_blocker_count"] = 1
        cases.append((receipt, "release-publish-execution-readiness-acceptance-receipt-summary-source-blocker-count-mismatch"))
        receipt_bool = self._acceptance().to_dict()
        receipt_bool["receipt_summary"]["source_blocker_count"] = False
        cases.append((receipt_bool, "release-publish-execution-readiness-acceptance-receipt-summary-source-blocker-count-mismatch"))
        receipt_float = self._acceptance().to_dict()
        receipt_float["receipt_summary"]["source_blocker_count"] = 0.0
        cases.append((receipt_float, "release-publish-execution-readiness-acceptance-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _acceptance = self._ledger_and_acceptance()
                before = ledger.to_dict()
                result = record_release_publish_execution_readiness_acceptance(
                    payload,
                    ledger=ledger,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_bad_to_dict_non_string_unsupported_secret_and_action_fail_closed(self) -> None:
        top_level_cases = (
            (
                lambda payload: RaisingToDictAcceptanceData(**payload),
                "malformed-release-publish-execution-readiness-acceptance-ledger-data",
            ),
            (
                lambda payload: NonMappingToDictAcceptance(payload),
                "malformed-release-publish-execution-readiness-acceptance-ledger-data",
            ),
        )
        for factory, blocker in top_level_cases:
            with self.subTest(blocker=blocker):
                ledger, acceptance = self._ledger_and_acceptance()
                before = ledger.to_dict()
                result = record_release_publish_execution_readiness_acceptance(
                    factory(acceptance.to_dict()),
                    ledger=ledger,
                )
                self.assertIn(blocker, result.blockers)
                self.assertIn(
                    "release-publish-execution-readiness-acceptance-wrong-type",
                    result.blockers,
                )
                self.assertEqual(before, ledger.to_dict())

        for summary_name, key, nested_value in (
            ("acceptance_summary", "run_id", RaisingToDictValue()),
            ("acceptance_summary", "run_id", NonMappingToDictValue()),
            ("receipt_summary", "run_id", RaisingToDictValue()),
            ("receipt_summary", "run_id", NonMappingToDictValue()),
        ):
            with self.subTest(summary_name=summary_name, nested_type=type(nested_value).__name__):
                ledger, acceptance = self._ledger_and_acceptance()
                payload = acceptance.to_dict()
                payload[summary_name][key] = nested_value
                before = ledger.to_dict()
                result = record_release_publish_execution_readiness_acceptance(
                    payload,
                    ledger=ledger,
                )
                self.assertIn(
                    "unsupported-object-release-publish-execution-readiness-acceptance-ledger-data",
                    result.blockers,
                )
                self.assertEqual(before, ledger.to_dict())

        hostile_ledger, hostile_acceptance = self._ledger_and_acceptance()
        hostile = HostileReleasePublishExecutionReadinessAcceptance(
            **hostile_acceptance.to_dict()
        )
        before_hostile = hostile_ledger.to_dict()
        hostile_result = record_release_publish_execution_readiness_acceptance(
            hostile,
            ledger=hostile_ledger,
        )
        self.assertIn(
            "malformed-release-publish-execution-readiness-acceptance-ledger-data",
            hostile_result.blockers,
        )
        self.assertEqual(before_hostile, hostile_ledger.to_dict())

        cases = []
        secret = self._acceptance().to_dict()
        secret["receipt_summary"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-execution-readiness-acceptance-ledger-data"))
        action = self._acceptance().to_dict()
        action["receipt_summary"]["runner"] = "manual"
        cases.append((action, "action-intent-release-publish-execution-readiness-acceptance-ledger-data"))
        non_string = self._acceptance().to_dict()
        non_string["receipt_summary"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-execution-readiness-acceptance-ledger-data"))
        unsupported = self._acceptance().to_dict()
        unsupported["receipt_summary"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-execution-readiness-acceptance-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _acceptance = self._ledger_and_acceptance()
                before = ledger.to_dict()
                result = record_release_publish_execution_readiness_acceptance(
                    payload,
                    ledger=ledger,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_ledger_run_and_source_record_tamper_cases_fail_closed(self) -> None:
        run_mismatch = self._acceptance().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["acceptance_summary"]["run_id"] = "run-999"
        run_mismatch["receipt_summary"]["run_id"] = "run-999"
        ledger, _acceptance = self._ledger_and_acceptance()
        before = ledger.to_dict()
        result = record_release_publish_execution_readiness_acceptance(
            run_mismatch,
            ledger=ledger,
        )
        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertEqual(before, ledger.to_dict())

        ledger, acceptance = self._ledger_and_acceptance()
        source_dependency, _source_event = self._execution_readiness_source_records(ledger)
        tampered = DependencyRecord(
            dependency_id=source_dependency.dependency_id,
            work_id=source_dependency.work_id,
            reference=source_dependency.reference,
            order=source_dependency.order,
            dependency_type=source_dependency.dependency_type,
            required=source_dependency.required,
            status=source_dependency.status,
            metadata={**source_dependency.metadata, "package_digest": "c" * 64},
        )
        bad_ledger = RunLedger(
            run_id=ledger.run_id,
            dependencies=tuple(
                tampered if record.dependency_id == source_dependency.dependency_id else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=ledger.snapshot().audit_events,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_execution_readiness_acceptance(
            acceptance,
            ledger=bad_ledger,
        )
        self.assertIn("source-execution-readiness-dependency-digest-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

        ledger, acceptance = self._ledger_and_acceptance()
        _source_dependency, source_event = self._execution_readiness_source_records(ledger)
        event_metadata = dict(source_event.metadata)
        event_metadata["canonical_payload"] = {
            **event_metadata["canonical_payload"],
            "format": "bad",
        }
        bad_ledger = self._ledger_with_execution_readiness_source_metadata(
            ledger,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_execution_readiness_acceptance(
            acceptance,
            ledger=bad_ledger,
        )
        self.assertIn(
            "source-execution-readiness-event-canonical-payload-format-mismatch",
            result.blockers,
        )
        self.assertEqual(before, bad_ledger.to_dict())

    def test_duplicates_hostile_existing_caller_mutation_and_result_safety(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        first = record_release_publish_execution_readiness_acceptance(
            acceptance,
            ledger=ledger,
        )
        before_existing = ledger.to_dict()
        duplicate_existing = record_release_publish_execution_readiness_acceptance(
            acceptance,
            ledger=ledger,
        )

        self.assertEqual((), first.blockers)
        self.assertIn(
            "release-publish-execution-readiness-acceptance-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-acceptance-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-acceptance-package-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_acceptance = self._ledger_and_acceptance()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_execution_readiness_acceptances(
            (batch_acceptance, batch_acceptance.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn(
            "release-publish-execution-readiness-acceptance-dependency-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-acceptance-event-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-acceptance-package-digest-duplicate",
            duplicate_batch.blockers,
        )
        self.assertEqual(before_batch, batch_ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-execution-readiness-acceptance-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-execution-readiness-acceptance-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_execution_readiness_acceptance(
                    self._acceptance(),
                    ledger=hostile,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

        ledger, acceptance = self._ledger_and_acceptance()
        payload = acceptance.to_dict()
        original_summary = copy.deepcopy(payload["acceptance_summary"])
        result = record_release_publish_execution_readiness_acceptance(payload, ledger=ledger)
        payload["acceptance_summary"]["dependency_id"] = "changed"

        dependency = self._acceptance_dependency(ledger)
        self.assertEqual(original_summary, dependency.metadata["acceptance_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-execution-readiness-acceptance"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["acceptance_summary"]["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(ledger_module)
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
            "social",
            "publisher",
            "executor",
            "Client(",
            "Service(",
            "datetime",
            "random",
        )
        forbidden_process = "sub" + "process"
        self.assertNotIn(forbidden_process, source)
        for token in forbidden:
            self.assertNotIn(token, source)

    def _ledger_and_acceptance(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishExecutionReadinessAcceptance]:
        ledger = RunLedger(run_id=run_id)
        acceptance = self._acceptance_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )
        return ledger, acceptance

    def _acceptance(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishExecutionReadinessAcceptance:
        ledger = RunLedger(run_id=run_id)
        return self._acceptance_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )

    def _acceptance_into_ledger_sources(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishExecutionReadinessAcceptance:
        helper = _ReleasePublishExecutionReadinessLedgerTests()
        readiness = helper._execution_readiness_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )
        readiness_result = record_release_publish_execution_readiness(
            readiness,
            ledger=ledger,
        )
        self.assertEqual((), readiness_result.blockers)
        verification = verify_release_publish_execution_readiness_receipt(
            readiness_result,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(verification.passed)
        acceptance = evaluate_release_publish_execution_readiness_acceptance(
            verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(acceptance.accepted)
        return acceptance

    def _execution_readiness_source_records(
        self,
        ledger: RunLedger,
    ) -> tuple[DependencyRecord, AuditEvent]:
        snapshot = ledger.snapshot()
        dependency = next(
            record
            for record in snapshot.dependencies
            if record.dependency_type == "release-publish-execution-readiness"
        )
        event = next(
            record
            for record in snapshot.audit_events
            if record.event_type == "release-publish-execution-readiness-ledger-record"
        )
        return dependency, event

    def _ledger_with_execution_readiness_source_metadata(
        self,
        ledger: RunLedger,
        *,
        dependency_metadata: dict[str, object] | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> RunLedger:
        source_dependency, source_event = self._execution_readiness_source_records(ledger)
        replacement_dependency = DependencyRecord(
            dependency_id=source_dependency.dependency_id,
            work_id=source_dependency.work_id,
            reference=source_dependency.reference,
            order=source_dependency.order,
            dependency_type=source_dependency.dependency_type,
            required=source_dependency.required,
            status=source_dependency.status,
            metadata=dependency_metadata if dependency_metadata is not None else source_dependency.metadata,
        )
        replacement_event = AuditEvent(
            event_id=source_event.event_id,
            work_id=source_event.work_id,
            event_type=source_event.event_type,
            status=source_event.status,
            message=source_event.message,
            occurred_at=source_event.occurred_at,
            actor=source_event.actor,
            metadata=event_metadata if event_metadata is not None else source_event.metadata,
        )
        return RunLedger(
            run_id=ledger.run_id,
            gate_decisions=ledger.snapshot().gate_decisions,
            dependencies=tuple(
                replacement_dependency
                if record.dependency_id == source_dependency.dependency_id
                else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=tuple(
                replacement_event if event.event_id == source_event.event_id else event
                for event in ledger.snapshot().audit_events
            ),
            tasks=ledger.snapshot().tasks,
            metadata=ledger.snapshot().metadata,
        )

    def _acceptance_dependency(self, ledger: RunLedger) -> DependencyRecord:
        return next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-execution-readiness-acceptance"
        )

    def _acceptance_event(self, ledger: RunLedger) -> AuditEvent:
        return next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-execution-readiness-acceptance-ledger-record"
        )


if __name__ == "__main__":
    unittest.main()
