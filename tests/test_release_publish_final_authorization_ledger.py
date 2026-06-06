from dataclasses import FrozenInstanceError, dataclass
import copy
import inspect
import json
import unittest

import harness_orchestrator.release_publish_final_authorization_ledger as final_ledger
from harness_orchestrator.release_publish_final_authorization import (
    ReleasePublishFinalAuthorization,
    authorize_release_publish_final,
)
from harness_orchestrator.release_publish_final_authorization_ledger import (
    ReleasePublishFinalAuthorizationLedgerResult,
    record_release_publish_final_authorization,
    record_release_publish_final_authorizations,
)
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
    evaluate_release_publish_handoff_completion_readiness,
)
from harness_orchestrator.release_publish_handoff_completion_readiness_ledger import (
    record_release_publish_handoff_completion_readiness,
)
from harness_orchestrator.release_publish_handoff_completion_receipt import (
    verify_release_publish_handoff_completion_receipt,
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
    acceptance_source_dependency_id: str
    acceptance_source_event_id: str
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
        return ("not", "a", "mapping")


class ReleasePublishFinalAuthorizationLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, authorization = self._ledger_and_final_authorization()

        result = record_release_publish_final_authorization(authorization, ledger=ledger)

        self.assertIsInstance(result, ReleasePublishFinalAuthorizationLedgerResult)
        self.assertEqual((), result.blockers)
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-final-authorization"
        )
        event = next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-final-authorization-ledger-record"
        )
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-final-authorization:work-001:{authorization.package_digest[:16]}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-final-authorization-recorded:work-001:{authorization.package_digest[:16]}",
            event.event_id,
        )
        self.assertEqual(120, dependency.order)
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

    def test_mapping_dataclass_to_dict_inputs_and_batch_record(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._final_authorization_into_ledger_sources(ledger, work_id="work-001")
        second = self._final_authorization_into_ledger_sources(ledger, work_id="work-002")
        third = self._final_authorization_into_ledger_sources(ledger, work_id="work-003")

        dataclass_authorization = AuthorizationData(**second.to_dict())
        result = record_release_publish_final_authorizations(
            (first.to_dict(), dataclass_authorization, ToDictAuthorization(third.to_dict())),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(3, len(result.recorded_dependency_ids))
        self.assertEqual(18, len(ledger.snapshot().dependencies))
        self.assertEqual(18, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_final_authorization(
            self._final_authorization(),
            ledger=None,
        )
        wrong_ledger = record_release_publish_final_authorization(
            self._final_authorization(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_final_authorization("bad", ledger=ledger)
        empty = record_release_publish_final_authorizations((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-final-authorization-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-final-authorization-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_not_authorized_schema_prefix_summary_and_count_cases_do_not_mutate(self) -> None:
        cases = []
        blocked = self._final_authorization().to_dict()
        blocked["authorized"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-final-authorization-not-authorized"))
        extra = self._final_authorization().to_dict()
        extra["extra"] = "bad"
        cases.append((extra, "unsafe-release-publish-final-authorization-schema"))
        prefix = self._final_authorization().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-final-authorization-package-digest-prefix-mismatch"))
        summary = self._final_authorization().to_dict()
        summary["authorization_summary"]["authorized"] = False
        cases.append((summary, "release-publish-final-authorization-authorization-summary-authorized-mismatch"))
        receipt = self._final_authorization().to_dict()
        receipt["receipt_summary"]["source_blocker_count"] = 1
        cases.append((receipt, "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch"))
        receipt_bool = self._final_authorization().to_dict()
        receipt_bool["receipt_summary"]["source_blocker_count"] = False
        cases.append((receipt_bool, "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch"))
        receipt_float = self._final_authorization().to_dict()
        receipt_float["receipt_summary"]["source_blocker_count"] = 0.0
        cases.append((receipt_float, "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _authorization = self._ledger_and_final_authorization()
                before = ledger.to_dict()
                result = record_release_publish_final_authorization(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_callable_to_dict_failures_fail_closed_without_mutation(self) -> None:
        top_level_cases = (
            (
                lambda payload: RaisingToDictAuthorizationData(**payload),
                "malformed-release-publish-final-authorization-ledger-data",
            ),
            (
                lambda payload: NonMappingToDictAuthorization(payload),
                "malformed-release-publish-final-authorization-ledger-data",
            ),
        )
        for factory, blocker in top_level_cases:
            with self.subTest(blocker=blocker):
                ledger, authorization = self._ledger_and_final_authorization()
                before = ledger.to_dict()
                result = record_release_publish_final_authorization(
                    factory(authorization.to_dict()),
                    ledger=ledger,
                )
                self.assertIn(blocker, result.blockers)
                self.assertIn(
                    "release-publish-final-authorization-wrong-type",
                    result.blockers,
                )
                self.assertEqual(before, ledger.to_dict())

        nested_cases = (
            ("authorization_summary", "run_id", RaisingToDictValue()),
            ("authorization_summary", "run_id", NonMappingToDictValue()),
            ("receipt_summary", "run_id", RaisingToDictValue()),
            ("receipt_summary", "run_id", NonMappingToDictValue()),
        )
        for summary_name, key, nested_value in nested_cases:
            with self.subTest(summary_name=summary_name, nested_type=type(nested_value).__name__):
                ledger, authorization = self._ledger_and_final_authorization()
                payload = authorization.to_dict()
                payload[summary_name][key] = nested_value
                before = ledger.to_dict()
                result = record_release_publish_final_authorization(payload, ledger=ledger)
                self.assertIn(
                    "unsupported-object-release-publish-final-authorization-ledger-data",
                    result.blockers,
                )
                self.assertEqual(before, ledger.to_dict())

    def test_ledger_run_and_source_completion_record_tamper_cases_fail_closed(self) -> None:
        run_mismatch = self._final_authorization().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["authorization_summary"]["run_id"] = "run-999"
        run_mismatch["receipt_summary"]["run_id"] = "run-999"
        ledger, _authorization = self._ledger_and_final_authorization()
        before = ledger.to_dict()
        result = record_release_publish_final_authorization(run_mismatch, ledger=ledger)
        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertEqual(before, ledger.to_dict())

        ledger, authorization = self._ledger_and_final_authorization()
        source_dependency, _source_event = self._completion_source_records(ledger)
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
        result = record_release_publish_final_authorization(authorization, ledger=bad_ledger)
        self.assertIn("source-completion-dependency-digest-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

        ledger, authorization = self._ledger_and_final_authorization()
        _source_dependency, source_event = self._completion_source_records(ledger)
        event_metadata = dict(source_event.metadata)
        event_metadata["package_digest_prefix"] = "0" * 12
        bad_ledger = self._ledger_with_completion_source_metadata(
            ledger,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_final_authorization(authorization, ledger=bad_ledger)
        self.assertIn(
            "source-completion-event-package-digest-prefix-parity-mismatch",
            result.blockers,
        )
        self.assertEqual(before, bad_ledger.to_dict())

    def test_duplicates_secret_action_non_string_unsupported_and_hostile_existing_block(self) -> None:
        ledger, authorization = self._ledger_and_final_authorization()
        first = record_release_publish_final_authorization(authorization, ledger=ledger)
        before_existing = ledger.to_dict()
        duplicate_existing = record_release_publish_final_authorization(
            authorization,
            ledger=ledger,
        )

        self.assertEqual((), first.blockers)
        self.assertIn(
            "release-publish-final-authorization-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-package-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_authorization = self._ledger_and_final_authorization()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_final_authorizations(
            (batch_authorization, batch_authorization.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn("release-publish-final-authorization-dependency-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-final-authorization-event-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-final-authorization-package-digest-duplicate", duplicate_batch.blockers)
        self.assertEqual(before_batch, batch_ledger.to_dict())

        cases = []
        secret = self._final_authorization().to_dict()
        secret["receipt_summary"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-final-authorization-ledger-data"))
        action = self._final_authorization().to_dict()
        action["receipt_summary"]["runner"] = "manual"
        cases.append((action, "action-intent-release-publish-final-authorization-ledger-data"))
        non_string = self._final_authorization().to_dict()
        non_string["receipt_summary"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-final-authorization-ledger-data"))
        unsupported = self._final_authorization().to_dict()
        unsupported["receipt_summary"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-final-authorization-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _authorization = self._ledger_and_final_authorization()
                before = ledger.to_dict()
                result = record_release_publish_final_authorization(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-final-authorization-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-final-authorization-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_final_authorization(
                    self._final_authorization(),
                    ledger=hostile,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        ledger, authorization = self._ledger_and_final_authorization()
        payload = authorization.to_dict()
        original_summary = copy.deepcopy(payload["authorization_summary"])

        result = record_release_publish_final_authorization(payload, ledger=ledger)
        payload["authorization_summary"]["dependency_id"] = "changed"

        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-final-authorization"
        )
        self.assertEqual(original_summary, dependency.metadata["authorization_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-final-authorization"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["authorization_summary"]["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan(self) -> None:
        source = inspect.getsource(final_ledger)
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

    def _ledger_and_final_authorization(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishFinalAuthorization]:
        ledger = RunLedger(run_id=run_id)
        authorization = self._final_authorization_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )
        return ledger, authorization

    def _final_authorization(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishFinalAuthorization:
        ledger = RunLedger(run_id=run_id)
        return self._final_authorization_into_ledger_sources(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )

    def _final_authorization_into_ledger_sources(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishFinalAuthorization:
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
            metadata={"ticket": "HAR-045", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        self.assertEqual((), record_release_publish_intent(intent, ledger=ledger).blockers)
        handoff_readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), handoff_readiness.blockers)
        self.assertEqual(
            (),
            record_release_publish_handoff_readiness(handoff_readiness, ledger=ledger).blockers,
        )
        package = build_release_publish_handoff_package(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), package.blockers)
        self.assertEqual((), record_release_publish_handoff_package(package, ledger=ledger).blockers)
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
        self.assertEqual((), record_release_publish_handoff_acceptance(acceptance, ledger=ledger).blockers)
        acceptance_verification = verify_release_publish_handoff_acceptance_receipt(
            ledger.snapshot().to_dict(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(acceptance_verification.passed)
        completion = evaluate_release_publish_handoff_completion_readiness(
            acceptance_verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(completion.ready)
        self.assertEqual(
            (),
            record_release_publish_handoff_completion_readiness(completion, ledger=ledger).blockers,
        )
        completion_verification = verify_release_publish_handoff_completion_receipt(
            ledger.snapshot().to_dict(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(completion_verification.passed)
        authorization = authorize_release_publish_final(
            completion_verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(authorization.authorized)
        return authorization

    def _completion_source_records(self, ledger: RunLedger) -> tuple[DependencyRecord, AuditEvent]:
        snapshot = ledger.snapshot()
        dependency = next(
            record
            for record in snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-completion"
        )
        event = next(
            record
            for record in snapshot.audit_events
            if record.event_type == "release-publish-handoff-completion-ledger-record"
        )
        return dependency, event

    def _ledger_with_completion_source_metadata(
        self,
        ledger: RunLedger,
        *,
        dependency_metadata: dict[str, object] | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> RunLedger:
        source_dependency, source_event = self._completion_source_records(ledger)
        replacement_dependency = DependencyRecord(
            dependency_id=source_dependency.dependency_id,
            work_id=source_dependency.work_id,
            reference=source_dependency.reference,
            order=source_dependency.order,
            dependency_type=source_dependency.dependency_type,
            required=source_dependency.required,
            status=source_dependency.status,
            metadata=(
                dependency_metadata if dependency_metadata is not None else source_dependency.metadata
            ),
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


if __name__ == "__main__":
    unittest.main()
