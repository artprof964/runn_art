import copy
from dataclasses import dataclass
import inspect
import unittest

from harness_orchestrator.identity_proof import (
    IdentityProofResult,
    build_identity_proof,
)


_CHECKPOINT_DIGEST = "a" * 64
_PAYLOAD_DIGEST = "b" * 64
_INTENT_DIGEST = "c" * 64


class ToDictRecord:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


class IdentityProofTests(unittest.TestCase):
    def test_pass_path_builds_frozen_deterministic_proof(self) -> None:
        result = self._build()

        self.assertIsInstance(result, IdentityProofResult)
        self.assertTrue(result.passed)
        self.assertEqual(result.blockers, ())
        self.assertEqual(len(result.canonical_digest), 64)
        self.assertEqual(result.canonical_payload["format"], "harness-identity-proof-v1")
        self.assertEqual(result.canonical_payload["expected"]["work_id"], "work-001")
        self.assertEqual(result.summary["source_record_count"], 6)

    def test_dataclass_and_to_dict_records_are_accepted(self) -> None:
        @dataclass(frozen=True)
        class WorkRecord:
            work_id: str
            request_id: str
            blockers: tuple[str, ...]

        records = (
            WorkRecord("work-001", "request-001", ()),
            ToDictRecord(
                {
                    "work_id": "work-001",
                    "run_id": "run-001",
                    "checkpoint_path": "checkpoints/run-001.json",
                    "checkpoint_digest": _CHECKPOINT_DIGEST,
                    "payload_digest": _PAYLOAD_DIGEST,
                    "blockers": (),
                }
            ),
            self._evidence_record(),
            self._media_record(),
            self._artifact_record(),
            self._promotion_record(),
        )

        result = self._build(records=records)

        self.assertTrue(result.passed)

    def test_work_mismatch_fails_closed(self) -> None:
        result = self._build(records=self._records(work_id="work-other"))

        self.assertFalse(result.passed)
        self.assertIn("work-id-mismatch", result.blockers)

    def test_evidence_media_and_artifact_mismatch_fail_closed(self) -> None:
        evidence = self._build(evidence_bundle_id="bundle-other")
        media = self._build(media_ids=("media-other",))
        artifact = self._build(artifact_ids=("artifact-other",))

        self.assertIn("evidence-bundle-id-mismatch", evidence.blockers)
        self.assertIn("media-ids-mismatch", media.blockers)
        self.assertIn("artifact-ids-mismatch", artifact.blockers)

    def test_checkpoint_digest_mismatch_fails_closed(self) -> None:
        result = self._build(checkpoint_digest="d" * 64)

        self.assertFalse(result.passed)
        self.assertIn("checkpoint-digest-mismatch", result.blockers)

    def test_duplicate_nested_identity_metadata_fails_closed(self) -> None:
        result = self._build(
            records=self._records(
                extra_work={"metadata": {"work_id": "work-001", "note": "duplicate"}}
            )
        )

        self.assertFalse(result.passed)
        self.assertIn("duplicate-identity-metadata", result.blockers)

    def test_blocked_source_record_fails_closed(self) -> None:
        result = self._build(records=self._records(extra_work={"blockers": ("gate",)}))

        self.assertFalse(result.passed)
        self.assertIn("source-blockers-present", result.blockers)
        self.assertIn("source-gate", result.blockers)

    def test_nested_source_blockers_fail_closed(self) -> None:
        records = list(self._records())
        records[3] = {
            "work_id": "work-001",
            "media_items": (
                {
                    "media_id": "media-001",
                    "work_id": "work-001",
                    "checks": {"blockers": ("nested-media-block",)},
                },
                {"media_id": "media-002", "work_id": "work-001"},
            ),
            "blockers": (),
        }

        result = self._build(records=records)

        self.assertFalse(result.passed)
        self.assertIn("source-blockers-present", result.blockers)
        self.assertIn("source-nested-media-block", result.blockers)

    def test_conflicting_observed_identity_values_fail_closed(self) -> None:
        records = list(self._records())
        records[2] = self._evidence_record(work_id="work-other")

        result = self._build(records=records)

        self.assertFalse(result.passed)
        self.assertIn("conflicting-source-work-id", result.blockers)
        self.assertIn("ambiguous-source-work-id", result.blockers)

    def test_secret_and_execution_intent_fail_closed(self) -> None:
        secret = self._build(records=self._records(extra_work={"api_key": "value"}))
        intent = self._build(records=self._records(extra_work={"cmd": "run"}))

        self.assertFalse(secret.passed)
        self.assertIn("secret-like-source-records", secret.blockers)
        self.assertFalse(intent.passed)
        self.assertIn("execution-intent-source-records", intent.blockers)
        self.assertNotIn("api_key", repr(secret.summary))
        self.assertNotIn("api_key", repr(secret.to_dict()))

    def test_execution_flag_keys_fail_closed(self) -> None:
        cases = (
            self._records(extra_work={"execute": True}),
            self._records(extra_work={"should_execute": True}),
            self._records(extra_work={"execution_intent": True}),
        )

        for records in cases:
            result = self._build(records=records)

            self.assertFalse(result.passed)
            self.assertIn("execution-intent-source-records", result.blockers)

    def test_nested_execution_flag_fails_closed_without_mutation(self) -> None:
        records = list(self._records())
        records[3] = {
            "work_id": "work-001",
            "media_items": (
                {
                    "media_id": "media-001",
                    "work_id": "work-001",
                    "review": {"execution": True},
                },
                {"media_id": "media-002", "work_id": "work-001"},
            ),
            "blockers": (),
        }
        before = copy.deepcopy(records)

        result = self._build(records=records)

        self.assertFalse(result.passed)
        self.assertIn("execution-intent-source-records", result.blockers)
        self.assertEqual(records, before)

    def test_secret_identity_values_are_redacted_from_canonical_and_to_dict(self) -> None:
        records = list(self._records())
        records[0] = {
            "work_id": "token-work-001",
            "request_id": "request-001",
            "blockers": (),
        }

        result = self._build(records=records)

        self.assertFalse(result.passed)
        self.assertIn("secret-like-source-records", result.blockers)
        self.assertNotIn("token-work-001", repr(result.canonical_payload))
        self.assertNotIn("token-work-001", repr(result.to_dict()))
        self.assertIn("<redacted>", repr(result.canonical_payload["observed"]))

    def test_caller_records_are_not_mutated(self) -> None:
        records = list(self._records())
        before = copy.deepcopy(records)

        self._build(records=records)

        self.assertEqual(records, before)

    def test_digest_is_deterministic_for_mapping_order(self) -> None:
        first = self._build()
        second = self._build(records=tuple(reversed(self._records())))

        self.assertEqual(first.canonical_digest, second.canonical_digest)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_malformed_data_fails_closed(self) -> None:
        result = self._build(records=(object(),))

        self.assertFalse(result.passed)
        self.assertIn("malformed-source-records", result.blockers)

    def test_source_scan_has_no_forbidden_behavior(self) -> None:
        import harness_orchestrator.identity_proof as module

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

    def _build(self, records: object = None, **overrides: object) -> IdentityProofResult:
        kwargs = {
            "source_records": self._records() if records is None else records,
            "work_id": "work-001",
            "run_id": "run-001",
            "request_id": "request-001",
            "evidence_bundle_id": "bundle-001",
            "media_ids": ("media-001", "media-002"),
            "artifact_ids": ("artifact-001",),
            "checkpoint_path": "checkpoints/run-001.json",
            "checkpoint_digest": _CHECKPOINT_DIGEST.upper(),
            "payload_digest": _PAYLOAD_DIGEST,
            "promotion_intent_digest": _INTENT_DIGEST,
        }
        kwargs.update(overrides)
        source_records = kwargs.pop("source_records")
        return build_identity_proof(source_records, **kwargs)

    def _records(
        self,
        *,
        work_id: str = "work-001",
        extra_work: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], ...]:
        work = {"work_id": work_id, "request_id": "request-001", "blockers": ()}
        if extra_work:
            work.update(extra_work)
        return (
            work,
            self._checkpoint_record(work_id=work_id),
            self._evidence_record(work_id=work_id),
            self._media_record(work_id=work_id),
            self._artifact_record(work_id=work_id),
            self._promotion_record(work_id=work_id),
        )

    def _checkpoint_record(self, *, work_id: str = "work-001") -> dict[str, object]:
        return {
            "work_id": work_id,
            "run_id": "run-001",
            "checkpoint_path": "checkpoints/run-001.json",
            "checkpoint_digest": _CHECKPOINT_DIGEST,
            "payload_digest": _PAYLOAD_DIGEST,
            "blockers": (),
        }

    def _evidence_record(self, *, work_id: str = "work-001") -> dict[str, object]:
        return {
            "work_id": work_id,
            "request_id": "request-001",
            "evidence_bundle_id": "bundle-001",
            "blockers": (),
        }

    def _media_record(self, *, work_id: str = "work-001") -> dict[str, object]:
        return {
            "work_id": work_id,
            "media_items": (
                {"media_id": "media-001", "work_id": work_id},
                {"media_id": "media-002", "work_id": work_id},
            ),
            "blockers": (),
        }

    def _artifact_record(self, *, work_id: str = "work-001") -> dict[str, object]:
        return {
            "work_id": work_id,
            "artifact_ids": ("artifact-001",),
            "blockers": (),
        }

    def _promotion_record(self, *, work_id: str = "work-001") -> dict[str, object]:
        return {
            "work_id": work_id,
            "promotion_intent_digest": _INTENT_DIGEST,
            "blockers": (),
        }


if __name__ == "__main__":
    unittest.main()
