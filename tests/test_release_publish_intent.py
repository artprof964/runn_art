import copy
import hashlib
import inspect
import json
from collections.abc import Mapping
from types import MappingProxyType
import unittest

from harness_orchestrator.release_publish_intent import (
    ReleasePublishIntent,
    ReleasePublishIntentResult,
    build_release_publish_intent,
)
from harness_orchestrator.release_publish_readiness import ReleasePublishReadiness


_BINDING_DIGEST = "a1b2c3d4e5f6" + ("0" * 52)
_PAYLOAD_DIGEST = "b" * 64


class ToDictReadiness:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._data)


class AttrReadiness:
    ready = True
    status = "ready"
    blockers = ()
    run_id = "run-001"
    work_id = "work-001"
    dependency_id = "dependency-001"
    event_id = "event-001"
    canonical_digest_prefix = "a1b2c3d4e5f6"
    summary = {"format": "harness-release-publish-readiness-v1"}


class DuplicateTicketMapping(Mapping):
    def __iter__(self):
        return iter(("ticket",))

    def __len__(self) -> int:
        return 2

    def __getitem__(self, key: str) -> str:
        if key == "ticket":
            return "HAR-031"
        raise KeyError(key)

    def items(self):
        return iter((("ticket", "HAR-031"), ("ticket", "HAR-032")))


class ReleasePublishIntentTests(unittest.TestCase):
    def test_happy_path_builds_frozen_deterministic_intent(self) -> None:
        result = self._build()
        again = self._build()

        self.assertIsInstance(result, ReleasePublishIntentResult)
        self.assertTrue(result.passed)
        self.assertEqual((), result.blockers)
        self.assertIsInstance(result.intent, ReleasePublishIntent)
        self.assertEqual(result.intent.intent_digest, again.intent.intent_digest)
        self.assertIsInstance(result.intent.canonical_payload, MappingProxyType)
        self.assertIn(
            "caller_supplied_intent_metadata",
            result.intent.canonical_payload,
        )
        self.assertEqual(
            "caller-supplied-not-cr-har-030-identity",
            result.intent.canonical_payload["caller_supplied_intent_metadata"][
                "verification_status"
            ],
        )
        expected = hashlib.sha256(
            json.dumps(
                result.intent.to_dict()["canonical_payload"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, result.intent.intent_digest)

    def test_accepts_mapping_to_dict_and_attribute_readiness(self) -> None:
        mapping = self._ready().to_dict()

        self.assertTrue(self._build(readiness=mapping).passed)
        self.assertTrue(self._build(readiness=ToDictReadiness(mapping)).passed)
        self.assertTrue(self._build(readiness=AttrReadiness()).passed)

    def test_blocked_readiness_fails_closed(self) -> None:
        ready = self._ready(ready=False, status="blocked", blockers=("not-done",))
        result = self._build(readiness=ready)

        self.assertFalse(result.passed)
        self.assertIsNone(result.intent)
        self.assertIn("readiness-not-ready", result.blockers)
        self.assertIn("readiness-status-not-ready", result.blockers)
        self.assertIn("readiness-blockers-present", result.blockers)
        self.assertIn("readiness-not-done", result.blockers)

    def test_status_and_ready_are_strict(self) -> None:
        loose_ready = self._ready().to_dict()
        loose_ready["ready"] = 1
        status_case = self._ready().to_dict()
        status_case["status"] = "Ready"

        self.assertIn("readiness-not-ready", self._build(readiness=loose_ready).blockers)
        self.assertIn(
            "readiness-status-not-ready",
            self._build(readiness=status_case).blockers,
        )

    def test_run_work_mismatch_and_digest_prefix_mismatch_block(self) -> None:
        run = self._build(run_id="run-002")
        work = self._build(work_id="work-002")
        digest = self._build(release_binding_digest="c" * 64)

        self.assertIn("readiness-run-id-mismatch", run.blockers)
        self.assertIn("readiness-work-id-mismatch", work.blockers)
        self.assertIn("release-binding-digest-prefix-mismatch", digest.blockers)

    def test_digest_formats_are_lowercase_exact_length(self) -> None:
        upper_binding = self._build(release_binding_digest=_BINDING_DIGEST.upper())
        short_prefix = self._ready(canonical_digest_prefix="a" * 11)
        upper_prefix = self._ready(canonical_digest_prefix="A1B2C3D4E5F6")

        self.assertIn("invalid-release-binding-digest", upper_binding.blockers)
        self.assertIn(
            "invalid-readiness-canonical-digest-prefix",
            self._build(readiness=short_prefix).blockers,
        )
        self.assertIn(
            "invalid-readiness-canonical-digest-prefix",
            self._build(readiness=upper_prefix).blockers,
        )

    def test_whitespace_padded_release_binding_digest_fails_closed(self) -> None:
        result = self._build(release_binding_digest=f" {_BINDING_DIGEST} ")

        self.assertFalse(result.passed)
        self.assertIsNone(result.intent)
        self.assertIn("invalid-release-binding-digest", result.blockers)

    def test_whitespace_padded_readiness_digest_prefix_fails_closed(self) -> None:
        result = self._build(
            readiness=self._ready(canonical_digest_prefix=" a1b2c3d4e5f6 ")
        )

        self.assertFalse(result.passed)
        self.assertIsNone(result.intent)
        self.assertIn("invalid-readiness-canonical-digest-prefix", result.blockers)

    def test_schema_exactness_for_caller_inputs_and_readiness(self) -> None:
        ready = self._ready().to_dict()
        ready["canonical_digest"] = "x"
        target = {"target_type": "local-dry-run", "target_id": "target-001", "extra": "x"}
        payload = {"payload_label": "release"}
        artifact = {"artifact_id": "artifact-001", "extra": "x"}

        self.assertIn("unsafe-readiness-schema", self._build(readiness=ready).blockers)
        self.assertIn("unsafe-publish-target-schema", self._build(publish_target=target).blockers)
        self.assertIn("unsafe-publish-payload-schema", self._build(publish_payload=payload).blockers)
        self.assertIn("unsafe-artifact-schema", self._build(artifact=artifact).blockers)

    def test_target_and_text_safety(self) -> None:
        unsupported = self._target(target_type="prod-release")
        url_target = self._target(target_id="https://example.test/release")
        handle_target = self._target(target_id="@release")
        path_target = self._target(target_id="release/path")

        self.assertIn(
            "unsupported-publish-target-type",
            self._build(publish_target=unsupported).blockers,
        )
        self.assertIn("unsafe-publish-target-id", self._build(publish_target=url_target).blockers)
        self.assertIn("unsafe-publish-target-id", self._build(publish_target=handle_target).blockers)
        self.assertIn("unsafe-publish-target-id", self._build(publish_target=path_target).blockers)

    def test_metadata_reserved_secret_action_nesting_and_non_string_keys(self) -> None:
        cases = (
            ({"run_id": "run-001"}, "reserved-metadata-key"),
            ({"api_key": "value"}, "secret-like-metadata"),
            ({"note": "contains token"}, "secret-like-metadata"),
            ({"deploy": "run command now"}, "action-intent-metadata"),
            ({"details": {"nested": "bad"}}, "nested-metadata"),
            ({1: "bad"}, "non-string-metadata-key"),
            ({"owner": 1.5}, "invalid-metadata-value"),
        )
        for metadata, blocker in cases:
            with self.subTest(blocker=blocker):
                self.assertIn(blocker, self._build(metadata=metadata).blockers)

    def test_duplicate_metadata_keys_fail_closed(self) -> None:
        result = self._build(metadata=DuplicateTicketMapping())

        self.assertFalse(result.passed)
        self.assertIsNone(result.intent)
        self.assertIn("duplicate-metadata-key", result.blockers)

    def test_result_is_plain_json_safe_immutable_and_caller_mutation_safe(self) -> None:
        target = self._target()
        payload = self._payload()
        artifact = self._artifact()
        metadata = {"ticket": "HAR-031", "approved": True, "count": 2}

        result = self._build(
            publish_target=target,
            publish_payload=payload,
            artifact=artifact,
            metadata=metadata,
        )
        target["target_id"] = "changed"
        payload["payload_label"] = "changed"
        artifact["artifact_id"] = "changed"
        metadata["ticket"] = "changed"
        plain = result.to_dict()

        json.dumps(plain, sort_keys=True)
        self.assertEqual("target-001", plain["intent"]["publish_target"]["target_id"])
        self.assertEqual("release", plain["intent"]["publish_payload"]["payload_label"])
        self.assertEqual("artifact-001", plain["intent"]["artifact"]["artifact_id"])
        self.assertEqual("HAR-031", plain["intent"]["metadata"]["ticket"])
        with self.assertRaises(TypeError):
            result.intent.metadata["ticket"] = "changed"
        with self.assertRaises(TypeError):
            result.intent.canonical_payload["format"] = "changed"

    def test_forbidden_source_scan(self) -> None:
        import harness_orchestrator.release_publish_intent as module

        source = inspect.getsource(module)
        forbidden_tokens = (
            "RunLedger(",
            ".save(",
            ".load(",
            "read_text",
            "write_text",
            "requests",
            "urllib",
            "http.client",
            "socket",
            "subprocess",
            "os.environ",
            "importlib",
            "pkg_resources",
            "ai_art",
            "maraca",
            "coordinator",
            "scheduler",
            "watch",
            "service",
            "client",
            "random",
            "datetime",
        )
        for token in forbidden_tokens:
            self.assertNotIn(token, source)

    def _build(self, readiness: object = None, **overrides: object) -> ReleasePublishIntentResult:
        kwargs = {
            "readiness": self._ready() if readiness is None else readiness,
            "run_id": "run-001",
            "work_id": "work-001",
            "release_binding_digest": _BINDING_DIGEST,
            "publish_target": self._target(),
            "publish_payload": self._payload(),
            "artifact": self._artifact(),
            "metadata": {"ticket": "HAR-031"},
        }
        kwargs.update(overrides)
        return build_release_publish_intent(**kwargs)

    def _ready(self, **overrides: object) -> ReleasePublishReadiness:
        data = {
            "ready": True,
            "status": "ready",
            "blockers": (),
            "run_id": "run-001",
            "work_id": "work-001",
            "dependency_id": "dependency-001",
            "event_id": "event-001",
            "canonical_digest_prefix": "a1b2c3d4e5f6",
            "summary": {"format": "harness-release-publish-readiness-v1"},
        }
        data.update(overrides)
        return ReleasePublishReadiness(**data)

    def _target(self, **overrides: object) -> dict[str, object]:
        data = {"target_type": "local-dry-run", "target_id": "target-001"}
        data.update(overrides)
        return data

    def _payload(self, **overrides: object) -> dict[str, object]:
        data = {"payload_digest": _PAYLOAD_DIGEST, "payload_label": "release"}
        data.update(overrides)
        return data

    def _artifact(self, **overrides: object) -> dict[str, object]:
        data = {"artifact_id": "artifact-001"}
        data.update(overrides)
        return data


if __name__ == "__main__":
    unittest.main()
