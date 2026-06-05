import copy
from dataclasses import dataclass
import inspect
import unittest

from harness_orchestrator.ledger_checkpoint_promotion_intent import (
    LedgerCheckpointPromotionIntent,
    LedgerCheckpointPromotionIntentResult,
    build_ledger_checkpoint_promotion_intent,
)


_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


class ToDictReadiness:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


class LedgerCheckpointPromotionIntentTests(unittest.TestCase):
    def test_passing_readiness_mapping_builds_deterministic_intent_dict(self) -> None:
        first = self._build(metadata={"approved_by": "review-board", "priority": 2})
        second = self._build(metadata={"priority": 2, "approved_by": "review-board"})

        self.assertIsInstance(first, LedgerCheckpointPromotionIntentResult)
        self.assertIsInstance(first.intent, LedgerCheckpointPromotionIntent)
        self.assertTrue(first.passed)
        self.assertEqual(first.blockers, ())
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.intent.intent_digest), 64)
        self.assertEqual(
            first.intent.intent_payload,
            {
                "format": "harness-ledger-checkpoint-promotion-intent-v1",
                "promotion": {
                    "promotion_id": "promotion-001",
                    "requested_by": "reviewer-001",
                    "target_ledger_id": "ledger-main",
                },
                "run": {"run_id": "run-001", "work_id": "work-001"},
                "checkpoint": {
                    "checkpoint_path": "checkpoints/run-001.json",
                    "checkpoint_digest": _DIGEST,
                    "payload_digest": _DIGEST,
                    "checkpoint_size_bytes": 128,
                },
                "metadata": {"approved_by": "review-board", "priority": 2},
            },
        )
        self.assertEqual(first.summary["checkpoint_path"], ".../run-001.json")
        self.assertEqual(first.summary["metadata_keys"], ("approved_by", "priority"))

    def test_dataclass_and_to_dict_style_readiness_build_equivalent_intent(self) -> None:
        @dataclass(frozen=True)
        class Readiness:
            passed: bool
            blockers: tuple[str, ...]
            work_id: str
            run_id: str
            checkpoint_path: str
            checkpoint_digest: str
            payload_digest: str
            checkpoint_size_bytes: int
            summary: object = None

        dataclass_result = self._build(readiness=Readiness(**self._readiness()))
        to_dict_result = self._build(readiness=ToDictReadiness(self._readiness()))

        self.assertTrue(dataclass_result.passed)
        self.assertEqual(dataclass_result.to_dict(), to_dict_result.to_dict())

    def test_failed_readiness_or_readiness_blockers_fail_closed(self) -> None:
        failed = self._build(readiness=self._readiness(passed=False))
        blocked = self._build(readiness=self._readiness(blockers=("blocked-gate",)))

        self.assertFalse(failed.passed)
        self.assertIsNone(failed.intent)
        self.assertIn("readiness-not-passed", failed.blockers)
        self.assertFalse(blocked.passed)
        self.assertIn("readiness-blockers-present", blocked.blockers)
        self.assertIn("readiness-blocked-gate", blocked.blockers)

    def test_run_work_path_digest_size_and_expected_mismatch_fail_closed(self) -> None:
        cases = (
            (
                self._build(readiness=self._readiness(run_id="run-other")),
                "readiness-run-id-mismatch",
            ),
            (
                self._build(readiness=self._readiness(work_id="work-other")),
                "readiness-work-id-mismatch",
            ),
            (
                self._build(readiness=self._readiness(checkpoint_path="../x.json")),
                "checkpoint-path-unsafe",
            ),
            (
                self._build(readiness=self._readiness(checkpoint_digest="bad")),
                "invalid-checkpoint-digest",
            ),
            (
                self._build(readiness=self._readiness(checkpoint_size_bytes=0)),
                "nonpositive-checkpoint-size-bytes",
            ),
            (
                self._build(expected_checkpoint_digest=_OTHER_DIGEST),
                "expected-checkpoint-digest-mismatch",
            ),
            (
                self._build(expected_payload_digest=_OTHER_DIGEST),
                "expected-payload-digest-mismatch",
            ),
            (
                self._build(expected_checkpoint_path="checkpoints/other.json"),
                "expected-checkpoint-path-mismatch",
            ),
        )

        for result, blocker in cases:
            self.assertFalse(result.passed)
            self.assertIsNone(result.intent)
            self.assertIn(blocker, result.blockers)

    def test_expected_path_matches_after_safe_normalization(self) -> None:
        result = self._build(expected_checkpoint_path="checkpoints\\run-001.json")

        self.assertTrue(result.passed)
        self.assertEqual(result.intent.checkpoint_path, "checkpoints/run-001.json")

    def test_missing_secret_like_or_execution_intent_fields_fail_closed(self) -> None:
        cases = (
            (
                self._build(promotion_id=" "),
                "missing-promotion-id",
            ),
            (
                self._build(requested_by="operator-token"),
                "secret-like-requested-by",
            ),
            (
                self._build(target_ledger_id="ledger`cmd`"),
                "execution-intent-target-ledger-id",
            ),
            (
                self._build(metadata={"api_key": "value"}),
                "secret-like-metadata",
            ),
            (
                self._build(metadata={"cmd": "promote"}),
                "execution-intent-metadata",
            ),
        )

        for result, blocker in cases:
            self.assertFalse(result.passed)
            self.assertIsNone(result.intent)
            self.assertIn(blocker, result.blockers)
            self.assertNotIn("api_key", repr(result.to_dict()))

    def test_nested_or_ambiguous_duplicate_metadata_fails_closed(self) -> None:
        readiness_duplicate = self._build(
            readiness=self._readiness(summary={"checkpoint_digest": _DIGEST})
        )
        metadata_duplicate = self._build(
            metadata={"notes": {"promotion_id": "promotion-001"}}
        )

        self.assertIn("duplicate-readiness-metadata", readiness_duplicate.blockers)
        self.assertIn("duplicate-metadata", metadata_duplicate.blockers)

    def test_caller_mapping_is_not_mutated(self) -> None:
        readiness = self._readiness(extra={"summary": {"safe": ["a", "b"]}})
        metadata = {"approved_by": "review-board", "labels": ["stable", "checked"]}
        before = copy.deepcopy({"readiness": readiness, "metadata": metadata})

        self._build(readiness=readiness, metadata=metadata)

        self.assertEqual({"readiness": readiness, "metadata": metadata}, before)

    def test_source_scan_has_no_forbidden_runtime_service_or_storage_behavior(self) -> None:
        import harness_orchestrator.ledger_checkpoint_promotion_intent as module

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

    def _build(self, readiness: object = None, **overrides: object):
        kwargs = {
            "readiness": self._readiness() if readiness is None else readiness,
            "promotion_id": "promotion-001",
            "requested_by": "reviewer-001",
            "run_id": "run-001",
            "work_id": "work-001",
            "target_ledger_id": "ledger-main",
            "expected_checkpoint_digest": _DIGEST.upper(),
            "expected_payload_digest": _DIGEST,
            "expected_checkpoint_path": "checkpoints/run-001.json",
            "metadata": None,
        }
        kwargs.update(overrides)
        readiness_arg = kwargs.pop("readiness")
        return build_ledger_checkpoint_promotion_intent(readiness_arg, **kwargs)

    def _readiness(self, extra: dict[str, object] | None = None, **overrides: object):
        readiness = {
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
        }
        if extra:
            readiness.update(extra)
        readiness.update(overrides)
        return readiness


if __name__ == "__main__":
    unittest.main()
