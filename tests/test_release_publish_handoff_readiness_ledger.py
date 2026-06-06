from dataclasses import FrozenInstanceError, dataclass, replace
import copy
import hashlib
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_readiness_ledger as handoff_ledger
from harness_orchestrator.release_publish_handoff_readiness import (
    ReleasePublishHandoffReadiness,
    evaluate_release_publish_handoff_readiness,
)
from harness_orchestrator.release_publish_handoff_readiness_ledger import (
    ReleasePublishHandoffReadinessLedgerResult,
    record_release_publish_handoff_readiness,
    record_release_publish_handoff_readinesses,
)
from harness_orchestrator.release_publish_intent import build_release_publish_intent
from harness_orchestrator.release_publish_intent_ledger import (
    record_release_publish_intent,
)
from harness_orchestrator.release_publish_readiness import ReleasePublishReadiness
from harness_orchestrator.run_ledger import DependencyRecord, RunLedger


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictRecord:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


@dataclass(frozen=True)
class PlainReadiness:
    ready: bool
    status: str
    blockers: tuple[str, ...]
    run_id: str
    work_id: str
    dependency_id: str
    event_id: str
    intent_digest_prefix: str
    release_binding_digest_prefix: str
    summary: dict[str, object]


class ReleasePublishHandoffReadinessLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger, readiness = self._ledger_and_readiness()

        result = record_release_publish_handoff_readiness(readiness, ledger=ledger)

        self.assertIsInstance(result, ReleasePublishHandoffReadinessLedgerResult)
        self.assertEqual((), result.blockers)
        self.assertEqual(2, len(ledger.snapshot().dependencies))
        self.assertEqual(2, len(ledger.snapshot().audit_events))
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-readiness"
        )
        event = next(
            record
            for record in ledger.snapshot().audit_events
            if record.event_type == "release-publish-handoff-readiness-ledger-record"
        )
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual("release-publish-handoff-readiness", dependency.dependency_type)
        self.assertEqual("release-publish-handoff-readiness-ledger-record", event.event_type)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("ready", event.status)
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])
        self.assertEqual(
            dependency.metadata["handoff_readiness_digest"],
            event.metadata["handoff_readiness_digest"],
        )
        self.assertEqual(
            self._canonical_digest(dependency.metadata["canonical_payload"]),
            dependency.metadata["handoff_readiness_digest"],
        )

    def test_plain_mapping_to_dict_dataclass_and_batch_record(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._readiness_into_ledger(ledger, work_id="work-001")
        second = self._readiness_into_ledger(ledger, work_id="work-002")
        third = self._readiness_into_ledger(ledger, work_id="work-003")

        result = record_release_publish_handoff_readinesses(
            (
                first.to_dict(),
                ToDictRecord(second.to_dict()),
                PlainReadiness(**third.to_dict()),
            ),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(3, len(result.recorded_dependency_ids))
        self.assertEqual(6, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_handoff_readiness(self._readiness(), ledger=None)
        wrong_ledger = record_release_publish_handoff_readiness(
            self._readiness(),
            ledger=object(),
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_handoff_readiness("bad", ledger=ledger)
        empty = record_release_publish_handoff_readinesses((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-handoff-readiness-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-handoff-readiness-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_status_mismatch_and_ledger_run_mismatch_do_not_mutate(self) -> None:
        cases = []
        cases.append(
            (
                replace(self._readiness(), ready=False, blockers=("operator-review-open",)),
                "release-publish-handoff-readiness-not-ready",
            )
        )
        cases.append(
            (
                replace(self._readiness(), status="blocked"),
                "release-publish-handoff-readiness-status-not-ready",
            )
        )
        cases.append((self._readiness(run_id="run-999"), "ledger-run-id-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_readiness()
                before = ledger.to_dict()
                result = record_release_publish_handoff_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_source_records_must_exist_in_injected_ledger(self) -> None:
        readiness = self._readiness()
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()

        result = record_release_publish_handoff_readiness(readiness, ledger=ledger)

        self.assertIn("source-dependency-missing", result.blockers)
        self.assertIn("source-event-missing", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_list_shaped_summary_metadata_keys_fail_closed_without_mutation(self) -> None:
        ledger, readiness = self._ledger_and_readiness()
        payload = readiness.to_dict()
        payload["summary"]["metadata_keys"] = ["ticket"]
        before = ledger.to_dict()

        result = record_release_publish_handoff_readiness(payload, ledger=ledger)

        self.assertIn("unsafe-summary-metadata-keys", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_summary_shape_and_identity_mismatches_do_not_mutate(self) -> None:
        cases = []
        extra = self._readiness().to_dict()
        extra["summary"]["extra"] = "blocked"
        cases.append((extra, "unsafe-handoff-readiness-summary-schema"))
        format_mismatch = self._readiness().to_dict()
        format_mismatch["summary"]["format"] = "wrong"
        cases.append(
            (
                format_mismatch,
                "release-publish-handoff-readiness-summary-format-mismatch",
            )
        )
        work_mismatch = self._readiness().to_dict()
        work_mismatch["summary"]["work_id"] = "work-999"
        cases.append(
            (
                work_mismatch,
                "release-publish-handoff-readiness-summary-work-id-mismatch",
            )
        )
        digest = self._readiness().to_dict()
        digest["intent_digest_prefix"] = "ABCDEF123456"
        cases.append((digest, "invalid-intent-digest-prefix"))
        target = self._readiness().to_dict()
        target["summary"]["target_type"] = "production"
        cases.append((target, "unsafe-summary-target-type"))
        payload = self._readiness().to_dict()
        payload["summary"]["payload_digest_prefix"] = "short"
        cases.append((payload, "invalid-payload-digest-prefix"))
        task_count = self._readiness().to_dict()
        task_count["summary"]["task_count"] = -1
        cases.append((task_count, "unsafe-summary-task-count"))
        metadata_keys = self._readiness().to_dict()
        metadata_keys["summary"]["metadata_keys"] = ("cmd",)
        cases.append((metadata_keys, "unsafe-summary-metadata-keys"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_readiness()
                before = ledger.to_dict()
                result = record_release_publish_handoff_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_duplicates_in_batch_and_existing_ledger_do_not_mutate(self) -> None:
        ledger, readiness = self._ledger_and_readiness()
        first = record_release_publish_handoff_readiness(readiness, ledger=ledger)
        before_existing = ledger.to_dict()

        duplicate_existing = record_release_publish_handoff_readiness(
            readiness,
            ledger=ledger,
        )

        self.assertIn(
            "release-publish-handoff-readiness-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            first.recorded_event_ids[0],
            tuple(event.event_id for event in ledger.snapshot().audit_events),
        )
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger, batch_readiness = self._ledger_and_readiness()
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_handoff_readinesses(
            (batch_readiness, batch_readiness.to_dict()),
            ledger=batch_ledger,
        )
        self.assertIn(
            "release-publish-handoff-readiness-dependency-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-event-id-duplicate",
            duplicate_batch.blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-digest-duplicate",
            duplicate_batch.blockers,
        )
        self.assertEqual(before_batch, batch_ledger.to_dict())

    def test_existing_digest_in_unrelated_record_blocks(self) -> None:
        seeded, readiness = self._ledger_and_readiness()
        first = record_release_publish_handoff_readiness(readiness, ledger=seeded)
        digest = next(
            record.metadata["handoff_readiness_digest"]
            for record in seeded.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-readiness"
        )
        ledger, _readiness = self._ledger_and_readiness()
        ledger.record_dependency(
            DependencyRecord(
                dependency_id="existing",
                work_id="work-999",
                reference="existing",
                order=1,
                metadata={"handoff_readiness_digest": digest},
            )
        )
        before = ledger.to_dict()

        result = record_release_publish_handoff_readiness(readiness, ledger=ledger)

        self.assertEqual((), first.blockers)
        self.assertIn(
            "release-publish-handoff-readiness-digest-already-recorded",
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_secret_action_malformed_non_string_and_unsupported_fail_closed(self) -> None:
        cases = []
        secret = self._readiness().to_dict()
        secret["summary"]["metadata_keys"] = ("api_key",)
        cases.append((secret, "secret-like-release-publish-handoff-readiness-ledger-data"))
        action = self._readiness().to_dict()
        action["summary"]["metadata_keys"] = ("runner",)
        cases.append((action, "action-intent-release-publish-handoff-readiness-ledger-data"))
        non_string = self._readiness().to_dict()
        non_string["summary"] = {object(): "bad"}
        cases.append(
            (
                non_string,
                "non-string-key-release-publish-handoff-readiness-ledger-data",
            )
        )
        unsupported = self._readiness().to_dict()
        unsupported["summary"]["target_id"] = object()
        cases.append((unsupported, "malformed-release-publish-handoff-readiness-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger, _readiness = self._ledger_and_readiness()
                before = ledger.to_dict()
                result = record_release_publish_handoff_readiness(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_hostile_existing_ledger_metadata_blocks_without_mutation(self) -> None:
        cases = (
            ({object(): "bad"}, "malformed-release-publish-handoff-readiness-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-handoff-readiness-ledger-data"),
        )
        for metadata, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001", metadata=metadata)
                before = ledger.to_dict()
                result = record_release_publish_handoff_readiness(
                    self._readiness(),
                    ledger=ledger,
                )
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        ledger, readiness = self._ledger_and_readiness()
        payload = readiness.to_dict()
        original_summary = copy.deepcopy(payload["summary"])

        result = record_release_publish_handoff_readiness(payload, ledger=ledger)
        payload["summary"]["work_id"] = "changed"
        payload["summary"]["metadata_keys"] = ("changed",)

        self.assertEqual((), result.blockers)
        dependency = next(
            record
            for record in ledger.snapshot().dependencies
            if record.dependency_type == "release-publish-handoff-readiness"
        )
        self.assertEqual(original_summary, dependency.metadata["handoff_readiness_summary"])
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_dependency = next(
            record
            for record in result.ledger_snapshot.dependencies
            if record.dependency_type == "release-publish-handoff-readiness"
        )
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["handoff_readiness_digest"] = "changed"
        with self.assertRaises(TypeError):
            snapshot_dependency.metadata["canonical_payload"]["format"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
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
        imports = tuple(
            line for line in source.splitlines() if line.startswith(("import ", "from "))
        )
        self.assertIn(
            "from harness_orchestrator.release_publish_handoff_readiness import (",
            imports,
        )

    def _ledger_and_readiness(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[RunLedger, ReleasePublishHandoffReadiness]:
        ledger = RunLedger(run_id=run_id)
        readiness = self._readiness_into_ledger(
            ledger,
            run_id=run_id,
            work_id=work_id,
        )
        return ledger, readiness

    def _readiness(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffReadiness:
        ledger = RunLedger(run_id=run_id)
        return self._readiness_into_ledger(ledger, run_id=run_id, work_id=work_id)

    def _readiness_into_ledger(
        self,
        ledger: RunLedger,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> ReleasePublishHandoffReadiness:
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
            metadata={"ticket": "HAR-034", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), result.blockers)
        readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), readiness.blockers)
        return readiness

    def _canonical_digest(self, payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


if __name__ == "__main__":
    unittest.main()
