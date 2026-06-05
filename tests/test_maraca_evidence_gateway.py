import inspect
import unittest

import harness_orchestrator.adapters.maraca_evidence_gateway as gateway_module
from harness_orchestrator.adapters.maraca_evidence_gateway import (
    MaracaEvidenceGateway,
    MaracaEvidenceGatewayConfig,
    MaracaEvidenceGatewayRequest,
)
from harness_orchestrator.contracts import EvidenceBundle, EvidenceRequest


class FakeCallableClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return self.response


class FakeEvaluateClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def evaluate(self, request):
        self.calls.append(request)
        return self.response


class FakeCollectClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def collect(self, request):
        self.calls.append(request)
        return self.response


class FakeQueryClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def query(self, request):
        self.calls.append(request)
        return self.response


class DictLikeRecord:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return dict(self.data)


class ObjectResponse:
    def __init__(self):
        self.bundle_id = "bundle-object"
        self.items = (
            DictLikeRecord(
                {
                    "source_id": "source-object",
                    "claim": "Object item normalized.",
                }
            ),
        )
        self.source_ids = ["source-object"]
        self.validator_notes = "object response checked"
        self.metadata = {"quality": "high"}


class MaracaEvidenceGatewayTests(unittest.TestCase):
    def test_default_gateway_is_inert_without_client(self) -> None:
        gateway = MaracaEvidenceGateway()

        bundle = gateway.collect(
            request_id="ev-001",
            work_id="work-001",
            query="Find governed context.",
        )

        self.assertIsInstance(bundle, EvidenceBundle)
        self.assertEqual(bundle.bundle_id, "maraca:ev-001:empty")
        self.assertEqual(bundle.evidence_items, ())
        self.assertEqual(bundle.source_ids, ())
        self.assertEqual(bundle.metadata["status"], "not_configured")
        self.assertIn("no client configured", bundle.validation_notes[0])

    def test_callable_client_receives_plain_gateway_request_copy(self) -> None:
        metadata = {"topic": "memory"}
        evidence_request = EvidenceRequest(
            request_id="ev-002",
            work_id="work-002",
            query="Collect source-backed notes.",
            required_sources=("source-a",),
            excluded_sources=("source-b",),
            freshness="recent",
            max_items=3,
            metadata=metadata,
        )
        client = FakeCallableClient({"evidence_items": ()})
        gateway = MaracaEvidenceGateway(client=client)

        bundle = gateway.collect(evidence_request)
        metadata["topic"] = "changed"

        self.assertEqual(len(client.calls), 1)
        request = client.calls[0]
        self.assertIsInstance(request, MaracaEvidenceGatewayRequest)
        self.assertEqual(request.request_id, "ev-002")
        self.assertEqual(request.work_id, "work-002")
        self.assertEqual(request.query, "Collect source-backed notes.")
        self.assertEqual(request.required_sources, ("source-a",))
        self.assertEqual(request.excluded_sources, ("source-b",))
        self.assertEqual(request.freshness, "recent")
        self.assertEqual(request.max_items, 3)
        self.assertEqual(request.metadata, {"topic": "memory"})
        self.assertIsNot(request.metadata, evidence_request.metadata)
        self.assertEqual(bundle.request_id, "ev-002")

    def test_object_client_injection_methods_work(self) -> None:
        collect_client = FakeCollectClient({"bundle_id": "bundle-collect"})
        evaluate_client = FakeEvaluateClient({"bundle_id": "bundle-evaluate"})
        query_client = FakeQueryClient({"bundle_id": "bundle-query"})

        self.assertEqual(
            MaracaEvidenceGateway(client=collect_client)
            .collect(request_id="ev-003", work_id="work-003", query="a")
            .bundle_id,
            "bundle-collect",
        )
        self.assertEqual(
            MaracaEvidenceGateway(client=evaluate_client)
            .collect(request_id="ev-004", work_id="work-004", query="b")
            .bundle_id,
            "bundle-evaluate",
        )
        self.assertEqual(
            MaracaEvidenceGateway(client=query_client)
            .evaluate(request_id="ev-005", work_id="work-005", query="c")
            .bundle_id,
            "bundle-query",
        )
        self.assertEqual(len(collect_client.calls), 1)
        self.assertEqual(len(evaluate_client.calls), 1)
        self.assertEqual(len(query_client.calls), 1)

    def test_mapping_and_object_outputs_normalize_into_bundle(self) -> None:
        mapping_bundle = MaracaEvidenceGateway(
            client=FakeCallableClient(
                {
                    "bundle_id": "bundle-map",
                    "candidates": [
                        {
                            "source_id": "source-map",
                            "claim": "Mapping item normalized.",
                        }
                    ],
                    "validator_notes": ["mapping checked"],
                    "metadata": {"confidence": 0.7},
                }
            )
        ).collect(request_id="ev-006", work_id="work-006", query="map")

        object_bundle = MaracaEvidenceGateway(
            client=FakeCallableClient(ObjectResponse())
        ).collect(request_id="ev-007", work_id="work-007", query="object")

        self.assertEqual(mapping_bundle.bundle_id, "bundle-map")
        self.assertEqual(mapping_bundle.evidence_items[0]["source_id"], "source-map")
        self.assertEqual(mapping_bundle.source_ids, ("source-map",))
        self.assertEqual(mapping_bundle.validation_notes, ("mapping checked",))
        self.assertEqual(mapping_bundle.metadata["confidence"], 0.7)
        self.assertEqual(mapping_bundle.metadata["status"], "collected")

        self.assertEqual(object_bundle.bundle_id, "bundle-object")
        self.assertEqual(object_bundle.evidence_items[0]["source_id"], "source-object")
        self.assertEqual(object_bundle.source_ids, ("source-object",))
        self.assertEqual(object_bundle.validation_notes, ("object response checked",))
        self.assertEqual(object_bundle.metadata["quality"], "high")

    def test_maraca_v2_evidence_key_maps_records_and_derives_sources(self) -> None:
        bundle = MaracaEvidenceGateway(
            client=FakeCallableClient(
                {
                    "bundle_id": "bundle-v2",
                    "evidence": [
                        {
                            "source_id": "source-by-source-id",
                            "claim": "source_id wins",
                        },
                        {
                            "source": "source-by-source",
                            "claim": "source fallback works",
                        },
                        {
                            "id": "source-by-id",
                            "claim": "id fallback works",
                        },
                    ],
                }
            )
        ).collect(request_id="ev-010", work_id="work-010", query="v2")

        self.assertEqual(bundle.bundle_id, "bundle-v2")
        self.assertEqual(len(bundle.evidence_items), 3)
        self.assertEqual(
            tuple(record["claim"] for record in bundle.evidence_items),
            ("source_id wins", "source fallback works", "id fallback works"),
        )
        self.assertEqual(
            bundle.source_ids,
            ("source-by-source-id", "source-by-source", "source-by-id"),
        )

    def test_config_values_are_inert_and_omitted_or_redacted(self) -> None:
        secret = "actual-secret"
        config = MaracaEvidenceGatewayConfig(
            connector_name="maraca-test",
            base_url="https://maraca.example.invalid/root",
            path="/collect",
            api_key_env_var="MARACA_KEY",
        )
        client = FakeCallableClient(
            {
                "bundle_id": f"bundle {config.base_url}",
                "items": [
                    {
                        "source_id": "source-secret",
                        "claim": f"checked {config.path}",
                        "api_key": secret,
                    }
                ],
                "source_ids": ["source-secret"],
                "validation_notes": [f"used {config.api_key_env_var}"],
                "metadata": {
                    "base_url": config.base_url,
                    "token": secret,
                    "nested": {"api_key": secret, "public": "ok"},
                },
            }
        )
        gateway = MaracaEvidenceGateway(config=config, client=client)

        bundle = gateway.collect(
            request_id="ev-008",
            work_id="work-008",
            query="config",
        )
        sent_request = client.calls[0]
        bundle_text = str(bundle)

        self.assertEqual(gateway.config.connector_name, "maraca-test")
        self.assertEqual(sent_request.base_url, config.base_url)
        self.assertEqual(sent_request.path, config.path)
        self.assertEqual(bundle.connector_name, "maraca-test")
        self.assertNotIn(config.base_url, bundle_text)
        self.assertNotIn(config.path, bundle_text)
        self.assertNotIn(config.api_key_env_var, bundle_text)
        self.assertNotIn(secret, bundle_text)
        self.assertEqual(bundle.metadata["nested"]["public"], "ok")

    def test_client_error_returns_empty_bundle_without_error_detail(self) -> None:
        secret = "error-secret"

        def failing_client(request):
            raise RuntimeError(f"boom {secret}")

        bundle = MaracaEvidenceGateway(client=failing_client).collect(
            request_id="ev-009",
            work_id="work-009",
            query="failure",
        )

        self.assertEqual(bundle.evidence_items, ())
        self.assertEqual(bundle.metadata["status"], "client_error")
        self.assertEqual(bundle.validation_notes, ("MARACA evidence gateway client error.",))
        self.assertNotIn(secret, str(bundle))

    def test_source_scan_guards_against_blocked_terms(self) -> None:
        blocked = (
            "re" + "quests",
            "ht" + "tpx",
            "so" + "cket",
            "sub" + "process",
            "pub" + "lish",
            "soc" + "ial",
            "sche" + "duler",
            "sc" + "rape",
            "ser" + "vice",
            "fi" + "le",
        )
        sources = [
            inspect.getsource(gateway_module),
            inspect.getsource(MaracaEvidenceGatewayTests),
        ]

        for source in sources:
            lowered = source.lower()
            for term in blocked:
                self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
