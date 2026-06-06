import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_handoff_receipt as receipt_module
from harness_orchestrator.release_publish_handoff_package import (
    ReleasePublishHandoffPackage,
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
    ReleasePublishHandoffReceiptVerification,
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


class ReleasePublishHandoffReceiptTests(unittest.TestCase):
    def test_happy_path_accepts_mapping_dataclass_and_to_dict_receipt_data(self) -> None:
        receipt, package = self._receipt_and_package()

        @dataclass(frozen=True)
        class ReceiptData:
            run_id: str
            dependencies: tuple[dict[str, object], ...]
            audit_events: tuple[dict[str, object], ...]
            blockers: tuple[str, ...] = ()

        dataclass_receipt = ReceiptData(
            run_id=receipt["run_id"],
            dependencies=receipt["dependencies"],
            audit_events=receipt["audit_events"],
        )
        expected_dependency_id = (
            f"release-publish-handoff-package:work-001:{package.package_digest[:16]}"
        )
        expected_event_id = (
            f"release-publish-handoff-package-recorded:work-001:{package.package_digest[:16]}"
        )

        results = (
            verify_release_publish_handoff_receipt(
                receipt,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=package.package_digest,
            ),
            verify_release_publish_handoff_receipt(
                dataclass_receipt,
                run_id="run-001",
                work_id="work-001",
            ),
            verify_release_publish_handoff_receipt(
                ToDictReceipt(receipt),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for result in results:
            with self.subTest(result=result):
                self.assertIsInstance(result, ReleasePublishHandoffReceiptVerification)
                self.assertTrue(result.passed)
                self.assertEqual((), result.blockers)
                self.assertEqual("run-001", result.run_id)
                self.assertEqual("work-001", result.work_id)
                self.assertEqual(expected_dependency_id, result.dependency_id)
                self.assertEqual(expected_event_id, result.event_id)
                self.assertEqual(package.package_digest, result.package_digest)
                self.assertEqual(package.package_digest[:12], result.package_digest_prefix)
                json.dumps(result.to_dict(), sort_keys=True)

    def test_requires_no_source_blockers_matching_run_work_and_single_pair(self) -> None:
        source_blockers, _package = self._receipt_and_package()
        source_blockers["blockers"] = ("operator-review-open",)
        run_mismatch, _package = self._receipt_and_package()
        run_mismatch["run_id"] = "run-999"
        work_mismatch, _package = self._receipt_and_package(work_id="work-002")
        missing_dependency, _package = self._receipt_and_package()
        missing_dependency["dependencies"] = tuple(
            record
            for record in missing_dependency["dependencies"]
            if record["dependency_type"] != "release-publish-handoff-package"
        )
        duplicate_event, _package = self._receipt_and_package()
        package_event = self._event(duplicate_event)
        duplicate_event["audit_events"] = (
            *duplicate_event["audit_events"],
            copy.deepcopy(package_event),
        )

        self.assertIn(
            "source-blockers-present",
            self._verify(source_blockers).blockers,
        )
        self.assertIn(
            "release-publish-handoff-receipt-receipt-run-id-mismatch",
            self._verify(run_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-dependency-missing",
            self._verify(work_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-dependency-missing",
            self._verify(missing_dependency).blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-event-ambiguous",
            self._verify(duplicate_event).blockers,
        )

    def test_deterministic_ids_expected_values_digest_prefix_and_canonical_data_are_verified(self) -> None:
        receipt, package = self._receipt_and_package()
        dependency_id = copy.deepcopy(receipt)
        self._dependency(dependency_id)["dependency_id"] = (
            "release-publish-handoff-package:work-001:ffffffffffffffff"
        )
        event_id = copy.deepcopy(receipt)
        self._event(event_id)["event_id"] = (
            "release-publish-handoff-package-recorded:work-001:ffffffffffffffff"
        )
        digest = copy.deepcopy(receipt)
        self._dependency(digest)["metadata"]["package_data"]["work_id"] = "work-999"
        prefix = copy.deepcopy(receipt)
        self._dependency(prefix)["metadata"]["package_digest_prefix"] = "0" * 12
        canonical = copy.deepcopy(receipt)
        self._dependency(canonical)["metadata"]["canonical_payload"]["handoff_package"][
            "work_id"
        ] = "work-999"

        self.assertIn(
            "release-publish-handoff-receipt-dependency-id-mismatch",
            self._verify(dependency_id).blockers,
        )
        self.assertIn(
            "release-publish-handoff-receipt-event-id-mismatch",
            self._verify(event_id).blockers,
        )
        self.assertIn(
            "release-publish-handoff-package-digest-mismatch",
            self._verify(digest).blockers,
        )
        self.assertIn(
            "release-publish-handoff-receipt-package-digest-prefix-mismatch",
            self._verify(prefix).blockers,
        )
        self.assertIn(
            "release-publish-handoff-receipt-canonical-payload-package-data-mismatch",
            self._verify(canonical).blockers,
        )
        self.assertIn(
            "expected-package-digest-mismatch",
            verify_release_publish_handoff_receipt(
                receipt,
                run_id="run-001",
                work_id="work-001",
                expected_package_digest="c" * 64,
            ).blockers,
        )
        self.assertIn(
            "expected-dependency-id-mismatch",
            verify_release_publish_handoff_receipt(
                receipt,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=(
                    f"release-publish-handoff-package:work-001:{'f' * 16}"
                ),
                expected_event_id=(
                    f"release-publish-handoff-package-recorded:work-001:{package.package_digest[:16]}"
                ),
            ).blockers,
        )

    def test_malformed_secret_action_non_string_key_and_unsupported_object_fail_closed(self) -> None:
        malformed = verify_release_publish_handoff_receipt(
            "bad",
            run_id="run-001",
            work_id="work-001",
        )
        non_string, _package = self._receipt_and_package()
        self._dependency(non_string)["metadata"]["package_data"]["metadata"] = {
            object(): "bad"
        }
        unsupported, _package = self._receipt_and_package()
        self._dependency(unsupported)["metadata"]["package_data"]["artifact"][
            "bad"
        ] = object()
        secret, _package = self._receipt_and_package()
        self._dependency(secret)["metadata"]["package_data"]["metadata"] = {
            "api_key": "raw"
        }
        action, _package = self._receipt_and_package()
        self._dependency(action)["metadata"]["package_data"]["metadata"] = {
            "runner": "manual"
        }

        self.assertIn("missing-release-publish-handoff-receipt", malformed.blockers)
        self.assertIn(
            "non-string-key-release-publish-handoff-receipt",
            self._verify(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-handoff-receipt",
            self._verify(unsupported).blockers,
        )
        self.assertIn("secret-like-receipt-data", self._verify(secret).blockers)
        self.assertIn("action-intent-receipt-data", self._verify(action).blockers)

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        receipt, package = self._receipt_and_package()
        result = self._verify(receipt)
        before = result.to_dict()

        self._dependency(receipt)["metadata"]["package_digest"] = "c" * 64
        plain = result.to_dict()
        plain["receipt_summary"]["dependency_id"] = "changed"

        self.assertEqual(before, result.to_dict())
        self.assertEqual(package.package_digest, result.package_digest)
        with self.assertRaises(TypeError):
            result.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.package_digest = "changed"

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
                "from dataclasses import asdict, dataclass, is_dataclass",
                "import hashlib",
                "import json",
                "import re",
                "from types import MappingProxyType",
                "from typing import Any, Mapping",
            ),
            imports,
        )

    def _verify(self, receipt: object) -> ReleasePublishHandoffReceiptVerification:
        return verify_release_publish_handoff_receipt(
            receipt,
            run_id="run-001",
            work_id="work-001",
        )

    def _receipt_and_package(
        self,
        *,
        run_id: str = "run-001",
        work_id: str = "work-001",
    ) -> tuple[dict[str, object], ReleasePublishHandoffPackage]:
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
            metadata={"ticket": "HAR-037", "approved": True, "count": 2},
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
        return ledger.snapshot().to_dict(), package

    def _dependency(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            record
            for record in receipt["dependencies"]
            if record["dependency_type"] == "release-publish-handoff-package"
        )

    def _event(self, receipt: dict[str, object]) -> dict[str, object]:
        return next(
            event
            for event in receipt["audit_events"]
            if event["event_type"] == "release-publish-handoff-package-ledger-record"
        )


if __name__ == "__main__":
    unittest.main()
