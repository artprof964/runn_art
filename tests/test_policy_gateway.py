import inspect
import unittest

from harness_orchestrator.adapters.policy_gateway import (
    PolicyGateway,
    PolicyGatewayConfig,
    PolicyGatewayRequest,
)
from harness_orchestrator.contracts import GateDecision, GovernedWorkRequest


class FakeCallableClient:
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


class ExplodingClient:
    def evaluate(self, request):
        raise RuntimeError("secret-token leaked from client")


class PolicyGatewayTests(unittest.TestCase):
    def test_default_policy_gateway_blocks_without_client(self) -> None:
        gateway = PolicyGateway()

        decision = gateway.evaluate(
            GovernedWorkRequest(
                work_id="work-001",
                requested_by="tester",
                objective="draft governed work",
            ),
            request_id="policy-001",
        )

        self.assertIsInstance(decision, GateDecision)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.gate_name, "policy")
        self.assertEqual(decision.blockers, ("client-not-configured",))
        self.assertIn("no client configured", decision.reason)
        self.assertEqual(decision.metadata["status"], "not_configured")

    def test_callable_client_receives_plain_governed_work_request_envelope(self) -> None:
        client = FakeCallableClient({"allowed": True})
        gateway = PolicyGateway(client=client)
        work_request = GovernedWorkRequest(
            work_id="work-002",
            requested_by="tester",
            objective="collect evidence",
            channel="manual",
            policy_scope="studio",
            service_targets=("policy", "evidence"),
            metadata={"trigger_type": "manual", "api_key": "secret-value"},
        )

        decision = gateway.evaluate(
            work_request,
            request_id="policy-002",
            operation="manual-start",
            payload={"extra": "value"},
        )

        self.assertTrue(decision.passed)
        self.assertEqual(len(client.calls), 1)
        request = client.calls[0]
        self.assertIsInstance(request, PolicyGatewayRequest)
        self.assertEqual(request.request_id, "policy-002")
        self.assertEqual(request.work_id, "work-002")
        self.assertEqual(request.operation, "manual-start")
        self.assertEqual(request.payload["objective"], "collect evidence")
        self.assertEqual(request.payload["extra"], "value")
        self.assertEqual(request.metadata["channel"], "manual")
        self.assertEqual(request.metadata["policy_scope"], "studio")
        self.assertEqual(request.metadata["trigger_type"], "manual")
        self.assertEqual(request.metadata["api_key"], "[redacted]")

    def test_object_client_evaluate_is_supported(self) -> None:
        client = FakeObjectClient({"decision": "approved", "reason": "operator policy ok"})
        gateway = PolicyGateway(client=client)

        decision = gateway.evaluate(
            {"request_id": "policy-003", "work_id": "work-003", "channel": "manual"},
            operation="manual-start",
        )

        self.assertTrue(decision.passed)
        self.assertEqual(decision.reason, "operator policy ok")
        self.assertEqual(len(client.calls), 1)

    def test_mapping_envelope_operation_is_preserved_by_default(self) -> None:
        client = FakeCallableClient({"decision": "allow"})
        gateway = PolicyGateway(client=client)

        decision = gateway.evaluate(
            {
                "request_id": "policy-003b",
                "work_id": "work-003b",
                "operation": "watch-candidate-review",
                "trigger_type": "watch",
            }
        )

        self.assertTrue(decision.passed)
        self.assertEqual(client.calls[0].operation, "watch-candidate-review")
        self.assertEqual(decision.metadata["operation"], "watch-candidate-review")
        self.assertEqual(decision.metadata["trigger_type"], "watch")

    def test_allowed_response_maps_to_passing_gate_decision(self) -> None:
        gateway = PolicyGateway(
            client=FakeCallableClient(
                {
                    "passed": True,
                    "reason": "policy accepted",
                    "metadata": {"review_mode": "automatic"},
                }
            )
        )

        decision = gateway.evaluate(request_id="policy-004", work_id="work-004")

        self.assertTrue(decision.passed)
        self.assertEqual(decision.blockers, ())
        self.assertEqual(decision.reason, "policy accepted")
        self.assertEqual(decision.metadata["status"], "allowed")
        self.assertEqual(decision.metadata["review_mode"], "automatic")

    def test_denied_response_maps_to_blocking_gate_decision(self) -> None:
        gateway = PolicyGateway(
            client=FakeObjectClient(
                {
                    "decision": "deny",
                    "reason": "missing reviewer approval",
                    "blockers": ["approval", "policy"],
                }
            )
        )

        decision = gateway.evaluate(request_id="policy-005", work_id="work-005")

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "missing reviewer approval")
        self.assertEqual(decision.blockers, ("approval", "policy"))
        self.assertEqual(decision.metadata["status"], "denied")

    def test_unknown_or_malformed_response_fails_closed(self) -> None:
        gateway = PolicyGateway(client=FakeCallableClient({"decision": "maybe"}))

        decision = gateway.evaluate(request_id="policy-006", work_id="work-006")

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("policy-response-invalid",))
        self.assertEqual(decision.metadata["status"], "invalid_response")

    def test_client_error_blocks_without_leaking_exception_details(self) -> None:
        gateway = PolicyGateway(client=ExplodingClient())

        decision = gateway.evaluate(request_id="policy-007", work_id="work-007")

        self.assertFalse(decision.passed)
        self.assertEqual(decision.blockers, ("client-error",))
        self.assertNotIn("secret-token", decision.reason)
        self.assertNotIn("secret-token", str(decision.metadata))

    def test_sensitive_values_are_redacted(self) -> None:
        secret = "raw-secret-token"
        gateway = PolicyGateway(
            config=PolicyGatewayConfig(redacted_values=(secret,)),
            client=FakeCallableClient(
                {
                    "allowed": False,
                    "reason": f"blocked because {secret}",
                    "blockers": [f"matched {secret}"],
                    "metadata": {
                        "api_key": secret,
                        "nested": {"token": secret, "note": f"contains {secret}"},
                    },
                }
            ),
        )

        decision = gateway.evaluate(
            request_id="policy-008",
            work_id="work-008",
            metadata={"operator_token": secret},
        )

        self.assertNotIn(secret, decision.reason)
        self.assertNotIn(secret, " ".join(decision.blockers))
        self.assertNotIn(secret, str(decision.metadata))
        self.assertEqual(decision.metadata["api_key"], "[redacted]")
        self.assertEqual(decision.metadata["nested"]["token"], "[redacted]")

    def test_source_has_no_disallowed_integration_terms(self) -> None:
        banned = (
            "re" + "quests",
            "ht" + "tpx",
            "so" + "cket",
            "sub" + "process",
            "sch" + "eduler",
            "pub" + "lish",
            "soc" + "ial",
            "scr" + "ape",
            "os.environ",
        )
        source = inspect.getsource(
            __import__(
                "harness_orchestrator.adapters.policy_gateway",
                fromlist=["unused"],
            )
        )
        lowered = source.lower()

        for term in banned:
            self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
