from dataclasses import FrozenInstanceError, is_dataclass
import inspect
import unittest

import harness_orchestrator.maraca_runtime_readiness as maraca_runtime_readiness
from harness_orchestrator.maraca_runtime_readiness import (
    DEFAULT_REQUIRED_PACKAGES,
    DEFAULT_REQUIRED_SETTINGS,
    REDACTED,
    MaracaRuntimeReadiness,
    MaracaRuntimeRequirements,
    evaluate_maraca_runtime_readiness,
)


class MaracaRuntimeReadinessTests(unittest.TestCase):
    def test_all_present_default_requirements_are_ready(self):
        readiness = self._ready()

        self.assertTrue(readiness.ready)
        self.assertEqual("ready", readiness.status)
        self.assertEqual((), readiness.blockers)
        self.assertEqual("present", readiness.snapshot["packages"]["langgraph"])
        self.assertEqual("collection-a", readiness.snapshot["environment"]["QDRANT_COLLECTION"])
        self.assertEqual("neo4j", readiness.snapshot["environment"]["NEO4J_DATABASE"])
        self.assertEqual(DEFAULT_REQUIRED_PACKAGES, readiness.requirements.required_packages)
        self.assertEqual(DEFAULT_REQUIRED_SETTINGS, readiness.requirements.required_environment)

    def test_missing_package_fails_closed(self):
        packages = self._packages()
        packages.pop("neo4j")

        readiness = evaluate_maraca_runtime_readiness(
            installed_packages=packages,
            environment=self._environment(),
        )

        self.assertFalse(readiness.ready)
        self.assertEqual("blocked", readiness.status)
        self.assertIn("missing-package:neo4j", readiness.blockers)
        self.assertEqual("missing", readiness.snapshot["packages"]["neo4j"])

    def test_missing_environment_value_fails_closed(self):
        environment = self._environment()
        environment.pop("NEO4J_DATABASE")

        readiness = evaluate_maraca_runtime_readiness(
            installed_packages=self._packages(),
            environment=environment,
        )

        self.assertFalse(readiness.ready)
        self.assertIn("missing-environment:NEO4J_DATABASE", readiness.blockers)
        self.assertEqual("missing", readiness.snapshot["environment"]["NEO4J_DATABASE"])

    def test_missing_config_value_fails_closed(self):
        requirements = MaracaRuntimeRequirements(
            required_packages=("langgraph",),
            required_environment=(),
            required_config=("CUSTOM_CONFIG",),
        )

        readiness = evaluate_maraca_runtime_readiness(
            installed_packages={"langgraph": True},
            config={},
            requirements=requirements,
        )

        self.assertFalse(readiness.ready)
        self.assertIn("missing-config:CUSTOM_CONFIG", readiness.blockers)
        self.assertEqual("missing", readiness.snapshot["config"]["CUSTOM_CONFIG"])

    def test_blank_values_fail_closed(self):
        readiness = evaluate_maraca_runtime_readiness(
            installed_packages={**self._packages(), "langgraph": "   "},
            environment={**self._environment(), "QDRANT_COLLECTION": ""},
        )

        self.assertFalse(readiness.ready)
        self.assertIn("missing-package:langgraph", readiness.blockers)
        self.assertIn("missing-environment:QDRANT_COLLECTION", readiness.blockers)

    def test_secret_like_keys_and_values_are_redacted_and_blocking(self):
        requirements = MaracaRuntimeRequirements(
            required_packages=("langgraph",),
            required_environment=("MARACA_API_KEY", "VISIBLE_SETTING"),
            required_config=("runtime_password", "safe_config"),
        )

        readiness = evaluate_maraca_runtime_readiness(
            installed_packages={"langgraph": "installed"},
            environment={
                "MARACA_API_KEY": "abc123",
                "VISIBLE_SETTING": "contains-token-value",
            },
            config={
                "runtime_password": "abc123",
                "safe_config": "very-secret-value",
            },
            requirements=requirements,
        )

        self.assertFalse(readiness.ready)
        self.assertIn("redacted-environment-requirement", readiness.blockers)
        self.assertIn("redacted-environment:VISIBLE_SETTING", readiness.blockers)
        self.assertIn("redacted-config-requirement", readiness.blockers)
        self.assertIn("redacted-config:safe_config", readiness.blockers)
        self.assertEqual(REDACTED, readiness.snapshot["environment"][REDACTED])
        self.assertEqual(REDACTED, readiness.snapshot["environment"]["VISIBLE_SETTING"])
        self.assertEqual(REDACTED, readiness.snapshot["config"][REDACTED])
        self.assertEqual(REDACTED, readiness.snapshot["config"]["safe_config"])
        self.assertNotIn("MARACA_API_KEY", str(readiness.to_dict()))
        self.assertNotIn("runtime_password", str(readiness.to_dict()))
        self.assertNotIn("abc123", str(readiness.to_dict()))
        self.assertNotIn("contains-token-value", str(readiness.to_dict()))
        self.assertNotIn("very-secret-value", str(readiness.to_dict()))

    def test_records_are_frozen_and_serialize_plain_data(self):
        for record_type in (MaracaRuntimeRequirements, MaracaRuntimeReadiness):
            self.assertTrue(is_dataclass(record_type))
            self.assertTrue(record_type.__dataclass_params__.frozen)

        readiness = self._ready()
        data = readiness.to_dict()

        self.assertEqual(True, data["ready"])
        self.assertIsInstance(data["requirements"], dict)
        self.assertIsInstance(data["snapshot"], dict)
        self.assertIsInstance(data["snapshot"]["packages"], dict)

        with self.assertRaises(FrozenInstanceError):
            readiness.status = "changed"

    def test_configurable_requirements(self):
        requirements = MaracaRuntimeRequirements(
            required_packages=("custom-runtime",),
            required_environment=("CUSTOM_ENV",),
            required_config=("CUSTOM_CONFIG",),
        )

        readiness = evaluate_maraca_runtime_readiness(
            installed_packages={"custom-runtime": True},
            environment={"CUSTOM_ENV": "enabled"},
            config={"CUSTOM_CONFIG": 7},
            requirements=requirements,
        )

        self.assertTrue(readiness.ready)
        self.assertEqual("present", readiness.snapshot["packages"]["custom-runtime"])
        self.assertEqual("enabled", readiness.snapshot["environment"]["CUSTOM_ENV"])
        self.assertEqual(7, readiness.snapshot["config"]["CUSTOM_CONFIG"])

    def test_malformed_requirement_names_fail_closed(self):
        requirements = MaracaRuntimeRequirements(
            required_packages=("langgraph", "", 7),
            required_environment=("QDRANT_COLLECTION", " "),
            required_config=(None,),
        )

        readiness = evaluate_maraca_runtime_readiness(
            installed_packages={"langgraph": True},
            environment={"QDRANT_COLLECTION": "collection-a"},
            requirements=requirements,
        )

        self.assertFalse(readiness.ready)
        self.assertIn("blank-package-requirement", readiness.blockers)
        self.assertIn("malformed-package-requirement", readiness.blockers)
        self.assertIn("blank-environment-requirement", readiness.blockers)
        self.assertIn("malformed-config-requirement", readiness.blockers)

    def test_defaults_do_not_read_real_environment(self):
        readiness = evaluate_maraca_runtime_readiness()

        self.assertFalse(readiness.ready)
        self.assertIn("missing-package:langgraph", readiness.blockers)
        self.assertIn("missing-environment:QDRANT_COLLECTION", readiness.blockers)

    def test_source_has_no_forbidden_runtime_or_service_behavior(self):
        source = inspect.getsource(maraca_runtime_readiness)
        forbidden = (
            "importlib",
            "pkg_resources",
            "subprocess",
            "pathlib",
            "requests",
            "httpx",
            "socket",
            "urllib",
            "os.environ",
            "getenv",
            "MARACA.",
            "import maraca",
            "from maraca",
            "AI-Art",
            "AI_Artist",
            "scheduler",
            "watch_social",
            "Client(",
            "Service(",
            "scan",
        )

        for term in forbidden:
            self.assertNotIn(term, source)

    def _ready(self):
        return evaluate_maraca_runtime_readiness(
            installed_packages=self._packages(),
            environment=self._environment(),
        )

    def _packages(self):
        return {
            "langgraph": True,
            "llama-index-core": "0.1",
            "neo4j": object(),
            "qdrant-client": "installed",
        }

    def _environment(self):
        return {
            "QDRANT_COLLECTION": "collection-a",
            "NEO4J_DATABASE": "neo4j",
        }


if __name__ == "__main__":
    unittest.main()
