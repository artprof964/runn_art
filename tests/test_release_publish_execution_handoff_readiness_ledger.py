from dataclasses import FrozenInstanceError, dataclass
import copy
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_handoff_readiness_ledger as handoff_ledger
import tests.test_release_publish_execution_authorization_ledger as authorization_ledger_tests
from harness_orchestrator.release_publish_execution_authorization_ledger import (
    record_release_publish_execution_authorization,
)
from harness_orchestrator.release_publish_execution_authorization_receipt import (
    verify_release_publish_execution_authorization_receipt,
)
from harness_orchestrator.release_publish_execution_handoff_readiness import (
    ReleasePublishExecutionHandoffReadiness,
    evaluate_release_publish_execution_handoff_readiness,
)
from harness_orchestrator.release_publish_execution_handoff_readiness_ledger import (
    ReleasePublishExecutionHandoffReadinessLedgerResult,
    record_release_publish_execution_handoff_readiness,
    record_release_publish_execution_handoff_readinesses,
)
from harness_orchestrator.run_ledger import AuditEvent, DependencyRecord, RunLedger


class ToDictReadiness:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


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
    authorization_dependency_id: str
    authorization_event_id: str
    readiness_summary: dict[str, object]
    authorization_summary: dict[str, object]
    receipt_summary: dict[str, object]


@dataclass(frozen=True)
class RaisingToDictReadinessData(ReadinessData):
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictReadiness:
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


class HostileReleasePublishExecutionHandoffReadiness(
    ReleasePublishExecutionHandoffReadiness
):
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class ReleasePublishExecutionHandoffReadinessLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, readiness = self._ledger_and_handoff_readiness()

        result = record_release_publish_execution_handoff_readiness(
            readiness,
            ledger=ledger,
        )

        self.assertIsInstance(result, ReleasePublishExecutionHandoffReadinessLedgerResult)
        self.assertEqual((), result.blockers)
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-execution-handoff-readiness"
        )
        event = next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-execution-handoff-readiness-ledger-record"
        )
        suffix = readiness.package_digest[:16]
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-execution-handoff-readiness:work-001:{suffix}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-execution-handoff-readiness-recorded:work-001:{suffix}",
            event.event_id,
        )
        self.assertEqual(140, dependency.order)
        self.assertTrue(dependency.required)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("ready", event.status)
        self.assertEqual("harness", event.actor)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(readiness.authorization_dependency_id, dependency.metadata["source_dependency_id"])
        self.assertEqual(readiness.authorization_event_id, dependency.metadata["source_event_id"])
        self.assertEqual(readiness.package_digest, dependency.metadata["package_digest"])
        self.assertEqual(
            readiness.to_dict()["readiness_summary"],
            dependency.metadata["readiness_summary"],
        )
        self.assertEqual(
            "harness-release-publish-execution-handoff-readiness-ledger-v1",
            dependency.metadata["canonical_payload"]["format"],
        )
        self.assertEqual(
            readiness.to_dict()["readiness_summary"],
            dependency.metadata["canonical_payload"]["release_publish_execution_handoff_readiness"],
        )

    def test_mapping_dataclass_to_dict_inputs_and_batch_record(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._handoff_readiness_into_ledger_sources(ledger, work_id="work-001")
        second = self._handoff_readiness_into_ledger_sources(ledger, work_id="work-002")
        third = self._handoff_readiness_into_ledger_sources(ledger, work_id="work-003")

        result = record_release_publish_execution_handoff_readinesses(
            (
                first.to_dict(),
                ReadinessData(**second.to_dict()),
                ToDictReadiness(third.to_dict()),
            ),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(3, len(result.recorded_dependency_ids))
        self.assertEqual(30, len(ledger.snapshot().dependencies))
        self.assertEqual(30, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_execution_handoff_readiness(
            self._handoff_readiness(),
            ledger=None,
        )
        wrong_ledger = record_release_publish_execution_handoff_readiness(
            self._handoff_readiness(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_execution_handoff_readiness("bad", ledger=ledger)
        empty = record_release_publish_execution_handoff_readinesses((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-execution-handoff-readiness-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-execution-handoff-readiness-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_not_ready_schema_prefix_summary_and_count_cases_do_not_mutate(self) -> None:
        cases = []
        blocked = self._handoff_readiness().to_dict()
        blocked["ready"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-execution-handoff-readiness-not-ready"))
        extra = self._handoff_readiness().to_dict()
        extra["extra"] = "bad"
        cases.append((extra, "unsafe-release-publish-execution-handoff-readiness-schema"))
        prefix = self._handoff_readiness().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-execution-handoff-readiness-package-digest-prefix-mismatch"))
        summary = self._handoff_readiness().to_dict()
        summary["readiness_summary"]["ready"] = False
        cases.append((summary, "release-publish-execution-handoff-readiness-readiness-summary-ready-mismatch"))
        receipt = self._handoff_readiness().to_dict()
        receipt["receipt_summary"]["source_blocker_count"] = 1
        cases.append((receipt, "release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch"))
        receipt_bool = self._handoff_readiness().to_dict()
        receipt_bool["receipt_summary"]["source_blocker_count"] = False
        cases.append((receipt_bool, "release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch"))
        receipt_float = self._handoff_readiness().to_dict()
        receipt_float["receipt_summary"]["source_blocker_count"] = 0.0
        cases.append((receipt_float, "release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_handoff_readiness()
                before = ledger.to_dict()
                result = record_release_publish_execution_handoff_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_callable_to_dict_failures_fail_closed_without_mutation(self) -> None:
        top_level_cases = (
            (
                lambda payload: RaisingToDictReadinessData(**payload),
                "malformed-release-publish-execution-handoff-readiness-ledger-data",
            ),
            (
                lambda payload: NonMappingToDictReadiness(payload),
                "malformed-release-publish-execution-handoff-readiness-ledger-data",
            ),
        )
        for factory, blocker in top_level_cases:
            with self.subTest(blocker=blocker):
                ledger, readiness = self._ledger_and_handoff_readiness()
                before = ledger.to_dict()
                result = record_release_publish_execution_handoff_readiness(
                    factory(readiness.to_dict()),
                    ledger=ledger,
                )
                self.assertIn(blocker, result.blockers)
                self.assertIn("release-publish-execution-handoff-readiness-wrong-type", result.blockers)
                self.assertEqual(before, ledger.to_dict())

        for summary_name, key, nested_value in (
            ("readiness_summary", "run_id", RaisingToDictValue()),
            ("readiness_summary", "run_id", NonMappingToDictValue()),
            ("receipt_summary", "run_id", RaisingToDictValue()),
            ("receipt_summary", "run_id", NonMappingToDictValue()),
        ):
            with self.subTest(summary_name=summary_name, nested_type=type(nested_value).__name__):
                ledger, readiness = self._ledger_and_handoff_readiness()
                payload = readiness.to_dict()
                payload[summary_name][key] = nested_value
                before = ledger.to_dict()
                result = record_release_publish_execution_handoff_readiness(payload, ledger=ledger)
                self.assertIn(
                    "unsupported-object-release-publish-execution-handoff-readiness-ledger-data",
                    result.blockers,
                )
                self.assertEqual(before, ledger.to_dict())

    def test_release_publish_execution_handoff_readiness_to_dict_failure_fails_closed_without_mutation(self) -> None:
        ledger, readiness = self._ledger_and_handoff_readiness()
        hostile = HostileReleasePublishExecutionHandoffReadiness(**readiness.to_dict())
        before = ledger.to_dict()

        result = record_release_publish_execution_handoff_readiness(hostile, ledger=ledger)

        self.assertIn(
            "malformed-release-publish-execution-handoff-readiness-ledger-data",
            result.blockers,
        )
        self.assertIn("release-publish-execution-handoff-readiness-wrong-type", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_ledger_run_and_source_execution_authorization_record_tamper_cases_fail_closed(self) -> None:
        run_mismatch = self._handoff_readiness().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["readiness_summary"]["run_id"] = "run-999"
        run_mismatch["authorization_summary"]["run_id"] = "run-999"
        run_mismatch["receipt_summary"]["run_id"] = "run-999"
        ledger, _readiness = self._ledger_and_handoff_readiness()
        before = ledger.to_dict()
        result = record_release_publish_execution_handoff_readiness(run_mismatch, ledger=ledger)
        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertEqual(before, ledger.to_dict())

        ledger, readiness = self._ledger_and_handoff_readiness()
        source_dependency, _source_event = self._authorization_source_records(ledger)
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
        result = record_release_publish_execution_handoff_readiness(readiness, ledger=bad_ledger)
        self.assertIn("source-execution-authorization-dependency-package-digest-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

        ledger, readiness = self._ledger_and_handoff_readiness()
        _source_dependency, source_event = self._authorization_source_records(ledger)
        event_metadata = dict(source_event.metadata)
        event_metadata["canonical_payload"] = {
            **event_metadata["canonical_payload"],
            "format": "bad",
        }
        bad_ledger = self._ledger_with_authorization_source_metadata(
            ledger,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_execution_handoff_readiness(readiness, ledger=bad_ledger)
        self.assertIn("source-execution-authorization-event-canonical-format-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_duplicates_secret_action_non_string_unsupported_and_hostile_existing_block(self) -> None:
        ledger, readiness = self._ledger_and_handoff_readiness()
        first = record_release_publish_execution_handoff_readiness(readiness, ledger=ledger)
        before_existing = ledger.to_dict()
        duplicate_existing = record_release_publish_execution_handoff_readiness(readiness, ledger=ledger)

        self.assertEqual((), first.blockers)
        self.assertIn("release-publish-execution-handoff-readiness-dependency-id-already-recorded", duplicate_existing.blockers)
        self.assertIn("release-publish-execution-handoff-readiness-event-id-already-recorded", duplicate_existing.blockers)
        self.assertIn("release-publish-execution-handoff-readiness-package-digest-already-recorded", duplicate_existing.blockers)
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_readiness = self._ledger_and_handoff_readiness()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_execution_handoff_readinesses(
            (batch_readiness, batch_readiness.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn("release-publish-execution-handoff-readiness-dependency-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-execution-handoff-readiness-event-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-execution-handoff-readiness-package-digest-duplicate", duplicate_batch.blockers)
        self.assertEqual(before_batch, batch_ledger.to_dict())

        cases = []
        secret = self._handoff_readiness().to_dict()
        secret["receipt_summary"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-execution-handoff-readiness-ledger-data"))
        action = self._handoff_readiness().to_dict()
        action["receipt_summary"]["runner"] = "manual"
        cases.append((action, "action-intent-release-publish-execution-handoff-readiness-ledger-data"))
        non_string = self._handoff_readiness().to_dict()
        non_string["receipt_summary"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-execution-handoff-readiness-ledger-data"))
        unsupported = self._handoff_readiness().to_dict()
        unsupported["receipt_summary"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-execution-handoff-readiness-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_handoff_readiness()
                before = ledger.to_dict()
                result = record_release_publish_execution_handoff_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-execution-handoff-readiness-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-execution-handoff-readiness-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_execution_handoff_readiness(
                    self._handoff_readiness(),
                    ledger=hostile,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        ledger, readiness = self._ledger_and_handoff_readiness()
        payload = readiness.to_dict()
        original_summary = copy.deepcopy(payload["readiness_summary"])

        result = record_release_publish_execution_handoff_readiness(payload, ledger=ledger)
        payload["readiness_summary"]["dependency_id"] = "changed"

        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-execution-handoff-readiness"
        )
        self.assertEqual(original_summary, dependency.metadata["readiness_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-execution-handoff-readiness"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["readiness_summary"]["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan(self) -> None:
        source = inspect.getsource(handoff_ledger)
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
        forbidden_executor = "exec" + "utor"
        self.assertNotIn(forbidden_process, source)
        self.assertNotIn(forbidden_executor, source)
        for token in forbidden:
            self.assertNotIn(token, source)

    def _ledger_and_handoff_readiness(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishExecutionHandoffReadiness]:
        ledger = RunLedger(run_id=run_id)
        readiness = self._handoff_readiness_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )
        return ledger, readiness

    def _handoff_readiness(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishExecutionHandoffReadiness:
        ledger = RunLedger(run_id=run_id)
        return self._handoff_readiness_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )

    def _handoff_readiness_into_ledger_sources(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishExecutionHandoffReadiness:
        helper = authorization_ledger_tests.ReleasePublishExecutionAuthorizationLedgerTests()
        source_ledger, authorization = helper._ledger_and_authorization(
            run_id=run_id,
            work_id=work_id,
        )
        for dependency in source_ledger.snapshot().dependencies:
            ledger.record_dependency(dependency=dependency)
        for event in source_ledger.snapshot().audit_events:
            ledger.record_audit_event(event=event)
        result = record_release_publish_execution_authorization(
            authorization,
            ledger=ledger,
        )
        self.assertEqual((), result.blockers)
        verification = verify_release_publish_execution_authorization_receipt(
            result,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(verification.passed)
        readiness = evaluate_release_publish_execution_handoff_readiness(
            verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(readiness.ready)
        return readiness

    def _authorization_source_records(self, ledger: RunLedger) -> tuple[DependencyRecord, AuditEvent]:
        snapshot = ledger.snapshot()
        dependency = next(
            record
            for record in snapshot.dependencies
            if record.dependency_type == "release-publish-execution-authorization"
        )
        event = next(
            record
            for record in snapshot.audit_events
            if record.event_type == "release-publish-execution-authorization-ledger-record"
        )
        return dependency, event

    def _ledger_with_authorization_source_metadata(
        self,
        ledger: RunLedger,
        *,
        dependency_metadata: dict[str, object] | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> RunLedger:
        source_dependency, source_event = self._authorization_source_records(ledger)
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
            dependencies=tuple(
                replacement_dependency if record.dependency_id == source_dependency.dependency_id else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=tuple(
                replacement_event if event.event_id == source_event.event_id else event
                for event in ledger.snapshot().audit_events
            ),
        )


if __name__ == "__main__":
    unittest.main()
