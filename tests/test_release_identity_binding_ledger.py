from dataclasses import FrozenInstanceError, dataclass, is_dataclass, replace
import copy
import inspect
import json
import unittest

import harness_orchestrator.release_identity_binding_ledger as binding_ledger
from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.identity_proof import IdentityProofResult
from harness_orchestrator.release_identity_binding import (
    ReleaseIdentityBindingResult,
    build_release_identity_binding,
)
from harness_orchestrator.release_identity_binding_ledger import (
    ReleaseIdentityBindingLedgerResult,
    record_release_identity_binding,
    record_release_identity_bindings,
)
from harness_orchestrator.run_ledger import RunLedger, TaskStatus


_PAYLOAD_DIGEST = "a" * 64
_CHECKPOINT_DIGEST = "b" * 64
_INTENT_DIGEST = "c" * 64
_PROOF_DIGEST = "d" * 64


class ToDictRecord:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


@dataclass(frozen=True)
class PlainBinding:
    passed: bool
    blockers: tuple[str, ...]
    canonical_payload: dict[str, object]
    canonical_digest: str
    summary: dict[str, object]


class ReleaseIdentityBindingLedgerTests(unittest.TestCase):
    def test_happy_path_records_dependency_and_audit_event(self) -> None:
        ledger = RunLedger(run_id="run-001")
        binding = self._binding()

        result = record_release_identity_binding(binding, ledger=ledger)

        self.assertIsInstance(result, ReleaseIdentityBindingLedgerResult)
        self.assertEqual((), result.blockers)
        self.assertEqual(result.ledger_snapshot, ledger.snapshot())
        self.assertEqual(1, len(ledger.snapshot().dependencies))
        self.assertEqual(1, len(ledger.snapshot().audit_events))
        dependency = ledger.snapshot().dependencies[0]
        event = ledger.snapshot().audit_events[0]
        self.assertEqual((dependency.dependency_id,), result.recorded_dependency_ids)
        self.assertEqual((event.event_id,), result.recorded_event_ids)
        self.assertEqual("release-identity-binding", dependency.dependency_type)
        self.assertEqual(85, dependency.order)
        self.assertEqual("ready", dependency.status)
        self.assertEqual("release-identity-binding-ledger-record", event.event_type)
        self.assertEqual(binding.canonical_digest, event.metadata["canonical_digest"])
        self.assertEqual(binding.canonical_payload["expected"]["work_id"], event.work_id)

    def test_plain_mapping_to_dict_and_dataclass_inputs_record(self) -> None:
        first = self._binding(work_id="work-001")
        second = self._binding(work_id="work-002", request_id="request-002")
        third = self._binding(work_id="work-003", request_id="request-003")
        dataclass_binding = PlainBinding(**third.to_dict())
        ledger = RunLedger(run_id="run-001")

        result = record_release_identity_bindings(
            (
                first.to_dict(),
                ToDictRecord(second.to_dict()),
                dataclass_binding,
            ),
            ledger=ledger,
        )

        self.assertEqual((), result.blockers)
        self.assertEqual(3, len(result.recorded_dependency_ids))
        self.assertEqual(3, len(ledger.snapshot().audit_events))

    def test_missing_ledger_empty_and_wrong_types_fail_closed(self) -> None:
        missing = record_release_identity_binding(self._binding(), ledger=None)
        wrong_ledger = record_release_identity_binding(self._binding(), ledger=object())
        ledger = RunLedger(run_id="run-001")
        before = ledger.to_dict()
        wrong_input = record_release_identity_binding("not-binding-data", ledger=ledger)
        empty = record_release_identity_bindings((), ledger=ledger)

        self.assertEqual(("ledger-missing",), missing.blockers)
        self.assertIsNone(missing.ledger_snapshot)
        self.assertEqual(("ledger-missing",), wrong_ledger.blockers)
        self.assertIn("release-identity-binding-wrong-type", wrong_input.blockers)
        self.assertEqual(("release-identity-bindings-empty",), empty.blockers)
        self.assertEqual(before, ledger.to_dict())

    def test_failed_or_blocked_binding_fails_closed_without_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        blocked = replace(
            self._binding(),
            passed=False,
            blockers=("operator-review-open",),
        )
        before = ledger.to_dict()

        result = record_release_identity_binding(blocked, ledger=ledger)

        self.assertIn("release-identity-binding-not-passed", result.blockers)
        self.assertIn("release-identity-binding-blockers-present", result.blockers)
        self.assertIn(blocked.canonical_digest, result.skipped_canonical_digests)
        self.assertEqual(before, ledger.to_dict())

    def test_digest_mismatch_fails_closed_without_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        payload = self._binding().to_dict()
        payload["canonical_payload"]["expected"]["request_id"] = "request-999"
        before = ledger.to_dict()

        result = record_release_identity_binding(payload, ledger=ledger)

        self.assertIn(
            "release-identity-binding-canonical-digest-mismatch",
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_summary_prefix_mismatch_fails_closed_without_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        payload = self._binding().to_dict()
        payload["summary"]["canonical_digest_prefix"] = "0" * 12
        before = ledger.to_dict()

        result = record_release_identity_binding(payload, ledger=ledger)

        self.assertIn(
            "release-identity-binding-summary-digest-prefix-mismatch",
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_duplicate_existing_dependency_event_and_digest_no_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        first = record_release_identity_binding(self._binding(), ledger=ledger)
        before = ledger.to_dict()

        duplicate = record_release_identity_binding(self._binding(), ledger=ledger)

        self.assertIn(
            "release-identity-binding-dependency-id-already-recorded",
            duplicate.blockers,
        )
        self.assertIn(
            "release-identity-binding-event-id-already-recorded",
            duplicate.blockers,
        )
        self.assertIn(
            "release-identity-binding-canonical-digest-already-recorded",
            duplicate.blockers,
        )
        self.assertEqual(first.recorded_event_ids[0], ledger.snapshot().audit_events[0].event_id)
        self.assertEqual(before, ledger.to_dict())

    def test_duplicate_in_batch_fails_closed_without_partial_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        binding = self._binding()
        before = ledger.to_dict()

        result = record_release_identity_bindings((binding, binding.to_dict()), ledger=ledger)

        self.assertIn(
            "release-identity-binding-dependency-id-duplicate",
            result.blockers,
        )
        self.assertIn("release-identity-binding-event-id-duplicate", result.blockers)
        self.assertIn(
            "release-identity-binding-canonical-digest-duplicate",
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_secret_like_and_execution_intent_fail_closed_without_mutation(self) -> None:
        ledger = RunLedger(run_id="run-001")
        secret = self._binding(work_id="work-001").to_dict()
        secret["summary"]["api_key"] = "raw"
        execute = self._binding(work_id="work-002", request_id="request-002").to_dict()
        execute["summary"]["cmd"] = "run"
        before = ledger.to_dict()

        result = record_release_identity_bindings((secret, execute), ledger=ledger)

        self.assertIn("secret-like-release-identity-binding-ledger-data", result.blockers)
        self.assertIn(
            "execution-intent-release-identity-binding-ledger-data",
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_broad_execution_key_fragments_fail_closed_without_mutation(self) -> None:
        cases = (
            "should_execute_now",
            "pre_execution_step",
            "execution_plan_url",
        )

        for index, key in enumerate(cases, start=1):
            ledger = RunLedger(run_id="run-001")
            payload = self._binding(
                work_id=f"work-exec-{index}",
                request_id=f"request-exec-{index}",
            ).to_dict()
            payload["summary"][key] = "blocked"
            before = ledger.to_dict()

            result = record_release_identity_binding(payload, ledger=ledger)

            self.assertIn(
                "execution-intent-release-identity-binding-ledger-data",
                result.blockers,
            )
            self.assertEqual(before, ledger.to_dict())

    def test_unknown_nested_object_fails_closed_without_stringifying_or_mutation(self) -> None:
        class UnknownNested:
            pass

        ledger = RunLedger(run_id="run-001")
        payload = self._binding().to_dict()
        payload["summary"]["review_context"] = {"unknown": UnknownNested()}
        before = ledger.to_dict()

        result = record_release_identity_binding(payload, ledger=ledger)

        self.assertIn(
            "malformed-release-identity-binding-ledger-data",
            result.blockers,
        )
        self.assertNotIn(
            "release-identity-binding-canonical-digest-mismatch",
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_nested_non_string_mapping_key_fails_closed_without_repr_leak(self) -> None:
        ledger = RunLedger(run_id="run-001")
        payload = self._binding().to_dict()
        payload["summary"]["review_context"] = {object(): "value"}
        before = ledger.to_dict()

        result = record_release_identity_binding(payload, ledger=ledger)

        self.assertIn(
            "malformed-release-identity-binding-ledger-data",
            result.blockers,
        )
        self.assertFalse(
            any("object at 0x" in blocker for blocker in result.blockers),
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_non_string_blocker_entry_fails_closed_without_repr_leak(self) -> None:
        ledger = RunLedger(run_id="run-001")
        blocked = replace(self._binding(), blockers=(object(),))
        before = ledger.to_dict()

        result = record_release_identity_binding(blocked, ledger=ledger)

        self.assertIn("release-identity-binding-blockers-present", result.blockers)
        self.assertIn("release-identity-binding-malformed-blockers", result.blockers)
        self.assertTrue(
            "malformed-blockers" in result.blockers
            or "release-identity-binding-malformed-blockers" in result.blockers
        )
        self.assertFalse(
            any("object at 0x" in blocker for blocker in result.blockers),
            result.blockers,
        )
        self.assertEqual(before, ledger.to_dict())

    def test_caller_input_is_not_mutated(self) -> None:
        ledger = RunLedger(run_id="run-001")
        payload = self._binding().to_dict()
        before_payload = copy.deepcopy(payload)

        result = record_release_identity_binding(payload, ledger=ledger)
        payload["canonical_payload"]["expected"]["work_id"] = "changed"
        payload["summary"]["work_id"] = "changed"

        self.assertEqual((), result.blockers)
        self.assertEqual(before_payload["canonical_payload"], ledger.snapshot().dependencies[0].metadata["canonical_payload"])
        self.assertNotEqual(payload["summary"], ledger.snapshot().dependencies[0].metadata["summary"])

    def test_result_snapshot_metadata_cannot_mutate_ledger_visible_data(self) -> None:
        ledger = RunLedger(run_id="run-001")
        result = record_release_identity_binding(self._binding(), ledger=ledger)
        before = ledger.to_dict()
        self.assertIsNotNone(result.ledger_snapshot)
        dependency = result.ledger_snapshot.dependencies[0]

        with self.assertRaises(TypeError):
            dependency.metadata["canonical_digest"] = "changed"
        with self.assertRaises(TypeError):
            dependency.metadata["summary"]["work_id"] = "changed"

        self.assertEqual(before, ledger.to_dict())

    def test_result_snapshot_gate_decision_metadata_is_immutable_and_serializable(
        self,
    ) -> None:
        top_level_key = object()
        nested_key = object()
        unsupported_leaf = object()
        decision = GateDecision(
            decision_id="decision-preloaded",
            work_id="work-001",
            gate_name="preloaded-gate",
            passed=True,
            reason="ready",
            metadata={
                top_level_key: "dropped",
                "outer": {
                    nested_key: "dropped",
                    "inner": "original",
                    "unsupported": unsupported_leaf,
                    "items": (object(), {"safe": "kept", object(): "dropped"}),
                },
            },
        )
        ledger = RunLedger(run_id="run-001", gate_decisions=(decision,))
        result = record_release_identity_binding(self._binding(), ledger=ledger)
        before = self._stable_metadata_view(ledger.to_dict())
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_decision = result.ledger_snapshot.gate_decisions[0]

        with self.assertRaises(TypeError):
            snapshot_decision.metadata["outer"] = {"inner": "changed"}
        with self.assertRaises(TypeError):
            snapshot_decision.metadata["outer"]["inner"] = "changed"

        self.assertEqual(before, self._stable_metadata_view(ledger.to_dict()))
        payload = result.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("object at 0x", serialized)
        decision_metadata = payload["ledger_snapshot"]["gate_decisions"][0]["metadata"]
        self.assertNotIn(top_level_key, decision_metadata)
        self.assertNotIn(nested_key, decision_metadata["outer"])
        self.assertEqual("<unsupported>", decision_metadata["outer"]["unsupported"])
        self.assertEqual("<unsupported>", decision_metadata["outer"]["items"][0])
        self.assertEqual({"safe": "kept"}, decision_metadata["outer"]["items"][1])

    def test_result_snapshot_task_metadata_is_immutable_and_serializable(self) -> None:
        top_level_key = object()
        nested_key = object()
        ledger = RunLedger(run_id="run-001")
        ledger.record_task(
            TaskStatus(
                task_id="task-preloaded",
                work_id="work-001",
                title="Preloaded task",
                metadata={
                    top_level_key: "dropped",
                    "outer": {
                        nested_key: "dropped",
                        "inner": "original",
                        "unsupported": object(),
                        "items": [object(), {"safe": "kept", object(): "dropped"}],
                    },
                },
            )
        )
        result = record_release_identity_binding(self._binding(), ledger=ledger)
        before = self._stable_metadata_view(ledger.to_dict())
        self.assertIsNotNone(result.ledger_snapshot)
        snapshot_task = result.ledger_snapshot.tasks[0]

        with self.assertRaises(TypeError):
            snapshot_task.metadata["outer"] = {"inner": "changed"}
        with self.assertRaises(TypeError):
            snapshot_task.metadata["outer"]["inner"] = "changed"

        self.assertEqual(before, self._stable_metadata_view(ledger.to_dict()))
        payload = result.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("object at 0x", serialized)
        task_metadata = payload["ledger_snapshot"]["tasks"][0]["metadata"]
        self.assertNotIn(top_level_key, task_metadata)
        self.assertNotIn(nested_key, task_metadata["outer"])
        self.assertEqual("<unsupported>", task_metadata["outer"]["unsupported"])
        self.assertEqual("<unsupported>", task_metadata["outer"]["items"][0])
        self.assertEqual({"safe": "kept"}, task_metadata["outer"]["items"][1])

    def test_result_snapshot_run_metadata_is_sanitized_and_serializable(self) -> None:
        top_level_key = object()
        nested_key = object()
        ledger = RunLedger(
            run_id="run-001",
            metadata={
                top_level_key: "dropped",
                "outer": {
                    nested_key: "dropped",
                    "inner": "original",
                    "unsupported": object(),
                    "items": (object(), {"safe": "kept", object(): "dropped"}),
                },
            },
        )

        result = record_release_identity_binding(self._binding(), ledger=ledger)
        before = ledger.to_dict()

        self.assertIsNotNone(result.ledger_snapshot)
        with self.assertRaises(TypeError):
            result.ledger_snapshot.metadata["outer"] = {"inner": "changed"}
        with self.assertRaises(TypeError):
            result.ledger_snapshot.metadata["outer"]["inner"] = "changed"
        self.assertEqual(before, ledger.to_dict())
        payload = result.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("object at 0x", serialized)
        run_metadata = payload["ledger_snapshot"]["metadata"]
        self.assertNotIn(top_level_key, run_metadata)
        self.assertNotIn(nested_key, run_metadata["outer"])
        self.assertEqual("<unsupported>", run_metadata["outer"]["unsupported"])
        self.assertEqual("<unsupported>", run_metadata["outer"]["items"][0])
        self.assertEqual({"safe": "kept"}, run_metadata["outer"]["items"][1])

    def test_result_is_frozen_and_plain_json_serializable(self) -> None:
        self.assertTrue(is_dataclass(ReleaseIdentityBindingLedgerResult))
        self.assertTrue(ReleaseIdentityBindingLedgerResult.__dataclass_params__.frozen)
        result = record_release_identity_binding(
            self._binding(),
            ledger=RunLedger(run_id="run-001"),
        )
        payload = result.to_dict()

        self.assertEqual(result.recorded_event_ids, payload["recorded_event_ids"])
        self.assertEqual("run-001", payload["ledger_snapshot"]["run_id"])
        json.dumps(payload, sort_keys=True)
        with self.assertRaises(FrozenInstanceError):
            result.blockers = ("changed",)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(binding_ledger)
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
        imports = tuple(
            line for line in source.splitlines() if line.startswith(("import ", "from "))
        )
        self.assertIn(
            "from harness_orchestrator.release_identity_binding import ReleaseIdentityBindingResult",
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
                or line.startswith("from harness_orchestrator.release_identity_binding")
                or line.startswith("from harness_orchestrator.run_ledger")
                for line in imports
            )
        )

    def _binding(self, **overrides: object) -> ReleaseIdentityBindingResult:
        work_id = str(overrides.get("work_id", "work-001"))
        request_id = str(overrides.get("request_id", "request-001"))
        kwargs = {
            "gate_decision": self._decision(
                work_id=work_id,
                metadata={
                    "request_id": request_id,
                    "media_ids": ("media-001", "media-002"),
                    "artifact_ids": ("artifact-001",),
                    "payload_digest": _PAYLOAD_DIGEST,
                    "checkpoint_digest": _CHECKPOINT_DIGEST,
                    "promotion_intent_digest": _INTENT_DIGEST,
                },
            ),
            "identity_proof": self._proof(work_id=work_id, request_id=request_id),
            "work_id": work_id,
            "request_id": request_id,
            "evidence_bundle_id": "bundle-001",
            "media_ids": ("media-001", "media-002"),
            "artifact_ids": ("artifact-001",),
            "payload_digest": _PAYLOAD_DIGEST,
            "checkpoint_digest": _CHECKPOINT_DIGEST,
            "promotion_intent_digest": _INTENT_DIGEST,
        }
        return build_release_identity_binding(**kwargs)

    def _decision(
        self,
        *,
        work_id: str = "work-001",
        metadata: dict[str, object] | None = None,
    ) -> GateDecision:
        return GateDecision(
            decision_id=f"decision-{work_id}",
            work_id=work_id,
            gate_name="ai-art-media-release",
            passed=True,
            reason="released",
            blockers=(),
            evidence_bundle_id="bundle-001",
            metadata=metadata or {},
        )

    def _proof(
        self,
        *,
        work_id: str = "work-001",
        request_id: str = "request-001",
    ) -> IdentityProofResult:
        return IdentityProofResult(
            passed=True,
            blockers=(),
            canonical_payload={
                "format": "harness-identity-proof-v1",
                "expected": {
                    "work_id": work_id,
                    "request_id": request_id,
                    "evidence_bundle_id": "bundle-001",
                    "media_ids": ("media-001", "media-002"),
                    "artifact_ids": ("artifact-001",),
                    "payload_digest": _PAYLOAD_DIGEST,
                    "checkpoint_digest": _CHECKPOINT_DIGEST,
                    "promotion_intent_digest": _INTENT_DIGEST,
                },
                "records": (
                    {"work_id": work_id, "request_id": request_id},
                    {"work_id": work_id, "payload_digest": _PAYLOAD_DIGEST},
                ),
            },
            canonical_digest=_PROOF_DIGEST,
            summary={"work_id": work_id, "request_id": request_id},
        )

    def _stable_metadata_view(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: self._stable_metadata_view(item)
                for key, item in value.items()
                if isinstance(key, str)
            }
        if isinstance(value, tuple):
            return tuple(self._stable_metadata_view(item) for item in value)
        if isinstance(value, list):
            return tuple(self._stable_metadata_view(item) for item in value)
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return "<unsupported>"


if __name__ == "__main__":
    unittest.main()
