from dataclasses import FrozenInstanceError, dataclass, replace
import copy
import hashlib
import inspect
import json
import unittest

import harness_orchestrator.release_publish_intent_ledger as intent_ledger
from harness_orchestrator.release_publish_intent import (
    ReleasePublishIntentResult,
    build_release_publish_intent,
)
from harness_orchestrator.release_publish_intent_ledger import (
    ReleasePublishIntentLedgerResult,
    record_release_publish_intent,
    record_release_publish_intents,
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
class PlainIntentResult:
    passed: bool
    blockers: tuple[str, ...]
    intent: dict[str, object] | None
    summary: dict[str, object] | None


class ReleasePublishIntentLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger = RunLedger(run_id="run-001")
        intent = self._intent()

        result = record_release_publish_intent(intent, ledger=ledger)

        self.assertIsInstance(result, ReleasePublishIntentLedgerResult)
        self.assertEqual((), result.blockers)
        self.assertEqual(1, len(ledger.snapshot().dependencies))
        self.assertEqual(1, len(ledger.snapshot().audit_events))
        dependency = ledger.snapshot().dependencies[0]
        event = ledger.snapshot().audit_events[0]
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual("release-publish-intent", dependency.dependency_type)
        self.assertEqual("release-publish-intent-ledger-record", event.event_type)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("ready", event.status)
        self.assertEqual(intent.intent.intent_digest, dependency.metadata["intent_digest"])
        self.assertEqual(dependency.dependency_id, event.metadata["dependency_id"])

    def test_plain_mapping_to_dict_dataclass_and_direct_intent_inputs_record(self) -> None:
        first = self._intent(work_id="work-001")
        second = self._intent(work_id="work-002")
        third = self._intent(work_id="work-003")
        fourth = self._intent(work_id="work-004")
        ledger = RunLedger(run_id="run-001")

        result = record_release_publish_intents(
            (
                first.to_dict(),
                ToDictRecord(second.to_dict()),
                PlainIntentResult(**third.to_dict()),
                fourth.intent,
            ),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(4, len(result.recorded_dependency_ids))
        self.assertEqual(4, len(ledger.snapshot().audit_events))

    def test_missing_wrong_ledger_empty_and_wrong_input_fail_closed(self) -> None:
        missing = record_release_publish_intent(self._intent(), ledger=None)
        wrong_ledger = record_release_publish_intent(self._intent(), ledger=object())
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_publish_intent("not-intent-data", ledger=ledger)
        empty = record_release_publish_intents((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-publish-intent-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-publish-intents-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_unpassed_and_missing_intent_do_not_mutate(self) -> None:
        ledger = RunLedger(run_id="run-001")
        blocked = replace(
            self._intent(),
            passed=False,
            blockers=("operator-review-open",),
        )
        missing_intent = replace(self._intent(), intent=None)
        before = ledger.to_dict()

        result = record_release_publish_intents((blocked, missing_intent), ledger=ledger)

        self.assertIn("release-publish-intent-not-passed", result.blockers)
        self.assertIn("release-publish-intent-blockers-present", result.blockers)
        self.assertIn("release-publish-intent-operator-review-open", result.blockers)
        self.assertIn("release-publish-intent-missing", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_run_digest_canonical_and_summary_mismatches_do_not_mutate(self) -> None:
        cases = []
        run_mismatch = self._intent().to_dict()
        run_mismatch["intent"]["run_id"] = "run-999"
        cases.append((run_mismatch, "ledger-run-id-mismatch"))
        digest_mismatch = self._intent().to_dict()
        digest_mismatch["intent"]["intent_digest"] = "0" * 64
        cases.append((digest_mismatch, "release-publish-intent-digest-mismatch"))
        canonical_format = self._intent().to_dict()
        canonical_format["intent"]["canonical_payload"]["format"] = "bad"
        cases.append((canonical_format, "release-publish-intent-format-mismatch"))
        canonical_field = self._intent().to_dict()
        canonical_field["intent"]["canonical_payload"]["readiness_binding"]["work_id"] = "work-999"
        cases.append((canonical_field, "release-publish-intent-canonical-work-id-mismatch"))
        summary_field = self._intent().to_dict()
        summary_field["summary"]["target_id"] = "target-999"
        cases.append((summary_field, "release-publish-intent-summary-target-id-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_canonical_schema_exactness_rejects_extra_fields_without_mutation(self) -> None:
        cases = []
        top_level = self._intent().to_dict()
        top_level["intent"]["canonical_payload"]["extra"] = "blocked"
        self._recompute_intent_digest(top_level)
        cases.append((top_level, "unsafe-canonical-payload-schema"))
        readiness = self._intent().to_dict()
        readiness["intent"]["canonical_payload"]["readiness_binding"]["extra"] = "blocked"
        self._recompute_intent_digest(readiness)
        cases.append((readiness, "unsafe-readiness-binding-schema"))
        caller = self._intent().to_dict()
        caller["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "extra"
        ] = "blocked"
        self._recompute_intent_digest(caller)
        cases.append((caller, "unsafe-caller-intent-metadata-schema"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_canonical_fixed_literals_are_verified_without_mutation(self) -> None:
        cases = []
        source = self._intent().to_dict()
        source["intent"]["canonical_payload"]["readiness_binding"]["source"] = "wrong"
        self._recompute_intent_digest(source)
        cases.append(
            (
                source,
                "release-publish-intent-canonical-readiness-source-mismatch",
            )
        )
        status = self._intent().to_dict()
        status["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "verification_status"
        ] = "wrong"
        self._recompute_intent_digest(status)
        cases.append(
            (
                status,
                "release-publish-intent-canonical-verification-status-mismatch",
            )
        )

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_canonical_digest_prefix_is_verified_without_mutation(self) -> None:
        cases = []
        mismatch = self._intent().to_dict()
        mismatch["intent"]["canonical_payload"]["readiness_binding"][
            "canonical_digest_prefix"
        ] = "cccccccccccc"
        self._recompute_intent_digest(mismatch)
        cases.append(
            (
                mismatch,
                "release-publish-intent-canonical-digest-prefix-mismatch",
            )
        )
        uppercase = self._intent().to_dict()
        uppercase["intent"]["canonical_payload"]["readiness_binding"][
            "canonical_digest_prefix"
        ] = "A1B2C3D4E5F6"
        self._recompute_intent_digest(uppercase)
        cases.append((uppercase, "invalid-canonical-digest-prefix"))
        short = self._intent().to_dict()
        short["intent"]["canonical_payload"]["readiness_binding"][
            "canonical_digest_prefix"
        ] = "a1b2c3"
        self._recompute_intent_digest(short)
        cases.append((short, "invalid-canonical-digest-prefix"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_nested_canonical_schema_is_verified_without_mutation(self) -> None:
        cases = []
        target = self._intent().to_dict()
        target["intent"]["publish_target"]["extra"] = "blocked"
        target["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "publish_target"
        ]["extra"] = "blocked"
        self._recompute_intent_digest(target)
        cases.append((target, "unsafe-canonical-publish-target-schema"))
        payload = self._intent().to_dict()
        payload["intent"]["publish_payload"]["extra"] = "blocked"
        payload["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "publish_payload"
        ]["extra"] = "blocked"
        self._recompute_intent_digest(payload)
        cases.append((payload, "unsafe-canonical-publish-payload-schema"))
        artifact = self._intent().to_dict()
        artifact["intent"]["artifact"]["extra"] = "blocked"
        artifact["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "artifact"
        ]["extra"] = "blocked"
        self._recompute_intent_digest(artifact)
        cases.append((artifact, "unsafe-canonical-artifact-schema"))
        nested_metadata = self._intent().to_dict()
        nested_metadata["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "metadata"
        ]["nested"] = {"value": "blocked"}
        self._recompute_intent_digest(nested_metadata)
        cases.append((nested_metadata, "unsafe-canonical-metadata-schema"))
        float_metadata = self._intent().to_dict()
        float_metadata["intent"]["canonical_payload"]["caller_supplied_intent_metadata"][
            "metadata"
        ]["score"] = 1.5
        self._recompute_intent_digest(float_metadata)
        cases.append((float_metadata, "unsafe-canonical-metadata-schema"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_canonical_metadata_reserved_keys_fail_without_mutation(self) -> None:
        for key in ("run_id", "identity", "url"):
            with self.subTest(key=key):
                payload = self._intent().to_dict()
                payload["intent"]["metadata"][key] = "blocked"
                payload["intent"]["canonical_payload"][
                    "caller_supplied_intent_metadata"
                ]["metadata"][key] = "blocked"
                self._recompute_intent_digest(payload)
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()

                result = record_release_publish_intent(payload, ledger=ledger)

                self.assertIn("unsafe-canonical-metadata-schema", result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_invalid_digest_and_strict_blank_identity_do_not_mutate(self) -> None:
        cases = []
        upper = self._intent().to_dict()
        upper["intent"]["release_binding_digest"] = _BINDING_DIGEST.upper()
        cases.append((upper, "invalid-release-binding-digest"))
        padded = self._intent().to_dict()
        padded["intent"]["intent_digest"] = f" {padded['intent']['intent_digest']} "
        cases.append((padded, "invalid-intent-digest"))
        blank_run = self._intent().to_dict()
        blank_run["intent"]["run_id"] = ""
        cases.append((blank_run, "missing-run-id"))
        padded_work = self._intent().to_dict()
        padded_work["intent"]["work_id"] = " work-001 "
        cases.append((padded_work, "unsafe-work-id"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_plain_mapping_run_id_with_slash_is_unsafe_without_mutation(self) -> None:
        payload = self._intent().to_dict()
        payload["intent"]["run_id"] = "run/001"
        payload["intent"]["canonical_payload"]["readiness_binding"]["run_id"] = "run/001"
        payload["summary"]["run_id"] = "run/001"
        self._recompute_intent_digest(payload)
        ledger = RunLedger(run_id="run/001")
        before = ledger.to_dict()

        result = record_release_publish_intent(payload, ledger=ledger)

        self.assertIn("unsafe-run-id", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_plain_mapping_work_id_with_slash_is_unsafe_without_mutation(self) -> None:
        payload = self._intent().to_dict()
        payload["intent"]["work_id"] = "work/001"
        payload["intent"]["canonical_payload"]["readiness_binding"]["work_id"] = "work/001"
        payload["summary"]["work_id"] = "work/001"
        self._recompute_intent_digest(payload)
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()

        result = record_release_publish_intent(payload, ledger=ledger)

        self.assertIn("unsafe-work-id", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_duplicates_in_batch_and_existing_ledger_do_not_mutate(self) -> None:
        ledger = RunLedger(run_id="run-001")
        intent = self._intent()
        first = record_release_publish_intent(intent, ledger=ledger)
        before_existing = ledger.to_dict()

        duplicate_existing = record_release_publish_intent(intent, ledger=ledger)

        self.assertIn(
            "release-publish-intent-dependency-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-intent-event-id-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertIn(
            "release-publish-intent-digest-already-recorded",
            duplicate_existing.blockers,
        )
        self.assertEqual(first.recorded_event_ids[0], ledger.snapshot().audit_events[0].event_id)
        self.assertEqual(before_existing, ledger.to_dict())

        batch_ledger = RunLedger(run_id="run-001")
        before_batch = batch_ledger.to_dict()
        duplicate_batch = record_release_publish_intents((intent, intent.to_dict()), ledger=batch_ledger)
        self.assertIn("release-publish-intent-dependency-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-intent-event-id-duplicate", duplicate_batch.blockers)
        self.assertIn("release-publish-intent-digest-duplicate", duplicate_batch.blockers)
        self.assertEqual(before_batch, batch_ledger.to_dict())

    def test_existing_digest_in_unrelated_record_blocks(self) -> None:
        intent = self._intent()
        ledger = RunLedger(
            run_id="run-001",
            dependencies=(
                DependencyRecord(
                    dependency_id="existing",
                    work_id="work-999",
                    reference="existing",
                    order=1,
                    metadata={"intent_digest": intent.intent.intent_digest},
                ),
            ),
        )
        before = ledger.to_dict()

        result = record_release_publish_intent(intent, ledger=ledger)

        self.assertIn("release-publish-intent-digest-already-recorded", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_hostile_existing_ledger_metadata_blocks_without_mutation(self) -> None:
        cases = (
            ({object(): "bad"}, "malformed-release-publish-intent-ledger-data"),
            ({"api_key": "raw"}, "secret-like-existing-ledger-metadata"),
            ({"cmd": "run"}, "action-intent-existing-ledger-metadata"),
            ({"nested": {"unknown": object()}}, "malformed-release-publish-intent-ledger-data"),
        )
        for metadata, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001", metadata=metadata)
                before = ledger.to_dict()
                result = record_release_publish_intent(self._intent(), ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_secret_action_malformed_and_non_string_keys_do_not_mutate(self) -> None:
        cases = []
        secret = self._intent().to_dict()
        secret["intent"]["metadata"]["api_key"] = "raw"
        cases.append((secret, "secret-like-release-publish-intent-ledger-data"))
        action = self._intent().to_dict()
        action["intent"]["canonical_payload"]["caller_supplied_intent_metadata"]["metadata"]["cmd"] = "run"
        cases.append((action, "action-intent-release-publish-intent-ledger-data"))
        non_string = self._intent().to_dict()
        non_string["intent"]["metadata"] = {object(): "bad"}
        cases.append((non_string, "malformed-release-publish-intent-ledger-data"))
        unknown = self._intent().to_dict()
        unknown["intent"]["artifact"]["unknown"] = object()
        cases.append((unknown, "malformed-release-publish-intent-ledger-data"))
        nested_action = self._intent().to_dict()
        nested_action["intent"]["canonical_payload"]["caller_supplied_intent_metadata"]["publish_payload"]["runner"] = "x"
        cases.append((nested_action, "action-intent-release-publish-intent-ledger-data"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                ledger = RunLedger(run_id="run-001")
                before = ledger.to_dict()
                result = record_release_publish_intent(payload, ledger=ledger)
                self.assertIn(blocker, result.blockers)
                self.assertEqual(before, ledger.to_dict())

    def test_immutable_plain_snapshots_and_caller_mutation_safety(self) -> None:
        ledger = RunLedger(run_id="run-001")
        payload = self._intent().to_dict()
        original = copy.deepcopy(payload)

        result = record_release_publish_intent(payload, ledger=ledger)
        payload["intent"]["canonical_payload"]["readiness_binding"]["work_id"] = "changed"
        payload["intent"]["metadata"]["ticket"] = "changed"

        self.assertEqual((), result.blockers)
        dependency = ledger.snapshot().dependencies[0]
        self.assertEqual(original["intent"]["canonical_payload"], dependency.metadata["canonical_payload"])
        self.assertEqual("HAR-032", dependency.metadata["intent_metadata"]["ticket"])
        self.assertIsNotNone(result.ledger_snapshot)
        with self.assertRaises(TypeError):
            result.ledger_snapshot.dependencies[0].metadata["intent_digest"] = "changed"
        with self.assertRaises(TypeError):
            result.ledger_snapshot.dependencies[0].metadata["canonical_payload"]["format"] = "changed"
        json.dumps(result.to_dict(), sort_keys=True)

    def test_result_is_frozen_and_plain_json_serializable(self) -> None:
        result = record_release_publish_intent(
            self._intent(),
            ledger=RunLedger(run_id="run-001"),
        )

        payload = result.to_dict()

        self.assertEqual(result.recorded_event_ids, payload["recorded_event_ids"])
        self.assertEqual("run-001", payload["ledger_snapshot"]["run_id"])
        json.dumps(payload, sort_keys=True)
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(intent_ledger)
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
            "from harness_orchestrator.release_publish_intent import (",
            imports,
        )
        self.assertTrue(
            all(
                line.startswith("from __future__")
                or line.startswith("from dataclasses")
                or line.startswith("import hashlib")
                or line.startswith("import json")
                or line.startswith("import re")
                or line.startswith("from types")
                or line.startswith("from typing")
                or line.startswith("from harness_orchestrator.contracts")
                or line.startswith("from harness_orchestrator.release_publish_intent")
                or line.startswith("from harness_orchestrator.run_ledger")
                for line in imports
            )
        )

    def _intent(self, *, work_id: str = "work-001") -> ReleasePublishIntentResult:
        return build_release_publish_intent(
            readiness=self._ready(work_id=work_id),
            run_id="run-001",
            work_id=work_id,
            release_binding_digest=_BINDING_DIGEST,
            publish_target={"target_type": "local-dry-run", "target_id": f"target-{work_id}"},
            publish_payload={"payload_digest": _PAYLOAD_DIGEST, "payload_label": "release"},
            artifact={"artifact_id": f"artifact-{work_id}"},
            metadata={"ticket": "HAR-032"},
        )

    def _ready(self, *, work_id: str = "work-001") -> ReleasePublishReadiness:
        return ReleasePublishReadiness(
            ready=True,
            status="ready",
            blockers=(),
            run_id="run-001",
            work_id=work_id,
            dependency_id=f"dependency-{work_id}",
            event_id=f"event-{work_id}",
            canonical_digest_prefix=_BINDING_DIGEST[:12],
            summary={"format": "harness-release-publish-readiness-v1"},
        )

    def _recompute_intent_digest(self, payload: dict[str, object]) -> None:
        intent = payload["intent"]
        intent["intent_digest"] = hashlib.sha256(
            json.dumps(
                intent["canonical_payload"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def test_helper_intent_digest_is_real(self) -> None:
        intent = self._intent().intent
        expected = hashlib.sha256(
            json.dumps(
                intent.to_dict()["canonical_payload"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, intent.intent_digest)


if __name__ == "__main__":
    unittest.main()
