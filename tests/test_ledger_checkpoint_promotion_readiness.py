import copy
from dataclasses import dataclass
import inspect
import unittest

from harness_orchestrator.ledger_checkpoint_promotion_readiness import (
    LedgerCheckpointPromotionReadiness,
    evaluate_ledger_checkpoint_promotion_readiness,
)


_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


class ToDictReceipt:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


class LedgerCheckpointPromotionReadinessTests(unittest.TestCase):
    def test_passing_receipt_mapping_returns_deterministic_readiness_dict(self) -> None:
        result = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            expected_checkpoint_digest=_DIGEST.upper(),
            expected_payload_digest=_DIGEST,
            expected_checkpoint_path="checkpoints/run-001.json",
        )

        self.assertIsInstance(result, LedgerCheckpointPromotionReadiness)
        self.assertTrue(result.passed)
        self.assertEqual(
            result.to_dict(),
            {
                "passed": True,
                "blockers": (),
                "work_id": "work-001",
                "run_id": "run-001",
                "checkpoint_path": "checkpoints/run-001.json",
                "checkpoint_digest": _DIGEST,
                "payload_digest": _DIGEST,
                "checkpoint_size_bytes": 128,
                "summary": {
                    "work_id": "work-001",
                    "run_id": "run-001",
                    "checkpoint_path": ".../run-001.json",
                    "checkpoint_digest_prefix": "aaaaaaaaaaaa",
                    "payload_digest_prefix": "aaaaaaaaaaaa",
                    "checkpoint_size_bytes": 128,
                    "optional_summary_count": 0,
                    "optional_summary_names": (),
                },
            },
        )

    def test_to_dict_and_dataclass_style_inputs_verify(self) -> None:
        @dataclass(frozen=True)
        class Summary:
            ready: bool
            status: str
            work_id: str
            run_id: str
            blockers: tuple[str, ...] = ()

        result = evaluate_ledger_checkpoint_promotion_readiness(
            ToDictReceipt(self._receipt()),
            work_id="work-001",
            run_id="run-001",
            preflight_summary=Summary(
                ready=True,
                status="ready",
                work_id="work-001",
                run_id="run-001",
            ),
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.summary["optional_summary_names"], ("preflight",))

    def test_failed_receipt_or_source_blockers_fail_closed(self) -> None:
        failed = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(passed=False),
            work_id="work-001",
            run_id="run-001",
        )
        blocked = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(blockers=("source-blocker",)),
            work_id="work-001",
            run_id="run-001",
        )

        self.assertFalse(failed.passed)
        self.assertIn("receipt-verification-not-passed", failed.blockers)
        self.assertFalse(blocked.passed)
        self.assertIn("receipt-blockers-present", blocked.blockers)
        self.assertIn("receipt-source-blocker", blocked.blockers)

    def test_run_work_path_and_digest_mismatch_fail_closed(self) -> None:
        run_mismatch = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(run_id="run-other"),
            work_id="work-001",
            run_id="run-001",
        )
        work_mismatch = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(work_id="work-other"),
            work_id="work-001",
            run_id="run-001",
        )
        path_mismatch = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            expected_checkpoint_path="checkpoints/other.json",
        )
        digest_mismatch = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            expected_checkpoint_digest=_OTHER_DIGEST,
        )

        self.assertIn("receipt-run-id-mismatch", run_mismatch.blockers)
        self.assertIn("receipt-work-id-mismatch", work_mismatch.blockers)
        self.assertIn("expected-checkpoint-path-mismatch", path_mismatch.blockers)
        self.assertIn("expected-checkpoint-digest-mismatch", digest_mismatch.blockers)

    def test_malformed_digest_path_and_size_fail_closed(self) -> None:
        invalid_digest = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(checkpoint_digest="not-a-digest"),
            work_id="work-001",
            run_id="run-001",
        )
        unsafe_path = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(checkpoint_path="../run-001.json"),
            work_id="work-001",
            run_id="run-001",
        )
        bad_size = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(checkpoint_size_bytes=0),
            work_id="work-001",
            run_id="run-001",
        )

        self.assertIn("invalid-checkpoint-digest", invalid_digest.blockers)
        self.assertIn("checkpoint-path-unsafe", unsafe_path.blockers)
        self.assertIn("nonpositive-checkpoint-size-bytes", bad_size.blockers)

    def test_optional_summaries_with_blockers_unfinished_or_not_ready_fail_closed(self) -> None:
        preflight = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            preflight_summary={"ready": False, "blockers": ("blocked-gate",)},
        )
        result_ledger = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            result_ledger_record={"passed": False, "status": "blocked"},
        )
        checkpoint = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            checkpoint_result={"passed": True, "tasks": [{"status": "open"}]},
        )

        self.assertIn("preflight-not-ready", preflight.blockers)
        self.assertIn("preflight-blockers-present", preflight.blockers)
        self.assertIn("result-ledger-not-passed", result_ledger.blockers)
        self.assertIn("result-ledger-status-not-ready", result_ledger.blockers)
        self.assertIn("checkpoint-result-unfinished-tasks", checkpoint.blockers)

    def test_optional_summary_identity_mismatch_fails_closed(self) -> None:
        result = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            checkpoint_result={
                "passed": True,
                "status": "ready",
                "run_id": "run-001",
                "checkpoint_path": "checkpoints/other.json",
                "checkpoint_digest": _OTHER_DIGEST,
            },
        )

        self.assertIn("checkpoint-result-checkpoint-path-mismatch", result.blockers)
        self.assertIn("checkpoint-result-checkpoint-digest-mismatch", result.blockers)

    def test_matching_checkpoint_result_summary_is_accepted(self) -> None:
        result = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            checkpoint_result={
                "passed": True,
                "status": "ready",
                "work_id": "work-001",
                "run_id": "run-001",
                "checkpoint_path": "checkpoints/run-001.json",
                "checkpoint_digest": _DIGEST,
                "payload_digest": _DIGEST,
                "checkpoint_size_bytes": 128,
            },
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.blockers, ())
        self.assertEqual(result.summary["optional_summary_names"], ("checkpoint-result",))

    def test_duplicate_promotion_and_checkpoint_metadata_fail_closed(self) -> None:
        receipt_duplicate = evaluate_ledger_checkpoint_promotion_readiness(
            {
                **self._receipt(),
                "promotion": {"promotion_metadata": {"run_id": "run-001"}},
            },
            work_id="work-001",
            run_id="run-001",
        )
        summary_duplicate = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            checkpoint_result={
                "passed": True,
                "status": "ready",
                "metadata": {"checkpoint_digest": _DIGEST},
            },
        )
        nested_checkpoint_result_duplicate = evaluate_ledger_checkpoint_promotion_readiness(
            self._receipt(),
            work_id="work-001",
            run_id="run-001",
            checkpoint_result={
                "passed": True,
                "status": "ready",
                "checkpoint_result": {"checkpoint_digest": _DIGEST},
            },
        )

        self.assertIn("duplicate-receipt-metadata", receipt_duplicate.blockers)
        self.assertIn(
            "duplicate-checkpoint-result-metadata",
            summary_duplicate.blockers,
        )
        self.assertIn(
            "duplicate-checkpoint-result-metadata",
            nested_checkpoint_result_duplicate.blockers,
        )

    def test_secret_like_values_and_execution_intent_fail_closed_with_redaction(self) -> None:
        secret = evaluate_ledger_checkpoint_promotion_readiness(
            {
                **self._receipt(checkpoint_path="checkpoints/token.json"),
                "metadata": {"api_key": "value"},
            },
            work_id="work-001",
            run_id="run-001",
        )
        execution = evaluate_ledger_checkpoint_promotion_readiness(
            {**self._receipt(), "operator": {"cmd": "run"}},
            work_id="work-001",
            run_id="run-001",
        )

        self.assertFalse(secret.passed)
        self.assertIn("secret-like-checkpoint-path", secret.blockers)
        self.assertIn("secret-like-receipt-data", secret.blockers)
        self.assertEqual(secret.summary["checkpoint_path"], ".../token.json")
        self.assertNotIn("api_key", repr(secret.to_dict()))
        self.assertIn("execution-intent-receipt-data", execution.blockers)

    def test_caller_mapping_is_not_mutated(self) -> None:
        receipt = self._receipt(extra={"nested": {"items": [1, 2, 3]}})
        preflight = {"ready": True, "summary": {"work_id": "work-001", "run_id": "run-001"}}
        before = copy.deepcopy({"receipt": receipt, "preflight": preflight})

        evaluate_ledger_checkpoint_promotion_readiness(
            receipt,
            work_id="work-001",
            run_id="run-001",
            preflight_summary=preflight,
        )

        self.assertEqual({"receipt": receipt, "preflight": preflight}, before)

    def test_source_scan_has_no_forbidden_runtime_service_or_storage_behavior(self) -> None:
        import harness_orchestrator.ledger_checkpoint_promotion_readiness as module

        source = inspect.getsource(module)
        forbidden_tokens = (
            "requests",
            "urllib",
            "http.client",
            "socket",
            "subprocess",
            "os.environ",
            "importlib",
            "pkg_resources",
            "scheduler",
            "watch",
            "publish",
            "ai_art",
            "maraca",
            "random",
            "datetime",
            "Path.exists",
            ".exists(",
            ".open(",
            "read_text",
            "write_text",
            "write_bytes",
            "read_bytes",
            "glob(",
            "rglob(",
        )

        for token in forbidden_tokens:
            self.assertNotIn(token, source)

    def _receipt(self, extra: dict[str, object] | None = None, **overrides: object):
        receipt = {
            "passed": True,
            "blockers": (),
            "work_id": "work-001",
            "run_id": "run-001",
            "checkpoint_path": "checkpoints/run-001.json",
            "checkpoint_digest": _DIGEST,
            "payload_digest": _DIGEST,
            "checkpoint_size_bytes": 128,
        }
        if extra:
            receipt.update(extra)
        receipt.update(overrides)
        return receipt


if __name__ == "__main__":
    unittest.main()
