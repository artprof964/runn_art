import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_authorization_ledger as ledger_module
from harness_orchestrator.release_publish_execution_authorization import (
    ReleasePublishExecutionAuthorization,
    authorize_release_publish_execution,
)
from harness_orchestrator.release_publish_execution_authorization_ledger import (
    ReleasePublishExecutionAuthorizationLedgerResult,
    record_release_publish_execution_authorization,
    record_release_publish_execution_authorizations,
)
from harness_orchestrator.release_publish_execution_readiness_acceptance_ledger import (
    record_release_publish_execution_readiness_acceptance,
)
from harness_orchestrator.release_publish_execution_readiness_acceptance_receipt import (
    verify_release_publish_execution_readiness_acceptance_receipt,
)
from harness_orchestrator.run_ledger import AuditEvent, DependencyRecord, RunLedger
from tests.test_release_publish_execution_readiness_acceptance_ledger import (
    ReleasePublishExecutionReadinessAcceptanceLedgerTests,
)


class ToDictAuthorization:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


@dataclass(frozen=True)
class AuthorizationData:
    authorized: bool
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
    authorization_summary: dict[str, object]
    receipt_summary: dict[str, object]


@dataclass(frozen=True)
class RaisingToDictAuthorizationData(AuthorizationData):
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictAuthorization:
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


class HostileReleasePublishExecutionAuthorization(
    ReleasePublishExecutionAuthorization
):
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class ReleasePublishExecutionAuthorizationLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, authorization = self._ledger_and_authorization()

        result = record_release_publish_execution_authorization(
            authorization,
            ledger=ledger,
        )

        self.assertIsInstance(result, ReleasePublishExecutionAuthorizationLedgerResult)
        self.assertEqual((), result.blockers)
        dependency = self._authorization_dependency(ledger)
        event = self._authorization_event(ledger)
        suffix = authorization.package_digest[:16]
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-execution-authorization:work-001:{suffix}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-execution-authorization-recorded:work-001:{suffix}",
            event.event_id,
        )
        self.assertEqual("release-publish-execution-authorization", dependency.dependency_type)
        self.assertEqual("release-publish-execution-authorization-ledger-record", event.event_type)
        self.assertEqual(135, dependency.order)
        self.assertTrue(dependency.required)
        self.assertEqual("authorized", dependency.status)
        self.assertEqual("authorized", event.status)
        self.assertEqual("harness", event.actor)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(authorization.dependency_id, dependency.metadata["source_dependency_id"])
        self.assertEqual(authorization.event_id, dependency.metadata["source_event_id"])
        self.assertEqual(authorization.package_digest, dependency.metadata["package_digest"])
        self.assertEqual(
            authorization.to_dict()["authorization_summary"],
            dependency.metadata["authorization_summary"],
        )
        self.assertEqual(
            authorization.to_dict()["receipt_summary"],
            dependency.metadata["receipt_summary"],
        )
        self.assertEqual(dependency.metadata, {k: v for k, v in event.metadata.items() if k != "dependency_id"})
        self.assertEqual(
            "harness-release-publish-execution-authorization-ledger-v1",
            dependency.metadata["canonical_payload"]["format"],
        )
        self.assertEqual(
            dependency.metadata["authorization_summary"],
            dependency.metadata["canonical_payload"]["release_publish_execution_authorization"],
        )

    def test_mapping_dataclass_to_dict_inputs_and_batch_record(self) -> None:
        first_ledger, first = self._ledger_and_authorization(work_id="work-001")
        second_ledger, second = self._ledger_and_authorization(work_id="work-002")
        third_ledger, third = self._ledger_and_authorization(work_id="work-003")
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

        result = record_release_publish_execution_authorizations(
            (
                first.to_dict(),
                AuthorizationData(**second.to_dict()),
                ToDictAuthorization(third.to_dict()),
            ),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(3, len(result.recorded_dependency_ids))
        self.assertEqual(27, len(ledger.snapshot().dependencies))
        self.assertEqual(27, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_execution_authorization(
            self._authorization(),
            ledger=None,
        )
        wrong_ledger = record_release_publish_execution_authorization(
            self._authorization(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_execution_authorization("bad", ledger=ledger)
        empty = record_release_publish_execution_authorizations((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-execution-authorization-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-execution-authorization-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_malformed_schema_digest_prefix_summary_and_count_no_mutation(self) -> None:
        cases = []
        blocked = self._authorization().to_dict()
        blocked["authorized"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-execution-authorization-not-authorized"))
        extra = self._authorization().to_dict()
        extra["extra"] = "bad"
        cases.append((extra, "unsafe-release-publish-execution-authorization-schema"))
        digest = self._authorization().to_dict()
        digest["package_digest"] = "A" * 64
        cases.append((digest, "invalid-package-digest"))
        prefix = self._authorization().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-execution-authorization-package-digest-prefix-mismatch"))
        summary = self._authorization().to_dict()
        summary["authorization_summary"]["authorized"] = False
        cases.append((summary, "release-publish-execution-authorization-authorization-summary-authorized-mismatch"))
        receipt = self._authorization().to_dict()
        receipt["receipt_summary"]["source_blocker_count"] = 1
        cases.append((receipt, "release-publish-execution-authorization-receipt-summary-source-blocker-count-mismatch"))
        receipt_bool = self._authorization().to_dict()
        receipt_bool["receipt_summary"]["source_blocker_count"] = False
        cases.append((receipt_bool, "release-publish-execution-authorization-receipt-summary-source-blocker-count-mismatch"))
        receipt_float = self._authorization().to_dict()
        receipt_float["receipt_summary"]["source_blocker_count"] = 0.0
        cases.append((receipt_float, "release-publish-execution-authorization-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _authorization = self._ledger_and_authorization()
                before = ledger.to_dict()
                result = record_release_publish_execution_authorization(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_bad_to_dict_non_string_unsupported_secret_and_action_fail_closed(self) -> None:
        top_level_cases = (
            (
                lambda payload: RaisingToDictAuthorizationData(**payload),
                "malformed-release-publish-execution-authorization-ledger-data",
            ),
            (
                lambda payload: NonMappingToDictAuthorization(payload),
                "malformed-release-publish-execution-authorization-ledger-data",
            ),
        )
        for factory, blocker in top_level_cases:
            with self.subTest(blocker=blocker):
                ledger, authorization = self._ledger_and_authorization()
                before = ledger.to_dict()
                result = record_release_publish_execution_authorization(factory(authorization.to_dict()), ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertIn("release-publish-execution-authorization-wrong-type", result.blockers)
                self.assertEqual(before, ledger.to_dict())

        for summary_name, key, nested_value in (
            ("authorization_summary", "run_id", RaisingToDictValue()),
            ("authorization_summary", "run_id", NonMappingToDictValue()),
            ("receipt_summary", "run_id", RaisingToDictValue()),
            ("receipt_summary", "run_id", NonMappingToDictValue()),
        ):
            with self.subTest(summary_name=summary_name, nested_type=type(nested_value).__name__):
                ledger, authorization = self._ledger_and_authorization()
                payload = authorization.to_dict()
                payload[summary_name][key] = nested_value
                before = ledger.to_dict()
                result = record_release_publish_execution_authorization(payload, ledger=ledger)
                self.assertIn(
                    "unsupported-object-release-publish-execution-authorization-ledger-data",
                    result.blockers,
                )
                self.assertEqual(before, ledger.to_dict())

        hostile_ledger, hostile_authorization = self._ledger_and_authorization()
        hostile = HostileReleasePublishExecutionAuthorization(**hostile_authorization.to_dict())
        before_hostile = hostile_ledger.to_dict()
        hostile_result = record_release_publish_execution_authorization(hostile, ledger=hostile_ledger)
        self.assertIn("malformed-release-publish-execution-authorization-ledger-data", hostile_result.blockers)
        self.assertEqual(before_hostile, hostile_ledger.to_dict())

        cases = []
        secret = self._authorization().to_dict()
        secret["receipt_summary"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-execution-authorization-ledger-data"))
        action = self._authorization().to_dict()
        action["receipt_summary"]["runner"] = "manual"
        cases.append((action, "action-intent-release-publish-execution-authorization-ledger-data"))
        non_string = self._authorization().to_dict()
        non_string["receipt_summary"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-execution-authorization-ledger-data"))
        unsupported = self._authorization().to_dict()
        unsupported["receipt_summary"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-execution-authorization-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _authorization = self._ledger_and_authorization()
                before = ledger.to_dict()
                result = record_release_publish_execution_authorization(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_ledger_run_and_source_record_tamper_cases_fail_closed(self) -> None:
        run_mismatch = self._authorization().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["authorization_summary"]["run_id"] = "run-999"
        run_mismatch["receipt_summary"]["run_id"] = "run-999"
        ledger, _authorization = self._ledger_and_authorization()
        before = ledger.to_dict()
        result = record_release_publish_execution_authorization(run_mismatch, ledger=ledger)
        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertEqual(before, ledger.to_dict())

        ledger, authorization = self._ledger_and_authorization()
        source_dependency, _source_event = self._source_records(ledger)
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
        result = record_release_publish_execution_authorization(authorization, ledger=bad_ledger)
        self.assertIn("source-execution-readiness-acceptance-dependency-digest-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

        ledger, authorization = self._ledger_and_authorization()
        _source_dependency, source_event = self._source_records(ledger)
        event_metadata = dict(source_event.metadata)
        event_metadata["canonical_payload"] = {**event_metadata["canonical_payload"], "format": "bad"}
        bad_ledger = self._ledger_with_source_metadata(ledger, event_metadata=event_metadata)
        before = bad_ledger.to_dict()
        result = record_release_publish_execution_authorization(authorization, ledger=bad_ledger)
        self.assertIn(
            "source-execution-readiness-acceptance-event-canonical-payload-format-mismatch",
            result.blockers,
        )
        self.assertEqual(before, bad_ledger.to_dict())

        missing_ledger = RunLedger(
            run_id=ledger.run_id,
            dependencies=tuple(
                record
                for record in ledger.snapshot().dependencies
                if record.dependency_id != authorization.dependency_id
            ),
            audit_events=ledger.snapshot().audit_events,
        )
        self.assertIn(
            "source-execution-readiness-acceptance-dependency-missing",
            record_release_publish_execution_authorization(authorization, ledger=missing_ledger).blockers,
        )

        duplicate_ledger = RunLedger(
            run_id=ledger.run_id,
            dependencies=(*ledger.snapshot().dependencies, self._source_records(ledger)[0]),
            audit_events=ledger.snapshot().audit_events,
        )
        self.assertIn(
            "source-execution-readiness-acceptance-dependency-ambiguous",
            record_release_publish_execution_authorization(authorization, ledger=duplicate_ledger).blockers,
        )

    def test_duplicates_hostile_existing_caller_mutation_and_result_safety(self) -> None:
        ledger, authorization = self._ledger_and_authorization()
        first = record_release_publish_execution_authorization(authorization, ledger=ledger)
        before_existing = ledger.to_dict()
        duplicate_existing = record_release_publish_execution_authorization(authorization, ledger=ledger)

        self.assertEqual((), first.blockers)
        self.assertIn("release-publish-execution-authorization-dependency-id-already-recorded", duplicate_existing.blockers)
        self.assertIn("release-publish-execution-authorization-event-id-already-recorded", duplicate_existing.blockers)
        self.assertIn("release-publish-execution-authorization-package-digest-already-recorded", duplicate_existing.blockers)
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_authorization = self._ledger_and_authorization()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_execution_authorizations(
            (batch_authorization, batch_authorization.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn("release-publish-execution-authorization-dependency-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-execution-authorization-event-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-execution-authorization-package-digest-duplicate", duplicate_batch.blockers)
        self.assertEqual(before_batch, batch_ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-execution-authorization-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-execution-authorization-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_execution_authorization(self._authorization(), ledger=hostile)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

        ledger, authorization = self._ledger_and_authorization()
        payload = authorization.to_dict()
        original_summary = copy.deepcopy(payload["authorization_summary"])
        result = record_release_publish_execution_authorization(payload, ledger=ledger)
        payload["authorization_summary"]["dependency_id"] = "changed"

        dependency = self._authorization_dependency(ledger)
        self.assertEqual(original_summary, dependency.metadata["authorization_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-execution-authorization"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["authorization_summary"]["dependency_id"] = "changed"
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

    def _ledger_and_authorization(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishExecutionAuthorization]:
        ledger = RunLedger(run_id=run_id)
        helper = ReleasePublishExecutionReadinessAcceptanceLedgerTests()
        acceptance = helper._acceptance_into_ledger_sources(ledger, run_id=run_id, work_id=work_id)
        acceptance_result = record_release_publish_execution_readiness_acceptance(acceptance, ledger=ledger)
        self.assertEqual((), acceptance_result.blockers)
        verification = verify_release_publish_execution_readiness_acceptance_receipt(
            acceptance_result,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(verification.passed)
        authorization = authorize_release_publish_execution(
            verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(authorization.authorized)
        return ledger, authorization

    def _authorization(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishExecutionAuthorization:
        _ledger, authorization = self._ledger_and_authorization(run_id=run_id, work_id=work_id)
        return authorization

    def _source_records(self, ledger: RunLedger) -> tuple[DependencyRecord, AuditEvent]:
        snapshot = ledger.snapshot()
        dependency = next(
            record
            for record in snapshot.dependencies
            if record.dependency_type == "release-publish-execution-readiness-acceptance"
        )
        event = next(
            record
            for record in snapshot.audit_events
            if record.event_type == "release-publish-execution-readiness-acceptance-ledger-record"
        )
        return dependency, event

    def _ledger_with_source_metadata(
        self,
        ledger: RunLedger,
        *,
        dependency_metadata: dict[str, object] | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> RunLedger:
        source_dependency, source_event = self._source_records(ledger)
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

    def _authorization_dependency(self, ledger: RunLedger) -> DependencyRecord:
        return next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-execution-authorization"
        )

    def _authorization_event(self, ledger: RunLedger) -> AuditEvent:
        return next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-execution-authorization-ledger-record"
        )


if __name__ == "__main__":
    unittest.main()
