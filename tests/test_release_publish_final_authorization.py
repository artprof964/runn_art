import copy
from dataclasses import FrozenInstanceError, dataclass
import inspect
import json
import unittest

import harness_orchestrator.release_publish_final_authorization as authorization_module
from harness_orchestrator.release_publish_final_authorization import (
    ReleasePublishFinalAuthorization,
    authorize_release_publish_final,
    evaluate_release_publish_final_authorization,
)
from harness_orchestrator.release_publish_handoff_completion_receipt import (
    ReleasePublishHandoffCompletionReceiptVerification,
)


_DIGEST = "0123456789abcdef" * 4


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
    acceptance_source_dependency_id: str
    acceptance_source_event_id: str
    receipt_summary: dict[str, object]


@dataclass(frozen=True)
class RaisingDataclassToDictVerification:
    data: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        raise RuntimeError("conversion failed")


@dataclass(frozen=True)
class NonMappingDataclassToDictVerification:
    data: dict[str, object]

    def to_dict(self) -> str:
        return "bad"


class ReleasePublishFinalAuthorizationTests(unittest.TestCase):
    def test_happy_path_accepts_cr_har_043_result_mapping_dataclass_and_to_dict(self) -> None:
        data = self._verification()
        verification_result = ReleasePublishHandoffCompletionReceiptVerification(**data)
        dataclass_data = VerificationData(**data)
        expected_dependency_id = f"release-publish-handoff-completion:work-001:{_DIGEST[:16]}"
        expected_event_id = (
            f"release-publish-handoff-completion-recorded:work-001:{_DIGEST[:16]}"
        )

        results = (
            authorize_release_publish_final(
                verification_result,
                run_id="run-001",
                work_id="work-001",
                expected_dependency_id=expected_dependency_id,
                expected_event_id=expected_event_id,
                expected_package_digest=_DIGEST,
            ),
            authorize_release_publish_final(data, run_id="run-001", work_id="work-001"),
            authorize_release_publish_final(
                dataclass_data,
                run_id="run-001",
                work_id="work-001",
            ),
            evaluate_release_publish_final_authorization(
                ToDictVerification(data),
                run_id="run-001",
                work_id="work-001",
            ),
        )

        for authorization in results:
            with self.subTest(authorization=authorization):
                self.assertIsInstance(authorization, ReleasePublishFinalAuthorization)
                self.assertTrue(authorization.authorized)
                self.assertEqual("authorized", authorization.status)
                self.assertEqual((), authorization.blockers)
                self.assertEqual("run-001", authorization.run_id)
                self.assertEqual("work-001", authorization.work_id)
                self.assertEqual(expected_dependency_id, authorization.dependency_id)
                self.assertEqual(expected_event_id, authorization.event_id)
                self.assertEqual(_DIGEST, authorization.package_digest)
                self.assertEqual(_DIGEST[:12], authorization.package_digest_prefix)
                self.assertEqual(
                    f"release-publish-handoff-package:work-001:{_DIGEST[:16]}",
                    authorization.acceptance_source_dependency_id,
                )
                self.assertTrue(authorization.authorization_summary["authorized"])
                json.dumps(authorization.to_dict(), sort_keys=True)

    def test_failed_blocked_mismatched_and_deterministic_id_cases_fail_closed(self) -> None:
        failed = self._verification()
        failed["passed"] = False
        blocked = self._verification()
        blocked["blockers"] = ("operator-review-open",)
        run_mismatch = self._verification()
        run_mismatch["run_id"] = "run-999"
        work_mismatch = self._verification()
        work_mismatch["work_id"] = "work-999"
        dependency_mismatch = self._verification()
        dependency_mismatch["dependency_id"] = (
            f"release-publish-handoff-completion:work-001:{'f' * 16}"
        )
        event_mismatch = self._verification()
        event_mismatch["event_id"] = (
            f"release-publish-handoff-completion-recorded:work-001:{'f' * 16}"
        )
        source_mismatch = self._verification()
        source_mismatch["source_dependency_id"] = (
            f"release-publish-handoff-acceptance:work-001:{'f' * 16}"
        )
        acceptance_source_mismatch = self._verification()
        acceptance_source_mismatch["acceptance_source_event_id"] = (
            f"release-publish-handoff-package-recorded:work-001:{'f' * 16}"
        )

        self.assertIn(
            "release-publish-handoff-completion-verification-not-passed",
            self._authorize(failed).blockers,
        )
        self.assertIn("source-blockers-present", self._authorize(blocked).blockers)
        self.assertIn(
            "release-publish-final-authorization-verification-run-id-mismatch",
            self._authorize(run_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-verification-work-id-mismatch",
            self._authorize(work_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-dependency-id-mismatch",
            self._authorize(dependency_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-event-id-mismatch",
            self._authorize(event_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-source-dependency-id-mismatch",
            self._authorize(source_mismatch).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-acceptance-source-event-id-mismatch",
            self._authorize(acceptance_source_mismatch).blockers,
        )

    def test_digest_prefix_expected_values_and_receipt_summary_parity_are_verified(self) -> None:
        bad_digest = self._verification()
        bad_digest["package_digest"] = "not-a-digest"
        bad_prefix = self._verification()
        bad_prefix["package_digest_prefix"] = "f" * 12
        summary_schema = self._verification()
        summary_schema["receipt_summary"]["extra"] = "bad"
        summary_parity = self._verification()
        summary_parity["receipt_summary"]["source_event_id"] = (
            f"release-publish-handoff-acceptance-recorded:work-001:{'f' * 16}"
        )
        summary_blockers = self._verification()
        summary_blockers["receipt_summary"]["source_blocker_count"] = 1
        summary_bool_blockers = self._verification()
        summary_bool_blockers["receipt_summary"]["source_blocker_count"] = False

        self.assertIn("invalid-package-digest", self._authorize(bad_digest).blockers)
        self.assertIn(
            "release-publish-final-authorization-package-digest-prefix-mismatch",
            self._authorize(bad_prefix).blockers,
        )
        self.assertIn("unsafe-receipt-summary-schema", self._authorize(summary_schema).blockers)
        self.assertIn(
            "release-publish-final-authorization-receipt-summary-source-event-id-mismatch",
            self._authorize(summary_parity).blockers,
        )
        self.assertIn(
            "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch",
            self._authorize(summary_blockers).blockers,
        )
        bool_authorization = self._authorize(summary_bool_blockers)
        self.assertFalse(bool_authorization.authorized)
        self.assertIn(
            "release-publish-final-authorization-receipt-summary-source-blocker-count-mismatch",
            bool_authorization.blockers,
        )
        self.assertIn(
            "expected-package-digest-mismatch",
            authorize_release_publish_final(
                self._verification(),
                run_id="run-001",
                work_id="work-001",
                expected_package_digest="f" * 64,
            ).blockers,
        )
        self.assertIn(
            "expected-source-event-id-mismatch",
            authorize_release_publish_final(
                self._verification(),
                run_id="run-001",
                work_id="work-001",
                expected_source_event_id=(
                    f"release-publish-handoff-acceptance-recorded:work-001:{'f' * 16}"
                ),
            ).blockers,
        )

    def test_malformed_non_string_key_unsupported_secret_action_and_bad_to_dict_fail_closed(self) -> None:
        non_string = self._verification()
        non_string["receipt_summary"] = {object(): "bad"}
        unsupported = self._verification()
        unsupported["receipt_summary"]["bad"] = object()
        secret = self._verification()
        secret["receipt_summary"]["api_key"] = "raw"
        action = self._verification()
        action["receipt_summary"]["runner"] = "manual"

        malformed_cases = (
            RaisingToDictVerification(),
            NonMappingToDictVerification(),
            RaisingDataclassToDictVerification(self._verification()),
            NonMappingDataclassToDictVerification(self._verification()),
        )
        for source in malformed_cases:
            with self.subTest(source=source):
                authorization = self._authorize(source)
                self.assertFalse(authorization.authorized)
                self.assertIn("malformed-release-publish-final-authorization", authorization.blockers)
                self.assertIn(
                    "missing-release-publish-handoff-completion-verification",
                    authorization.blockers,
                )

        self.assertIn(
            "missing-release-publish-handoff-completion-verification",
            self._authorize("bad").blockers,
        )
        self.assertIn(
            "non-string-key-release-publish-final-authorization",
            self._authorize(non_string).blockers,
        )
        self.assertIn(
            "unsupported-object-release-publish-final-authorization",
            self._authorize(unsupported).blockers,
        )
        self.assertIn("secret-like-verification-data", self._authorize(secret).blockers)
        self.assertIn("action-intent-verification-data", self._authorize(action).blockers)

    def test_result_is_frozen_plain_json_safe_and_caller_mutation_safe(self) -> None:
        source = self._verification()
        authorization = self._authorize(source)
        before = authorization.to_dict()

        source["package_digest"] = "f" * 64
        plain = authorization.to_dict()
        plain["receipt_summary"]["dependency_id"] = "changed"
        plain["authorization_summary"]["authorized"] = False

        self.assertEqual(before, authorization.to_dict())
        self.assertEqual(_DIGEST, authorization.package_digest)
        with self.assertRaises(TypeError):
            authorization.receipt_summary["dependency_id"] = "changed"
        with self.assertRaises(TypeError):
            authorization.authorization_summary["authorized"] = False
        with self.assertRaises(FrozenInstanceError):
            authorization.package_digest = "changed"

    def test_forbidden_source_scan_and_import_boundary(self) -> None:
        source = inspect.getsource(authorization_module)
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

    def _authorize(self, verification: object) -> ReleasePublishFinalAuthorization:
        return authorize_release_publish_final(
            verification,
            run_id="run-001",
            work_id="work-001",
        )

    def _verification(self) -> dict[str, object]:
        dependency_id = f"release-publish-handoff-completion:work-001:{_DIGEST[:16]}"
        event_id = f"release-publish-handoff-completion-recorded:work-001:{_DIGEST[:16]}"
        source_dependency_id = f"release-publish-handoff-acceptance:work-001:{_DIGEST[:16]}"
        source_event_id = (
            f"release-publish-handoff-acceptance-recorded:work-001:{_DIGEST[:16]}"
        )
        acceptance_source_dependency_id = (
            f"release-publish-handoff-package:work-001:{_DIGEST[:16]}"
        )
        acceptance_source_event_id = (
            f"release-publish-handoff-package-recorded:work-001:{_DIGEST[:16]}"
        )
        return {
            "passed": True,
            "blockers": (),
            "run_id": "run-001",
            "work_id": "work-001",
            "dependency_id": dependency_id,
            "event_id": event_id,
            "package_digest": _DIGEST,
            "package_digest_prefix": _DIGEST[:12],
            "source_dependency_id": source_dependency_id,
            "source_event_id": source_event_id,
            "acceptance_source_dependency_id": acceptance_source_dependency_id,
            "acceptance_source_event_id": acceptance_source_event_id,
            "receipt_summary": {
                "run_id": "run-001",
                "work_id": "work-001",
                "dependency_id": dependency_id,
                "event_id": event_id,
                "package_digest_prefix": _DIGEST[:12],
                "source_dependency_id": source_dependency_id,
                "source_event_id": source_event_id,
                "acceptance_source_dependency_id": acceptance_source_dependency_id,
                "acceptance_source_event_id": acceptance_source_event_id,
                "source_blocker_count": 0,
            },
        }


if __name__ == "__main__":
    unittest.main()
