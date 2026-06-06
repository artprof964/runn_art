import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_execution_readiness_receipt as receipt_module
from harness_orchestrator.release_publish_execution_readiness_ledger import (
    ReleasePublishExecutionReadinessLedgerResult,
    record_release_publish_execution_readiness,
)
from harness_orchestrator.release_publish_execution_readiness_receipt import (
    ReleasePublishExecutionReadinessReceiptVerification,
    verify_release_publish_execution_readiness_receipt,
)
from tests.test_release_publish_execution_readiness_ledger import (
    ReleasePublishExecutionReadinessLedgerTests,
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


class ReleasePublishExecutionReadinessReceiptTests(unittest.TestCase):
    def test_happy_path_accepts_result_snapshot_dataclass_and_to_dict_sources(self) -> None:
        result, package_digest = self._ledger_result()
        data = result.to_dict()
        metadata = self._dependency(data)["metadata"]
        expected_dependency_id = (
            f"release-publish-execution-readiness:work-001:{package_digest[:16]}"
        )
        expected_event_id = (
            f"release-publish-execution-readiness-recorded:work-001:{package_digest[:16]}"
        )

        results = (
            verify_release_publish_execution_readiness_receipt(
                data,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=package_digest,
            ),
            verify_release_publish_execution_readiness_receipt(
                data["ledger_snapshot"],
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_execution_readiness_receipt(
                ResultData(**data),
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_execution_readiness_receipt(
                ToDictReceipt(data),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for verified in results:
            with self.subTest(verified=verified):
                self.assertIsInstance(
                    verified,
                    ReleasePublishExecutionReadinessReceiptVerification,
                )
                self.assertTrue(verified.passed)
                self.assertEqual((), verified.blockers)
                self.assertEqual("run-001", verified.run_id)
                self.assertEqual("work-001", verified.work_id)
                self.assertEqual(expected_dependency_id, verified.dependency_id)
                self.assertEqual(expected_event_id, verified.event_id)
                self.assertEqual(package_digest, verified.package_digest)
                self.assertEqual(package_digest[:12], verified.package_digest_prefix)
                self.assertEqual(
                    metadata["completion_source_dependency_id"],
                    verified.completion_source_dependency_id,
                )
                self.assertEqual(
                    metadata["acceptance_source_event_id"],
                    verified.receipt_summary["acceptance_source_event_id"],
                )
                self.assertEqual(
                    metadata["final_authorization_dependency_id"],
                    verified.final_authorization_dependency_id,
                )
                self.assertEqual(package_digest[:12], verified.receipt_summary["package_digest_prefix"])
                json.dumps(verified.to_dict(), sort_keys=True)

    def test_requires_no_source_blockers_skips_matching_run_work_and_single_pair(self) -> None:
        blocked, _digest = self._ledger_result()
        blocked_data = blocked.to_dict()
        blocked_data["blockers"] = ("operator-review-open",)
        skipped, _digest = self._ledger_result()
        skipped_data = skipped.to_dict()
        skipped_data["skipped_event_ids"] = ("event-skipped",)
        run_mismatch, _digest = self._ledger_result()
        run_data = run_mismatch.to_dict()
        run_data["ledger_snapshot"]["run_id"] = "run-999"
        work_mismatch, _digest = self._ledger_result(work_id="work-002")
        missing_dependency, _digest = self._ledger_result()
        missing_data = missing_dependency.to_dict()
        missing_data["ledger_snapshot"]["dependencies"] = tuple(
            record
            for record in missing_data["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] != "release-publish-execution-readiness"
        )
        duplicate_event, _digest = self._ledger_result()
        duplicate_data = duplicate_event.to_dict()
        event = self._event(duplicate_data)
        duplicate_data["ledger_snapshot"]["audit_events"] = (
            *duplicate_data["ledger_snapshot"]["audit_events"],
            copy.deepcopy(event),
        )
        duplicate_recorded, _digest = self._ledger_result()
        duplicate_recorded_data = duplicate_recorded.to_dict()
        duplicate_recorded_data["recorded_dependency_ids"] = (
            *duplicate_recorded_data["recorded_dependency_ids"],
            duplicate_recorded_data["recorded_dependency_ids"][0],
        )

        self.assertIn("source-blockers-present", self._verify(blocked_data).blockers)
        self.assertIn("skipped-event-ids-present", self._verify(skipped_data).blockers)
        self.assertIn(
            "release-publish-execution-readiness-receipt-receipt-run-id-mismatch",
            self._verify(run_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-receipt-dependency-work-id-mismatch",
            self._verify(work_mismatch.to_dict()).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-dependency-missing",
            self._verify(missing_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-event-ambiguous",
            self._verify(duplicate_data).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-recorded-dependency-id-ambiguous",
            self._verify(duplicate_recorded_data).blockers,
        )

    def test_pair_metadata_ids_digest_canonical_and_expected_values_are_verified(self) -> None:
        result, digest = self._ledger_result()
        base = result.to_dict()
        dependency_id = copy.deepcopy(base)
        self._dependency(dependency_id)["dependency_id"] = (
            "release-publish-execution-readiness:work-001:ffffffffffffffff"
        )
        event_dependency = copy.deepcopy(base)
        self._event(event_dependency)["metadata"]["dependency_id"] = "changed"
        prefix = copy.deepcopy(base)
        self._dependency(prefix)["metadata"]["package_digest_prefix"] = "0" * 12
        digest_payload = copy.deepcopy(base)
        self._dependency(digest_payload)["metadata"]["package_digest"] = "A" * 64
        canonical = copy.deepcopy(base)
        self._dependency(canonical)["metadata"]["canonical_payload"]["format"] = "bad"
        summary = copy.deepcopy(base)
        self._dependency(summary)["metadata"]["canonical_payload"]["execution_readiness"][
            "ready"
        ] = False

        self.assertIn(
            "release-publish-execution-readiness-receipt-dependency-id-mismatch",
            self._verify(dependency_id).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-receipt-event-dependency-id-mismatch",
            self._verify(event_dependency).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-receipt-package-digest-prefix-mismatch",
            self._verify(prefix).blockers,
        )
        self.assertIn("invalid-package-digest", self._verify(digest_payload).blockers)
        self.assertIn(
            "release-publish-execution-readiness-receipt-canonical-payload-format-mismatch",
            self._verify(canonical).blockers,
        )
        self.assertIn(
            "release-publish-execution-readiness-receipt-canonical-payload-execution-readiness-mismatch",
            self._verify(summary).blockers,
        )
        self.assertIn(
            "expected-package-digest-mismatch",
            verify_release_publish_execution_readiness_receipt(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_package_digest="c" * 64,
            ).blockers,
        )
        self.assertIn(
            "expected-source-dependency-id-mismatch",
            verify_release_publish_execution_readiness_receipt(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_source_dependency_id="changed",
            ).blockers,
        )
        self.assertTrue(
            verify_release_publish_execution_readiness_receipt(
                base,
                run_id="run-001",
                work_id="work-001",
                expected_final_authorization_dependency_id=(
                    f"release-publish-final-authorization:work-001:{digest[:16]}"
                ),
            ).passed
        )

    def test_status_actor_metadata_schema_source_ids_and_source_blocker_count_fail_closed(self) -> None:
        result, _digest = self._ledger_result()
        base = result.to_dict()
        cases = []
        dep_status = copy.deepcopy(base)
        self._dependency(dep_status)["status"] = "pending"
        cases.append((dep_status, "release-publish-execution-readiness-dependency-status-not-ready"))
        not_required = copy.deepcopy(base)
        self._dependency(not_required)["required"] = False
        cases.append((not_required, "release-publish-execution-readiness-dependency-not-required"))
        event_actor = copy.deepcopy(base)
        self._event(event_actor)["actor"] = "operator"
        cases.append((event_actor, "release-publish-execution-readiness-event-actor-mismatch"))
        metadata_extra = copy.deepcopy(base)
        self._dependency(metadata_extra)["metadata"]["extra"] = "bad"
        cases.append((metadata_extra, "unsafe-dependency-metadata-schema"))
        source_id = copy.deepcopy(base)
        self._dependency(source_id)["metadata"]["source_dependency_id"] = "changed"
        cases.append((source_id, "release-publish-execution-readiness-receipt-event-source-dependency-id-mismatch"))
        readiness_dependency_id = copy.deepcopy(base)
        self._dependency(readiness_dependency_id)["metadata"]["readiness_summary"]["dependency_id"] = "changed"
        cases.append((readiness_dependency_id, "release-publish-execution-readiness-receipt-readiness-summary-dependency-id-mismatch"))
        receipt_event_id = copy.deepcopy(base)
        self._dependency(receipt_event_id)["metadata"]["receipt_summary"]["event_id"] = "changed"
        cases.append((receipt_event_id, "release-publish-execution-readiness-receipt-receipt-summary-event-id-mismatch"))
        readiness_source_id = copy.deepcopy(base)
        self._dependency(readiness_source_id)["metadata"]["readiness_summary"]["source_dependency_id"] = "changed"
        cases.append((readiness_source_id, "release-publish-execution-readiness-receipt-readiness-summary-source-dependency-id-mismatch"))
        receipt_source_event_id = copy.deepcopy(base)
        self._dependency(receipt_source_event_id)["metadata"]["receipt_summary"]["source_event_id"] = "changed"
        cases.append((receipt_source_event_id, "release-publish-execution-readiness-receipt-receipt-summary-source-event-id-mismatch"))
        count_bool = copy.deepcopy(base)
        self._dependency(count_bool)["metadata"]["receipt_summary"]["source_blocker_count"] = False
        cases.append((count_bool, "release-publish-execution-readiness-receipt-summary-source-blocker-count-mismatch"))
        count_float = copy.deepcopy(base)
        self._dependency(count_float)["metadata"]["receipt_summary"]["source_blocker_count"] = 0.0
        cases.append((count_float, "release-publish-execution-readiness-receipt-summary-source-blocker-count-mismatch"))

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                self.assertIn(blocker, self._verify(payload).blockers)

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
            "missing-release-publish-execution-readiness-receipt",
            verify_release_publish_execution_readiness_receipt(
                "bad",
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-readiness-receipt",
            verify_release_publish_execution_readiness_receipt(
                RaisingToDictReceipt(),
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "malformed-release-publish-execution-readiness-receipt",
            verify_release_publish_execution_readiness_receipt(
                NonMappingToDictReceipt(),
                run_id="run-001",
                work_id="work-001",
            ).blockers,
        )
        self.assertIn(
            "non-string-key-release-publish-execution-readiness-receipt",
            self._verify(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-readiness-receipt",
            self._verify(unsupported).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-readiness-receipt",
            self._verify(bad_value_to_dict).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-execution-readiness-receipt",
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
        plain["receipt_summary"]["dependency_id"] = "changed"

        self.assertEqual(before, verified.to_dict())
        self.assertEqual(digest, verified.package_digest)
        with self.assertRaises(TypeError):
            verified.receipt_summary["dependency_id"] = "changed"
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
                "from dataclasses import asdict, dataclass, is_dataclass",
                "import re",
                "from types import MappingProxyType",
                "from typing import Any, Mapping",
            ),
            imports,
        )

    def _verify(self, receipt: object) -> ReleasePublishExecutionReadinessReceiptVerification:
        return verify_release_publish_execution_readiness_receipt(
            receipt,
            run_id="run-001",
            work_id="work-001",
        )

    def _ledger_result(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[ReleasePublishExecutionReadinessLedgerResult, str]:
        helper = ReleasePublishExecutionReadinessLedgerTests()
        ledger, readiness = helper._ledger_and_execution_readiness(
            run_id=run_id,
            work_id=work_id,
        )
        result = record_release_publish_execution_readiness(readiness, ledger=ledger)
        self.assertEqual((), result.blockers)
        return result, readiness.package_digest

    def _dependency(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            record
            for record in receipt["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] == "release-publish-execution-readiness"
        )

    def _event(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            event
            for event in receipt["ledger_snapshot"]["audit_events"]
            if event["event_type"] == "release-publish-execution-readiness-ledger-record"
        )


if __name__ == "__main__":
    unittest.main()
