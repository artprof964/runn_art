from dataclasses import FrozenInstanceError, dataclass
import copy
import inspect
import json
import unittest

import harness_orchestrator.release_publish_final_authorization_receipt as receipt_module
from harness_orchestrator.release_publish_final_authorization_receipt import (
    ReleasePublishFinalAuthorizationReceiptVerification,
    evaluate_release_publish_final_authorization_receipt,
    verify_release_publish_final_authorization_receipt,
)
from harness_orchestrator.release_publish_final_authorization_ledger import (
    record_release_publish_final_authorization,
)
from tests.test_release_publish_final_authorization_ledger import (
    ReleasePublishFinalAuthorizationLedgerTests,
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
class SnapshotData:
    run_id: str
    gate_decisions: tuple[object, ...]
    dependencies: tuple[object, ...]
    audit_events: tuple[object, ...]
    tasks: tuple[object, ...]
    metadata: dict[str, object]


class ReleasePublishFinalAuthorizationReceiptTests(unittest.TestCase):
    def test_happy_path_real_chain_result_mapping_snapshot_dataclass_and_to_dict(self) -> None:
        ledger, authorization, result = self._recorded_result()
        result_dict = result.to_dict()
        snapshot = result.ledger_snapshot
        self.assertIsNotNone(snapshot)
        snapshot_data = SnapshotData(
            run_id=result_dict["ledger_snapshot"]["run_id"],
            gate_decisions=result_dict["ledger_snapshot"]["gate_decisions"],
            dependencies=result_dict["ledger_snapshot"]["dependencies"],
            audit_events=result_dict["ledger_snapshot"]["audit_events"],
            tasks=result_dict["ledger_snapshot"]["tasks"],
            metadata=result_dict["ledger_snapshot"]["metadata"],
        )

        verifications = (
            verify_release_publish_final_authorization_receipt(
                result,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=result.recorded_dependency_ids[0],
                expected_event_id=result.recorded_event_ids[0],
                expected_package_digest=authorization.package_digest,
                expected_source_dependency_id=authorization.dependency_id,
                expected_source_event_id=authorization.event_id,
                expected_acceptance_source_dependency_id=(
                    authorization.acceptance_source_dependency_id
                ),
                expected_acceptance_source_event_id=authorization.acceptance_source_event_id,
            ),
            verify_release_publish_final_authorization_receipt(
                result_dict,
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_final_authorization_receipt(
                result_dict["ledger_snapshot"],
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_final_authorization_receipt(
                snapshot_data,
                run_id="run-001",
                work_id="work-001",
            ),
            evaluate_release_publish_final_authorization_receipt(
                ToDictReceipt(result_dict),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for verification in verifications:
            with self.subTest(verification=verification):
                self.assertIsInstance(
                    verification,
                    ReleasePublishFinalAuthorizationReceiptVerification,
                )
                self.assertTrue(verification.passed)
                self.assertEqual((), verification.blockers)
                self.assertEqual(result.recorded_dependency_ids[0], verification.dependency_id)
                self.assertEqual(result.recorded_event_ids[0], verification.event_id)
                self.assertEqual(authorization.package_digest, verification.package_digest)
                json.dumps(verification.to_dict(), sort_keys=True)
        self.assertEqual(ledger.run_id, verifications[0].run_id)

    def test_missing_duplicate_tampered_and_expected_mismatches_fail_closed(self) -> None:
        _ledger, authorization, result = self._recorded_result()
        missing = result.to_dict()
        missing["ledger_snapshot"]["dependencies"] = tuple(
            record
            for record in missing["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] != "release-publish-final-authorization"
        )
        duplicate = result.to_dict()
        final_dependency = next(
            record
            for record in duplicate["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] == "release-publish-final-authorization"
        )
        duplicate["ledger_snapshot"]["dependencies"] = (
            *duplicate["ledger_snapshot"]["dependencies"],
            copy.deepcopy(final_dependency),
        )
        bad_status = result.to_dict()
        self._final_dependency(bad_status)["status"] = "ready"
        bad_actor = result.to_dict()
        self._final_event(bad_actor)["actor"] = "operator"
        bad_metadata = result.to_dict()
        self._final_event(bad_metadata)["metadata"]["dependency_id"] = "wrong"
        expected_mismatch = verify_release_publish_final_authorization_receipt(
            result,
            run_id="run-001",
            work_id="work-001",
            expected_dependency_id="release-publish-final-authorization:work-001:ffffffffffffffff",
            expected_event_id="release-publish-final-authorization-recorded:work-001:ffffffffffffffff",
            expected_package_digest="f" * 64,
            expected_source_dependency_id=(
                "release-publish-handoff-completion:work-001:ffffffffffffffff"
            ),
            expected_source_event_id=(
                "release-publish-handoff-completion-recorded:work-001:ffffffffffffffff"
            ),
            expected_acceptance_source_dependency_id=(
                "release-publish-handoff-package:work-001:ffffffffffffffff"
            ),
            expected_acceptance_source_event_id=(
                "release-publish-handoff-package-recorded:work-001:ffffffffffffffff"
            ),
        )

        self.assertIn(
            "release-publish-final-authorization-dependency-missing",
            self._verify(missing).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-dependency-ambiguous",
            self._verify(duplicate).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-dependency-status-not-authorized",
            self._verify(bad_status).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-event-actor-mismatch",
            self._verify(bad_actor).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-receipt-event-dependency-id-mismatch",
            self._verify(bad_metadata).blockers,
        )
        self.assertIn("expected-dependency-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-event-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-package-digest-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-source-dependency-id-mismatch", expected_mismatch.blockers)
        self.assertIn("expected-source-event-id-mismatch", expected_mismatch.blockers)
        self.assertIn(
            "expected-acceptance-source-dependency-id-mismatch",
            expected_mismatch.blockers,
        )
        self.assertIn(
            "expected-acceptance-source-event-id-mismatch",
            expected_mismatch.blockers,
        )
        self.assertEqual(authorization.package_digest, result.to_dict()["ledger_snapshot"]["dependencies"][-1]["metadata"]["package_digest"])

    def test_metadata_canonical_summary_digest_and_source_blocker_count_fail_closed(self) -> None:
        cases = []
        bad_digest = self._result_dict()
        self._metadata(bad_digest)["package_digest"] = "A" * 64
        cases.append((bad_digest, "invalid-package-digest"))
        bad_prefix = self._result_dict()
        self._metadata(bad_prefix)["package_digest_prefix"] = "f" * 12
        cases.append(
            (
                bad_prefix,
                "release-publish-final-authorization-receipt-package-digest-prefix-mismatch",
            )
        )
        bad_canonical_format = self._result_dict()
        self._metadata(bad_canonical_format)["canonical_payload"]["format"] = "bad"
        cases.append(
            (
                bad_canonical_format,
                "release-publish-final-authorization-receipt-canonical-payload-format-mismatch",
            )
        )
        bad_canonical_value = self._result_dict()
        self._metadata(bad_canonical_value)["canonical_payload"]["final_authorization"] = {}
        cases.append(
            (
                bad_canonical_value,
                "release-publish-final-authorization-receipt-canonical-payload-final-authorization-mismatch",
            )
        )
        bad_summary = self._result_dict()
        self._metadata(bad_summary)["authorization_summary"]["authorized"] = False
        cases.append(
            (
                bad_summary,
                "release-publish-final-authorization-receipt-authorization-summary-authorized-mismatch",
            )
        )
        bool_count = self._result_dict()
        self._metadata(bool_count)["receipt_summary"]["source_blocker_count"] = False
        cases.append(
            (
                bool_count,
                "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch",
            )
        )
        float_count = self._result_dict()
        self._metadata(float_count)["receipt_summary"]["source_blocker_count"] = 0.0
        cases.append(
            (
                float_count,
                "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch",
            )
        )

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                self.assertIn(blocker, self._verify(payload).blockers)

    def test_source_blockers_skipped_ids_bad_to_dict_secret_action_and_unsupported_fail_closed(self) -> None:
        source_blocked = self._result_dict()
        source_blocked["blockers"] = ("operator-review-open",)
        skipped = self._result_dict()
        skipped["skipped_dependency_ids"] = ("release-publish-final-authorization:work-001:abc",)
        skipped["skipped_event_ids"] = (
            "release-publish-final-authorization-recorded:work-001:abc",
        )
        skipped["skipped_package_digests"] = ("a" * 64,)
        bad_nested = self._result_dict()
        self._metadata(bad_nested)["receipt_summary"]["run_id"] = RaisingToDictValue()
        non_mapping_nested = self._result_dict()
        self._metadata(non_mapping_nested)["receipt_summary"]["run_id"] = NonMappingToDictValue()
        non_string = self._result_dict()
        self._metadata(non_string)["receipt_summary"] = {object(): "bad"}
        unsupported = self._result_dict()
        self._metadata(unsupported)["receipt_summary"]["bad"] = object()
        secret = self._result_dict()
        self._metadata(secret)["receipt_summary"]["api_key"] = "raw"
        action = self._result_dict()
        self._metadata(action)["receipt_summary"]["runner"] = "manual"

        self.assertIn("source-blockers-present", self._verify(source_blocked).blockers)
        skipped_result = self._verify(skipped)
        self.assertIn("skipped-final-authorization-dependency-ids-present", skipped_result.blockers)
        self.assertIn("skipped-final-authorization-event-ids-present", skipped_result.blockers)
        self.assertIn("skipped-final-authorization-package-digests-present", skipped_result.blockers)
        for source in (RaisingToDictReceipt(), NonMappingToDictReceipt()):
            with self.subTest(source=source):
                result = self._verify(source)
                self.assertIn("malformed-release-publish-final-authorization-receipt", result.blockers)
                self.assertIn("missing-release-publish-final-authorization-receipt", result.blockers)
        for payload in (bad_nested, non_mapping_nested):
            self.assertIn(
                "unsupported-object-release-publish-final-authorization-receipt",
                self._verify(payload).blockers,
            )
        self.assertIn(
            "non-string-key-release-publish-final-authorization-receipt",
            self._verify(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-final-authorization-receipt",
            self._verify(unsupported).blockers,
        )
        self.assertIn("secret-like-receipt-data", self._verify(secret).blockers)
        self.assertIn("action-intent-receipt-data", self._verify(action).blockers)

    def test_result_is_frozen_json_safe_and_caller_mutation_safe(self) -> None:
        payload = self._result_dict()
        original_digest = self._metadata(payload)["package_digest"]
        verification = self._verify(payload)
        before = verification.to_dict()

        self._metadata(payload)["package_digest"] = "f" * 64
        plain = verification.to_dict()
        plain["receipt_summary"]["dependency_id"] = "changed"

        self.assertTrue(verification.passed)
        self.assertEqual(before, verification.to_dict())
        self.assertEqual(original_digest, verification.package_digest)
        with self.assertRaises(TypeError):
            verification.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            verification.package_digest = "changed"
        json.dumps(verification.to_dict(), sort_keys=True)

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

    def _verify(self, source: object) -> ReleasePublishFinalAuthorizationReceiptVerification:
        return verify_release_publish_final_authorization_receipt(
            source,
            run_id="run-001",
            work_id="work-001",
        )

    def _result_dict(self) -> dict[str, object]:
        return self._recorded_result()[2].to_dict()

    def _recorded_result(self) -> tuple[object, object, object]:
        helper = ReleasePublishFinalAuthorizationLedgerTests()
        ledger, authorization = helper._ledger_and_final_authorization()
        result = record_release_publish_final_authorization(authorization, ledger=ledger)
        self.assertEqual((), result.blockers)
        return ledger, authorization, result

    def _final_dependency(self, payload: dict[str, object]) -> dict[str, object]:
        return next(
            record
            for record in payload["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] == "release-publish-final-authorization"
        )

    def _final_event(self, payload: dict[str, object]) -> dict[str, object]:
        return next(
            record
            for record in payload["ledger_snapshot"]["audit_events"]
            if record["event_type"] == "release-publish-final-authorization-ledger-record"
        )

    def _metadata(self, payload: dict[str, object]) -> dict[str, object]:
        return self._final_dependency(payload)["metadata"]


if __name__ == "__main__":
    unittest.main()
