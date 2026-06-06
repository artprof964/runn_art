import copy
from dataclasses import FrozenInstanceError
import hashlib
import inspect
import json
from types import MappingProxyType
import unittest

import harness_orchestrator.release_publish_handoff_package as package_module
from harness_orchestrator.release_publish_handoff_package import (
    ReleasePublishHandoffPackage,
    build_release_publish_handoff_package,
    evaluate_release_publish_handoff_package,
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
from harness_orchestrator.run_ledger import RunLedger


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictSnapshot:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class ReleasePublishHandoffPackageTests(unittest.TestCase):
    def test_happy_path_accepts_snapshot_mapping_and_to_dict(self) -> None:
        snapshot = self._snapshot()

        results = (
            self._package(snapshot),
            self._package(snapshot.to_dict()),
            self._package(ToDictSnapshot(snapshot.to_dict())),
            evaluate_release_publish_handoff_package(
                snapshot,
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for result in results:
            with self.subTest(kind=type(result).__name__):
                self.assertIsInstance(result, ReleasePublishHandoffPackage)
                self.assertTrue(result.ready)
                self.assertEqual("ready", result.status)
                self.assertEqual((), result.blockers)
                self.assertEqual("run-001", result.run_id)
                self.assertEqual("work-001", result.work_id)
                self.assertEqual(_BINDING_DIGEST[:12], result.release_binding_digest_prefix)
                self.assertEqual(12, len(result.intent_digest_prefix))
                self.assertEqual(64, len(result.package_digest))
                self.assertEqual(result.package_digest[:12], result.package_digest_prefix)
                self.assertIsInstance(result.package_data, MappingProxyType)
                self.assertEqual(
                    result.package_digest,
                    self._canonical_digest(result.to_dict()["package_data"]),
                )
                json.dumps(result.to_dict(), sort_keys=True)

    def test_package_data_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        snapshot = self._snapshot().to_dict()
        intent_dependency = self._dependency(snapshot, "release-publish-intent")
        original_target = copy.deepcopy(intent_dependency["metadata"]["publish_target"])

        result = self._package(snapshot)
        intent_dependency["metadata"]["publish_target"]["target_id"] = "changed"

        self.assertEqual(original_target, result.to_dict()["package_data"]["publish_target"])
        with self.assertRaises(TypeError):
            result.package_data["publish_target"]["target_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.status = "changed"
        plain = result.to_dict()
        plain["package_data"]["publish_target"]["target_id"] = "changed"
        self.assertEqual(original_target, result.to_dict()["package_data"]["publish_target"])

    def test_requires_matching_run_work_and_exactly_one_ready_readiness_dependency_and_event(self) -> None:
        run = self._package(self._snapshot(), run_id="run-999")
        work = self._package(self._snapshot(work_id="work-002"), work_id="work-001")
        missing_dependency = self._snapshot().to_dict()
        missing_dependency["dependencies"] = tuple(
            record
            for record in missing_dependency["dependencies"]
            if record["dependency_type"] != "release-publish-handoff-readiness"
        )
        duplicate_dependency = self._snapshot().to_dict()
        duplicate_dependency["dependencies"] = (
            *duplicate_dependency["dependencies"],
            copy.deepcopy(
                self._dependency(
                    duplicate_dependency,
                    "release-publish-handoff-readiness",
                )
            ),
        )
        missing_event = self._snapshot().to_dict()
        missing_event["audit_events"] = tuple(
            event
            for event in missing_event["audit_events"]
            if event["event_type"] != "release-publish-handoff-readiness-ledger-record"
        )
        blocked_dependency = self._snapshot().to_dict()
        self._dependency(
            blocked_dependency,
            "release-publish-handoff-readiness",
        )["status"] = "blocked"

        self.assertIn("snapshot-run-id-mismatch", run.blockers)
        self.assertIn("release-publish-handoff-readiness-dependency-missing", work.blockers)
        self.assertIn("release-publish-handoff-readiness-event-missing", work.blockers)
        self.assertIn(
            "release-publish-handoff-readiness-dependency-missing",
            self._package(missing_dependency).blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-dependency-ambiguous",
            self._package(duplicate_dependency).blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-event-missing",
            self._package(missing_event).blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-dependency-status-not-ready",
            self._package(blocked_dependency).blockers,
        )

    def test_readiness_event_pair_and_source_intent_references_are_verified(self) -> None:
        event_pair = self._snapshot().to_dict()
        event_pair["audit_events"][1]["metadata"]["dependency_id"] = "other"
        source_missing = self._snapshot().to_dict()
        source_missing["dependencies"] = tuple(
            record
            for record in source_missing["dependencies"]
            if record["dependency_type"] != "release-publish-intent"
        )
        source_event = self._snapshot().to_dict()
        source_event["audit_events"][0]["metadata"]["dependency_id"] = "other"
        source_run = self._snapshot().to_dict()
        self._dependency(
            source_run,
            "release-publish-intent",
        )["metadata"]["run_id"] = "run-999"
        source_work = self._snapshot().to_dict()
        self._dependency(source_work, "release-publish-intent")["work_id"] = "work-999"

        self.assertIn(
            "readiness-event-dependency-id-mismatch",
            self._package(event_pair).blockers,
        )
        self.assertIn(
            "source-release-publish-intent-dependency-missing",
            self._package(source_missing).blockers,
        )
        self.assertIn(
            "source-intent-event-dependency-id-mismatch",
            self._package(source_event).blockers,
        )
        self.assertIn(
            "source-intent-dependency-run-id-mismatch",
            self._package(source_run).blockers,
        )
        self.assertIn(
            "source-release-publish-intent-dependency-work-id-mismatch",
            self._package(source_work).blockers,
        )

    def test_readiness_deterministic_dependency_and_event_ids_are_verified(self) -> None:
        dependency_id = self._snapshot().to_dict()
        self._dependency(
            dependency_id,
            "release-publish-handoff-readiness",
        )["dependency_id"] = "release-publish-handoff-readiness:work-001:ffffffffffffffff"
        event_id = self._snapshot().to_dict()
        self._event(
            event_id,
            "release-publish-handoff-readiness-ledger-record",
        )["event_id"] = "release-publish-handoff-readiness-recorded:work-001:ffffffffffffffff"

        self.assertIn(
            "release-publish-handoff-readiness-dependency-id-mismatch",
            self._package(dependency_id).blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-event-id-mismatch",
            self._package(event_id).blockers,
        )

    def test_digest_prefix_canonical_payload_and_package_digest_mismatches_block(self) -> None:
        intent_prefix = self._snapshot().to_dict()
        self._dependency(
            intent_prefix,
            "release-publish-handoff-readiness",
        )["metadata"]["intent_digest_prefix"] = "0" * 12
        readiness_digest = self._snapshot().to_dict()
        self._dependency(
            readiness_digest,
            "release-publish-handoff-readiness",
        )["metadata"]["canonical_payload"][
            "handoff_readiness"
        ]["work_id"] = "work-999"
        intent_digest = self._snapshot().to_dict()
        self._dependency(
            intent_digest,
            "release-publish-intent",
        )["metadata"]["canonical_payload"][
            "readiness_binding"
        ]["work_id"] = "work-999"
        source_id = self._snapshot().to_dict()
        self._dependency(source_id, "release-publish-intent")[
            "dependency_id"
        ] = "release-publish-intent:work-001:ffffffffffffffff"

        self.assertIn(
            "readiness-intent-digest-prefix-mismatch",
            self._package(intent_prefix).blockers,
        )
        self.assertIn(
            "release-publish-handoff-readiness-digest-mismatch",
            self._package(readiness_digest).blockers,
        )
        self.assertIn(
            "release-publish-intent-digest-mismatch",
            self._package(intent_digest).blockers,
        )
        self.assertIn(
            "source-release-publish-intent-dependency-missing",
            self._package(source_id).blockers,
        )

    def test_publish_target_payload_artifact_and_metadata_schema_fail_closed(self) -> None:
        target = self._snapshot().to_dict()
        self._dependency(target, "release-publish-intent")["metadata"]["publish_target"][
            "target_type"
        ] = "production"
        payload = self._snapshot().to_dict()
        self._dependency(payload, "release-publish-intent")["metadata"]["publish_payload"][
            "payload_digest"
        ] = "bad"
        artifact = self._snapshot().to_dict()
        self._dependency(artifact, "release-publish-intent")["metadata"]["artifact"][
            "artifact_id"
        ] = "bad/path"
        metadata = self._snapshot().to_dict()
        self._dependency(metadata, "release-publish-intent")["metadata"][
            "intent_metadata"
        ] = {"api_key": "raw"}
        canonical_schema = self._snapshot().to_dict()
        self._dependency(canonical_schema, "release-publish-intent")["metadata"][
            "canonical_payload"
        ][
            "caller_supplied_intent_metadata"
        ]["extra"] = "bad"

        self.assertIn("unsafe-publish-target-schema", self._package(target).blockers)
        self.assertIn("unsafe-publish-payload-schema", self._package(payload).blockers)
        self.assertIn("unsafe-artifact-schema", self._package(artifact).blockers)
        self.assertIn("secret-like-snapshot-data", self._package(metadata).blockers)
        self.assertIn(
            "unsafe-caller-intent-metadata-schema",
            self._package(canonical_schema).blockers,
        )

    def test_malformed_unsafe_secret_action_and_unsupported_inputs_fail_closed(self) -> None:
        non_string = self._snapshot().to_dict()
        non_string["metadata"] = {object(): "bad"}
        unsupported = self._snapshot().to_dict()
        self._dependency(unsupported, "release-publish-intent")["metadata"]["artifact"][
            "bad"
        ] = object()
        unsafe = self._snapshot().to_dict()
        self._dependency(
            unsafe,
            "release-publish-handoff-readiness",
        )["metadata"]["source_event_id"] = "bad/event"
        action = self._snapshot().to_dict()
        self._dependency(action, "release-publish-intent")["metadata"][
            "intent_metadata"
        ] = {"runner": "manual"}

        self.assertIn("non-string-mapping-key", self._package(non_string).blockers)
        self.assertIn("unsupported-nested-object", self._package(unsupported).blockers)
        self.assertIn("unsafe-source-event-id", self._package(unsafe).blockers)
        self.assertIn("action-intent-snapshot-data", self._package(action).blockers)
        self.assertIn(
            "missing-ledger-snapshot",
            self._package(None).blockers,
        )

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(package_module)
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
        self.assertEqual(
            (
                "from __future__ import annotations",
                "from dataclasses import dataclass",
                "import hashlib",
                "import json",
                "import re",
                "from types import MappingProxyType",
                "from typing import Any, Mapping",
                "from harness_orchestrator.run_ledger import RunLedgerSnapshot",
            ),
            imports,
        )

    def _package(
        self,
        snapshot: object,
        **overrides: object,
    ) -> ReleasePublishHandoffPackage:
        kwargs = {
            "ledger_snapshot": snapshot,
            "run_id": "run-001",
            "work_id": "work-001",
        }
        kwargs.update(overrides)
        return build_release_publish_handoff_package(**kwargs)

    def _snapshot(self, *, work_id: str = "work-001"):
        ledger = RunLedger(run_id="run-001")
        intent = build_release_publish_intent(
            readiness=ReleasePublishReadiness(
                ready=True,
                status="ready",
                blockers=(),
                run_id="run-001",
                work_id=work_id,
                dependency_id=f"dependency-{work_id}",
                event_id=f"event-{work_id}",
                canonical_digest_prefix=_BINDING_DIGEST[:12],
                summary={"format": "harness-release-publish-readiness-v1"},
            ),
            run_id="run-001",
            work_id=work_id,
            release_binding_digest=_BINDING_DIGEST,
            publish_target={"target_type": "local-dry-run", "target_id": f"target-{work_id}"},
            publish_payload={
                "payload_digest": _PAYLOAD_DIGEST,
                "payload_label": "release",
            },
            artifact={"artifact_id": f"artifact-{work_id}"},
            metadata={"ticket": "HAR-035", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        intent_result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), intent_result.blockers)
        readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id="run-001",
            work_id=work_id,
        )
        self.assertEqual((), readiness.blockers)
        handoff_result = record_release_publish_handoff_readiness(
            readiness,
            ledger=ledger,
        )
        self.assertEqual((), handoff_result.blockers)
        return ledger.snapshot()

    def _dependency(
        self,
        snapshot: dict[str, object],
        dependency_type: str,
    ) -> dict[str, object]:
        return next(
            record
            for record in snapshot["dependencies"]
            if record["dependency_type"] == dependency_type
        )

    def _event(
        self,
        snapshot: dict[str, object],
        event_type: str,
    ) -> dict[str, object]:
        return next(
            event
            for event in snapshot["audit_events"]
            if event["event_type"] == event_type
        )

    def _canonical_digest(self, payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


if __name__ == "__main__":
    unittest.main()
