import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_completion_readiness as readiness_module
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
    ReleasePublishHandoffCompletionReadiness,
    evaluate_release_publish_handoff_completion_readiness,
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
from harness_orchestrator.run_ledger import RunLedger


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictVerification:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class RaisingToDictVerification:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("conversion failed")


class NonMappingToDictVerification:
    def to_dict(self) -> str:
        return "bad"


class ReleasePublishHandoffCompletionReadinessTests(unittest.TestCase):
    def test_happy_path_accepts_mapping_dataclass_and_to_dict_verification(self) -> None:
        verification = self._verification()
        data = verification.to_dict()

        @dataclass(frozen=True)
        class VerificationData:
            passed: bool
            blockers: tuple[str, ...]
            run_id: str
            work_id: str
            dependency_id: str
            event_id: str
            package_digest: str
            package_digest_prefix: str
            source_dependency_id: str
            source_event_id: str
            receipt_summary: dict[str, object]

        dataclass_verification = VerificationData(**data)
        results = (
            evaluate_release_publish_handoff_completion_readiness(
                data,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=data["dependency_id"],
                expected_event_id=data["event_id"],
                expected_package_digest=data["package_digest"],
            ),
            evaluate_release_publish_handoff_completion_readiness(
                dataclass_verification,
                run_id="run-001",
                work_id="work-001",
            ),
            evaluate_release_publish_handoff_completion_readiness(
                ToDictVerification(data),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for result in results:
            with self.subTest(result=result):
                self.assertIsInstance(result, ReleasePublishHandoffCompletionReadiness)
                self.assertTrue(result.ready)
                self.assertEqual("ready", result.status)
                self.assertEqual((), result.blockers)
                self.assertEqual("run-001", result.run_id)
                self.assertEqual("work-001", result.work_id)
                self.assertEqual(data["dependency_id"], result.dependency_id)
                self.assertEqual(data["event_id"], result.event_id)
                self.assertEqual(data["package_digest"], result.package_digest)
                self.assertEqual(data["package_digest_prefix"], result.package_digest_prefix)
                self.assertEqual(data["source_dependency_id"], result.source_dependency_id)
                self.assertEqual(data["source_event_id"], result.source_event_id)
                self.assertTrue(result.readiness_summary["ready"])
                json.dumps(result.to_dict(), sort_keys=True)

    def test_requires_passed_empty_blockers_matching_run_work_safe_ids_and_digest_prefix(self) -> None:
        base = self._verification().to_dict()
        cases = (
            ("passed", False, "release-publish-handoff-acceptance-verification-not-passed"),
            ("blockers", ("operator-review-open",), "source-blockers-present"),
            ("run_id", "run-999", "release-publish-handoff-completion-readiness-verification-run-id-mismatch"),
            ("work_id", "work-999", "release-publish-handoff-completion-readiness-verification-work-id-mismatch"),
            ("dependency_id", " release-publish-handoff-acceptance:work-001:bad", "unsafe-dependency-id"),
            ("event_id", "", "missing-event-id"),
            ("package_digest", "f" * 63, "invalid-package-digest"),
            ("package_digest_prefix", "0" * 12, "release-publish-handoff-completion-readiness-package-digest-prefix-mismatch"),
            ("source_dependency_id", "", "missing-source-dependency-id"),
            ("source_event_id", "source runner", "action-intent-source-event-id"),
        )

        for key, value, blocker in cases:
            payload = copy.deepcopy(base)
            payload[key] = value
            with self.subTest(key=key):
                self.assertIn(blocker, self._evaluate(payload).blockers)

    def test_deterministic_acceptance_ids_expected_values_and_summary_parity_are_verified(self) -> None:
        base = self._verification().to_dict()
        package_digest = base["package_digest"]
        dependency_id = f"release-publish-handoff-acceptance:work-001:{package_digest[:16]}"
        event_id = f"release-publish-handoff-acceptance-recorded:work-001:{package_digest[:16]}"
        wrong_dependency = copy.deepcopy(base)
        wrong_dependency["dependency_id"] = "release-publish-handoff-acceptance:work-001:ffffffffffffffff"
        wrong_event = copy.deepcopy(base)
        wrong_event["event_id"] = "release-publish-handoff-acceptance-recorded:work-001:ffffffffffffffff"
        wrong_summary = copy.deepcopy(base)
        wrong_summary["receipt_summary"]["source_dependency_id"] = "changed-source"
        blocked_summary = copy.deepcopy(base)
        blocked_summary["receipt_summary"]["source_blocker_count"] = 1

        self.assertEqual(dependency_id, base["dependency_id"])
        self.assertEqual(event_id, base["event_id"])
        self.assertIn(
            "release-publish-handoff-completion-readiness-dependency-id-mismatch",
            self._evaluate(wrong_dependency).blockers,
        )
        self.assertIn(
            "release-publish-handoff-completion-readiness-event-id-mismatch",
            self._evaluate(wrong_event).blockers,
        )
        self.assertIn(
            "release-publish-handoff-completion-readiness-receipt-summary-source-dependency-id-mismatch",
            self._evaluate(wrong_summary).blockers,
        )
        self.assertIn(
            "release-publish-handoff-completion-readiness-receipt-summary-source-blocker-count-mismatch",
            self._evaluate(blocked_summary).blockers,
        )
        self.assertIn(
            "expected-dependency-id-mismatch",
            evaluate_release_publish_handoff_completion_readiness(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id="release-publish-handoff-acceptance:work-001:ffffffffffffffff",
            ).blockers,
        )
        self.assertIn(
            "expected-event-id-mismatch",
            evaluate_release_publish_handoff_completion_readiness(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_event_id="release-publish-handoff-acceptance-recorded:work-001:ffffffffffffffff",
            ).blockers,
        )
        self.assertIn(
            "expected-package-digest-mismatch",
            evaluate_release_publish_handoff_completion_readiness(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_package_digest="c" * 64,
            ).blockers,
        )

    def test_malformed_secret_action_non_string_key_and_unsupported_object_fail_closed(self) -> None:
        base = self._verification().to_dict()
        extra = copy.deepcopy(base)
        extra["extra"] = "bad"
        non_string = copy.deepcopy(base)
        non_string["receipt_summary"] = {object(): "bad"}
        unsupported = copy.deepcopy(base)
        unsupported["receipt_summary"]["bad"] = object()
        secret = copy.deepcopy(base)
        secret["receipt_summary"]["api_key"] = "raw"
        action = copy.deepcopy(base)
        action["receipt_summary"]["runner"] = "manual"

        self.assertIn(
            "missing-release-publish-handoff-acceptance-verification",
            evaluate_release_publish_handoff_completion_readiness(
                "bad",
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        for source in (RaisingToDictVerification(), NonMappingToDictVerification()):
            with self.subTest(source=source):
                result = self._evaluate(source)
                self.assertIn(
                    "malformed-release-publish-handoff-completion-readiness",
                    result.blockers,
                )
                self.assertIn(
                    "missing-release-publish-handoff-acceptance-verification",
                    result.blockers,
                )
        self.assertIn(
            "unsafe-release-publish-handoff-completion-verification-schema",
            self._evaluate(extra).blockers,
        )
        self.assertIn(
            "non-string-key-release-publish-handoff-completion-readiness",
            self._evaluate(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-handoff-completion-readiness",
            self._evaluate(unsupported).blockers,
        )
        self.assertIn("secret-like-verification-data", self._evaluate(secret).blockers)
        self.assertIn("action-intent-verification-data", self._evaluate(action).blockers)

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        payload = self._verification().to_dict()
        result = self._evaluate(payload)
        before = result.to_dict()

        payload["package_digest"] = "c" * 64
        payload["receipt_summary"]["dependency_id"] = "changed"
        plain = result.to_dict()
        plain["readiness_summary"]["dependency_id"] = "changed"

        self.assertEqual(before, result.to_dict())
        with self.assertRaises(TypeError):
            result.readiness_summary["dependency_id"] = "changed"
        with self.assertRaises(TypeError):
            result.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.package_digest = "changed"

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(readiness_module)
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
        imports = tuple(
            line for line in source.splitlines() if line.startswith(("import ", "from "))
        )
        self.assertEqual(
            (
                "from __future__ import annotations",
                "from dataclasses import dataclass, fields, is_dataclass",
                "import re",
                "from types import MappingProxyType",
                "from typing import Any, Mapping",
            ),
            imports,
        )

    def _evaluate(self, payload: object) -> ReleasePublishHandoffCompletionReadiness:
        return evaluate_release_publish_handoff_completion_readiness(
            payload,
            run_id="run-001",
            work_id="work-001",
        )

    def _verification(self) -> object:
        ledger = RunLedger(run_id="run-001")
        intent = build_release_publish_intent(
            readiness=ReleasePublishReadiness(
                ready=True,
                status="ready",
                blockers=(),
                run_id="run-001",
                work_id="work-001",
                dependency_id="dependency-work-001",
                event_id="event-work-001",
                canonical_digest_prefix=_BINDING_DIGEST[:12],
                summary={"format": "harness-release-publish-readiness-v1"},
            ),
            run_id="run-001",
            work_id="work-001",
            release_binding_digest=_BINDING_DIGEST,
            publish_target={
                "target_type": "local-dry-run",
                "target_id": "target-work-001",
            },
            publish_payload={
                "payload_digest": _PAYLOAD_DIGEST,
                "payload_label": "release",
            },
            artifact={"artifact_id": "artifact-work-001"},
            metadata={"ticket": "HAR-041", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        intent_result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), intent_result.blockers)
        readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id="run-001",
            work_id="work-001",
        )
        self.assertEqual((), readiness.blockers)
        readiness_result = record_release_publish_handoff_readiness(
            readiness,
            ledger=ledger,
        )
        self.assertEqual((), readiness_result.blockers)
        package = build_release_publish_handoff_package(
            ledger.snapshot(),
            run_id="run-001",
            work_id="work-001",
        )
        self.assertEqual((), package.blockers)
        package_result = record_release_publish_handoff_package(package, ledger=ledger)
        self.assertEqual((), package_result.blockers)
        package_verification = verify_release_publish_handoff_receipt(
            ledger.snapshot().to_dict(),
            run_id="run-001",
            work_id="work-001",
        )
        self.assertTrue(package_verification.passed)
        acceptance = evaluate_release_publish_handoff_acceptance(
            package_verification,
            run_id="run-001",
            work_id="work-001",
        )
        self.assertTrue(acceptance.accepted)
        acceptance_result = record_release_publish_handoff_acceptance(
            acceptance,
            ledger=ledger,
        )
        self.assertEqual((), acceptance_result.blockers)
        verification = verify_release_publish_handoff_acceptance_receipt(
            ledger.snapshot().to_dict(),
            run_id="run-001",
            work_id="work-001",
        )
        self.assertTrue(verification.passed)
        return verification


if __name__ == "__main__":
    unittest.main()
