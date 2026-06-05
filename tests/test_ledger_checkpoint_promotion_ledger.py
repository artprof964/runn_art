from dataclasses import FrozenInstanceError, is_dataclass, replace
import copy
import inspect
import json
import unittest

import harness_orchestrator.ledger_checkpoint_promotion_ledger as promotion_ledger
from harness_orchestrator.ledger_checkpoint_promotion_intent import (
    LedgerCheckpointPromotionIntent,
    build_ledger_checkpoint_promotion_intent,
)
from harness_orchestrator.ledger_checkpoint_promotion_ledger import (
    LedgerCheckpointPromotionLedgerResult,
    record_ledger_checkpoint_promotion_intent,
    record_ledger_checkpoint_promotion_intents,
)
from harness_orchestrator.run_ledger import RunLedger


_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


class LedgerCheckpointPromotionLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger = RunLedger(run_id="run-001")
        intent_result = self._intent_result()

        result = record_ledger_checkpoint_promotion_intents(
            (intent_result,),
            ledger=ledger,
        )

        self.assertIsInstance(result, LedgerCheckpointPromotionLedgerResult)
        self.assertEqual((), result.blockers)
        self.assertEqual(result.ledger_snapshot, ledger.snapshot())
        self.assertEqual(1, len(ledger.snapshot().dependencies))
        self.assertEqual(1, len(ledger.snapshot().audit_events))
        dependency = ledger.snapshot().dependencies[0]
        event = ledger.snapshot().audit_events[0]
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual("checkpoint-promotion-intent", dependency.dependency_type)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("checkpoint-promotion-intent-ledger-record", event.event_type)
        self.assertEqual(intent_result.intent.intent_digest, event.metadata["intent_digest"])
        self.assertEqual(intent_result.intent.promotion_id, event.metadata["promotion_id"])

    def test_plain_mapping_input_records_without_mutating_caller_data(self) -> None:
        ledger = RunLedger(run_id="run-001")
        payload = self._intent_result().to_dict()
        before = copy.deepcopy(payload)

        result = record_ledger_checkpoint_promotion_intent(payload, ledger=ledger)

        self.assertEqual((), result.blockers)
        self.assertEqual(1, len(ledger.snapshot().dependencies))
        self.assertEqual(payload, before)

    def test_direct_intent_data_records(self) -> None:
        ledger = RunLedger(run_id="run-001")
        intent = self._intent_result().intent

        result = record_ledger_checkpoint_promotion_intent(intent, ledger=ledger)

        self.assertEqual((), result.blockers)
        self.assertEqual((ledger.snapshot().audit_events[0].event_id,), result.recorded_event_ids)

    def test_missing_ledger_wrong_type_and_empty_input_fail_closed(self) -> None:
        missing = record_ledger_checkpoint_promotion_intent(
            self._intent_result(),
            ledger=None,
        )
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong = record_ledger_checkpoint_promotion_intent("not-intent-data", ledger=ledger)
        empty = record_ledger_checkpoint_promotion_intents((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIn("checkpoint-promotion-intent-wrong-type", wrong.blockers)
        self.assertEqual(("checkpoint-promotion-intents-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_blocked_or_unpassed_result_fails_closed_without_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        blocked = replace(
            self._intent_result(),
            passed=False,
            blockers=("operator-review-open",),
        )
        before = ledger.to_dict()

        result = record_ledger_checkpoint_promotion_intent(blocked, ledger=ledger)

        self.assertIn("checkpoint-promotion-intent-not-passed", result.blockers)
        self.assertIn("checkpoint-promotion-intent-blockers-present", result.blockers)
        self.assertEqual((blocked.intent.promotion_id,), result.skipped_promotion_ids)
        self.assertEqual((blocked.intent.intent_digest,), result.skipped_intent_digests)
        self.assertEqual(before, ledger.to_dict())

    def test_missing_intent_or_mismatched_run_work_fields_fail_closed(self) -> None:
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        missing_intent = {"passed": True, "blockers": (), "intent": None}
        wrong_run = self._intent_mapping(run_id="run-other")
        missing_work = self._intent_mapping(work_id="")

        result = record_ledger_checkpoint_promotion_intents(
            (missing_intent, wrong_run, missing_work),
            ledger=ledger,
        )

        self.assertIn("checkpoint-promotion-intent-missing", result.blockers)
        self.assertIn("ledger-run-id-mismatch", result.blockers)
        self.assertIn("missing-work-id", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_malformed_digest_path_size_and_identity_fields_fail_closed(self) -> None:
        ledger = RunLedger(run_id="run-001")
        cases = (
            (self._intent_mapping(promotion_id=" "), "missing-promotion-id"),
            (self._intent_mapping(requested_by=" "), "missing-requested-by"),
            (self._intent_mapping(target_ledger_id=" "), "missing-target-ledger-id"),
            (self._intent_mapping(checkpoint_digest="bad"), "invalid-checkpoint-digest"),
            (self._intent_mapping(payload_digest="bad"), "invalid-payload-digest"),
            (self._intent_mapping(intent_digest="bad"), "invalid-intent-digest"),
            (self._intent_mapping(checkpoint_path="../run.json"), "checkpoint-path-unsafe"),
            (
                self._intent_mapping(checkpoint_size_bytes=0),
                "nonpositive-checkpoint-size-bytes",
            ),
        )

        for payload, blocker in cases:
            before = ledger.to_dict()
            result = record_ledger_checkpoint_promotion_intent(payload, ledger=ledger)
            self.assertIn(blocker, result.blockers)
            self.assertEqual(before, ledger.to_dict())

    def test_duplicate_existing_event_dependency_and_intent_digest_no_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = record_ledger_checkpoint_promotion_intent(
            self._intent_result(),
            ledger=ledger,
        )
        before = ledger.to_dict()

        duplicate = record_ledger_checkpoint_promotion_intent(
            self._intent_result(),
            ledger=ledger,
        )

        self.assertIn(
            "checkpoint-promotion-dependency-id-already-recorded",
            duplicate.blockers,
        )
        self.assertIn("checkpoint-promotion-event-id-already-recorded", duplicate.blockers)
        self.assertIn(
            "checkpoint-promotion-intent-digest-already-recorded",
            duplicate.blockers,
        )
        self.assertEqual(first.recorded_event_ids[0], ledger.snapshot().audit_events[0].event_id)
        self.assertEqual(before, ledger.to_dict())

    def test_duplicate_in_batch_fails_closed_without_partial_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = self._intent_result()
        second = self._intent_result()
        before = ledger.to_dict()

        result = record_ledger_checkpoint_promotion_intents((first, second), ledger=ledger)

        self.assertIn("checkpoint-promotion-dependency-id-duplicate", result.blockers)
        self.assertIn("checkpoint-promotion-event-id-duplicate", result.blockers)
        self.assertIn("checkpoint-promotion-intent-digest-duplicate", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_secret_like_and_execution_intent_fail_closed_without_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        secret = self._intent_mapping(metadata={"api_key": "raw"})
        execute = self._intent_mapping(metadata={"cmd": "promote"})
        before = ledger.to_dict()

        result = record_ledger_checkpoint_promotion_intents((secret, execute), ledger=ledger)

        self.assertIn("secret-like-checkpoint-promotion-ledger-data", result.blockers)
        self.assertIn("execution-intent-checkpoint-promotion-ledger-data", result.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_result_is_frozen_and_plain_json_serializable(self) -> None:
        self.assertTrue(is_dataclass(LedgerCheckpointPromotionLedgerResult))
        self.assertTrue(LedgerCheckpointPromotionLedgerResult.__dataclass_params__.frozen)
        result = record_ledger_checkpoint_promotion_intent(
            self._intent_result(),
            ledger=RunLedger(run_id="run-001"),
        )
        payload = result.to_dict()

        self.assertEqual(result.recorded_event_ids, payload["recorded_event_ids"])
        self.assertEqual("run-001", payload["ledger_snapshot"]["run_id"])
        json.dumps(payload, sort_keys=True)
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)

    def test_source_guard_has_no_forbidden_side_effect_runtime_tokens(self) -> None:
        source = inspect.getsource(promotion_ledger)
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
            "scheduler",
            "watch_social",
            "Client(",
            "Service(",
            "datetime",
            "random",
        )
        forbidden_process = "sub" + "process"
        self.assertNotIn(forbidden_process, source)
        for token in forbidden:
            self.assertNotIn(token, source)

    def _intent_result(self, **overrides):
        return build_ledger_checkpoint_promotion_intent(
            {
                "passed": True,
                "blockers": (),
                "work_id": "work-001",
                "run_id": "run-001",
                "checkpoint_path": "checkpoints/run-001.json",
                "checkpoint_digest": _DIGEST,
                "payload_digest": _DIGEST,
                "checkpoint_size_bytes": 128,
            },
            promotion_id=overrides.pop("promotion_id", "promotion-001"),
            requested_by=overrides.pop("requested_by", "reviewer-001"),
            run_id=overrides.pop("run_id", "run-001"),
            work_id=overrides.pop("work_id", "work-001"),
            target_ledger_id=overrides.pop("target_ledger_id", "ledger-main"),
            expected_checkpoint_digest=overrides.pop("checkpoint_digest", _DIGEST),
            expected_payload_digest=overrides.pop("payload_digest", _DIGEST),
            expected_checkpoint_path=overrides.pop(
                "checkpoint_path", "checkpoints/run-001.json"
            ),
            metadata=overrides.pop("metadata", {"approved_by": "review-board"}),
        )

    def _intent_mapping(self, **overrides):
        intent = self._intent_result().intent
        self.assertIsInstance(intent, LedgerCheckpointPromotionIntent)
        payload = intent.to_dict()
        payload.update(overrides)
        if "checkpoint_digest" in overrides and overrides["checkpoint_digest"] != _DIGEST:
            payload["intent_digest"] = _OTHER_DIGEST
        return payload


if __name__ == "__main__":
    unittest.main()
