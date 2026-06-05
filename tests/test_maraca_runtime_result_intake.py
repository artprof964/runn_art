from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, is_dataclass
from pathlib import Path
import copy
import unittest

from harness_orchestrator.maraca_runtime_invocation import prepare_maraca_runtime_invocation
from harness_orchestrator.maraca_runtime_result_intake import (
    ALLOWED_RUNTIME_STATUSES,
    REDACTED,
    MaracaRuntimeResultIntakeRequirements,
    MaracaRuntimeResultIntakeResult,
    MaracaRuntimeResultRecord,
    intake_maraca_runtime_result,
)


def _preflight():
    return {"ready": True, "summary": {"work_id": "work-1", "run_id": "work-1"}}


def _readiness():
    return {"ready": True, "work_id": "work-1", "run_id": "work-1"}


def _invocation():
    return prepare_maraca_runtime_invocation(
        work_id="work-1",
        run_id="work-1",
        operation="collect-evidence",
        payload={"topic": "boundary"},
        preflight=_preflight(),
        maraca_readiness=_readiness(),
    )


def _runtime_result(**overrides):
    value = {
        "work_id": "work-1",
        "run_id": "work-1",
        "operation": "collect-evidence",
        "status": "succeeded",
        "evidence_items": ({"source_id": "source-1", "claim": "plain"},),
        "output": {"summary": "complete"},
        "metadata": {"source": "unit-test"},
    }
    value.update(overrides)
    return value


def _result(**overrides):
    invocation = overrides.pop("invocation", _invocation())
    runtime_result = overrides.pop("runtime_result", _runtime_result(**overrides))
    return intake_maraca_runtime_result(
        invocation=invocation,
        runtime_result=runtime_result,
    )


class MaracaRuntimeResultIntakeTests(unittest.TestCase):
    def test_happy_path_accepts_explicit_result_for_prepared_invocation(self) -> None:
        result = _result()
        serialized = result.to_dict()

        self.assertTrue(result.accepted)
        self.assertEqual("accepted", result.status)
        self.assertEqual((), result.blockers)
        self.assertEqual(ALLOWED_RUNTIME_STATUSES, result.requirements.allowed_runtime_statuses)
        self.assertIsInstance(result.result, MaracaRuntimeResultRecord)
        self.assertEqual("work-1", serialized["result"]["work_id"])
        self.assertEqual("collect-evidence", serialized["result"]["operation"])
        self.assertEqual("succeeded", serialized["result"]["runtime_status"])
        self.assertEqual("source-1", serialized["result"]["evidence_items"][0]["source_id"])

    def test_missing_or_malformed_inputs_fail_closed(self) -> None:
        result = intake_maraca_runtime_result(
            invocation=None,
            runtime_result=object(),
        )

        self.assertFalse(result.accepted)
        self.assertIsNone(result.result)
        self.assertIn("missing-invocation", result.blockers)
        self.assertIn("missing-runtime-result", result.blockers)
        self.assertIn("missing-result-work-id", result.blockers)
        self.assertIn("missing-runtime-status", result.blockers)

    def test_identity_mismatch_fails_closed(self) -> None:
        result = _result(work_id="other-work", run_id="other-run", operation="other-operation")

        self.assertFalse(result.accepted)
        self.assertIn("work-id-mismatch", result.blockers)
        self.assertIn("run-id-mismatch", result.blockers)
        self.assertIn("operation-mismatch", result.blockers)

    def test_missing_or_malformed_status_and_evidence_fail_closed(self) -> None:
        missing = _result(status="", evidence_items=())
        unsupported = _result(status="running")
        malformed = _result(evidence_items=("not-a-record",))

        self.assertIn("missing-runtime-status", missing.blockers)
        self.assertIn("missing-evidence", missing.blockers)
        self.assertIn("unsupported-runtime-status", unsupported.blockers)
        self.assertIn("malformed-evidence", malformed.blockers)
        self.assertIn("missing-evidence", malformed.blockers)

    def test_failed_and_blocked_terminal_statuses_are_valid_when_explicit(self) -> None:
        failed = _result(status="failed")
        blocked = _result(status="blocked")

        self.assertTrue(failed.accepted)
        self.assertTrue(blocked.accepted)
        self.assertEqual("failed", failed.result.runtime_status)
        self.assertEqual("blocked", blocked.result.runtime_status)

    def test_secret_like_names_and_values_are_blocking_and_redacted(self) -> None:
        result = _result(
            evidence_items=(
                {"source_id": "source-1", "api_key": "raw-key-value"},
                {"source_id": "source-2", "claim": "contains-token-value"},
            ),
            output={"password": "raw-password-value"},
            metadata={"client_secret": "raw-secret-value"},
        )
        serialized_text = repr(result.to_dict())

        self.assertFalse(result.accepted)
        self.assertIn("redacted-evidence-name", result.blockers)
        self.assertIn("redacted-evidence-value", result.blockers)
        self.assertIn("redacted-output-name", result.blockers)
        self.assertIn("redacted-metadata-name", result.blockers)
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

    def test_nested_dataclass_and_to_dict_objects_are_blocked_and_redacted(self) -> None:
        @dataclass(frozen=True)
        class EvidenceObject:
            source_id: str
            api_key: str

        class OutputObject:
            def to_dict(self):
                return {"dispatch": True, "token": "raw-token-value"}

        result = _result(
            evidence_items=(EvidenceObject("source-1", "raw-key-value"),),
            output={"nested": OutputObject()},
        )
        serialized_text = repr(result.to_dict())

        self.assertFalse(result.accepted)
        self.assertIn("redacted-evidence-name", result.blockers)
        self.assertIn("redacted-output-name", result.blockers)
        self.assertIn("execution-flag:output", result.blockers)
        self.assertIn(REDACTED, serialized_text)
        self.assertNotIn("api_key", serialized_text)
        self.assertNotIn("raw-key-value", serialized_text)
        self.assertNotIn("raw-token-value", serialized_text)

    def test_execution_flags_are_blocking(self) -> None:
        result = _result(
            evidence_items=({"source_id": "source-1", "execute": True},),
            output={"should_run": True},
            metadata={"publish": "yes"},
        )

        self.assertFalse(result.accepted)
        self.assertIn("execution-flag:runtime-result", result.blockers)
        self.assertIn("execution-flag:evidence", result.blockers)
        self.assertIn("execution-flag:output", result.blockers)
        self.assertIn("execution-flag:metadata", result.blockers)

    def test_records_are_frozen_dataclasses_and_serialize_plain_data(self) -> None:
        for record_type in (
            MaracaRuntimeResultIntakeRequirements,
            MaracaRuntimeResultRecord,
            MaracaRuntimeResultIntakeResult,
        ):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        result = _result()
        serialized = result.to_dict()

        self.assertIsInstance(serialized, dict)
        self.assertIsInstance(serialized["requirements"], dict)
        self.assertIsInstance(serialized["result"], dict)
        self.assertIsInstance(serialized["result"]["evidence_items"], tuple)
        with self.assertRaises(FrozenInstanceError):
            result.status = "changed"  # type: ignore[misc]

    def test_accepts_mapping_invocation_and_objects_with_to_dict(self) -> None:
        class RuntimeResultObject:
            def to_dict(self):
                return _runtime_result(evidence={"source_id": "source-1"})

        invocation = _invocation().to_dict()["envelope"]
        result = intake_maraca_runtime_result(
            invocation=invocation,
            runtime_result=RuntimeResultObject(),
        )

        self.assertTrue(result.accepted)

    def test_blocked_invocation_result_fails_closed(self) -> None:
        blocked_invocation = prepare_maraca_runtime_invocation(
            work_id="work-1",
            run_id="work-1",
            operation="collect-evidence",
            payload={"topic": "boundary"},
            preflight={"ready": False, "summary": {"work_id": "work-1", "run_id": "work-1"}},
            maraca_readiness=_readiness(),
        )

        result = _result(invocation=blocked_invocation)

        self.assertFalse(result.accepted)
        self.assertIn("blocked-invocation", result.blockers)

    def test_caller_inputs_are_not_mutated(self) -> None:
        invocation = _invocation().to_dict()
        runtime_result = _runtime_result(
            evidence_items=[{"source_id": "source-1", "claim": "plain"}],
            output={"summary": {"text": "ok"}},
            metadata={"source": "unit-test"},
        )
        before = copy.deepcopy({"invocation": invocation, "runtime_result": runtime_result})

        intake_maraca_runtime_result(
            invocation=invocation,
            runtime_result=runtime_result,
        )

        self.assertEqual(before["invocation"], invocation)
        self.assertEqual(before["runtime_result"], runtime_result)

    def test_source_has_no_forbidden_runtime_behavior(self) -> None:
        source = Path(
            "src/harness_orchestrator/maraca_runtime_result_intake.py",
        ).read_text(encoding="utf-8")

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
        forbidden_process = "sub" + "process"
        self.assertNotIn(forbidden_process, source.replace('"sub" + "process"', ""))
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
