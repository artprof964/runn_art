import copy
from dataclasses import dataclass
import inspect
from types import MappingProxyType
import unittest

from harness_orchestrator.contracts import GateDecision
from harness_orchestrator.identity_proof import IdentityProofResult
from harness_orchestrator.release_identity_binding import (
    ReleaseIdentityBindingResult,
    build_release_identity_binding,
)


_PAYLOAD_DIGEST = "a" * 64
_CHECKPOINT_DIGEST = "b" * 64
_INTENT_DIGEST = "c" * 64
_PROOF_DIGEST = "d" * 64


class ToDictRecord:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


class ReleaseIdentityBindingTests(unittest.TestCase):
    def test_pass_path_builds_frozen_plain_result(self) -> None:
        result = self._build()

        self.assertIsInstance(result, ReleaseIdentityBindingResult)
        self.assertTrue(result.passed)
        self.assertEqual(result.blockers, ())
        self.assertEqual(len(result.canonical_digest), 64)
        self.assertEqual(
            result.canonical_payload["format"],
            "harness-release-identity-binding-v1",
        )
        self.assertEqual(result.summary["work_id"], "work-001")

    def test_plain_equivalent_inputs_are_accepted(self) -> None:
        result = self._build(
            decision=ToDictRecord(self._decision().to_dict()),
            proof=ToDictRecord(self._proof().to_dict()),
        )

        self.assertTrue(result.passed)

    def test_dataclass_plain_equivalent_inputs_are_accepted(self) -> None:
        @dataclass(frozen=True)
        class PlainProof:
            passed: bool
            blockers: tuple[str, ...]
            canonical_digest: str
            canonical_payload: dict[str, object]
            summary: dict[str, object]

        proof = PlainProof(
            passed=True,
            blockers=(),
            canonical_digest=_PROOF_DIGEST,
            canonical_payload=self._proof_payload(),
            summary={"work_id": "work-001"},
        )

        result = self._build(proof=proof)

        self.assertTrue(result.passed)

    def test_malformed_and_failed_inputs_fail_closed(self) -> None:
        malformed = self._build(decision=object())
        failed_gate = self._build(decision=self._decision(passed=False))
        failed_proof = self._build(proof=self._proof(passed=False))
        wrong_gate = self._build(decision=self._decision(gate_name="other-gate"))
        bad_digest = self._build(proof=self._proof(canonical_digest=""))

        self.assertIn("malformed-gate-decision", malformed.blockers)
        self.assertIn("gate-not-passed", failed_gate.blockers)
        self.assertIn("proof-not-passed", failed_proof.blockers)
        self.assertIn("release-gate-mismatch", wrong_gate.blockers)
        self.assertIn("missing-proof-canonical-digest", bad_digest.blockers)

    def test_gate_and_proof_blockers_fail_closed(self) -> None:
        gate = self._build(decision=self._decision(blockers=("rights",)))
        proof = self._build(proof=self._proof(blockers=("identity",)))

        self.assertFalse(gate.passed)
        self.assertIn("gate-blockers-present", gate.blockers)
        self.assertIn("gate-rights", gate.blockers)
        self.assertFalse(proof.passed)
        self.assertIn("proof-blockers-present", proof.blockers)
        self.assertIn("proof-identity", proof.blockers)

    def test_identity_mismatch_classes_fail_closed(self) -> None:
        decision = self._build(decision=self._decision(work_id="work-999"))
        proof = self._build(proof=self._proof(work_id="work-999"))
        media = self._build(media_ids=("media-999",))
        artifact = self._build(artifact_ids=("artifact-999",))
        evidence = self._build(evidence_bundle_id="bundle-999")
        payload = self._build(payload_digest="9" * 64)
        checkpoint = self._build(checkpoint_digest="8" * 64)
        intent = self._build(promotion_intent_digest="7" * 64)

        self.assertIn("gate-work-id-mismatch", decision.blockers)
        self.assertIn("proof-work-id-mismatch", proof.blockers)
        self.assertIn("gate-media-ids-mismatch", media.blockers)
        self.assertIn("proof-media-ids-mismatch", media.blockers)
        self.assertIn("gate-artifact-ids-mismatch", artifact.blockers)
        self.assertIn("proof-artifact-ids-mismatch", artifact.blockers)
        self.assertIn("gate-evidence-bundle-id-mismatch", evidence.blockers)
        self.assertIn("proof-evidence-bundle-id-mismatch", evidence.blockers)
        self.assertIn("gate-payload-digest-mismatch", payload.blockers)
        self.assertIn("proof-payload-digest-mismatch", payload.blockers)
        self.assertIn("gate-checkpoint-digest-mismatch", checkpoint.blockers)
        self.assertIn("proof-checkpoint-digest-mismatch", checkpoint.blockers)
        self.assertIn("gate-promotion-intent-digest-mismatch", intent.blockers)
        self.assertIn("proof-promotion-intent-digest-mismatch", intent.blockers)

    def test_missing_optional_expected_identity_in_source_fails_closed(self) -> None:
        decision = self._decision().to_dict()
        del decision["metadata"]["payload_digest"]

        result = self._build(decision=decision)

        self.assertFalse(result.passed)
        self.assertIn("missing-gate-payload-digest", result.blockers)

    def test_conflicting_visible_identity_fails_closed(self) -> None:
        decision = self._decision(metadata={"work_id": "work-999"})

        result = self._build(decision=decision)

        self.assertFalse(result.passed)
        self.assertIn("conflicting-gate-work-id", result.blockers)

    def test_ambiguous_nested_identity_metadata_fails_closed(self) -> None:
        decision = self._decision(
            metadata={"nested": {"metadata": {"media_ids": ("media-001", "media-002")}}}
        )
        proof_payload = self._proof_payload()
        proof_payload["metadata"] = {"work_id": "work-001"}

        gate_result = self._build(decision=decision)
        proof_result = self._build(proof=self._proof(canonical_payload=proof_payload))

        self.assertIn("ambiguous-gate-decision-identity-metadata", gate_result.blockers)
        self.assertIn("ambiguous-identity-proof-identity-metadata", proof_result.blockers)

    def test_secret_and_execution_intent_are_rejected(self) -> None:
        secret = self._build(decision=self._decision(metadata={"api_key": "value"}))
        command = self._build(decision=self._decision(metadata={"cmd": "run"}))
        flag = self._build(proof=self._proof(canonical_payload={"execution": True}))
        false_flag = self._build(
            proof=self._proof(canonical_payload={"execution": False})
        )
        value = self._build(request_id="execute release")

        self.assertIn("secret-like-gate-decision", secret.blockers)
        self.assertIn("execution-intent-gate-decision", command.blockers)
        self.assertIn("execution-intent-identity-proof", flag.blockers)
        self.assertIn("execution-intent-identity-proof", false_flag.blockers)
        self.assertIn("execution-intent-request-id", value.blockers)
        self.assertNotIn("api_key", repr(secret.to_dict()))

    def test_digest_is_deterministic_for_mapping_order(self) -> None:
        first = self._build()
        second = self._build(
            decision={
                "metadata": dict(reversed(list(self._decision().metadata.items()))),
                "reviewer": None,
                "evidence_bundle_id": "bundle-001",
                "blockers": (),
                "reason": "released",
                "passed": True,
                "gate_name": "ai-art-media-release",
                "work_id": "work-001",
                "decision_id": "decision-001",
            },
            proof=self._proof(
                canonical_payload={
                    "records": tuple(reversed(self._proof_payload()["records"])),
                    "expected": self._proof_payload()["expected"],
                    "format": "harness-identity-proof-v1",
                }
            ),
        )

        self.assertEqual(first.canonical_digest, second.canonical_digest)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_caller_inputs_are_not_mutated(self) -> None:
        decision = self._decision().to_dict()
        proof = self._proof().to_dict()
        original_decision = copy.deepcopy(decision)
        original_proof = copy.deepcopy(proof)

        self._build(decision=decision, proof=proof)

        self.assertEqual(decision, original_decision)
        self.assertEqual(proof, original_proof)

    def test_returned_payload_and_summary_are_recursively_immutable(self) -> None:
        result = self._build()
        digest = result.canonical_digest

        self.assertIsInstance(result.canonical_payload, MappingProxyType)
        self.assertIsInstance(result.summary, MappingProxyType)
        self.assertIsInstance(result.canonical_payload["expected"], MappingProxyType)
        self.assertIsInstance(
            result.canonical_payload["identity_proof"]["canonical_payload"],
            MappingProxyType,
        )

        with self.assertRaises(TypeError):
            result.canonical_payload["format"] = "changed"
        with self.assertRaises(TypeError):
            result.canonical_payload["expected"]["work_id"] = "changed"
        with self.assertRaises(TypeError):
            result.canonical_payload["identity_proof"]["canonical_payload"][
                "format"
            ] = "changed"
        with self.assertRaises(TypeError):
            result.summary["work_id"] = "changed"

        copied = result.to_dict()
        copied["canonical_payload"]["expected"]["work_id"] = "changed"
        copied["summary"]["work_id"] = "changed"

        self.assertEqual(result.canonical_payload["expected"]["work_id"], "work-001")
        self.assertEqual(result.summary["work_id"], "work-001")
        self.assertEqual(result.canonical_digest, digest)

    def test_forbidden_source_scan(self) -> None:
        import harness_orchestrator.release_identity_binding as module

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

    def _build(
        self,
        *,
        decision: object = None,
        proof: object = None,
        **overrides: object,
    ) -> ReleaseIdentityBindingResult:
        kwargs = {
            "gate_decision": self._decision() if decision is None else decision,
            "identity_proof": self._proof() if proof is None else proof,
            "work_id": "work-001",
            "request_id": "request-001",
            "evidence_bundle_id": "bundle-001",
            "media_ids": ("media-001", "media-002"),
            "artifact_ids": ("artifact-001",),
            "payload_digest": _PAYLOAD_DIGEST,
            "checkpoint_digest": _CHECKPOINT_DIGEST,
            "promotion_intent_digest": _INTENT_DIGEST,
        }
        kwargs.update(overrides)
        return build_release_identity_binding(**kwargs)

    def _decision(
        self,
        *,
        work_id: str = "work-001",
        passed: bool = True,
        gate_name: str = "ai-art-media-release",
        blockers: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> GateDecision:
        release_metadata = {
            "request_id": "request-001",
            "media_ids": ("media-001", "media-002"),
            "artifact_ids": ("artifact-001",),
            "payload_digest": _PAYLOAD_DIGEST,
            "checkpoint_digest": _CHECKPOINT_DIGEST,
            "promotion_intent_digest": _INTENT_DIGEST,
        }
        if metadata:
            release_metadata.update(metadata)
        return GateDecision(
            decision_id="decision-001",
            work_id=work_id,
            gate_name=gate_name,
            passed=passed,
            reason="released",
            blockers=blockers,
            evidence_bundle_id="bundle-001",
            metadata=release_metadata,
        )

    def _proof(
        self,
        *,
        work_id: str = "work-001",
        passed: bool = True,
        blockers: tuple[str, ...] = (),
        canonical_digest: str = _PROOF_DIGEST,
        canonical_payload: dict[str, object] | None = None,
    ) -> IdentityProofResult:
        payload = self._proof_payload(work_id=work_id)
        if canonical_payload is not None:
            payload = canonical_payload
        return IdentityProofResult(
            passed=passed,
            blockers=blockers,
            canonical_payload=payload,
            canonical_digest=canonical_digest,
            summary={"work_id": work_id, "request_id": "request-001"},
        )

    def _proof_payload(self, *, work_id: str = "work-001") -> dict[str, object]:
        return {
            "format": "harness-identity-proof-v1",
            "expected": {
                "work_id": work_id,
                "request_id": "request-001",
                "evidence_bundle_id": "bundle-001",
                "media_ids": ("media-001", "media-002"),
                "artifact_ids": ("artifact-001",),
                "payload_digest": _PAYLOAD_DIGEST,
                "checkpoint_digest": _CHECKPOINT_DIGEST,
                "promotion_intent_digest": _INTENT_DIGEST,
            },
            "records": (
                {
                    "work_id": work_id,
                    "request_id": "request-001",
                    "evidence_bundle_id": "bundle-001",
                    "payload_digest": _PAYLOAD_DIGEST,
                },
                {
                    "work_id": work_id,
                    "media_ids": ("media-001", "media-002"),
                    "artifact_ids": ("artifact-001",),
                    "checkpoint_digest": _CHECKPOINT_DIGEST,
                    "promotion_intent_digest": _INTENT_DIGEST,
                },
            ),
        }


if __name__ == "__main__":
    unittest.main()
