import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_handoff_readiness_receipt as receipt_module
from harness_orchestrator.release_publish_execution_handoff_readiness_ledger import (
    ReleasePublishExecutionHandoffReadinessLedgerResult,
    record_release_publish_execution_handoff_readiness,
)
from harness_orchestrator.release_publish_execution_handoff_readiness_receipt import (
    ReleasePublishExecutionHandoffReadinessReceiptVerification,
    verify_release_publish_execution_handoff_readiness_receipt,
)
from tests.test_release_publish_execution_handoff_readiness_ledger import (
    ReleasePublishExecutionHandoffReadinessLedgerTests,
)


class ToDictReceipt:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class RaisingToDictReceipt:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictReceipt:
    def to_dict(self) -> tuple[str, ...]:
        return ("bad",)


class RaisingToDictValue:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("to_dict failed")


class NonMappingToDictValue:
    def to_dict(self) -> tuple[str, ...]:
        return ("bad",)


@dataclass(frozen=True)
class ResultData:
    recorded_event_ids: tuple[str, ...]
    recorded_dependency_ids: tuple[str, ...]
    skipped_event_ids: tuple[str, ...]
    skipped_dependency_ids: tuple[str, ...]
    skipped_package_digests: tuple[str, ...]
    blockers: tuple[str, ...]
    ledger_snapshot: dict[str, object]


class ReleasePublishExecutionHandoffReadinessReceiptTests(unittest.TestCase):
    def test_happy_path_accepts_result_snapshot_dataclass_and_to_dict_sources(self) -> None:
        result, package_digest = self._ledger_result()
        data = result.to_dict()
        expected_dependency_id = (
            f"release-publish-execution-handoff-readiness:work-001:{package_digest[:16]}"
        )
        expected_event_id = (
            f"release-publish-execution-handoff-readiness-recorded:work-001:{package_digest[:16]}"
        )
        metadata = self._dependency(data)["metadata"]

        results = (
            verify_release_publish_execution_handoff_readiness_receipt(
                data,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=package_digest,
            ),
            verify_release_publish_execution_handoff_readiness_receipt(
                data["ledger_snapshot"],
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_execution_handoff_readiness_receipt(
                ResultData(**data),
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_execution_handoff_readiness_receipt(
                ToDictReceipt(data),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for verified in results:
            with self.subTest(verified=verified):
                self.assertIsInstance(
                    verified,
                    ReleasePublishExecutionHandoffReadinessReceiptVerification,
                )
                self.assertTrue(verified.passed)
                self.assertEqual((), verified.blockers)
                self.assertEqual("run-001", verified.run_id)
                self.assertEqual("work-001", verified.work_id)
                self.assertEqual(expected_dependency_id, verified.dependency_id)
                self.assertEqual(expected_event_id, verified.event_id)
                self.assertEqual(package_digest, verified.package_digest)
                self.assertEqual(package_digest[:12], verified.package_digest_prefix)
                self.assertEqual(metadata["source_dependency_id"], verified.source_dependency_id)
                self.assertEqual(metadata["source_event_id"], verified.source_event_id)
                self.assertEqual(
                    metadata["readiness_summary"],
                    verified.to_dict()["readiness_summary"],
                )
                self.assertEqual(
                    metadata["authorization_summary"],
                    verified.to_dict()["authorization_summary"],
                )
                self.assertEqual(
                    metadata["receipt_summary"],
                    verified.to_dict()["receipt_summary"],
                )
                json.dumps(verified.to_dict(), sort_keys=True)

    def test_requires_clean_result_matching_identity_single_pair_recorded_ids_and_order(self) -> None:
        blocked, _digest = self._ledger_result()
        blocked_data = blocked.to_dict()
        blocked_data["blockers"] = ("operator-review-open",)
        skipped, _digest = self._ledger_result()
        skipped_data = skipped.to_dict()
        skipped_data["skipped_dependency_ids"] = ("dependency-skipped",)
        run_mismatch, _digest = self._ledger_result()
        run_data = run_mismatch.to_dict()
        run_data["ledger_snapshot"]["run_id"] = "run-999"
        work_mismatch, _digest = self._ledger_result(work_id="work-002")
        duplicate_event, _digest = self._ledger_result()
        duplicate_data = duplicate_event.to_dict()
        event = self._event(duplicate_data)
        duplicate_data["ledger_snapshot"]["audit_events"] = (
            *duplicate_data["ledger_snapshot"]["audit_events"],
            copy.deepcopy(event),
        )
        duplicate_recorded, _digest = self._ledger_result()
        duplicate_recorded_data = duplicate_recorded.to_dict()
        duplicate_recorded_data["recorded_event_ids"] = (
            *duplicate_recorded_data["recorded_event_ids"],
            duplicate_recorded_data["recorded_event_ids"][0],
        )
        bad_order, _digest = self._ledger_result()
        bad_order_data = bad_order.to_dict()
        self._dependency(bad_order_data)["order"] = 139
        float_order, _digest = self._ledger_result()
        float_order_data = float_order.to_dict()
        self._dependency(float_order_data)["order"] = 140.0

        self.assertIn("source-blockers-present", self._verify(blocked_data).blockers)
        self.assertIn("skipped-dependency-ids-present", self._verify(skipped_data).blockers)
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-receipt-run-id-mismatch",
            self._verify(run_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-dependency-work-id-mismatch",
            self._verify(work_mismatch.to_dict()).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-event-ambiguous",
            self._verify(duplicate_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-recorded-event-id-ambiguous",
            self._verify(duplicate_recorded_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-dependency-order-mismatch",
            self._verify(bad_order_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-dependency-order-mismatch",
            self._verify(float_order_data).blockers,
        )

    def test_metadata_canonical_summaries_digest_and_expected_values_are_verified(self) -> None:
        result, digest = self._ledger_result()
        base = result.to_dict()
        dependency_id = copy.deepcopy(base)
        self._dependency(dependency_id)["dependency_id"] = (
            "release-publish-execution-handoff-readiness:work-001:ffffffffffffffff"
        )
        event_dependency = copy.deepcopy(base)
        self._event(event_dependency)["metadata"]["dependency_id"] = "changed"
        prefix = copy.deepcopy(base)
        self._dependency(prefix)["metadata"]["package_digest_prefix"] = "0" * 12
        canonical = copy.deepcopy(base)
        self._dependency(canonical)["metadata"]["canonical_payload"]["format"] = "bad"
        readiness = copy.deepcopy(base)
        self._dependency(readiness)["metadata"]["readiness_summary"]["source_dependency_id"] = "changed"
        readiness_int = copy.deepcopy(base)
        self._dependency(readiness_int)["metadata"]["readiness_summary"]["ready"] = 1
        authorization = copy.deepcopy(base)
        self._dependency(authorization)["metadata"]["authorization_summary"]["authorized"] = False
        authorization_int = copy.deepcopy(base)
        self._dependency(authorization_int)["metadata"]["authorization_summary"]["authorized"] = 1
        receipt = copy.deepcopy(base)
        self._dependency(receipt)["metadata"]["receipt_summary"]["source_blocker_count"] = False
        receipt_float = copy.deepcopy(base)
        self._dependency(receipt_float)["metadata"]["receipt_summary"]["source_blocker_count"] = 0.0

        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-dependency-id-mismatch",
            self._verify(dependency_id).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-event-dependency-id-mismatch",
            self._verify(event_dependency).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-package-digest-prefix-mismatch",
            self._verify(prefix).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-canonical-payload-format-mismatch",
            self._verify(canonical).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-readiness-summary-source-dependency-id-mismatch",
            self._verify(readiness).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-readiness-summary-ready-mismatch",
            self._verify(readiness_int).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-authorization-summary-authorized-mismatch",
            self._verify(authorization).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-authorization-summary-authorized-mismatch",
            self._verify(authorization_int).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch",
            self._verify(receipt).blockers,
        )
        self.assertIn(
            "release-publish-execution-handoff-readiness-receipt-summary-source-blocker-count-mismatch",
            self._verify(receipt_float).blockers,
        )
        self.assertIn(
            "expected-package-digest-mismatch",
            verify_release_publish_execution_handoff_readiness_receipt(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_package_digest="c" * 64,
            ).blockers,
        )
        self.assertTrue(
            verify_release_publish_execution_handoff_readiness_receipt(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_source_dependency_id=(
                    f"release-publish-execution-authorization:work-001:{digest[:16]}"
                ),
            ).passed
        )

    def test_malformed_secret_action_non_string_key_unsupported_and_bad_to_dict_fail_closed(self) -> None:
        result, _digest = self._ledger_result()
        base = result.to_dict()
        non_string = copy.deepcopy(base)
        self._dependency(non_string)["metadata"]["receipt_summary"] = {object(): "bad"}
        unsupported = copy.deepcopy(base)
        self._dependency(unsupported)["metadata"]["receipt_summary"]["bad"] = object()
        bad_value_to_dict = copy.deepcopy(base)
        self._dependency(bad_value_to_dict)["metadata"]["receipt_summary"]["run_id"] = RaisingToDictValue()
        non_mapping_value = copy.deepcopy(base)
        self._dependency(non_mapping_value)["metadata"]["receipt_summary"]["run_id"] = NonMappingToDictValue()
        secret = copy.deepcopy(base)
        self._dependency(secret)["metadata"]["receipt_summary"]["api_key"] = "raw"
        action = copy.deepcopy(base)
        self._dependency(action)["metadata"]["receipt_summary"]["runner"] = "manual"

        self.assertIn(
            "missing-release-publish-execution-handoff-readiness-receipt",
            verify_release_publish_execution_handoff_readiness_receipt(
                "bad",
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-handoff-readiness-receipt",
            verify_release_publish_execution_handoff_readiness_receipt(
                RaisingToDictReceipt(),
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-handoff-readiness-receipt",
            verify_release_publish_execution_handoff_readiness_receipt(
                NonMappingToDictReceipt(),
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "non-string-key-release-publish-execution-handoff-readiness-receipt",
            self._verify(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-handoff-readiness-receipt",
            self._verify(unsupported).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-handoff-readiness-receipt",
            self._verify(bad_value_to_dict).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-handoff-readiness-receipt",
            self._verify(non_mapping_value).blockers,
        )
        self.assertIn("secret-like-receipt-data", self._verify(secret).blockers)
        self.assertIn("action-intent-receipt-data", self._verify(action).blockers)

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        result, digest = self._ledger_result()
        data = result.to_dict()
        verified = self._verify(data)
        before = verified.to_dict()

        self._dependency(data)["metadata"]["package_digest"] = "c" * 64
        plain = verified.to_dict()
        plain["readiness_summary"]["dependency_id"] = "changed"

        self.assertEqual(before, verified.to_dict())
        self.assertEqual(digest, verified.package_digest)
        with self.assertRaises(TypeError):
            verified.readiness_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            verified.package_digest = "changed"

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(receipt_module)
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
                "from dataclasses import asdict, dataclass, is_dataclass",
                "import re",
                "from types import MappingProxyType",
                "from typing import Any, Mapping",
            ),
            imports,
        )

    def _verify(self, receipt: object) -> ReleasePublishExecutionHandoffReadinessReceiptVerification:
        return verify_release_publish_execution_handoff_readiness_receipt(
            receipt,
            run_id="run-001",
            work_id="work-001",
        )

    def _ledger_result(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[ReleasePublishExecutionHandoffReadinessLedgerResult, str]:
        helper = ReleasePublishExecutionHandoffReadinessLedgerTests()
        ledger, readiness = helper._ledger_and_handoff_readiness(
            run_id=run_id,
            work_id=work_id,
        )
        result = record_release_publish_execution_handoff_readiness(readiness, ledger=ledger)
        self.assertEqual((), result.blockers)
        return result, readiness.package_digest

    def _dependency(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            record
            for record in receipt["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] == "release-publish-execution-handoff-readiness"
        )

    def _event(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            event
            for event in receipt["ledger_snapshot"]["audit_events"]
            if event["event_type"] == "release-publish-execution-handoff-readiness-ledger-record"
        )


if __name__ == "__main__":
    unittest.main()
