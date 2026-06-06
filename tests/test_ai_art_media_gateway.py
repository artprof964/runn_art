import copy
import inspect
import pathlib
import unittest

from harness_orchestrator.adapters.ai_art_media_gateway import (
    AIArtMediaGateway,
    AIArtMediaGatewayRequest,
)
from harness_orchestrator.contracts import GateDecision, MediaReleaseRequest


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return self.response


class FakeObjectClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def evaluate(self, request):
        self.calls.append(request)
        return self.response


class ToDictResult:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return dict(self.payload)


class AIArtMediaGatewayTests(unittest.TestCase):
    def _request(self) -> MediaReleaseRequest:
        return MediaReleaseRequest(
            request_id="release-001",
            work_id="work-001",
            media_items=(
                {
                    "media_id": "media-001",
                    "artifact_id": "artifact-001",
                    "title": "Launch visual",
                },
            ),
            target_channels=("gallery",),
            required_gates=("ai-art-media-release",),
            evidence_bundle_id="evidence-001",
            metadata={"campaign": "spring"},
        )

    def test_default_fail_closed_without_client_or_result(self) -> None:
        decision = AIArtMediaGateway().evaluate(self._request())

        self.assertIsInstance(decision, GateDecision)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.gate_name, "ai-art-media-release")
        self.assertEqual(decision.blockers, ("client-or-result-not-configured",))
        self.assertEqual(decision.metadata["status"], "not_configured")

    def test_injected_passing_result_maps_to_gate_decision(self) -> None:
        client = FakeClient(
            {
                "allowed": True,
                "work_id": "work-001",
                "media_id": "media-001",
                "artifact_id": "artifact-001",
                "evidence_bundle_id": "evidence-001",
                "metadata": {"review_id": "review-001"},
            }
        )

        decision = AIArtMediaGateway(client=client).evaluate(self._request())

        self.assertTrue(decision.passed)
        self.assertEqual(decision.blockers, ())
        self.assertEqual(decision.work_id, "work-001")
        self.assertEqual(decision.gate_name, "ai-art-media-release")
        self.assertEqual(decision.evidence_bundle_id, "evidence-001")
        self.assertEqual(decision.metadata["status"], "allowed")
        self.assertEqual(decision.metadata["result"]["review_id"], "review-001")
        self.assertEqual(len(client.calls), 1)
        self.assertIsInstance(client.calls[0], AIArtMediaGatewayRequest)
        self.assertEqual(client.calls[0].work_id, "work-001")

    def test_explicit_plain_result_data_path(self) -> None:
        result = ToDictResult(
            {
                "allowed": True,
                "metadata": {"media_ids": ("media-001",)},
                "checks": [{"name": "license", "allowed": True}],
            }
        )

        decision = AIArtMediaGateway().evaluate(self._request(), result_data=result)

        self.assertTrue(decision.passed)
        self.assertEqual(decision.blockers, ())
        self.assertEqual(decision.metadata["status"], "allowed")

    def test_object_client_and_blocked_checks_mapping(self) -> None:
        client = FakeObjectClient(
            {
                "allowed": True,
                "checks": [
                    {"name": "license", "allowed": True},
                    {
                        "name": "model-release",
                        "blocked": True,
                        "blockers": ["missing-release"],
                    },
                    {"id": "rights", "status": "failed"},
                ],
            }
        )

        decision = AIArtMediaGateway(client=client).evaluate(self._request())

        self.assertFalse(decision.passed)
        self.assertIn("missing-release", decision.blockers)
        self.assertIn("model-release", decision.blockers)
        self.assertIn("rights", decision.blockers)
        self.assertEqual(
            decision.metadata["blocked_checks"], ("model-release", "rights")
        )
        self.assertEqual(len(client.calls), 1)

    def test_malformed_result_fails_closed(self) -> None:
        decision = AIArtMediaGateway(result_data={"allowed": "yes"}).evaluate(
            self._request()
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("malformed-result",))
        self.assertEqual(decision.metadata["status"], "malformed")

    def test_contradictory_allowed_blocked_results_fail_closed(self) -> None:
        for result in (
            {"allowed": True, "blocked": True},
            {"allowed": False, "blocked": False},
        ):
            with self.subTest(result=result):
                decision = AIArtMediaGateway(result_data=result).evaluate(
                    self._request()
                )

                self.assertFalse(decision.passed)
                self.assertEqual(decision.blockers, ("malformed-result",))
                self.assertEqual(decision.metadata["status"], "malformed")

    def test_identity_mismatch_fails_closed(self) -> None:
        result = {
            "allowed": True,
            "work_id": "work-999",
            "media_id": "media-999",
            "artifact_id": "artifact-999",
            "evidence_bundle_id": "evidence-999",
        }

        decision = AIArtMediaGateway(result_data=result).evaluate(self._request())

        self.assertFalse(decision.passed)
        self.assertIn("work-identity-mismatch", decision.blockers)
        self.assertIn("media-identity-mismatch", decision.blockers)
        self.assertIn("artifact-identity-mismatch", decision.blockers)
        self.assertIn("evidence-identity-mismatch", decision.blockers)
        self.assertEqual(decision.metadata["status"], "identity_mismatch")

    def test_secret_redaction_in_reason_blockers_and_metadata(self) -> None:
        raw = "api_key=secret-token"
        result = {
            "allowed": False,
            "reason": f"blocked because {raw}",
            "blockers": [f"matched {raw}"],
            "metadata": {
                "token": "secret-token",
                "nested": {"password": "hidden", "note": f"uses {raw}"},
            },
        }

        decision = AIArtMediaGateway(result_data=result).evaluate(self._request())

        rendered = repr(decision.to_dict())
        self.assertFalse(decision.passed)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("hidden", rendered)
        self.assertIn("[redacted]", rendered)

    def test_caller_inputs_are_not_mutated(self) -> None:
        media_item = {"media_id": "media-001", "metadata": {"labels": ["a"]}}
        request_metadata = {"campaign": {"name": "spring"}}
        request = MediaReleaseRequest(
            request_id="release-002",
            work_id="work-002",
            media_items=(media_item,),
            metadata=request_metadata,
        )
        result = {
            "allowed": False,
            "blockers": ["policy"],
            "metadata": {"notes": ["original"]},
        }
        original_request = request.to_dict()
        original_result = copy.deepcopy(result)

        AIArtMediaGateway(result_data=result).evaluate(request)

        self.assertEqual(request.to_dict(), original_request)
        self.assertEqual(result, original_result)

    def test_source_has_no_forbidden_imports_or_calls(self) -> None:
        forbidden = (
            "re" + "quests",
            "ht" + "tpx",
            "url" + "lib",
            "so" + "cket",
            "sub" + "process",
            "en" + "viron",
            "get" + "env",
            "ma" + "raca",
            "pub" + "lish",
            "scrap" + "ing",
            "sche" + "duler",
            "wa" + "tch",
            "co" + "ordinator",
            "run" + "time",
        )
        sources = [
            inspect.getsource(
                __import__(
                    "harness_orchestrator.adapters.ai_art_media_gateway",
                    fromlist=["unused"],
                )
            ),
            pathlib.Path(__file__).read_text(encoding="utf-8"),
        ]

        for source in sources:
            lowered = source.lower()
            for term in forbidden:
                self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
