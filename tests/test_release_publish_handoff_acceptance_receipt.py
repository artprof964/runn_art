import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_acceptance_receipt as receipt_module
from harness_orchestrator.release_publish_handoff_acceptance import (
    evaluate_release_publish_handoff_acceptance,
)
from harness_orchestrator.release_publish_handoff_acceptance_ledger import (
    ReleasePublishHandoffAcceptanceLedgerResult,
    record_release_publish_handoff_acceptance,
)
from harness_orchestrator.release_publish_handoff_acceptance_receipt import (
    ReleasePublishHandoffAcceptanceReceiptVerification,
    verify_release_publish_handoff_acceptance_receipt,
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


class ToDictReceipt:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class RaisingToDictReceipt:
    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("conversion failed")


class NonMappingToDictReceipt:
    def to_dict(self) -> str:
        return "bad"


class ReleasePublishHandoffAcceptanceReceiptTests(unittest.TestCase):
    def test_happy_path_accepts_result_snapshot_mapping_dataclass_and_to_dict(self) -> None:
        result, package_digest = self._recorded_result()
        data = result.to_dict()
        snapshot = data["ledger_snapshot"]

        @dataclass(frozen=True)
        class SnapshotData:
            run_id: str
            gate_decisions: tuple[dict[str, object], ...]
            dependencies: tuple[dict[str, object], ...]
            audit_events: tuple[dict[str, object], ...]
            tasks: tuple[dict[str, object], ...]
            metadata: dict[str, object]

        dataclass_snapshot = SnapshotData(**snapshot)
        expected_dependency_id = (
            f"release-publish-handoff-acceptance:work-001:{package_digest[:16]}"
        )
        expected_event_id = (
            f"release-publish-handoff-acceptance-recorded:work-001:{package_digest[:16]}"
        )

        results = (
            verify_release_publish_handoff_acceptance_receipt(
                data,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=package_digest,
            ),
            verify_release_publish_handoff_acceptance_receipt(
                result,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=package_digest,
            ),
            verify_release_publish_handoff_acceptance_receipt(
                snapshot,
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_handoff_acceptance_receipt(
                result.ledger_snapshot,
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_handoff_acceptance_receipt(
                dataclass_snapshot,
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_handoff_acceptance_receipt(
                ToDictReceipt(data),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for verification in results:
            with self.subTest(verification=verification):
                self.assertIsInstance(
                    verification,
                    ReleasePublishHandoffAcceptanceReceiptVerification,
                )
                self.assertTrue(verification.passed)
                self.assertEqual((), verification.blockers)
                self.assertEqual("run-001", verification.run_id)
                self.assertEqual("work-001", verification.work_id)
                self.assertEqual(expected_dependency_id, verification.dependency_id)
                self.assertEqual(expected_event_id, verification.event_id)
                self.assertEqual(package_digest, verification.package_digest)
                self.assertEqual(package_digest[:12], verification.package_digest_prefix)
                self.assertEqual(0, verification.receipt_summary["source_blocker_count"])
                json.dumps(verification.to_dict(), sort_keys=True)

    def test_malformed_to_dict_fails_closed_with_blockers(self) -> None:
        raising = verify_release_publish_handoff_acceptance_receipt(
            RaisingToDictReceipt(),
            run_id="run-001",
            work_id="work-001",
        )
        non_mapping = verify_release_publish_handoff_acceptance_receipt(
            NonMappingToDictReceipt(),
            run_id="run-001",
            work_id="work-001",
        )

        for verification in (raising, non_mapping):
            with self.subTest(blockers=verification.blockers):
                self.assertFalse(verification.passed)
                self.assertIn(
                    "malformed-release-publish-handoff-acceptance-receipt",
                    verification.blockers,
                )
                self.assertIn(
                    "missing-release-publish-handoff-acceptance-receipt",
                    verification.blockers,
                )

    def test_requires_no_source_blockers_matching_run_work_and_single_pair(self) -> None:
        blocked, _digest = self._receipt()
        blocked["blockers"] = ("operator-review-open",)
        run_mismatch, _digest = self._receipt()
        run_mismatch["ledger_snapshot"]["run_id"] = "run-999"
        work_mismatch, _digest = self._receipt(work_id="work-002")
        missing_dependency, _digest = self._receipt()
        missing_dependency["ledger_snapshot"]["dependencies"] = tuple(
            record
            for record in missing_dependency["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] != "release-publish-handoff-acceptance"
        )
        duplicate_event, _digest = self._receipt()
        event = self._event(duplicate_event)
        duplicate_event["ledger_snapshot"]["audit_events"] = (
            *duplicate_event["ledger_snapshot"]["audit_events"],
            copy.deepcopy(event),
        )

        self.assertIn("source-blockers-present", self._verify(blocked).blockers)
        self.assertIn(
            "release-publish-handoff-acceptance-receipt-receipt-run-id-mismatch",
            self._verify(run_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-dependency-missing",
            self._verify(work_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-dependency-missing",
            self._verify(missing_dependency).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-event-ambiguous",
            self._verify(duplicate_event).blockers,
        )

    def test_status_required_ids_expected_values_digest_prefix_and_metadata_are_verified(self) -> None:
        receipt, digest = self._receipt()
        dependency_status = copy.deepcopy(receipt)
        self._dependency(dependency_status)["status"] = "blocked"
        event_status = copy.deepcopy(receipt)
        self._event(event_status)["status"] = "blocked"
        not_required = copy.deepcopy(receipt)
        self._dependency(not_required)["required"] = False
        dependency_id = copy.deepcopy(receipt)
        self._dependency(dependency_id)["dependency_id"] = (
            "release-publish-handoff-acceptance:work-001:ffffffffffffffff"
        )
        event_id = copy.deepcopy(receipt)
        self._event(event_id)["event_id"] = (
            "release-publish-handoff-acceptance-recorded:work-001:ffffffffffffffff"
        )
        prefix = copy.deepcopy(receipt)
        self._dependency(prefix)["metadata"]["package_digest_prefix"] = "0" * 12
        event_pairing = copy.deepcopy(receipt)
        self._event(event_pairing)["metadata"]["dependency_id"] = (
            "release-publish-handoff-acceptance:work-001:ffffffffffffffff"
        )

        self.assertIn(
            "release-publish-handoff-acceptance-dependency-status-not-accepted",
            self._verify(dependency_status).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-event-status-not-accepted",
            self._verify(event_status).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-dependency-not-required",
            self._verify(not_required).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-receipt-dependency-id-mismatch",
            self._verify(dependency_id).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-receipt-event-id-mismatch",
            self._verify(event_id).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-receipt-package-digest-prefix-mismatch",
            self._verify(prefix).blockers,
        )
        self.assertIn(
            "release-publish-handoff-acceptance-receipt-event-dependency-id-mismatch",
            self._verify(event_pairing).blockers,
        )
        self.assertIn(
            "expected-package-digest-mismatch",
            verify_release_publish_handoff_acceptance_receipt(
                receipt,
                run_id="run-001",
                work_id="work-001",
                expected_package_digest="c" * 64,
            ).blockers,
        )
        self.assertIn(
            "expected-dependency-id-mismatch",
            verify_release_publish_handoff_acceptance_receipt(
                receipt,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=(
                    f"release-publish-handoff-acceptance:work-001:{'f' * 16}"
                ),
                expected_event_id=(
                    f"release-publish-handoff-acceptance-recorded:work-001:{digest[:16]}"
                ),
            ).blockers,
        )

    def test_metadata_schema_parity_canonical_and_summary_tampering_fail_closed(self) -> None:
        cases = []
        dependency_extra, _digest = self._receipt()
        self._dependency(dependency_extra)["metadata"]["extra"] = "bad"
        cases.append((dependency_extra, "unsafe-dependency-metadata-schema"))
        event_extra, _digest = self._receipt()
        self._event(event_extra)["metadata"]["extra"] = "bad"
        cases.append((event_extra, "unsafe-event-metadata-schema"))
        event_parity, _digest = self._receipt()
        self._event(event_parity)["metadata"]["source_event_id"] = "event-work-001-changed"
        cases.append(
            (
                event_parity,
                "release-publish-handoff-acceptance-receipt-event-source-event-id-mismatch",
            )
        )
        acceptance_summary, _digest = self._receipt()
        self._dependency(acceptance_summary)["metadata"]["acceptance_summary"][
            "accepted"
        ] = False
        cases.append(
            (
                acceptance_summary,
                "release-publish-handoff-acceptance-receipt-acceptance-summary-accepted-mismatch",
            )
        )
        receipt_summary, _digest = self._receipt()
        self._dependency(receipt_summary)["metadata"]["receipt_summary"][
            "source_blocker_count"
        ] = 1
        cases.append(
            (
                receipt_summary,
                "release-publish-handoff-acceptance-receipt-receipt-summary-source-blocker-count-mismatch",
            )
        )
        canonical_format, _digest = self._receipt()
        self._dependency(canonical_format)["metadata"]["canonical_payload"]["format"] = "changed"
        cases.append(
            (
                canonical_format,
                "release-publish-handoff-acceptance-receipt-canonical-payload-format-mismatch",
            )
        )
        canonical_summary, _digest = self._receipt()
        self._dependency(canonical_summary)["metadata"]["canonical_payload"][
            "handoff_acceptance"
        ]["accepted"] = False
        cases.append(
            (
                canonical_summary,
                "release-publish-handoff-acceptance-receipt-canonical-payload-acceptance-summary-mismatch",
            )
        )

        for payload, blocker in cases:
            with self.subTest(blocker=blocker):
                self.assertIn(blocker, self._verify(payload).blockers)

    def test_malformed_secret_action_non_string_key_unsupported_and_snapshot_errors_fail_closed(self) -> None:
        malformed = verify_release_publish_handoff_acceptance_receipt(
            "bad",
            run_id="run-001",
            work_id="work-001",
        )
        missing_snapshot = verify_release_publish_handoff_acceptance_receipt(
            {"run_id": "run-001"},
            run_id="run-001",
            work_id="work-001",
        )
        non_string, _digest = self._receipt()
        self._dependency(non_string)["metadata"]["receipt_summary"] = {object(): "bad"}
        unsupported, _digest = self._receipt()
        self._dependency(unsupported)["metadata"]["receipt_summary"]["bad"] = object()
        secret, _digest = self._receipt()
        self._dependency(secret)["metadata"]["receipt_summary"]["api_key"] = "raw"
        action, _digest = self._receipt()
        self._dependency(action)["metadata"]["receipt_summary"]["runner"] = "manual"

        self.assertIn(
            "missing-release-publish-handoff-acceptance-receipt",
            malformed.blockers,
        )
        self.assertIn("missing-ledger-snapshot", missing_snapshot.blockers)
        self.assertIn(
            "non-string-key-release-publish-handoff-acceptance-receipt",
            self._verify(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-handoff-acceptance-receipt",
            self._verify(unsupported).blockers,
        )
        self.assertIn("secret-like-receipt-data", self._verify(secret).blockers)
        self.assertIn("action-intent-receipt-data", self._verify(action).blockers)

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        receipt, digest = self._receipt()
        verification = self._verify(receipt)
        before = verification.to_dict()

        self._dependency(receipt)["metadata"]["package_digest"] = "c" * 64
        plain = verification.to_dict()
        plain["receipt_summary"]["dependency_id"] = "changed"

        self.assertEqual(before, verification.to_dict())
        self.assertEqual(digest, verification.package_digest)
        with self.assertRaises(TypeError):
            verification.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            verification.package_digest = "changed"

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

    def _verify(
        self,
        receipt: object,
    ) -> ReleasePublishHandoffAcceptanceReceiptVerification:
        return verify_release_publish_handoff_acceptance_receipt(
            receipt,
            run_id="run-001",
            work_id="work-001",
        )

    def _receipt(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[dict[str, object], str]:
        result, digest = self._recorded_result(run_id=run_id, work_id=work_id)
        return result.to_dict(), digest

    def _recorded_result(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[ReleasePublishHandoffAcceptanceLedgerResult, str]:
        ledger = RunLedger(run_id=run_id)
        intent = build_release_publish_intent(
            readiness=ReleasePublishReadiness(
                ready=True,
                status="ready",
                blockers=(),
                run_id=run_id,
                work_id=work_id,
                dependency_id=f"dependency-{work_id}",
                event_id=f"event-{work_id}",
                canonical_digest_prefix=_BINDING_DIGEST[:12],
                summary={"format": "harness-release-publish-readiness-v1"},
            ),
            run_id=run_id,
            work_id=work_id,
            release_binding_digest=_BINDING_DIGEST,
            publish_target={
                "target_type": "local-dry-run",
                "target_id": f"target-{work_id}",
            },
            publish_payload={
                "payload_digest": _PAYLOAD_DIGEST,
                "payload_label": "release",
            },
            artifact={"artifact_id": f"artifact-{work_id}"},
            metadata={"ticket": "HAR-040", "approved": True, "count": 2},
        )
        self.assertEqual((), intent.blockers)
        intent_result = record_release_publish_intent(intent, ledger=ledger)
        self.assertEqual((), intent_result.blockers)
        readiness = evaluate_release_publish_handoff_readiness(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), readiness.blockers)
        readiness_result = record_release_publish_handoff_readiness(
            readiness,
            ledger=ledger,
        )
        self.assertEqual((), readiness_result.blockers)
        package = build_release_publish_handoff_package(
            ledger.snapshot(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertEqual((), package.blockers)
        package_result = record_release_publish_handoff_package(package, ledger=ledger)
        self.assertEqual((), package_result.blockers)
        package_verification = verify_release_publish_handoff_receipt(
            ledger.snapshot().to_dict(),
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(package_verification.passed)
        acceptance = evaluate_release_publish_handoff_acceptance(
            package_verification,
            run_id=run_id,
            work_id=work_id,
        )
        self.assertTrue(acceptance.accepted)
        result = record_release_publish_handoff_acceptance(acceptance, ledger=ledger)
        self.assertEqual((), result.blockers)
        return result, acceptance.package_digest

    def _dependency(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            record
            for record in receipt["ledger_snapshot"]["dependencies"]
            if record["dependency_type"] == "release-publish-handoff-acceptance"
        )

    def _event(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            event
            for event in receipt["ledger_snapshot"]["audit_events"]
            if event["event_type"] == "release-publish-handoff-acceptance-ledger-record"
        )


if __name__ == "__main__":
    unittest.main()
