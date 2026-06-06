import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_readiness_acceptance as acceptance_module
from harness_orchestrator.release_publish_execution_readiness_acceptance import (
    ReleasePublishExecutionReadinessAcceptance,
    evaluate_release_publish_execution_readiness_acceptance,
)
from harness_orchestrator.release_publish_execution_readiness_ledger import (
    ReleasePublishExecutionReadinessLedgerResult,
    record_release_publish_execution_readiness,
)
from harness_orchestrator.release_publish_execution_readiness_receipt import (
    verify_release_publish_execution_readiness_receipt,
)
from tests.test_release_publish_execution_readiness_ledger import (
    ReleasePublishExecutionReadinessLedgerTests,
)


class ToDictVerification:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class RaisingToDictVerification:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictVerification:
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
    receipt_summary: dict[str, object]


class ReleasePublishExecutionReadinessAcceptanceTests(unittest.TestCase):
    def test_happy_path_accepts_mapping_dataclass_and_to_dict_verification(self) -> None:
        verification = self._verification()
        data = verification.to_dict()
        dataclass_verification = VerificationData(**copy.deepcopy(data))

        results = (
            evaluate_release_publish_execution_readiness_acceptance(
                verification,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=data["dependency_id"],
                expected_event_id=data["event_id"],
                expected_package_digest=data["package_digest"],
                expected_source_dependency_id=data["source_dependency_id"],
                expected_source_event_id=data["source_event_id"],
                expected_final_authorization_dependency_id=(
                    data["final_authorization_dependency_id"]
                ),
                expected_final_authorization_event_id=data["final_authorization_event_id"],
            ),
            evaluate_release_publish_execution_readiness_acceptance(
                data,
                run_id="run-001",
                work_id="work-001",
            ),
            evaluate_release_publish_execution_readiness_acceptance(
                dataclass_verification,
                run_id="run-001",
                work_id="work-001",
            ),
            evaluate_release_publish_execution_readiness_acceptance(
                ToDictVerification(data),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for result in results:
            with self.subTest(result=result):
                self.assertIsInstance(result, ReleasePublishExecutionReadinessAcceptance)
                self.assertTrue(result.accepted)
                self.assertEqual("accepted", result.status)
                self.assertEqual((), result.blockers)
                self.assertEqual("run-001", result.run_id)
                self.assertEqual("work-001", result.work_id)
                self.assertEqual(data["dependency_id"], result.dependency_id)
                self.assertEqual(data["event_id"], result.event_id)
                self.assertEqual(data["package_digest"], result.package_digest)
                self.assertEqual(data["package_digest_prefix"], result.package_digest_prefix)
                self.assertEqual(data["source_dependency_id"], result.source_dependency_id)
                self.assertEqual(
                    data["completion_source_event_id"],
                    result.acceptance_summary["completion_source_event_id"],
                )
                self.assertEqual(
                    data["final_authorization_dependency_id"],
                    result.receipt_summary["final_authorization_dependency_id"],
                )
                self.assertEqual(0, result.receipt_summary["source_blocker_count"])
                json.dumps(result.to_dict(), sort_keys=True)

    def test_failed_blocked_mismatch_schema_ids_digest_and_expected_values_fail_closed(self) -> None:
        base = self._verification().to_dict()
        cases = (
            ("passed", False, "release-publish-execution-readiness-verification-not-passed"),
            ("blockers", ("operator-review-open",), "source-blockers-present"),
            ("run_id", "run-999", "release-publish-execution-readiness-acceptance-verification-run-id-mismatch"),
            ("work_id", "work-999", "release-publish-execution-readiness-acceptance-verification-work-id-mismatch"),
            ("dependency_id", " release-publish-execution-readiness:work-001:bad", "unsafe-dependency-id"),
            ("event_id", "", "missing-event-id"),
            ("package_digest", "A" * 64, "invalid-package-digest"),
            ("package_digest_prefix", "0" * 12, "release-publish-execution-readiness-acceptance-package-digest-prefix-mismatch"),
            ("source_dependency_id", " source", "unsafe-source-dependency-id"),
            ("final_authorization_event_id", "", "missing-final-authorization-event-id"),
        )

        for key, value, blocker in cases:
            payload = copy.deepcopy(base)
            payload[key] = value
            with self.subTest(key=key):
                self.assertIn(blocker, self._evaluate(payload).blockers)

        missing = copy.deepcopy(base)
        missing.pop("receipt_summary")
        extra = copy.deepcopy(base)
        extra["extra"] = "bad"
        self.assertIn(
            "unsafe-release-publish-execution-readiness-verification-schema",
            self._evaluate(missing).blockers,
        )
        self.assertIn(
            "unsafe-release-publish-execution-readiness-verification-schema",
            self._evaluate(extra).blockers,
        )

        expected_mismatch = evaluate_release_publish_execution_readiness_acceptance(
            base,
            run_id="run-001",
            work_id="work-001",
            expected_dependency_id="release-publish-execution-readiness:work-001:ffffffffffffffff",
            expected_event_id=(
                "release-publish-execution-readiness-recorded:work-001:ffffffffffffffff"
            ),
            expected_package_digest="f" * 64,
            expected_source_dependency_id="changed",
            expected_source_event_id="changed",
            expected_final_authorization_dependency_id="changed",
            expected_final_authorization_event_id="changed",
        )
        self.assertIn("expected-dependency-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-event-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-package-digest-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-source-dependency-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-source-event-id-mismatch", expected_mismatch.blockers)
        self.assertIn(
            "expected-final-authorization-dependency-id-mismatch",
            expected_mismatch.blockers,
        )
        self.assertIn(
            "expected-final-authorization-event-id-mismatch",
            expected_mismatch.blockers,
        )

    def test_deterministic_id_source_chain_and_receipt_summary_parity_are_verified(self) -> None:
        base = self._verification().to_dict()
        digest = base["package_digest"]
        self.assertEqual(
            f"release-publish-execution-readiness:work-001:{digest[:16]}",
            base["dependency_id"],
        )
        self.assertEqual(
            f"release-publish-execution-readiness-recorded:work-001:{digest[:16]}",
            base["event_id"],
        )

        cases = []
        dependency = copy.deepcopy(base)
        dependency["dependency_id"] = "release-publish-execution-readiness:work-001:ffffffffffffffff"
        cases.append((dependency, "release-publish-execution-readiness-acceptance-dependency-id-mismatch"))
        event = copy.deepcopy(base)
        event["event_id"] = "release-publish-execution-readiness-recorded:work-001:ffffffffffffffff"
        cases.append((event, "release-publish-execution-readiness-acceptance-event-id-mismatch"))
        for key in (
            "source_dependency_id",
            "source_event_id",
            "completion_source_dependency_id",
            "completion_source_event_id",
            "acceptance_source_dependency_id",
            "acceptance_source_event_id",
            "final_authorization_dependency_id",
            "final_authorization_event_id",
        ):
            payload = copy.deepcopy(base)
            payload["receipt_summary"][key] = "changed"
            cases.append(
                (
                    payload,
                    f"release-publish-execution-readiness-acceptance-receipt-summary-{key.replace('_', '-')}-mismatch",
                )
            )
        blocked_summary = copy.deepcopy(base)
        blocked_summary["receipt_summary"]["source_blocker_count"] = 1
        cases.append(
            (
                blocked_summary,
                "release-publish-execution-readiness-acceptance-receipt-summary-source-blocker-count-mismatch",
            )
        )

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                self.assertIn(blocker, self._evaluate(payload).blockers)

    def test_bool_and_float_source_blocker_count_reject(self) -> None:
        for value in (False, 0.0):
            payload = self._verification().to_dict()
            payload["receipt_summary"]["source_blocker_count"] = value
            with self.subTest(value=value):
                self.assertIn(
                    "release-publish-execution-readiness-acceptance-receipt-summary-source-blocker-count-mismatch",
                    self._evaluate(payload).blockers,
                )

    def test_secret_action_non_string_key_unsupported_object_and_bad_to_dict_fail_closed(self) -> None:
        base = self._verification().to_dict()
        non_string = copy.deepcopy(base)
        non_string["receipt_summary"] = {object(): "bad"}
        unsupported = copy.deepcopy(base)
        unsupported["receipt_summary"]["bad"] = object()
        bad_value_to_dict = copy.deepcopy(base)
        bad_value_to_dict["receipt_summary"]["run_id"] = RaisingToDictValue()
        non_mapping_value = copy.deepcopy(base)
        non_mapping_value["receipt_summary"]["run_id"] = NonMappingToDictValue()
        secret = copy.deepcopy(base)
        secret["receipt_summary"]["api_key"] = "raw"
        action = copy.deepcopy(base)
        action["receipt_summary"]["runner"] = "manual"

        self.assertIn(
            "missing-release-publish-execution-readiness-verification",
            evaluate_release_publish_execution_readiness_acceptance(
                object(),
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-readiness-acceptance",
            self._evaluate(RaisingToDictVerification()).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-readiness-acceptance",
            self._evaluate(NonMappingToDictVerification()).blockers,
        )
        self.assertIn(
            "non-string-key-release-publish-execution-readiness-acceptance",
            self._evaluate(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-readiness-acceptance",
            self._evaluate(unsupported).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-readiness-acceptance",
            self._evaluate(bad_value_to_dict).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-readiness-acceptance",
            self._evaluate(non_mapping_value).blockers,
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
        plain["acceptance_summary"]["dependency_id"] = "changed"
        plain["receipt_summary"]["dependency_id"] = "changed-again"

        self.assertTrue(result.accepted)
        self.assertEqual(before, result.to_dict())
        with self.assertRaises(TypeError):
            result.acceptance_summary["dependency_id"] = "changed"
        with self.assertRaises(TypeError):
            result.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.package_digest = "changed"
        json.dumps(result.to_dict(), sort_keys=True)

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(acceptance_module)
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
            "executor",
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

    def _evaluate(self, payload: object) -> ReleasePublishExecutionReadinessAcceptance:
        return evaluate_release_publish_execution_readiness_acceptance(
            payload,
            run_id="run-001",
            work_id="work-001",
        )

    def _verification(self) -> object:
        result, _digest = self._ledger_result()
        verification = verify_release_publish_execution_readiness_receipt(
            result,
            run_id="run-001",
            work_id="work-001",
        )
        self.assertTrue(verification.passed)
        return verification

    def _ledger_result(self) -> tuple[ReleasePublishExecutionReadinessLedgerResult, str]:
        helper = ReleasePublishExecutionReadinessLedgerTests()
        ledger, readiness = helper._ledger_and_execution_readiness()
        result = record_release_publish_execution_readiness(readiness, ledger=ledger)
        self.assertEqual((), result.blockers)
        return result, readiness.package_digest


if __name__ == "__main__":
    unittest.main()
