import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_handoff_readiness as handoff_module
import tests.test_release_publish_execution_authorization_ledger as authorization_ledger_tests
from harness_orchestrator.release_publish_execution_authorization_ledger import (
    record_release_publish_execution_authorization,
)
from harness_orchestrator.release_publish_execution_authorization_receipt import (
    verify_release_publish_execution_authorization_receipt,
)
from harness_orchestrator.release_publish_execution_handoff_readiness import (
    ReleasePublishExecutionHandoffReadiness,
    evaluate_release_publish_execution_handoff_readiness,
    verify_release_publish_execution_handoff_readiness,
)


class ToDictHandoffSource:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class RaisingToDictSource:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictSource:
    def to_dict(self) -> tuple[str, ...]:
        return ("bad",)


class RaisingToDictValue:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictValue:
    def to_dict(self) -> tuple[str, ...]:
        return ("bad",)


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
    completion_source_dependency_id: str
    completion_source_event_id: str
    acceptance_source_dependency_id: str
    acceptance_source_event_id: str
    final_authorization_dependency_id: str
    final_authorization_event_id: str
    authorization_summary: dict[str, object]
    receipt_summary: dict[str, object]


class ReleasePublishExecutionHandoffReadinessTests(unittest.TestCase):
    def test_happy_path_accepts_verification_mapping_dataclass_to_dict_and_expected_values(self) -> None:
        verification = self._verification()
        payload = verification.to_dict()
        digest = payload["package_digest"]
        suffix = digest[:16]
        expected_dependency_id = (
            f"release-publish-execution-handoff-readiness:work-001:{suffix}"
        )
        expected_event_id = (
            f"release-publish-execution-handoff-readiness-recorded:work-001:{suffix}"
        )

        results = (
            evaluate_release_publish_execution_handoff_readiness(
                verification,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=digest,
                expected_source_dependency_id=payload["dependency_id"],
                expected_source_event_id=payload["event_id"],
                expected_authorization_dependency_id=payload["dependency_id"],
                expected_authorization_event_id=payload["event_id"],
            ),
            evaluate_release_publish_execution_handoff_readiness(
                payload,
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_execution_handoff_readiness(
                ToDictHandoffSource(payload),
                run_id="run-001",
                work_id="work-001",
            ),
            evaluate_release_publish_execution_handoff_readiness(
                VerificationData(**copy.deepcopy(payload)),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for result in results:
            with self.subTest(result=result):
                self.assertIsInstance(result, ReleasePublishExecutionHandoffReadiness)
                self.assertTrue(result.ready)
                self.assertEqual("ready", result.status)
                self.assertEqual((), result.blockers)
                self.assertEqual(expected_dependency_id, result.dependency_id)
                self.assertEqual(expected_event_id, result.event_id)
                self.assertEqual(payload["dependency_id"], result.source_dependency_id)
                self.assertEqual(payload["event_id"], result.source_event_id)
                self.assertEqual(payload["authorization_summary"], result.authorization_summary)
                self.assertEqual(payload["receipt_summary"], result.receipt_summary)
                self.assertTrue(result.readiness_summary["ready"])
                self.assertEqual(digest[:12], result.package_digest_prefix)
                json.dumps(result.to_dict(), sort_keys=True)

    def test_missing_failed_blocked_mismatched_digest_ids_and_expected_values_fail_closed(self) -> None:
        payload = self._payload()
        digest = payload["package_digest"]
        suffix = digest[:16]
        missing = copy.deepcopy(payload)
        missing.pop("authorization_summary")
        extra = copy.deepcopy(payload)
        extra["publish"] = "now"
        failed = copy.deepcopy(payload)
        failed["passed"] = False
        blocked = copy.deepcopy(payload)
        blocked["blockers"] = ("operator-review-open",)
        bad_run = copy.deepcopy(payload)
        bad_run["run_id"] = "run-999"
        bad_digest = copy.deepcopy(payload)
        bad_digest["package_digest"] = "A" * 64
        bad_prefix = copy.deepcopy(payload)
        bad_prefix["package_digest_prefix"] = "f" * 12
        bad_authorization_id = copy.deepcopy(payload)
        bad_authorization_id["dependency_id"] = (
            "release-publish-execution-authorization:work-001:ffffffffffffffff"
        )
        bad_final = copy.deepcopy(payload)
        bad_final["final_authorization_event_id"] = (
            "release-publish-final-authorization-recorded:work-001:ffffffffffffffff"
        )

        expected_mismatch = evaluate_release_publish_execution_handoff_readiness(
            payload,
            run_id="run-001",
            work_id="work-001",
            expected_dependency_id=(
                "release-publish-execution-handoff-readiness:work-001:ffffffffffffffff"
            ),
            expected_event_id=(
                "release-publish-execution-handoff-readiness-recorded:work-001:ffffffffffffffff"
            ),
            expected_package_digest="f" * 64,
            expected_source_dependency_id=(
                "release-publish-execution-authorization:work-001:ffffffffffffffff"
            ),
            expected_source_event_id=(
                "release-publish-execution-authorization-recorded:work-001:ffffffffffffffff"
            ),
        )

        self.assertIn("unsafe-release-publish-execution-handoff-readiness-verification-schema", self._evaluate(missing).blockers)
        self.assertIn("unsafe-release-publish-execution-handoff-readiness-verification-schema", self._evaluate(extra).blockers)
        self.assertIn("release-publish-execution-authorization-receipt-verification-not-passed", self._evaluate(failed).blockers)
        self.assertIn("source-blockers-present", self._evaluate(blocked).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-verification-run-id-mismatch", self._evaluate(bad_run).blockers)
        self.assertIn("invalid-package-digest", self._evaluate(bad_digest).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-package-digest-prefix-mismatch", self._evaluate(bad_prefix).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-authorization-dependency-id-mismatch", self._evaluate(bad_authorization_id).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-final-authorization-event-id-mismatch", self._evaluate(bad_final).blockers)
        self.assertIn("expected-dependency-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-event-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-package-digest-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-source-dependency-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-source-event-id-mismatch", expected_mismatch.blockers)
        self.assertEqual(
            f"release-publish-execution-handoff-readiness:work-001:{suffix}",
            self._evaluate(payload).dependency_id,
        )

    def test_summary_parity_source_blocker_count_and_safety_fail_closed(self) -> None:
        auth_mismatch = self._payload()
        auth_mismatch["authorization_summary"]["completion_source_dependency_id"] = "changed"
        receipt_mismatch = self._payload()
        receipt_mismatch["receipt_summary"]["dependency_id"] = "changed"
        bool_count = self._payload()
        bool_count["receipt_summary"]["source_blocker_count"] = False
        float_count = self._payload()
        float_count["receipt_summary"]["source_blocker_count"] = 0.0
        non_string = self._payload()
        non_string["receipt_summary"] = {object(): "bad"}
        unsupported = self._payload()
        unsupported["receipt_summary"]["bad"] = object()
        bad_nested = self._payload()
        bad_nested["receipt_summary"]["run_id"] = RaisingToDictValue()
        non_mapping_nested = self._payload()
        non_mapping_nested["receipt_summary"]["run_id"] = NonMappingToDictValue()
        secret = self._payload()
        secret["receipt_summary"]["api_key"] = "raw"
        action = self._payload()
        action["receipt_summary"]["runner"] = "manual"
        unsafe_id = self._payload()
        unsafe_id["event_id"] = " release-publish"

        self.assertIn("release-publish-execution-handoff-readiness-authorization-summary-completion-source-dependency-id-mismatch", self._evaluate(auth_mismatch).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-receipt-summary-dependency-id-mismatch", self._evaluate(receipt_mismatch).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch", self._evaluate(bool_count).blockers)
        self.assertIn("release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch", self._evaluate(float_count).blockers)
        self.assertIn("non-string-key-release-publish-execution-handoff-readiness", self._evaluate(non_string).blockers)
        self.assertIn("unsupported-object-release-publish-execution-handoff-readiness", self._evaluate(unsupported).blockers)
        self.assertIn("unsupported-object-release-publish-execution-handoff-readiness", self._evaluate(bad_nested).blockers)
        self.assertIn("unsupported-object-release-publish-execution-handoff-readiness", self._evaluate(non_mapping_nested).blockers)
        self.assertIn("secret-like-verification-data", self._evaluate(secret).blockers)
        self.assertIn("action-intent-verification-data", self._evaluate(action).blockers)
        self.assertIn("unsafe-authorization-event-id", self._evaluate(unsafe_id).blockers)

    def test_bad_top_level_to_dict_and_unsupported_source_fail_closed(self) -> None:
        for source in (RaisingToDictSource(), NonMappingToDictSource(), object()):
            with self.subTest(source=source):
                result = self._evaluate(source)
                self.assertFalse(result.ready)
                self.assertIn(
                    "missing-release-publish-execution-authorization-receipt-verification",
                    result.blockers,
                )
        self.assertIn(
            "malformed-release-publish-execution-handoff-readiness",
            self._evaluate(RaisingToDictSource()).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-handoff-readiness",
            self._evaluate(NonMappingToDictSource()).blockers,
        )

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        payload = self._payload()
        original_digest = payload["package_digest"]
        result = self._evaluate(payload)
        before = result.to_dict()

        payload["package_digest"] = "f" * 64
        payload["receipt_summary"]["dependency_id"] = "changed"
        plain = result.to_dict()
        plain["readiness_summary"]["dependency_id"] = "changed"
        plain["authorization_summary"]["dependency_id"] = "changed"
        plain["receipt_summary"]["dependency_id"] = "changed-again"

        self.assertTrue(result.ready)
        self.assertEqual(before, result.to_dict())
        self.assertEqual(original_digest, result.package_digest)
        with self.assertRaises(TypeError):
            result.readiness_summary["dependency_id"] = "changed"
        with self.assertRaises(TypeError):
            result.authorization_summary["dependency_id"] = "changed"
        with self.assertRaises(TypeError):
            result.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.package_digest = "changed"
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(handoff_module)
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
            "social",
            "publisher",
            "Client(",
            "Service(",
            "datetime",
            "random",
        )
        forbidden_process = "sub" + "process"
        forbidden_executor = "exec" + "utor"
        self.assertNotIn(forbidden_process, source)
        self.assertNotIn(forbidden_executor, source)
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

    def _evaluate(self, source: object) -> ReleasePublishExecutionHandoffReadiness:
        return evaluate_release_publish_execution_handoff_readiness(
            source,
            run_id="run-001",
            work_id="work-001",
        )

    def _payload(self) -> dict[str, object]:
        return self._verification().to_dict()

    def _verification(self) -> object:
        helper = authorization_ledger_tests.ReleasePublishExecutionAuthorizationLedgerTests()
        ledger, authorization = helper._ledger_and_authorization(
            run_id="run-001",
            work_id="work-001",
        )
        result = record_release_publish_execution_authorization(authorization, ledger=ledger)
        self.assertEqual((), result.blockers)
        verification = verify_release_publish_execution_authorization_receipt(
            result,
            run_id="run-001",
            work_id="work-001",
        )
        self.assertTrue(verification.passed)
        return verification


if __name__ == "__main__":
    unittest.main()
