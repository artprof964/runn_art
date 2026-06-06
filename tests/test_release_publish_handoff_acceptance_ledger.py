from dataclasses import FrozenInstanceError
import copy
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_acceptance_ledger as acceptance_ledger
from harness_orchestrator.release_publish_handoff_acceptance import (
    ReleasePublishHandoffAcceptance,
    evaluate_release_publish_handoff_acceptance,
)
from harness_orchestrator.release_publish_handoff_acceptance_ledger import (
    ReleasePublishHandoffAcceptanceLedgerResult,
    record_release_publish_handoff_acceptance,
    record_release_publish_handoff_acceptances,
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


class ToDictAcceptance:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class ReleasePublishHandoffAcceptanceLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()

        result = record_release_publish_handoff_acceptance(acceptance, ledger=ledger)

        self.assertIsInstance(result, ReleasePublishHandoffAcceptanceLedgerResult)
        self.assertEqual((), result.blockers)
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-acceptance"
        )
        event = next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-handoff-acceptance-ledger-record"
        )
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-handoff-acceptance:work-001:{acceptance.package_digest[:16]}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-handoff-acceptance-recorded:work-001:{acceptance.package_digest[:16]}",
            event.event_id,
        )
        self.assertEqual("accepted", dependency.status)
        self.assertEqual("accepted", event.status)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(acceptance.dependency_id, dependency.metadata["source_dependency_id"])
        self.assertEqual(acceptance.event_id, dependency.metadata["source_event_id"])
        self.assertEqual(acceptance.package_digest, dependency.metadata["package_digest"])
        self.assertEqual(
            acceptance.to_dict()["acceptance_summary"],
            dependency.metadata["acceptance_summary"],
        )

    def test_mapping_to_dict_inputs_and_batch_record(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._acceptance_into_ledger(ledger, work_id="work-001")
        second = self._acceptance_into_ledger(ledger, work_id="work-002")

        result = record_release_publish_handoff_acceptances(
            (first.to_dict(), ToDictAcceptance(second.to_dict())),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(2, len(result.recorded_dependency_ids))
        self.assertEqual(8, len(ledger.snapshot().dependencies))
        self.assertEqual(8, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_handoff_acceptance(self._acceptance(), ledger=None)
        wrong_ledger = record_release_publish_handoff_acceptance(
            self._acceptance(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_handoff_acceptance("bad", ledger=ledger)
        empty = record_release_publish_handoff_acceptances((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-handoff-acceptance-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-handoff-acceptance-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_malformed_schema_prefix_and_summary_mismatch_do_not_mutate(self) -> None:
        cases = []
        blocked = self._acceptance().to_dict()
        blocked["accepted"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-handoff-acceptance-not-accepted"))
        extra = self._acceptance().to_dict()
        extra["extra"] = "bad"
        cases.append((extra, "unsafe-release-publish-handoff-acceptance-schema"))
        digest = self._acceptance().to_dict()
        digest["package_digest"] = "f" * 63
        cases.append((digest, "invalid-package-digest"))
        prefix = self._acceptance().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-handoff-acceptance-package-digest-prefix-mismatch"))
        summary = self._acceptance().to_dict()
        summary["acceptance_summary"]["accepted"] = False
        cases.append((summary, "release-publish-handoff-acceptance-acceptance-summary-accepted-mismatch"))
        receipt = self._acceptance().to_dict()
        receipt["receipt_summary"]["source_blocker_count"] = 1
        cases.append((receipt, "release-publish-handoff-acceptance-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _acceptance = self._ledger_and_acceptance()
                before = ledger.to_dict()
                result = record_release_publish_handoff_acceptance(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_ledger_run_work_source_record_mismatch_and_metadata_parity_fail_closed(self) -> None:
        cases = []
        run_mismatch = self._acceptance().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["acceptance_summary"]["run_id"] = "run-999"
        run_mismatch["receipt_summary"]["run_id"] = "run-999"
        cases.append((run_mismatch, "ledger-run-id-mismatch"))
        work_mismatch = self._acceptance().to_dict()
        work_mismatch["work_id"] = "work-999"
        work_mismatch["acceptance_summary"]["work_id"] = "work-999"
        work_mismatch["receipt_summary"]["work_id"] = "work-999"
        cases.append((work_mismatch, "release-publish-handoff-acceptance-dependency-id-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _acceptance = self._ledger_and_acceptance()
                before = ledger.to_dict()
                result = record_release_publish_handoff_acceptance(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

        ledger, acceptance = self._ledger_and_acceptance()
        package_dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-package"
        )
        tampered = DependencyRecord(
            dependency_id=package_dependency.dependency_id,
            work_id=package_dependency.work_id,
            reference=package_dependency.reference,
            order=package_dependency.order,
            dependency_type=package_dependency.dependency_type,
            required=package_dependency.required,
            status=package_dependency.status,
            metadata={**package_dependency.metadata, "package_digest": "c" * 64},
        )
        bad_ledger = RunLedger(
            run_id="run-001",
            dependencies=tuple(
                tampered if record is package_dependency else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=ledger.snapshot().audit_events,
        )
        before = bad_ledger.to_dict()
        result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)
        self.assertIn("source-package-dependency-digest-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_source_package_dependency_metadata_schema_tampering_blocks_without_mutation(self) -> None:
        cases = []
        ledger, _acceptance = self._ledger_and_acceptance()
        package_dependency, _package_event = self._package_source_records(ledger)

        extra = dict(package_dependency.metadata)
        extra["extra"] = "bad"
        cases.append((extra, "source-package-dependency-metadata-schema-mismatch"))

        missing = dict(package_dependency.metadata)
        missing.pop("canonical_payload")
        cases.append((missing, "source-package-dependency-metadata-schema-mismatch"))

        for metadata, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, acceptance = self._ledger_and_acceptance()
                bad_ledger = self._ledger_with_package_source_metadata(
                    ledger,
                    dependency_metadata=metadata,
                )
                before = bad_ledger.to_dict()
                result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, bad_ledger.to_dict())

    def test_source_package_event_metadata_schema_tampering_blocks_without_mutation(self) -> None:
        cases = []
        ledger, _acceptance = self._ledger_and_acceptance()
        _package_dependency, package_event = self._package_source_records(ledger)

        extra = dict(package_event.metadata)
        extra["extra"] = "bad"
        cases.append((extra, "source-package-event-metadata-schema-mismatch"))

        missing = dict(package_event.metadata)
        missing.pop("canonical_payload")
        cases.append((missing, "source-package-event-metadata-schema-mismatch"))

        for metadata, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, acceptance = self._ledger_and_acceptance()
                bad_ledger = self._ledger_with_package_source_metadata(
                    ledger,
                    event_metadata=metadata,
                )
                before = bad_ledger.to_dict()
                result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, bad_ledger.to_dict())

    def test_source_package_event_metadata_parity_tampering_blocks_without_mutation(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        _package_dependency, package_event = self._package_source_records(ledger)
        event_metadata = dict(package_event.metadata)
        event_metadata["intent_digest_prefix"] = "0" * 12
        bad_ledger = self._ledger_with_package_source_metadata(
            ledger,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()

        result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)

        self.assertIn("source-package-event-intent-digest-prefix-parity-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_source_package_event_dependency_id_tampering_blocks_without_mutation(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        _package_dependency, package_event = self._package_source_records(ledger)
        event_metadata = dict(package_event.metadata)
        event_metadata["dependency_id"] = "release-publish-handoff-package:elsewhere:0000000000000000"
        bad_ledger = self._ledger_with_package_source_metadata(
            ledger,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()

        result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)

        self.assertIn("source-package-event-dependency-id-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_source_package_canonical_payload_tampering_blocks_without_mutation(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        package_dependency, _package_event = self._package_source_records(ledger)
        dependency_metadata = dict(package_dependency.metadata)
        event_metadata = dict(dependency_metadata)
        dependency_metadata["canonical_payload"] = {
            **dependency_metadata["canonical_payload"],
            "format": "changed",
        }
        event_metadata["canonical_payload"] = dependency_metadata["canonical_payload"]
        event_metadata["dependency_id"] = package_dependency.dependency_id
        bad_ledger = self._ledger_with_package_source_metadata(
            ledger,
            dependency_metadata=dependency_metadata,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()

        result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)

        self.assertIn("source-package-dependency-canonical-payload-format-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_source_package_data_canonical_parity_tampering_blocks_without_mutation(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        package_dependency, _package_event = self._package_source_records(ledger)
        dependency_metadata = dict(package_dependency.metadata)
        package_data = dict(dependency_metadata["package_data"])
        package_data["intent_event_id"] = "event-work-001-changed"
        dependency_metadata["package_data"] = package_data
        event_metadata = {"dependency_id": package_dependency.dependency_id, **dependency_metadata}
        bad_ledger = self._ledger_with_package_source_metadata(
            ledger,
            dependency_metadata=dependency_metadata,
            event_metadata=event_metadata,
        )
        before = bad_ledger.to_dict()

        result = record_release_publish_handoff_acceptance(acceptance, ledger=bad_ledger)

        self.assertIn("source-package-dependency-package-data-digest-mismatch", result.blockers)
        self.assertIn("source-package-dependency-canonical-payload-package-mismatch", result.blockers)
        self.assertEqual(before, bad_ledger.to_dict())

    def test_secret_action_non_string_key_unsupported_and_hostile_existing_metadata_block(self) -> None:
        cases = []
        secret = self._acceptance().to_dict()
        secret["acceptance_summary"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-handoff-acceptance-ledger-data"))
        action = self._acceptance().to_dict()
        action["acceptance_summary"]["runner"] = "manual"
        cases.append((action, "action-intent-release-publish-handoff-acceptance-ledger-data"))
        non_string = self._acceptance().to_dict()
        non_string["receipt_summary"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-handoff-acceptance-ledger-data"))
        unsupported = self._acceptance().to_dict()
        unsupported["receipt_summary"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-handoff-acceptance-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _acceptance = self._ledger_and_acceptance()
                before = ledger.to_dict()
                result = record_release_publish_handoff_acceptance(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-handoff-acceptance-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-handoff-acceptance-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_handoff_acceptance(self._acceptance(), ledger=hostile)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

    def test_duplicates_in_batch_and_existing_ledger_do_not_mutate(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        first = record_release_publish_handoff_acceptance(acceptance, ledger=ledger)
        before_existing = ledger.to_dict()

        duplicate_existing = record_release_publish_handoff_acceptance(acceptance, ledger=ledger)

        self.assertEqual((), first.blockers)
        self.assertIn(
            "release-publish-handoff-acceptance-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-package-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_acceptance = self._ledger_and_acceptance()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_handoff_acceptances(
            (batch_acceptance, batch_acceptance.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-dependency-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-event-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-package-digest-duplicate",
            duplicate_batch.blockers,
        )
        self.assertEqual(before_batch, batch_ledger.to_dict())

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        ledger, acceptance = self._ledger_and_acceptance()
        payload = acceptance.to_dict()
        original_summary = copy.deepcopy(payload["acceptance_summary"])

        result = record_release_publish_handoff_acceptance(payload, ledger=ledger)
        payload["acceptance_summary"]["dependency_id"] = "changed"

        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-acceptance"
        )
        self.assertEqual(original_summary, dependency.metadata["acceptance_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-acceptance"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["acceptance_summary"]["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(acceptance_ledger)
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

    def _ledger_and_acceptance(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishHandoffAcceptance]:
        ledger = RunLedger(run_id=run_id)
        acceptance = self._acceptance_into_ledger(ledger, run_id=run_id, work_id=work_id)
        return ledger, acceptance

    def _acceptance(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffAcceptance:
        ledger = RunLedger(run_id=run_id)
        return self._acceptance_into_ledger(ledger, run_id=run_id, work_id=work_id)

    def _acceptance_into_ledger(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffAcceptance:
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
            metadata={"ticket": "HAR-039", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        intent_result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), intent_result.blockers)
        readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), readiness.blockers)
        readiness_result = record_release_publish_handoff_readiness(readiness, ledger=ledger)
        self.assertEqual((), readiness_result.blockers)
        package = build_release_publish_handoff_package(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), package.blockers)
        package_result = record_release_publish_handoff_package(package, ledger=ledger)
        self.assertEqual((), package_result.blockers)
        verification = verify_release_publish_handoff_receipt(
            ledger.snapshot().to_dict(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(verification.passed)
        acceptance = evaluate_release_publish_handoff_acceptance(
            verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(acceptance.accepted)
        return acceptance

    def _package_source_records(self, ledger: RunLedger) -> tuple[DependencyRecord, AuditEvent]:
        snapshot = ledger.snapshot()
        package_dependency = next(
            record
            for record in snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-package"
        )
        package_event = next(
            event
            for event in snapshot.audit_events
            if event.event_type == "release-publish-handoff-package-ledger-record"
        )
        return package_dependency, package_event

    def _ledger_with_package_source_metadata(
        self,
        ledger: RunLedger,
        *,
        dependency_metadata: dict[str, object] | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> RunLedger:
        package_dependency, package_event = self._package_source_records(ledger)
        replacement_dependency = DependencyRecord(
            dependency_id=package_dependency.dependency_id,
            work_id=package_dependency.work_id,
            reference=package_dependency.reference,
            order=package_dependency.order,
            dependency_type=package_dependency.dependency_type,
            required=package_dependency.required,
            status=package_dependency.status,
            metadata=(
                dependency_metadata
                if dependency_metadata is not None
                else package_dependency.metadata
            ),
        )
        replacement_event = AuditEvent(
            event_id=package_event.event_id,
            work_id=package_event.work_id,
            event_type=package_event.event_type,
            status=package_event.status,
            message=package_event.message,
            occurred_at=package_event.occurred_at,
            actor=package_event.actor,
            metadata=event_metadata if event_metadata is not None else package_event.metadata,
        )
        return RunLedger(
            run_id=ledger.run_id,
            gate_decisions=ledger.snapshot().gate_decisions,
            dependencies=tuple(
                replacement_dependency
                if record.dependency_id == package_dependency.dependency_id
                else record
                for record in ledger.snapshot().dependencies
            ),
            audit_events=tuple(
                replacement_event
                if event.event_id == package_event.event_id
                else event
                for event in ledger.snapshot().audit_events
            ),
            tasks=ledger.snapshot().tasks,
            metadata=ledger.snapshot().metadata,
        )


if __name__ == "__main__":
    unittest.main()
