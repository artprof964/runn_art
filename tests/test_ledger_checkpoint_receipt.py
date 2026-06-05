import copy
from dataclasses import dataclass
import inspect
import unittest

from harness_orchestrator.ledger_checkpoint import LedgerCheckpointResult
from harness_orchestrator.ledger_checkpoint_receipt import (
    LedgerCheckpointReceiptVerification,
    verify_ledger_checkpoint_receipt,
)


_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


class LedgerCheckpointReceiptTests(unittest.TestCase):
    def test_result_like_object_verifies_with_deterministic_dict(self) -> None:
        result = LedgerCheckpointResult(
            checkpoint_path="checkpoints/run-001.json",
            checkpoint_digest=_DIGEST,
            payload_digest=_DIGEST,
            checkpoint_size_bytes=128,
            run_id="run-001",
        )

        verified = verify_ledger_checkpoint_receipt(result, run_id="run-001")

        self.assertIsInstance(verified, LedgerCheckpointReceiptVerification)
        self.assertTrue(verified.passed)
        self.assertEqual(verified.blockers, ())
        self.assertEqual(
            verified.to_dict(),
            {
                "passed": True,
                "blockers": (),
                "run_id": "run-001",
                "checkpoint_path": "checkpoints/run-001.json",
                "checkpoint_digest": _DIGEST,
                "payload_digest": _DIGEST,
                "checkpoint_size_bytes": 128,
                "receipt_summary": {
                    "run_id": "run-001",
                    "checkpoint_path": ".../run-001.json",
                    "checkpoint_digest_prefix": "aaaaaaaaaaaa",
                    "payload_digest_prefix": "aaaaaaaaaaaa",
                    "checkpoint_size_bytes": 128,
                    "source_blocker_count": 0,
                },
            },
        )

    def test_plain_mapping_with_expected_values_verifies(self) -> None:
        verified = verify_ledger_checkpoint_receipt(
            self._receipt(),
            run_id="run-001",
            expected_checkpoint_digest=_DIGEST.upper(),
            expected_payload_digest=_DIGEST,
            expected_checkpoint_path=" checkpoints/run-001.json ",
        )

        self.assertTrue(verified.passed)
        self.assertEqual(verified.checkpoint_digest, _DIGEST)

    def test_existing_source_blockers_fail_closed(self) -> None:
        verified = verify_ledger_checkpoint_receipt(
            self._receipt(blockers=("unfinished-ledger-tasks",)),
            run_id="run-001",
        )

        self.assertFalse(verified.passed)
        self.assertIn("source-blockers-present", verified.blockers)
        self.assertIn("source-unfinished-ledger-tasks", verified.blockers)

    def test_run_mismatch_fails_closed(self) -> None:
        verified = verify_ledger_checkpoint_receipt(
            self._receipt(run_id="run-other"),
            run_id="run-001",
        )

        self.assertFalse(verified.passed)
        self.assertIn("checkpoint-run-id-mismatch", verified.blockers)

    def test_invalid_or_mismatched_digest_fails_closed(self) -> None:
        invalid = verify_ledger_checkpoint_receipt(
            self._receipt(checkpoint_digest="not-a-digest"),
            run_id="run-001",
        )
        mismatched = verify_ledger_checkpoint_receipt(
            self._receipt(payload_digest=_OTHER_DIGEST),
            run_id="run-001",
        )
        expected_mismatch = verify_ledger_checkpoint_receipt(
            self._receipt(),
            run_id="run-001",
            expected_checkpoint_digest=_OTHER_DIGEST,
        )

        self.assertIn("invalid-checkpoint-digest", invalid.blockers)
        self.assertIn("checkpoint-payload-digest-mismatch", mismatched.blockers)
        self.assertIn(
            "expected-checkpoint-digest-mismatch",
            expected_mismatch.blockers,
        )

    def test_unsafe_or_secret_like_path_and_data_fail_closed(self) -> None:
        unsafe = verify_ledger_checkpoint_receipt(
            self._receipt(checkpoint_path="../checkpoint.json"),
            run_id="run-001",
        )
        secret = verify_ledger_checkpoint_receipt(
            {
                **self._receipt(checkpoint_path="checkpoints/token.json"),
                "metadata": {"api_key": "value"},
            },
            run_id="run-001",
        )
        duplicate = verify_ledger_checkpoint_receipt(
            {
                **self._receipt(),
                "ledger_snapshot": {"metadata": {"checkpoint_digest": _DIGEST}},
            },
            run_id="run-001",
        )

        self.assertIn("checkpoint-path-unsafe", unsafe.blockers)
        self.assertIn("secret-like-checkpoint-path", secret.blockers)
        self.assertIn("secret-like-checkpoint-data", secret.blockers)
        self.assertIn("duplicate-checkpoint-metadata", duplicate.blockers)

    def test_nonpositive_or_malformed_size_fails_closed(self) -> None:
        nonpositive = verify_ledger_checkpoint_receipt(
            self._receipt(checkpoint_size_bytes=0),
            run_id="run-001",
        )
        malformed = verify_ledger_checkpoint_receipt(
            self._receipt(checkpoint_size_bytes="128"),
            run_id="run-001",
        )

        self.assertIn("nonpositive-checkpoint-size-bytes", nonpositive.blockers)
        self.assertIn("invalid-checkpoint-size-bytes", malformed.blockers)

    def test_execution_intent_fields_fail_closed(self) -> None:
        verified = verify_ledger_checkpoint_receipt(
            {
                **self._receipt(),
                "operator": {"cmd": "run something"},
            },
            run_id="run-001",
        )

        self.assertFalse(verified.passed)
        self.assertIn("execution-intent-checkpoint-data", verified.blockers)

    def test_caller_mapping_is_not_mutated(self) -> None:
        receipt = self._receipt(extra={"nested": {"items": [1, 2, 3]}})
        before = copy.deepcopy(receipt)

        verify_ledger_checkpoint_receipt(receipt, run_id="run-001")

        self.assertEqual(receipt, before)

    def test_dataclass_mapping_is_accepted(self) -> None:
        @dataclass(frozen=True)
        class Receipt:
            run_id: str
            checkpoint_path: str
            checkpoint_digest: str
            payload_digest: str
            checkpoint_size_bytes: int
            blockers: tuple[str, ...] = ()

        verified = verify_ledger_checkpoint_receipt(
            Receipt(
                run_id="run-001",
                checkpoint_path="checkpoints/run-001.json",
                checkpoint_digest=_DIGEST,
                payload_digest=_DIGEST,
                checkpoint_size_bytes=128,
            ),
            run_id="run-001",
        )

        self.assertTrue(verified.passed)

    def test_source_scan_has_no_forbidden_runtime_service_or_file_behavior(self) -> None:
        import harness_orchestrator.ledger_checkpoint_receipt as module

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
            "run_id": "run-001",
            "checkpoint_path": "checkpoints/run-001.json",
            "checkpoint_digest": _DIGEST,
            "payload_digest": _DIGEST,
            "checkpoint_size_bytes": 128,
            "blockers": (),
        }
        if extra:
            receipt.update(extra)
        receipt.update(overrides)
        return receipt


if __name__ == "__main__":
    unittest.main()
