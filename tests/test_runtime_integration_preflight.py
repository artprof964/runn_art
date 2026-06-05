from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
import unittest

from harness_orchestrator.runtime_integration_preflight import (
    REDACTED,
    RuntimeIntegrationPreflightRequirements,
    RuntimeIntegrationPreflightSummary,
    evaluate_runtime_integration_preflight,
)


def _gate(name: str, passed: bool = True, work_id: str = "work-1") -> dict[str, object]:
    return {
        "decision_id": f"decision:{name}",
        "work_id": work_id,
        "gate_name": name,
        "passed": passed,
        "reason": "ok" if passed else "blocked",
        "metadata": {},
    }


def _event(
    event_type: str,
    status: str = "passed",
    work_id: str = "work-1",
) -> dict[str, object]:
    return {
        "event_id": f"event:{event_type}",
        "work_id": work_id,
        "event_type": event_type,
        "status": status,
        "metadata": {},
    }


def _snapshot() -> dict[str, object]:
    return {
        "run_id": "work-1",
        "gate_decisions": (
            _gate("policy"),
            _gate("evidence"),
            _gate("ai-art-safety"),
            _gate("human-review"),
            _gate("manual-run-final"),
        ),
        "dependencies": (
            {
                "dependency_id": "evidence:bundle-1",
                "work_id": "work-1",
                "reference": "bundle-1",
                "order": 10,
                "dependency_type": "evidence",
                "status": "ready",
                "metadata": {},
            },
        ),
        "audit_events": (
            _event("gate:policy"),
            _event("gate:evidence"),
            _event("gate:ai-art-safety"),
            _event("gate:human-review"),
            _event("approval-audit"),
            _event("gate:manual-run-final"),
        ),
        "tasks": (),
        "metadata": {},
    }


def _readiness(ready: bool = True) -> dict[str, object]:
    return {
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "blockers": () if ready else ("missing-package:langgraph",),
        "snapshot": {},
    }


class RuntimeIntegrationPreflightTests(unittest.TestCase):
    def test_all_present_runtime_preflight_is_ready(self) -> None:
        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=_snapshot(),
            maraca_readiness=_readiness(),
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.blockers, ())
        self.assertEqual(result.to_dict()["summary"]["run_id"], "work-1")

    def test_missing_required_gate_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["gate_decisions"] = tuple(
            gate
            for gate in snapshot["gate_decisions"]
            if gate["gate_name"] != "human-review"
        )

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            maraca_readiness=_readiness(),
        )

        self.assertFalse(result.ready)
        self.assertIn("missing-gate:human-review", result.blockers)

    def test_failed_required_gate_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["gate_decisions"] = (
            _gate("policy"),
            _gate("evidence"),
            _gate("ai-art-safety", passed=False),
            _gate("human-review"),
            _gate("manual-run-final"),
        )

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            maraca_readiness=_readiness(),
        )

        self.assertFalse(result.ready)
        self.assertIn("blocked-gate:ai-art-safety", result.blockers)

    def test_missing_approval_audit_event_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["audit_events"] = tuple(
            event
            for event in snapshot["audit_events"]
            if event["event_type"] != "approval-audit"
        )

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            maraca_readiness=_readiness(),
        )

        self.assertFalse(result.ready)
        self.assertIn("missing-audit-event:approval-audit", result.blockers)

    def test_unfinished_task_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["tasks"] = (
            {
                "task_id": "runtime-wireup",
                "work_id": "work-1",
                "title": "Wire runtime",
                "status": "open",
            },
        )

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            maraca_readiness=_readiness(),
        )

        self.assertFalse(result.ready)
        self.assertIn("unfinished-task:runtime-wireup", result.blockers)

    def test_missing_or_failed_maraca_readiness_fails_when_required(self) -> None:
        missing = evaluate_runtime_integration_preflight(ledger_snapshot=_snapshot())
        failed = evaluate_runtime_integration_preflight(
            ledger_snapshot=_snapshot(),
            maraca_readiness=_readiness(False),
        )

        self.assertIn("missing-maraca-readiness", missing.blockers)
        self.assertIn("blocked-maraca-readiness", failed.blockers)

    def test_configurable_requirements_can_disable_maraca_and_task_checks(self) -> None:
        snapshot = _snapshot()
        snapshot["tasks"] = (
            {
                "task_id": "manual-follow-up",
                "work_id": "work-1",
                "title": "Manual follow-up",
                "status": "open",
            },
        )
        requirements = RuntimeIntegrationPreflightRequirements(
            required_gates=("policy",),
            required_dependency_types=(),
            required_audit_event_types=("gate:policy",),
            require_no_unfinished_tasks=False,
            require_maraca_readiness=False,
        )

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            requirements=requirements,
        )

        self.assertTrue(result.ready)

    def test_malformed_and_wrong_work_data_fail_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["gate_decisions"] = (_gate("policy", work_id="other-work"), object())

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            maraca_readiness=_readiness(),
            work_id="work-1",
        )

        self.assertFalse(result.ready)
        self.assertIn("gate-work-id-mismatch", result.blockers)
        self.assertIn("malformed-gate-record", result.blockers)
        self.assertIn("missing-gate:evidence", result.blockers)

    def test_run_id_work_id_mismatch_fails_closed(self) -> None:
        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=_snapshot(),
            maraca_readiness=_readiness(),
            work_id="other-work",
        )

        self.assertFalse(result.ready)
        self.assertIn("run-id-work-id-mismatch", result.blockers)

    def test_frozen_dataclass_and_plain_serialization_with_redaction(self) -> None:
        snapshot = _snapshot()
        snapshot["metadata"] = {"api_key": "raw-secret-value"}
        snapshot["gate_decisions"] = (
            {
                **_gate("policy"),
                "metadata": {"access_token": "token-123"},
            },
            _gate("evidence"),
            _gate("ai-art-safety"),
            _gate("human-review"),
            _gate("manual-run-final"),
        )

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=snapshot,
            maraca_readiness={
                **_readiness(),
                "snapshot": {"runtime_password": "password-123"},
            },
        )
        serialized = result.to_dict()
        serialized_text = repr(serialized)

        self.assertTrue(is_dataclass(result))
        with self.assertRaises(FrozenInstanceError):
            result.ready = False  # type: ignore[misc]
        self.assertIsInstance(serialized, dict)
        self.assertIn(REDACTED, serialized_text)
        self.assertNotIn("access_token", serialized_text)
        self.assertNotIn("token-123", serialized_text)
        self.assertNotIn("runtime_password", serialized_text)
        self.assertNotIn("password-123", serialized_text)

    def test_accepts_objects_with_to_dict(self) -> None:
        class SnapshotObject:
            def to_dict(self) -> dict[str, object]:
                return _snapshot()

        class ReadinessObject:
            def to_dict(self) -> dict[str, object]:
                return _readiness()

        result = evaluate_runtime_integration_preflight(
            ledger_snapshot=SnapshotObject(),
            maraca_readiness=ReadinessObject(),
        )

        self.assertTrue(result.ready)

    def test_source_guard_for_forbidden_runtime_behavior(self) -> None:
        source = Path(
            "src/harness_orchestrator/runtime_integration_preflight.py",
        ).read_text(encoding="utf-8")

        forbidden = (
            "os.environ",
            "importlib",
            "pkg_resources",
            "requests",
            "httpx",
            "socket",
            "subprocess",
            "urllib",
            "RunLedger(",
            ".save(",
            ".load(",
            "MARACA",
            "AI-Art",
            "scheduler",
            "watch",
            "publish",
        )
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
