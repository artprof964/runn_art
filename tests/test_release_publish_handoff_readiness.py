import copy
import hashlib
import inspect
import json
from types import MappingProxyType
import unittest

from harness_orchestrator.release_publish_handoff_readiness import (
    ReleasePublishHandoffReadiness,
    evaluate_release_publish_handoff_readiness,
)
from harness_orchestrator.release_publish_intent import build_release_publish_intent
from harness_orchestrator.release_publish_intent_ledger import record_release_publish_intent
from harness_orchestrator.release_publish_readiness import ReleasePublishReadiness
from harness_orchestrator.run_ledger import RunLedger, TaskStatus


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictSnapshot:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class ReleasePublishHandoffReadinessTests(unittest.TestCase):
    def test_happy_path_accepts_snapshot_mapping_and_to_dict(self) -> None:
        snapshot = self._snapshot()

        results = (
            self._evaluate(snapshot),
            self._evaluate(snapshot.to_dict()),
            self._evaluate(ToDictSnapshot(snapshot.to_dict())),
        )

        for result in results:
            with self.subTest(kind=type(result).__name__):
                self.assertIsInstance(result, ReleasePublishHandoffReadiness)
                self.assertTrue(result.ready)
                self.assertEqual("ready", result.status)
                self.assertEqual((), result.blockers)
                self.assertEqual("run-001", result.run_id)
                self.assertEqual("work-001", result.work_id)
                self.assertEqual(_BINDING_DIGEST[:12], result.release_binding_digest_prefix)
                self.assertEqual(12, len(result.intent_digest_prefix))
                self.assertIsInstance(result.summary, MappingProxyType)
                json.dumps(result.to_dict(), sort_keys=True)

    def test_requires_matching_run_and_work_identity(self) -> None:
        run = self._evaluate(self._snapshot(), run_id="run-999")
        work = self._evaluate(self._snapshot(work_id="work-002"), work_id="work-001")

        self.assertFalse(run.ready)
        self.assertIn("snapshot-run-id-mismatch", run.blockers)
        self.assertFalse(work.ready)
        self.assertIn("release-publish-intent-dependency-missing", work.blockers)
        self.assertIn("release-publish-intent-event-missing", work.blockers)

    def test_requires_exactly_one_matching_dependency_and_event_pair(self) -> None:
        missing_dependency = self._snapshot().to_dict()
        missing_dependency["dependencies"] = ()
        duplicate_dependency = self._snapshot().to_dict()
        duplicate_dependency["dependencies"] = (
            *duplicate_dependency["dependencies"],
            copy.deepcopy(duplicate_dependency["dependencies"][0]),
        )
        missing_event = self._snapshot().to_dict()
        missing_event["audit_events"] = ()
        duplicate_event = self._snapshot().to_dict()
        duplicate_event["audit_events"] = (
            *duplicate_event["audit_events"],
            copy.deepcopy(duplicate_event["audit_events"][0]),
        )

        self.assertIn(
            "release-publish-intent-dependency-missing",
            self._evaluate(missing_dependency).blockers,
        )
        self.assertIn(
            "release-publish-intent-dependency-ambiguous",
            self._evaluate(duplicate_dependency).blockers,
        )
        self.assertIn(
            "release-publish-intent-event-missing",
            self._evaluate(missing_event).blockers,
        )
        self.assertIn(
            "release-publish-intent-event-ambiguous",
            self._evaluate(duplicate_event).blockers,
        )

    def test_event_dependency_pairing_mismatch_blocks(self) -> None:
        snapshot = self._snapshot().to_dict()
        snapshot["audit_events"][0]["metadata"]["dependency_id"] = "other"

        result = self._evaluate(snapshot)

        self.assertFalse(result.ready)
        self.assertIn("event-dependency-id-mismatch", result.blockers)

    def test_metadata_run_ids_are_required_when_removed(self) -> None:
        snapshot = self._snapshot().to_dict()
        del snapshot["dependencies"][0]["metadata"]["run_id"]
        del snapshot["audit_events"][0]["metadata"]["run_id"]

        result = self._evaluate(snapshot)

        self.assertFalse(result.ready)
        self.assertIn("missing-dependency-run-id", result.blockers)
        self.assertIn("missing-event-run-id", result.blockers)

    def test_metadata_run_ids_are_required_when_blank(self) -> None:
        snapshot = self._snapshot().to_dict()
        snapshot["dependencies"][0]["metadata"]["run_id"] = ""
        snapshot["audit_events"][0]["metadata"]["run_id"] = ""

        result = self._evaluate(snapshot)

        self.assertFalse(result.ready)
        self.assertIn("missing-dependency-run-id", result.blockers)
        self.assertIn("missing-event-run-id", result.blockers)

    def test_metadata_run_ids_must_match_when_present(self) -> None:
        snapshot = self._snapshot().to_dict()
        snapshot["dependencies"][0]["metadata"]["run_id"] = "run-999"
        snapshot["audit_events"][0]["metadata"]["run_id"] = "run-998"

        result = self._evaluate(snapshot)

        self.assertFalse(result.ready)
        self.assertIn("dependency-run-id-mismatch", result.blockers)
        self.assertIn("event-run-id-mismatch", result.blockers)

    def test_top_level_event_id_must_match_intent_digest_suffix(self) -> None:
        snapshot = self._snapshot().to_dict()
        snapshot["audit_events"][0][
            "event_id"
        ] = "release-publish-intent-recorded:work-001:ffffffffffffffff"

        result = self._evaluate(snapshot)

        self.assertFalse(result.ready)
        self.assertIn("release-publish-intent-event-id-mismatch", result.blockers)

    def test_top_level_dependency_id_must_match_intent_digest_suffix(self) -> None:
        snapshot = self._snapshot().to_dict()
        snapshot["dependencies"][0][
            "dependency_id"
        ] = "release-publish-intent:work-001:ffffffffffffffff"

        result = self._evaluate(snapshot)

        self.assertFalse(result.ready)
        self.assertIn("release-publish-intent-dependency-id-mismatch", result.blockers)

    def test_status_and_blockers_are_strict(self) -> None:
        dependency_done = self._snapshot().to_dict()
        dependency_done["dependencies"][0]["status"] = "done"
        event_ready_case = self._snapshot().to_dict()
        event_ready_case["audit_events"][0]["status"] = "Ready"
        unfinished = self._snapshot(
            tasks=(TaskStatus("task-1", "work-001", "open"),)
        )
        task_blocker = self._snapshot(
            tasks=(TaskStatus("task-1", "work-001", "done", blockers=("open",)),)
        )

        self.assertIn(
            "release-publish-intent-dependency-status-not-ready",
            self._evaluate(dependency_done).blockers,
        )
        self.assertIn(
            "release-publish-intent-event-status-not-ready",
            self._evaluate(event_ready_case).blockers,
        )
        self.assertIn("unfinished-tasks-present", self._evaluate(unfinished).blockers)
        self.assertIn("unfinished-tasks-present", self._evaluate(task_blocker).blockers)
        self.assertTrue(
            self._evaluate(unfinished, require_finished_tasks=False).ready
        )

    def test_digest_canonical_and_summary_mismatch_block(self) -> None:
        digest = self._snapshot().to_dict()
        digest["dependencies"][0]["metadata"]["intent_digest"] = "0" * 64
        canonical = self._snapshot().to_dict()
        canonical["dependencies"][0]["metadata"]["canonical_payload"][
            "readiness_binding"
        ]["release_binding_digest"] = "c" * 64
        summary = self._snapshot().to_dict()
        summary["dependencies"][0]["metadata"]["release_binding_digest_prefix"] = "0" * 12
        event = self._snapshot().to_dict()
        event["audit_events"][0]["metadata"]["intent_digest"] = "1" * 64

        self.assertIn(
            "release-publish-intent-digest-mismatch",
            self._evaluate(digest).blockers,
        )
        self.assertIn(
            "canonical-release-binding-digest-mismatch",
            self._evaluate(canonical).blockers,
        )
        self.assertIn(
            "release-binding-digest-prefix-mismatch",
            self._evaluate(summary).blockers,
        )
        self.assertIn("event-intent-digest-mismatch", self._evaluate(event).blockers)

    def test_safe_publish_target_payload_artifact_metadata_are_required(self) -> None:
        target = self._snapshot().to_dict()
        target["dependencies"][0]["metadata"]["publish_target"]["target_type"] = "prod"
        payload = self._snapshot().to_dict()
        payload["dependencies"][0]["metadata"]["publish_payload"]["payload_digest"] = "bad"
        artifact = self._snapshot().to_dict()
        artifact["dependencies"][0]["metadata"]["artifact"]["artifact_id"] = "bad/path"
        metadata = self._snapshot().to_dict()
        metadata["dependencies"][0]["metadata"]["intent_metadata"] = {
            "details": {"nested": "bad"}
        }

        self.assertIn("unsafe-publish-target-schema", self._evaluate(target).blockers)
        self.assertIn("unsafe-publish-payload-schema", self._evaluate(payload).blockers)
        self.assertIn("unsafe-artifact-schema", self._evaluate(artifact).blockers)
        self.assertIn("unsafe-intent-metadata-schema", self._evaluate(metadata).blockers)

    def test_non_string_keys_unsupported_objects_and_caller_mutation_fail_closed(self) -> None:
        non_string = self._snapshot().to_dict()
        non_string["metadata"] = {1: "bad"}
        unsupported = self._snapshot().to_dict()
        unsupported["dependencies"][0]["metadata"]["artifact"]["bad"] = object()
        snapshot = self._snapshot().to_dict()
        original_digest = snapshot["dependencies"][0]["metadata"]["intent_digest"]

        result = self._evaluate(snapshot)
        snapshot["dependencies"][0]["metadata"]["intent_digest"] = "0" * 64

        self.assertIn("non-string-mapping-key", self._evaluate(non_string).blockers)
        self.assertIn("unsupported-nested-object", self._evaluate(unsupported).blockers)
        self.assertEqual(original_digest[:12], result.intent_digest_prefix)
        self.assertEqual(original_digest[:12], result.to_dict()["intent_digest_prefix"])
        with self.assertRaises(TypeError):
            result.summary["work_id"] = "changed"

    def test_result_to_dict_is_plain_copy(self) -> None:
        result = self._evaluate(self._snapshot())
        plain = result.to_dict()

        plain["summary"]["work_id"] = "changed"

        self.assertEqual("work-001", result.summary["work_id"])
        self.assertEqual("work-001", result.to_dict()["summary"]["work_id"])

    def test_forbidden_source_scan(self) -> None:
        import harness_orchestrator.release_publish_handoff_readiness as module

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

    def _evaluate(
        self,
        snapshot: object,
        **overrides: object,
    ) -> ReleasePublishHandoffReadiness:
        kwargs = {
            "ledger_snapshot": snapshot,
            "run_id": "run-001",
            "work_id": "work-001",
        }
        kwargs.update(overrides)
        return evaluate_release_publish_handoff_readiness(**kwargs)

    def _snapshot(
        self,
        *,
        work_id: str = "work-001",
        tasks: tuple[TaskStatus, ...] = (),
    ):
        ledger = RunLedger(run_id="run-001", tasks=tasks)
        intent = build_release_publish_intent(
            readiness=self._readiness(work_id=work_id),
            run_id="run-001",
            work_id=work_id,
            release_binding_digest=_BINDING_DIGEST,
            publish_target={"target_type": "local-dry-run", "target_id": "target-001"},
            publish_payload={
                "payload_digest": _PAYLOAD_DIGEST,
                "payload_label": "release",
            },
            artifact={"artifact_id": "artifact-001"},
            metadata={"ticket": "HAR-033", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), result.blockers)
        return ledger.snapshot()

    def _readiness(self, *, work_id: str = "work-001") -> ReleasePublishReadiness:
        return ReleasePublishReadiness(
            ready=True,
            status="ready",
            blockers=(),
            run_id="run-001",
            work_id=work_id,
            dependency_id="dependency-001",
            event_id="event-001",
            canonical_digest_prefix=_BINDING_DIGEST[:12],
            summary={"format": "harness-release-publish-readiness-v1"},
        )


def _sha256_payload(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    unittest.main()
