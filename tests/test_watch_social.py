from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.watch_social as watch_module
from harness_orchestrator.watch_social import (
    SocialWatch,
    WatchCandidate,
    WatchCandidateRequest,
    WatchCandidateResult,
    WatchConfig,
    candidates,
)


class FakeCallableConnector:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return self.response


class FakeObjectConnector:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def list_candidates(self, request):
        self.calls.append(request)
        return self.response


class DictLikeCandidate:
    def to_dict(self):
        return {
            "id": "dict-like",
            "source": "fixture",
            "title": "Dict-like candidate",
        }


class WatchSocialTests(unittest.TestCase):
    def test_records_are_frozen_dataclasses(self) -> None:
        for record_type in (
            WatchCandidate,
            WatchCandidateRequest,
            WatchCandidateResult,
            WatchConfig,
        ):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        candidate = WatchCandidate(candidate_id="c-001", work_id="work-001", source_name="x")
        with self.assertRaises(FrozenInstanceError):
            candidate.title = "changed"

    def test_default_watch_is_inert_and_does_not_call_connector(self) -> None:
        connector = FakeCallableConnector({"candidates": [{"id": "should-not-run"}]})
        watch = SocialWatch(connector=connector)

        result = watch.candidates(
            request_id="watch-001",
            work_id="work-001",
            topics=("memory",),
        )

        self.assertEqual(connector.calls, [])
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blockers, ("watch-disabled",))
        self.assertEqual(result.candidates, ())
        self.assertIn("disabled", result.notes[0])

    def test_enabled_allowed_but_missing_connector_blocks_by_default(self) -> None:
        watch = SocialWatch(
            config=WatchConfig(
                enabled=True,
                connector_name="fixture",
                allowed_connectors=("fixture",),
            )
        )

        result = watch.candidates(request_id="watch-002", work_id="work-002")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blockers, ("connector-not-configured",))
        self.assertEqual(result.candidates, ())

    def test_allowed_fake_connector_normalizes_candidates(self) -> None:
        connector = FakeCallableConnector(
            {
                "candidates": [
                    {
                        "id": "raw-001",
                        "source": "fixture",
                        "title": "First candidate",
                        "body": "Plain text only.",
                        "url": "https://example.invalid/item/1",
                        "tags": ["alpha", "beta"],
                        "metadata": {"score": 0.8, "token": "hidden"},
                    },
                    DictLikeCandidate(),
                ],
                "notes": ["normalized"],
            }
        )
        watch = SocialWatch(
            config=WatchConfig(
                enabled=True,
                connector_name="fixture",
                allowed_connectors=("fixture",),
                max_candidates=5,
            ),
            connector=connector,
        )

        result = watch.candidates(
            request_id="watch-003",
            work_id="work-003",
            topics=("topic-a",),
        )

        self.assertEqual(len(connector.calls), 1)
        self.assertIsInstance(connector.calls[0], WatchCandidateRequest)
        self.assertEqual(connector.calls[0].topics, ("topic-a",))
        self.assertEqual(result.status, "candidates_available")
        self.assertEqual(result.notes, ("normalized",))
        self.assertEqual(result.candidates[0].candidate_id, "raw-001")
        self.assertEqual(result.candidates[0].work_id, "work-003")
        self.assertEqual(result.candidates[0].source_name, "fixture")
        self.assertEqual(result.candidates[0].summary, "Plain text only.")
        self.assertEqual(result.candidates[0].reference, "https://example.invalid/item/1")
        self.assertEqual(result.candidates[0].tags, ("alpha", "beta"))
        self.assertEqual(result.candidates[0].metadata, {"score": 0.8})
        self.assertEqual(result.candidates[1].candidate_id, "dict-like")

    def test_connector_allow_list_blocks_before_connector_call(self) -> None:
        connector = FakeCallableConnector({"candidates": [{"id": "blocked"}]})
        watch = SocialWatch(
            config=WatchConfig(
                enabled=True,
                connector_name="not-allowed",
                allowed_connectors=("fixture",),
            ),
            connector=connector,
        )

        result = watch.candidates(request_id="watch-004", work_id="work-004")

        self.assertEqual(connector.calls, [])
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blockers, ("connector-not-allowed",))

    def test_local_candidates_are_available_only_when_enabled_and_allowed(self) -> None:
        watch = SocialWatch(
            config=WatchConfig(
                enabled=True,
                connector_name="manual",
                allowed_connectors=("manual",),
                local_candidates=(
                    {
                        "candidate_id": "local-001",
                        "work_id": "other-work",
                        "source_name": "manual-note",
                        "title": "Manual candidate",
                    },
                ),
            )
        )

        result = watch.candidates(request_id="watch-005", work_id="work-005")

        self.assertEqual(result.status, "local")
        self.assertEqual(result.candidates[0].candidate_id, "local-001")
        self.assertEqual(result.candidates[0].work_id, "other-work")
        self.assertEqual(result.candidates[0].source_name, "manual-note")

    def test_object_connector_and_convenience_wrapper(self) -> None:
        connector = FakeObjectConnector(
            [
                {
                    "title": "Generated identifier",
                    "summary": "No id supplied.",
                }
            ]
        )
        watch = SocialWatch(
            config=WatchConfig(
                enabled=True,
                connector_name="fixture",
                allowed_connectors=("fixture",),
            ),
            connector=connector,
        )

        result = watch.evaluate(request_id="watch-006", work_id="work-006")
        default_result = candidates(request_id="watch-007", work_id="work-007")

        self.assertEqual(len(connector.calls), 1)
        self.assertEqual(result.candidates[0].candidate_id, "watch-006:1")
        self.assertEqual(default_result.blockers, ("watch-disabled",))

    def test_serialization_is_plain_data(self) -> None:
        result = WatchCandidateResult(
            request_id="watch-008",
            work_id="work-008",
            connector_name="manual",
            status="local",
            candidates=(
                WatchCandidate(
                    candidate_id="cand-008",
                    work_id="work-008",
                    source_name="manual",
                    tags=("review",),
                ),
            ),
            metadata={"candidate_count": 1},
        )

        self.assertEqual(
            result.to_dict(),
            {
                "request_id": "watch-008",
                "work_id": "work-008",
                "connector_name": "manual",
                "status": "local",
                "candidates": (
                    {
                        "candidate_id": "cand-008",
                        "work_id": "work-008",
                        "source_name": "manual",
                        "title": "",
                        "summary": "",
                        "reference": None,
                        "tags": ("review",),
                        "metadata": {},
                    },
                ),
                "blockers": (),
                "notes": (),
                "metadata": {"candidate_count": 1},
            },
        )

    def test_source_guard_for_blocked_side_effect_terms(self) -> None:
        blocked = (
            "re" + "quests",
            "ht" + "tpx",
            "so" + "cket",
            "sub" + "process",
            "pub" + "lish",
            "sche" + "duler",
            "sc" + "rape",
            "ser" + "vice",
            "sle" + "ep",
            "thr" + "ead",
            "tim" + "er",
        )
        sources = [
            inspect.getsource(watch_module),
            inspect.getsource(WatchSocialTests),
        ]

        for source in sources:
            lowered = source.lower()
            for term in blocked:
                self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
