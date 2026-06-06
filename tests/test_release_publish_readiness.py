import copy
import inspect
from types import MappingProxyType
import unittest

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.identity_proof import IdentityProofResult
from harness_orchestrator.release_identity_binding import build_release_identity_binding
from harness_orchestrator.release_identity_binding_ledger import (
    record_release_identity_binding,
)
from harness_orchestrator.release_publish_readiness import (
    ReleasePublishReadiness,
    evaluate_release_publish_readiness,
)
from harness_orchestrator.run_ledger import RunLedger, TaskStatus


_PAYLOAD_DIGEST = "a" * 64
_CHECKPOINT_DIGEST = "b" * 64
_INTENT_DIGEST = "c" * 64
_PROOF_DIGEST = "d" * 64


class ToDictSnapshot:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class ReleasePublishReadinessTests(unittest.TestCase):
    def test_happy_path_accepts_snapshot_mapping_and_to_dict(self) -> None:
        snapshot = self._snapshot()

        from_snapshot = self._evaluate(snapshot)
        from_mapping = self._evaluate(snapshot.to_dict())
        from_to_dict = self._evaluate(ToDictSnapshot(snapshot.to_dict()))

        for result in (from_snapshot, from_mapping, from_to_dict):
            self.assertIsInstance(result, ReleasePublishReadiness)
            self.assertTrue(result.ready)
            self.assertEqual("ready", result.status)
            self.assertEqual((), result.blockers)
            self.assertEqual("run-001", result.run_id)
            self.assertEqual("work-001", result.work_id)
            self.assertEqual(12, len(result.canonical_digest_prefix))
            self.assertIsInstance(result.summary, MappingProxyType)

    def test_requires_matching_run_and_work_identity(self) -> None:
        run = self._evaluate(self._snapshot(), run_id="run-999")
        work = self._evaluate(self._snapshot(work_id="work-002"), work_id="work-001")

        self.assertFalse(run.ready)
        self.assertIn("snapshot-run-id-mismatch", run.blockers)
        self.assertFalse(work.ready)
        self.assertIn("release-identity-binding-dependency-missing", work.blockers)
        self.assertIn("release-identity-binding-event-missing", work.blockers)

    def test_requires_exactly_one_matching_dependency_and_event_pair(self) -> None:
        missing = self._snapshot().to_dict()
        missing["dependencies"] = ()
        duplicate = self._snapshot().to_dict()
        duplicate["dependencies"] = (
            *duplicate["dependencies"],
            copy.deepcopy(duplicate["dependencies"][0]),
        )

        missing_result = self._evaluate(missing)
        duplicate_result = self._evaluate(duplicate)

        self.assertIn("release-identity-binding-dependency-missing", missing_result.blockers)
        self.assertIn(
            "release-identity-binding-dependency-ambiguous",
            duplicate_result.blockers,
        )

    def test_pairing_metadata_digest_and_summary_are_verified(self) -> None:
        dependency_mismatch = self._snapshot().to_dict()
        dependency_mismatch["audit_events"][0]["metadata"]["dependency_id"] = "other"
        digest_mismatch = self._snapshot().to_dict()
        digest_mismatch["dependencies"][0]["metadata"]["canonical_digest"] = "0" * 64
        summary_mismatch = self._snapshot().to_dict()
        summary_mismatch["dependencies"][0]["metadata"]["summary"][
            "canonical_digest_prefix"
        ] = "0" * 12

        paired = self._evaluate(dependency_mismatch)
        digest = self._evaluate(digest_mismatch)
        summary = self._evaluate(summary_mismatch)

        self.assertIn("event-dependency-id-mismatch", paired.blockers)
        self.assertIn("event-canonical-digest-mismatch", digest.blockers)
        self.assertIn("release-identity-binding-canonical-digest-mismatch", digest.blockers)
        self.assertIn(
            "release-identity-binding-summary-canonical-digest-prefix-mismatch",
            summary.blockers,
        )

    def test_missing_canonical_payload_gate_decision_blocks(self) -> None:
        snapshot = self._snapshot().to_dict()
        del snapshot["dependencies"][0]["metadata"]["canonical_payload"]["gate_decision"]
        result = self._evaluate(snapshot)

        self.assertIn("release-identity-binding-gate-decision-missing", result.blockers)

    def test_missing_canonical_payload_identity_proof_blocks(self) -> None:
        snapshot = self._snapshot().to_dict()
        del snapshot["dependencies"][0]["metadata"]["canonical_payload"]["identity_proof"]
        result = self._evaluate(snapshot)

        self.assertIn("release-identity-binding-identity-proof-missing", result.blockers)

    def test_duplicate_matching_audit_events_block(self) -> None:
        snapshot = self._snapshot().to_dict()
        snapshot["audit_events"] = (
            *snapshot["audit_events"],
            copy.deepcopy(snapshot["audit_events"][0]),
        )

        result = self._evaluate(snapshot)

        self.assertIn("release-identity-binding-event-ambiguous", result.blockers)

    def test_missing_summary_identity_fields_block(self) -> None:
        for key in (
            "work_id",
            "request_id",
            "evidence_bundle_id",
            "media_ids",
            "artifact_ids",
        ):
            with self.subTest(key=key):
                snapshot = self._snapshot().to_dict()
                del snapshot["dependencies"][0]["metadata"]["summary"][key]
                result = self._evaluate(snapshot)

                self.assertIn(
                    f"release-identity-binding-summary-{key.replace('_', '-')}-missing",
                    result.blockers,
                )

    def test_tampered_summary_identity_fields_block(self) -> None:
        tampered_values = {
            "work_id": "work-999",
            "request_id": "request-999",
            "evidence_bundle_id": "bundle-999",
            "media_ids": ("media-999",),
            "artifact_ids": ("artifact-999",),
        }
        for key, value in tampered_values.items():
            with self.subTest(key=key):
                snapshot = self._snapshot().to_dict()
                snapshot["dependencies"][0]["metadata"]["summary"][key] = value
                result = self._evaluate(snapshot)

                self.assertIn(
                    f"release-identity-binding-summary-{key.replace('_', '-')}-mismatch",
                    result.blockers,
                )

    def test_missing_and_tampered_summary_digest_prefixes_block(self) -> None:
        prefix_keys = (
            "canonical_digest_prefix",
            "payload_digest_prefix",
            "checkpoint_digest_prefix",
            "promotion_intent_digest_prefix",
        )
        for key in prefix_keys:
            with self.subTest(key=key, mode="missing"):
                snapshot = self._snapshot().to_dict()
                del snapshot["dependencies"][0]["metadata"]["summary"][key]
                result = self._evaluate(snapshot)

                self.assertIn(
                    f"release-identity-binding-summary-{key.replace('_', '-')}-missing",
                    result.blockers,
                )
            with self.subTest(key=key, mode="tampered"):
                snapshot = self._snapshot().to_dict()
                snapshot["dependencies"][0]["metadata"]["summary"][key] = "0" * 12
                result = self._evaluate(snapshot)

                self.assertIn(
                    f"release-identity-binding-summary-{key.replace('_', '-')}-mismatch",
                    result.blockers,
                )

    def test_dependency_passed_and_event_done_statuses_block(self) -> None:
        dependency_passed = self._snapshot().to_dict()
        dependency_passed["dependencies"][0]["status"] = "passed"
        event_done = self._snapshot().to_dict()
        event_done["audit_events"][0]["status"] = "done"

        self.assertIn(
            "release-identity-binding-dependency-status-not-ready",
            self._evaluate(dependency_passed).blockers,
        )
        self.assertIn(
            "release-identity-binding-event-status-not-ready",
            self._evaluate(event_done).blockers,
        )

    def test_task_done_aliases_still_pass_finished_task_checks(self) -> None:
        for status in ("done", "complete", "completed", "closed"):
            with self.subTest(status=status):
                snapshot = self._snapshot(
                    tasks=(TaskStatus("task-1", "work-001", "finished", status=status),)
                )
                result = self._evaluate(snapshot)

                self.assertTrue(result.ready)
                self.assertNotIn("unfinished-tasks-present", result.blockers)

    def test_expected_request_evidence_media_artifact_and_digest_identity(self) -> None:
        request = self._evaluate(self._snapshot(), expected_request_id="request-999")
        evidence = self._evaluate(
            self._snapshot(),
            expected_evidence_bundle_id="bundle-999",
        )
        media = self._evaluate(self._snapshot(), expected_media_ids=("media-999",))
        artifact = self._evaluate(
            self._snapshot(),
            expected_artifact_ids=("artifact-999",),
        )
        payload = self._evaluate(
            self._snapshot(),
            expected_payload_digest="9" * 64,
        )
        checkpoint = self._evaluate(
            self._snapshot(),
            expected_checkpoint_digest="8" * 64,
        )
        promotion = self._evaluate(
            self._snapshot(),
            expected_promotion_intent_digest="7" * 64,
        )

        self.assertIn(
            "release-identity-binding-expected-request-id-mismatch",
            request.blockers,
        )
        self.assertIn(
            "release-identity-binding-expected-evidence-bundle-id-mismatch",
            evidence.blockers,
        )
        self.assertIn(
            "release-identity-binding-expected-media-ids-mismatch",
            media.blockers,
        )
        self.assertIn(
            "release-identity-binding-expected-artifact-ids-mismatch",
            artifact.blockers,
        )
        self.assertIn(
            "release-identity-binding-expected-payload-digest-mismatch",
            payload.blockers,
        )
        self.assertIn(
            "release-identity-binding-expected-checkpoint-digest-mismatch",
            checkpoint.blockers,
        )
        self.assertIn(
            "release-identity-binding-expected-promotion-intent-digest-mismatch",
            promotion.blockers,
        )

    def test_blocked_records_unfinished_tasks_malformed_data_and_unsafe_content_fail(self) -> None:
        blocked = self._snapshot().to_dict()
        blocked["dependencies"][0]["status"] = "blocked"
        unfinished = self._snapshot(tasks=(TaskStatus("task-1", "work-001", "open"),))
        malformed = self._snapshot().to_dict()
        malformed["dependencies"][0]["metadata"]["canonical_payload"]["bad"] = object()
        non_string_key = self._snapshot().to_dict()
        non_string_key["metadata"] = {1: "bad"}
        secret = self._snapshot().to_dict()
        secret["metadata"] = {"api_key": "value"}
        execution = self._snapshot().to_dict()
        execution["metadata"] = {"cmd": "run"}

        self.assertIn(
            "release-identity-binding-dependency-status-not-ready",
            self._evaluate(blocked).blockers,
        )
        self.assertIn("unfinished-tasks-present", self._evaluate(unfinished).blockers)
        self.assertIn("unsupported-nested-object", self._evaluate(malformed).blockers)
        self.assertIn("non-string-mapping-key", self._evaluate(non_string_key).blockers)
        self.assertIn("secret-like-snapshot-data", self._evaluate(secret).blockers)
        self.assertIn("execution-intent-snapshot-data", self._evaluate(execution).blockers)

    def test_result_is_frozen_plain_redacted_and_caller_mutation_safe(self) -> None:
        snapshot = self._snapshot().to_dict()
        original = copy.deepcopy(snapshot)
        result = self._evaluate(snapshot)

        snapshot["dependencies"][0]["metadata"]["canonical_digest"] = "0" * 64
        copied = result.to_dict()
        copied["summary"]["work_id"] = "changed"

        self.assertEqual(
            original["dependencies"][0]["metadata"]["canonical_digest"][:12],
            result.canonical_digest_prefix,
        )
        self.assertEqual("work-001", result.summary["work_id"])
        self.assertEqual("work-001", result.to_dict()["summary"]["work_id"])
        self.assertNotIn(original["dependencies"][0]["metadata"]["canonical_digest"], repr(result.to_dict()))
        with self.assertRaises(TypeError):
            result.summary["work_id"] = "changed"

    def test_forbidden_source_scan(self) -> None:
        import harness_orchestrator.release_publish_readiness as module

        source = inspect.getsource(module)
        forbidden_tokens = (
            "RunLedger(",
            ".save(",
            ".load(",
            "read_text",
            "write_text",
            "requests",
            "urllib",
            "http.client",
            "socket",
            "subprocess",
            "os.environ",
            "importlib",
            "pkg_resources",
            "release_identity_binding_ledger",
            "ai_art",
            "maraca",
            "coordinator",
            "runtime",
            "scheduler",
            "watch",
            "service",
            "client",
            "random",
            "datetime",
        )
        for token in forbidden_tokens:
            self.assertNotIn(token, source)

    def _evaluate(self, snapshot: object, **overrides: object) -> ReleasePublishReadiness:
        kwargs = {
            "ledger_snapshot": snapshot,
            "run_id": "run-001",
            "work_id": "work-001",
            "expected_request_id": "request-001",
            "expected_evidence_bundle_id": "bundle-001",
            "expected_media_ids": ("media-001", "media-002"),
            "expected_artifact_ids": ("artifact-001",),
            "expected_payload_digest": _PAYLOAD_DIGEST,
            "expected_checkpoint_digest": _CHECKPOINT_DIGEST,
            "expected_promotion_intent_digest": _INTENT_DIGEST,
        }
        kwargs.update(overrides)
        return evaluate_release_publish_readiness(**kwargs)

    def _snapshot(
        self,
        *,
        work_id: str = "work-001",
        tasks: tuple[TaskStatus, ...] = (),
    ):
        ledger = RunLedger(run_id="run-001", tasks=tasks)
        binding = build_release_identity_binding(
            gate_decision=self._decision(work_id=work_id),
            identity_proof=self._proof(work_id=work_id),
            work_id=work_id,
            request_id="request-001",
            evidence_bundle_id="bundle-001",
            media_ids=("media-001", "media-002"),
            artifact_ids=("artifact-001",),
            payload_digest=_PAYLOAD_DIGEST,
            checkpoint_digest=_CHECKPOINT_DIGEST,
            promotion_intent_digest=_INTENT_DIGEST,
        )
        result = record_release_identity_binding(binding, ledger=ledger)
        self.assertEqual((), result.blockers)
        return ledger.snapshot()

    def _decision(self, *, work_id: str = "work-001") -> GateDecision:
        return GateDecision(
            decision_id="decision-001",
            work_id=work_id,
            gate_name="ai-art-media-release",
            passed=True,
            reason="released",
            evidence_bundle_id="bundle-001",
            metadata={
                "request_id": "request-001",
                "media_ids": ("media-001", "media-002"),
                "artifact_ids": ("artifact-001",),
                "payload_digest": _PAYLOAD_DIGEST,
                "checkpoint_digest": _CHECKPOINT_DIGEST,
                "promotion_intent_digest": _INTENT_DIGEST,
            },
        )

    def _proof(self, *, work_id: str = "work-001") -> IdentityProofResult:
        return IdentityProofResult(
            passed=True,
            blockers=(),
            canonical_digest=_PROOF_DIGEST,
            canonical_payload={
                "format": "harness-identity-proof-v1",
                "expected": {
                    "work_id": work_id,
                    "request_id": "request-001",
                    "evidence_bundle_id": "bundle-001",
                    "media_ids": ("media-001", "media-002"),
                    "artifact_ids": ("artifact-001",),
                    "payload_digest": _PAYLOAD_DIGEST,
                    "checkpoint_digest": _CHECKPOINT_DIGEST,
                    "promotion_intent_digest": _INTENT_DIGEST,
                },
                "records": (
                    {
                        "work_id": work_id,
                        "request_id": "request-001",
                        "evidence_bundle_id": "bundle-001",
                        "payload_digest": _PAYLOAD_DIGEST,
                    },
                    {
                        "work_id": work_id,
                        "media_ids": ("media-001", "media-002"),
                        "artifact_ids": ("artifact-001",),
                        "checkpoint_digest": _CHECKPOINT_DIGEST,
                        "promotion_intent_digest": _INTENT_DIGEST,
                    },
                ),
            },
            summary={"work_id": work_id, "request_id": "request-001"},
        )


if __name__ == "__main__":
    unittest.main()
