from dataclasses import FrozenInstanceError
import copy
import hashlib
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_package_ledger as package_ledger
from harness_orchestrator.release_publish_handoff_package import (
    ReleasePublishHandoffPackage,
    build_release_publish_handoff_package,
)
from harness_orchestrator.release_publish_handoff_package_ledger import (
    ReleasePublishHandoffPackageLedgerResult,
    record_release_publish_handoff_package,
    record_release_publish_handoff_packages,
)
from harness_orchestrator.release_publish_handoff_readiness import (
    evaluate_release_publish_handoff_readiness,
)
from harness_orchestrator.release_publish_handoff_readiness_ledger import (
    record_release_publish_handoff_readiness,
)
from harness_orchestrator.release_publish_intent import build_release_publish_intent
from harness_orchestrator.release_publish_intent_ledger import record_release_publish_intent
from harness_orchestrator.release_publish_readiness import ReleasePublishReadiness
from harness_orchestrator.run_ledger import DependencyRecord, RunLedger


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictRecord:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class ReleasePublishHandoffPackageLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, package = self._ledger_and_package()

        result = record_release_publish_handoff_package(package, ledger=ledger)

        self.assertIsInstance(result, ReleasePublishHandoffPackageLedgerResult)
        self.assertEqual((), result.blockers)
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-package"
        )
        event = next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-handoff-package-ledger-record"
        )
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual(
            f"release-publish-handoff-package:work-001:{package.package_digest[:16]}",
            dependency.dependency_id,
        )
        self.assertEqual(
            f"release-publish-handoff-package-recorded:work-001:{package.package_digest[:16]}",
            event.event_id,
        )
        self.assertEqual("ready", dependency.status)
        self.assertEqual("ready", event.status)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(package.package_digest, dependency.metadata["package_digest"])
        self.assertEqual(package.to_dict()["package_data"], dependency.metadata["package_data"])

    def test_mapping_to_dict_inputs_and_batch_record(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._package_into_ledger(ledger, work_id="work-001")
        second = self._package_into_ledger(ledger, work_id="work-002")

        result = record_release_publish_handoff_packages(
            (first.to_dict(), ToDictRecord(second.to_dict())),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(2, len(result.recorded_dependency_ids))
        self.assertEqual(6, len(ledger.snapshot().dependencies))
        self.assertEqual(6, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_handoff_package(self._package(), ledger=None)
        wrong_ledger = record_release_publish_handoff_package(self._package(), ledger=object())
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_handoff_package("bad", ledger=ledger)
        empty = record_release_publish_handoff_packages((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-handoff-package-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-handoff-packages-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_package_run_work_and_source_mismatch_do_not_mutate(self) -> None:
        cases = []
        blocked = self._package().to_dict()
        blocked["ready"] = False
        blocked["status"] = "blocked"
        blocked["blockers"] = ("operator-review-open",)
        cases.append((blocked, "release-publish-handoff-package-not-ready"))
        run_mismatch = self._package().to_dict()
        run_mismatch["run_id"] = "run-999"
        run_mismatch["package_data"]["run_id"] = "run-999"
        run_mismatch["package_digest"] = self._canonical_digest(run_mismatch["package_data"])
        run_mismatch["package_digest_prefix"] = run_mismatch["package_digest"][:12]
        cases.append((run_mismatch, "ledger-run-id-mismatch"))
        work_mismatch = self._package().to_dict()
        work_mismatch["work_id"] = "work-999"
        cases.append((work_mismatch, "release-publish-handoff-package-package-data-work-id-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _package = self._ledger_and_package()
                before = ledger.to_dict()
                result = record_release_publish_handoff_package(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_malformed_schema_digest_tampering_prefix_and_metadata_block(self) -> None:
        cases = []
        extra = self._package().to_dict()
        extra["package_data"]["extra"] = "bad"
        cases.append((extra, "unsafe-package-data-schema"))
        digest = self._package().to_dict()
        digest["package_data"]["work_id"] = "work-999"
        cases.append((digest, "release-publish-handoff-package-digest-mismatch"))
        prefix = self._package().to_dict()
        prefix["package_digest_prefix"] = "0" * 12
        cases.append((prefix, "release-publish-handoff-package-package-digest-prefix-mismatch"))
        target = self._package().to_dict()
        target["package_data"]["publish_target"]["target_type"] = "production"
        cases.append((target, "unsafe-publish-target-schema"))
        metadata = self._package().to_dict()
        metadata["package_data"]["metadata"] = {"api_key": "raw"}
        cases.append((metadata, "secret-like-release-publish-handoff-package-ledger-data"))
        action = self._package().to_dict()
        action["package_data"]["metadata"] = {"runner": "manual"}
        cases.append((action, "action-intent-release-publish-handoff-package-ledger-data"))
        non_string = self._package().to_dict()
        non_string["package_data"]["metadata"] = {object(): "bad"}
        cases.append((non_string, "non-string-key-release-publish-handoff-package-ledger-data"))
        unsupported = self._package().to_dict()
        unsupported["package_data"]["artifact"]["bad"] = object()
        cases.append((unsupported, "unsupported-object-release-publish-handoff-package-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _package = self._ledger_and_package()
                before = ledger.to_dict()
                result = record_release_publish_handoff_package(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_duplicates_in_batch_and_existing_ledger_do_not_mutate(self) -> None:
        ledger, package = self._ledger_and_package()
        first = record_release_publish_handoff_package(package, ledger=ledger)
        before_existing = ledger.to_dict()

        duplicate_existing = record_release_publish_handoff_package(package, ledger=ledger)

        self.assertEqual((), first.blockers)
        self.assertIn(
            "release-publish-handoff-package-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_package = self._ledger_and_package()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_handoff_packages(
            (batch_package, batch_package.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn(
            "release-publish-handoff-package-dependency-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-event-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-digest-duplicate",
            duplicate_batch.blockers,
        )
        self.assertEqual(before_batch, batch_ledger.to_dict())

    def test_existing_digest_in_unrelated_record_and_hostile_existing_metadata_block(self) -> None:
        seeded, package = self._ledger_and_package()
        first = record_release_publish_handoff_package(package, ledger=seeded)
        ledger, package = self._ledger_and_package()
        ledger.record_dependency(
            DependencyRecord(
                dependency_id="existing",
                work_id="work-999",
                reference="existing",
                order=1,
                metadata={"package_digest": first.skipped_package_digests[0] if first.skipped_package_digests else package.package_digest},
            )
        )
        before = ledger.to_dict()

        duplicate_digest = record_release_publish_handoff_package(package, ledger=ledger)

        self.assertIn(
            "release-publish-handoff-package-digest-already-recorded",
            duplicate_digest.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

        for metadata, blocker in (
            ({object(): "bad"}, "malformed-release-publish-handoff-package-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-handoff-package-ledger-data"),
        ):
            with self.subTest(blocker=blocker):
                hostile = RunLedger(run_id="run-001", metadata=metadata)
                before_hostile = hostile.to_dict()
                result = record_release_publish_handoff_package(self._package(), ledger=hostile)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before_hostile, hostile.to_dict())

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        ledger, package = self._ledger_and_package()
        payload = package.to_dict()
        original_data = copy.deepcopy(payload["package_data"])

        result = record_release_publish_handoff_package(payload, ledger=ledger)
        payload["package_data"]["publish_target"]["target_id"] = "changed"

        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-package"
        )
        self.assertEqual(original_data, dependency.metadata["package_data"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-package"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["package_data"]["format"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(package_ledger)
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

    def _ledger_and_package(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishHandoffPackage]:
        ledger = RunLedger(run_id=run_id)
        package = self._package_into_ledger(ledger, run_id=run_id, work_id=work_id)
        return ledger, package

    def _package(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffPackage:
        ledger = RunLedger(run_id=run_id)
        return self._package_into_ledger(ledger, run_id=run_id, work_id=work_id)

    def _package_into_ledger(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffPackage:
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
            metadata={"ticket": "HAR-036", "approved": True, "count": 2},
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
        handoff_result = record_release_publish_handoff_readiness(readiness, ledger=ledger)
        self.assertEqual((), handoff_result.blockers)
        package = build_release_publish_handoff_package(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), package.blockers)
        return package

    def _canonical_digest(self, payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


if __name__ == "__main__":
    unittest.main()
