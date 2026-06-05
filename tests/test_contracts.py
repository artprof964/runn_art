from dataclasses import FrozenInstanceError, is_dataclass
import unittest

from harness_orchestrator.contracts import (
    EvidenceBundle,
    EvidenceRequest,
    GateDecision,
    GovernedWorkRequest,
    MediaReleaseRequest,
)


class ContractRecordTests(unittest.TestCase):
    def test_contract_records_are_frozen_dataclasses(self) -> None:
        for record_type in (
            GovernedWorkRequest,
            EvidenceRequest,
            EvidenceBundle,
            MediaReleaseRequest,
            GateDecision,
        ):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

    def test_governed_work_request_defaults_and_serialization(self) -> None:
        request = GovernedWorkRequest(
            work_id="work-001",
            requested_by="tester",
            objective="Collect governed evidence before media planning.",
            service_targets=("policy", "evidence"),
            metadata={"campaign": "spring"},
        )

        self.assertEqual(request.channel, "manual")
        self.assertEqual(request.priority, "normal")
        self.assertEqual(request.policy_scope, "default")
        self.assertEqual(
            request.to_dict(),
            {
                "work_id": "work-001",
                "requested_by": "tester",
                "objective": "Collect governed evidence before media planning.",
                "channel": "manual",
                "priority": "normal",
                "policy_scope": "default",
                "service_targets": ("policy", "evidence"),
                "metadata": {"campaign": "spring"},
            },
        )

    def test_evidence_request_keeps_connector_replaceable(self) -> None:
        request = EvidenceRequest(
            request_id="ev-001",
            work_id="work-001",
            query="Find source-backed context.",
            connector_name="alternate-evidence-service",
            required_sources=("source-a",),
            excluded_sources=("source-b",),
            max_items=5,
        )

        self.assertEqual(request.connector_name, "alternate-evidence-service")
        self.assertEqual(request.required_sources, ("source-a",))
        self.assertEqual(request.excluded_sources, ("source-b",))
        self.assertEqual(request.freshness, "current")
        self.assertEqual(request.to_dict()["max_items"], 5)

    def test_evidence_bundle_preserves_plain_evidence_records(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="bundle-001",
            request_id="ev-001",
            work_id="work-001",
            evidence_items=(
                {
                    "source_id": "source-a",
                    "claim": "The source supports the request.",
                    "confidence": 0.9,
                },
            ),
            source_ids=("source-a",),
            validation_notes=("source checked",),
        )

        self.assertEqual(bundle.connector_name, "maraca")
        self.assertEqual(bundle.evidence_items[0]["confidence"], 0.9)
        self.assertEqual(bundle.to_dict()["validation_notes"], ("source checked",))

    def test_media_release_request_channels_are_data_not_integrations(self) -> None:
        request = MediaReleaseRequest(
            request_id="media-001",
            work_id="work-001",
            media_items=({"artifact_id": "asset-001", "kind": "image"},),
            target_channels=("telegram", "rss"),
            required_gates=("provenance", "human-review"),
            evidence_bundle_id="bundle-001",
            connector_name="replaceable-media-service",
        )

        self.assertEqual(request.target_channels, ("telegram", "rss"))
        self.assertEqual(request.required_gates, ("provenance", "human-review"))
        self.assertEqual(request.connector_name, "replaceable-media-service")
        self.assertEqual(request.to_dict()["evidence_bundle_id"], "bundle-001")

    def test_gate_decision_captures_pass_and_block_states(self) -> None:
        passed = GateDecision(
            decision_id="gate-001",
            work_id="work-001",
            gate_name="provenance",
            passed=True,
            reason="Evidence bundle is attached.",
            evidence_bundle_id="bundle-001",
        )
        blocked = GateDecision(
            decision_id="gate-002",
            work_id="work-001",
            gate_name="human-review",
            passed=False,
            reason="Reviewer approval is missing.",
            blockers=("approval",),
        )

        self.assertEqual(passed.blockers, ())
        self.assertEqual(blocked.to_dict()["blockers"], ("approval",))
        self.assertFalse(blocked.passed)

    def test_records_are_immutable_at_top_level(self) -> None:
        request = GovernedWorkRequest(
            work_id="work-001",
            requested_by="tester",
            objective="Protect top-level record fields.",
        )

        with self.assertRaises(FrozenInstanceError):
            request.priority = "urgent"


if __name__ == "__main__":
    unittest.main()
