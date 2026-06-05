import inspect
import pathlib
import unittest

from harness_orchestrator.adapters.ai_art_safety_gateway import (
    AIArtSafetyGateway,
    AIArtSafetyGatewayConfig,
    SafetyGatewayRequest,
)
from harness_orchestrator.contracts import GateDecision


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


class AIArtSafetyGatewayTests(unittest.TestCase):
    def test_default_gateway_is_inert_without_client(self) -> None:
        gateway = AIArtSafetyGateway()

        decision = gateway.evaluate(
            request_id="safe-001",
            work_id="work-001",
            operation="image-safety-check",
            payload={"asset_id": "asset-001"},
        )

        self.assertIsInstance(decision, GateDecision)
        self.assertEqual(gateway.config.api_key_env_var, "deepseek-open-art")
        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("client-not-configured",))
        self.assertIn("no client configured", decision.reason)
        self.assertEqual(decision.metadata["status"], "not_configured")

    def test_custom_config_keeps_secret_out_of_decision(self) -> None:
        secret = "super-secret-token"
        config = AIArtSafetyGatewayConfig(
            base_url="https://safety.example.test/api",
            api_key_env_var="AI_ART_TEST_KEY",
            safety_path="/v2/check",
        )
        client = FakeClient(
            {
                "allowed": False,
                "reason": f"denied with {secret}",
                "blockers": [f"policy matched {secret}"],
            }
        )
        gateway = AIArtSafetyGateway(
            config=config,
            client=client,
            env={"AI_ART_TEST_KEY": secret},
        )

        decision = gateway.evaluate(
            request_id="safe-002",
            work_id="work-002",
            payload={"asset_id": "asset-002"},
        )

        self.assertEqual(gateway.config.base_url, "https://safety.example.test/api")
        self.assertEqual(gateway.config.safety_path, "/v2/check")
        self.assertEqual(gateway.config.api_key_env_var, "AI_ART_TEST_KEY")
        self.assertNotIn(secret, decision.reason)
        self.assertNotIn(secret, " ".join(decision.blockers))
        self.assertNotIn(secret, str(decision.metadata))

    def test_callable_client_receives_inert_request_data(self) -> None:
        secret = "configured-token"
        client = FakeClient({"allowed": True})
        gateway = AIArtSafetyGateway(
            config=AIArtSafetyGatewayConfig(
                base_url="https://safety.example.test/root/",
                api_key_env_var="SAFETY_KEY",
                safety_path="checks",
            ),
            client=client,
            env={"SAFETY_KEY": secret},
        )

        gateway.evaluate(
            request_id="safe-003",
            work_id="work-003",
            operation="render-review",
            payload={"asset_id": "asset-003"},
        )

        self.assertEqual(len(client.calls), 1)
        request = client.calls[0]
        self.assertIsInstance(request, SafetyGatewayRequest)
        self.assertEqual(request.url, "https://safety.example.test/root/checks")
        self.assertEqual(request.path, "checks")
        self.assertEqual(request.headers, {"Authorization": f"Bearer {secret}"})
        self.assertEqual(
            request.payload,
            {
                "request_id": "safe-003",
                "work_id": "work-003",
                "operation": "render-review",
                "payload": {"asset_id": "asset-003"},
            },
        )

    def test_allowed_response_maps_to_passing_gate_decision(self) -> None:
        gateway = AIArtSafetyGateway(client=FakeClient({"allowed": True}))

        decision = gateway.evaluate(request_id="safe-004", work_id="work-004")

        self.assertTrue(decision.passed)
        self.assertEqual(decision.blockers, ())
        self.assertEqual(decision.gate_name, "ai-art-safety")
        self.assertEqual(decision.metadata["status"], "allowed")

    def test_denied_response_maps_to_blocking_gate_decision(self) -> None:
        gateway = AIArtSafetyGateway(
            client=FakeObjectClient(
                {
                    "allowed": False,
                    "reason": "unsafe content detected",
                    "blockers": ("unsafe-content", "manual-review"),
                }
            )
        )

        decision = gateway.evaluate(request_id="safe-005", work_id="work-005")

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "unsafe content detected")
        self.assertEqual(decision.blockers, ("unsafe-content", "manual-review"))
        self.assertEqual(decision.metadata["status"], "denied")

    def test_source_has_no_disallowed_integration_terms(self) -> None:
        banned = (
            "re" + "quests",
            "ht" + "tpx",
            "so" + "cket",
            "sub" + "process",
        )
        side_effect_terms = ("pub" + "lish", "soc" + "ial")
        sources = [
            inspect.getsource(
                __import__(
                    "harness_orchestrator.adapters.ai_art_safety_gateway",
                    fromlist=["unused"],
                )
            ),
            pathlib.Path(__file__).read_text(encoding="utf-8"),
        ]

        for source in sources:
            lowered = source.lower()
            for term in banned + side_effect_terms:
                self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
