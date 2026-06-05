from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
import copy
import unittest

from harness_orchestrator.maraca_runtime_invocation import (
    REDACTED,
    MaracaRuntimeInvocationRequest,
    MaracaRuntimeInvocationRequirements,
    MaracaRuntimeInvocationResult,
    prepare_maraca_runtime_invocation,
)


def _preflight(ready: bool = True, work_id: str = "work-1", run_id: str = "work-1"):
    return {
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "blockers": () if ready else ("blocked-gate:policy",),
        "summary": {
            "work_id": work_id,
            "run_id": run_id,
        },
    }


def _readiness(ready: bool = True, work_id: str = "work-1", run_id: str = "work-1"):
    return {
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "blockers": () if ready else ("missing-package:langgraph",),
        "work_id": work_id,
        "run_id": run_id,
    }


def _result(**overrides):
    values = {
        "work_id": "work-1",
        "run_id": "work-1",
        "operation": "prepare-artist-memory",
        "payload": {"topic": "harness boundary", "items": ({"id": "item-1"},)},
        "preflight": _preflight(),
        "maraca_readiness": _readiness(),
        "runtime_settings": {"collection": "collection-a"},
        "runtime_config": {"database": "neo4j"},
        "metadata": {"source": "unit-test"},
    }
    values.update(overrides)
    return prepare_maraca_runtime_invocation(**values)


class MaracaRuntimeInvocationTests(unittest.TestCase):
    def test_happy_path_returns_ready_redacted_plain_envelope(self) -> None:
        result = _result()
        serialized = result.to_dict()

        self.assertTrue(result.ready)
        self.assertEqual("ready", result.status)
        self.assertEqual((), result.blockers)
        self.assertIsInstance(result.envelope, MaracaRuntimeInvocationRequest)
        self.assertEqual("work-1", serialized["envelope"]["work_id"])
        self.assertEqual("prepare-artist-memory", serialized["envelope"]["operation"])
        self.assertEqual("collection-a", serialized["envelope"]["runtime_settings"]["collection"])

    def test_missing_identity_operation_and_payload_fail_closed(self) -> None:
        result = _result(work_id=" ", run_id=None, operation="", payload=None)

        self.assertFalse(result.ready)
        self.assertIsNone(result.envelope)
        self.assertIn("missing-work-id", result.blockers)
        self.assertIn("missing-run-id", result.blockers)
        self.assertIn("missing-operation", result.blockers)
        self.assertIn("missing-payload", result.blockers)

    def test_empty_payload_fails_closed(self) -> None:
        result = _result(payload={})

        self.assertFalse(result.ready)
        self.assertIn("missing-payload", result.blockers)

    def test_failed_or_missing_preflight_and_readiness_fail_closed(self) -> None:
        failed = _result(preflight=_preflight(False), maraca_readiness=_readiness(False))
        missing = _result(preflight=None, maraca_readiness=None)

        self.assertFalse(failed.ready)
        self.assertIn("blocked-preflight", failed.blockers)
        self.assertIn("blocked-maraca-readiness", failed.blockers)
        self.assertIn("missing-preflight", missing.blockers)
        self.assertIn("missing-maraca-readiness", missing.blockers)

    def test_work_and_run_mismatches_fail_closed(self) -> None:
        direct = _result(run_id="other-work")
        preflight = _result(preflight=_preflight(work_id="other-work"))
        readiness = _result(maraca_readiness=_readiness(run_id="other-work"))

        self.assertIn("work-id-run-id-mismatch", direct.blockers)
        self.assertIn("preflight-work-id-mismatch", preflight.blockers)
        self.assertIn("preflight-work-id-run-id-mismatch", preflight.blockers)
        self.assertIn("maraca-readiness-run-id-mismatch", readiness.blockers)
        self.assertIn("maraca-readiness-work-id-run-id-mismatch", readiness.blockers)

    def test_secret_like_names_and_values_are_blocking_and_redacted(self) -> None:
        result = _result(
            payload={"visible": "ok", "api_key": "raw-key"},
            runtime_settings={"endpoint": "contains-token-value"},
            runtime_config={"password": "abc123"},
            metadata={"nested": {"client_secret": "secret-value"}},
        )
        serialized_text = repr(result.to_dict())

        self.assertFalse(result.ready)
        self.assertIn("redacted-payload-name", result.blockers)
        self.assertIn("redacted-payload-value", result.blockers)
        self.assertIn("redacted-runtime-settings-value", result.blockers)
        self.assertIn("redacted-runtime-config-name", result.blockers)
        self.assertIn("redacted-metadata-name", result.blockers)
        self.assertIn(REDACTED, serialized_text)
        self.assertNotIn("api_key", serialized_text)
        self.assertNotIn("raw-key", serialized_text)
        self.assertNotIn("contains-token-value", serialized_text)
        self.assertNotIn("client_secret", serialized_text)
        self.assertNotIn("secret-value", serialized_text)

    def test_nested_request_like_objects_are_blocked_and_redacted(self) -> None:
        nested_request = MaracaRuntimeInvocationRequest(
            work_id="work-1",
            run_id="work-1",
            operation="nested",
            payload={
                "api_key": "raw-key-value",
                "execute": True,
            },
            preflight=_preflight(),
            maraca_readiness=_readiness(),
            runtime_settings={"endpoint": "contains-token-value"},
            runtime_config={"password": "raw-password-value"},
            metadata={"client_secret": "raw-secret-value"},
            requirements=MaracaRuntimeInvocationRequirements(),
        )

        result = _result(payload={"nested": nested_request})
        serialized_text = repr(result.to_dict())

        self.assertFalse(result.ready)
        self.assertIn("redacted-payload-name", result.blockers)
        self.assertIn("redacted-payload-value", result.blockers)
        self.assertIn("execution-flag:payload", result.blockers)
        self.assertIn(REDACTED, serialized_text)
        for raw in (
            "api_key",
            "raw-key-value",
            "contains-token-value",
            "password",
            "raw-password-value",
            "client_secret",
            "raw-secret-value",
        ):
            self.assertNotIn(raw, serialized_text)

    def test_execution_flags_are_blocking(self) -> None:
        result = _result(
            payload={"execute": True},
            runtime_settings={"should_run": True},
            runtime_config={"dispatch": 1},
            metadata={"publish": "yes"},
        )

        self.assertFalse(result.ready)
        self.assertIn("execution-flag:payload", result.blockers)
        self.assertIn("execution-flag:runtime-settings", result.blockers)
        self.assertIn("execution-flag:runtime-config", result.blockers)
        self.assertIn("execution-flag:metadata", result.blockers)

    def test_records_are_frozen_dataclasses_and_serialize_plain_data(self) -> None:
        for record_type in (
            MaracaRuntimeInvocationRequirements,
            MaracaRuntimeInvocationRequest,
            MaracaRuntimeInvocationResult,
        ):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        result = _result()
        serialized = result.to_dict()

        self.assertIsInstance(serialized, dict)
        self.assertIsInstance(serialized["requirements"], dict)
        self.assertIsInstance(serialized["envelope"], dict)
        self.assertIsInstance(serialized["envelope"]["payload"], dict)
        with self.assertRaises(FrozenInstanceError):
            result.status = "changed"  # type: ignore[misc]

    def test_accepts_objects_with_to_dict(self) -> None:
        class SnapshotObject:
            def __init__(self, value):
                self.value = value

            def to_dict(self):
                return self.value

        result = _result(
            payload=SnapshotObject({"topic": "object-data"}),
            preflight=SnapshotObject(_preflight()),
            maraca_readiness=SnapshotObject(_readiness()),
        )

        self.assertTrue(result.ready)

    def test_caller_mappings_are_not_mutated(self) -> None:
        payload = {"items": [{"id": "item-1"}]}
        preflight = _preflight()
        readiness = _readiness()
        settings = {"collection": "collection-a"}
        config = {"database": "neo4j"}
        metadata = {"source": "unit-test"}
        before = copy.deepcopy(
            {
                "payload": payload,
                "preflight": preflight,
                "readiness": readiness,
                "settings": settings,
                "config": config,
                "metadata": metadata,
            },
        )

        _result(
            payload=payload,
            preflight=preflight,
            maraca_readiness=readiness,
            runtime_settings=settings,
            runtime_config=config,
            metadata=metadata,
        )

        self.assertEqual(before["payload"], payload)
        self.assertEqual(before["preflight"], preflight)
        self.assertEqual(before["readiness"], readiness)
        self.assertEqual(before["settings"], settings)
        self.assertEqual(before["config"], config)
        self.assertEqual(before["metadata"], metadata)

    def test_malformed_config_settings_and_metadata_fail_closed(self) -> None:
        result = _result(
            runtime_settings=object(),
            runtime_config=("bad",),
            metadata=7,
        )

        self.assertFalse(result.ready)
        self.assertIn("malformed-runtime-settings", result.blockers)
        self.assertIn("malformed-runtime-config", result.blockers)
        self.assertIn("malformed-metadata", result.blockers)

    def test_source_has_no_forbidden_runtime_behavior(self) -> None:
        source = Path(
            "src/harness_orchestrator/maraca_runtime_invocation.py",
        ).read_text(encoding="utf-8")

        forbidden = (
            "os.environ",
            "getenv",
            "importlib",
            "pkg_resources",
            "requests",
            "httpx",
            "socket",
            "subprocess",
            "urllib",
            "open(",
            "read_text(",
            "write_text(",
            "RunLedger(",
            ".save(",
            ".load(",
            "MARACA.",
            "import maraca",
            "from maraca",
            "AI-Art",
            "AI_Artist",
            "scheduler",
            "watch_social",
            "Client(",
            "Service(",
        )
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
