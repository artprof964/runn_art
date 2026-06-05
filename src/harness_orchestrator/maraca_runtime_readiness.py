"""Pure readiness records for optional future MARACA runtime boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


DEFAULT_REQUIRED_PACKAGES = (
    "langgraph",
    "llama-index-core",
    "neo4j",
    "qdrant-client",
)
DEFAULT_REQUIRED_SETTINGS = ("QDRANT_COLLECTION", "NEO4J_DATABASE")
REDACTED = "<redacted>"
_SECRET_TERMS = ("key", "token", "secret", "password")


PackageAvailability = Mapping[str, object]
Settings = Mapping[str, object]


@dataclass(frozen=True)
class MaracaRuntimeRequirements:
    """Explicit MARACA runtime names the Harness boundary may check."""

    required_packages: tuple[str, ...] = DEFAULT_REQUIRED_PACKAGES
    required_environment: tuple[str, ...] = DEFAULT_REQUIRED_SETTINGS
    required_config: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "required_packages": _redacted_names(self.required_packages),
            "required_environment": _redacted_names(self.required_environment),
            "required_config": _redacted_names(self.required_config),
        }


@dataclass(frozen=True)
class MaracaRuntimeReadiness:
    """Plain readiness result built only from caller-injected mappings."""

    ready: bool
    status: str
    blockers: tuple[str, ...]
    requirements: MaracaRuntimeRequirements
    snapshot: Mapping[str, Mapping[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": self.blockers,
            "requirements": self.requirements.to_dict(),
            "snapshot": asdict(self)["snapshot"],
        }


def evaluate_maraca_runtime_readiness(
    *,
    installed_packages: PackageAvailability | None = None,
    environment: Settings | None = None,
    config: Settings | None = None,
    requirements: MaracaRuntimeRequirements | None = None,
) -> MaracaRuntimeReadiness:
    """Evaluate optional MARACA readiness from explicit injected data only."""

    resolved = requirements or MaracaRuntimeRequirements()
    packages = dict(installed_packages or {})
    env = dict(environment or {})
    cfg = dict(config or {})

    blockers: list[str] = []
    blockers.extend(_requirement_name_blockers("package", resolved.required_packages))
    blockers.extend(_requirement_name_blockers("environment", resolved.required_environment))
    blockers.extend(_requirement_name_blockers("config", resolved.required_config))

    snapshot = {
        "packages": _package_snapshot(resolved.required_packages, packages, blockers),
        "environment": _settings_snapshot(
            "environment",
            resolved.required_environment,
            env,
            blockers,
        ),
        "config": _settings_snapshot("config", resolved.required_config, cfg, blockers),
    }
    deduped_blockers = tuple(dict.fromkeys(blockers))
    ready = not deduped_blockers
    return MaracaRuntimeReadiness(
        ready=ready,
        status="ready" if ready else "blocked",
        blockers=deduped_blockers,
        requirements=resolved,
        snapshot=snapshot,
    )


def _package_snapshot(
    required_packages: tuple[str, ...],
    installed_packages: dict[str, object],
    blockers: list[str],
) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for package in required_packages:
        if not _valid_requirement_name(package):
            continue

        snapshot_name = _snapshot_name(package)
        if _is_secret_like(package):
            blockers.append("redacted-package-requirement")
            snapshot[snapshot_name] = REDACTED
            continue

        value = installed_packages.get(package)
        if _missing_or_blank(value):
            blockers.append(f"missing-package:{package}")
            snapshot[package] = "missing"
            continue
        if _is_secret_like(value):
            blockers.append(f"redacted-package:{package}")
            snapshot[snapshot_name] = REDACTED
            continue

        snapshot[snapshot_name] = "present"
    return snapshot


def _settings_snapshot(
    kind: str,
    required_names: tuple[str, ...],
    supplied: dict[str, object],
    blockers: list[str],
) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for name in required_names:
        if not _valid_requirement_name(name):
            continue

        snapshot_name = _snapshot_name(name)
        if _is_secret_like(name):
            blockers.append(f"redacted-{kind}-requirement")
            snapshot[snapshot_name] = REDACTED
            continue

        value = supplied.get(name)
        if _missing_or_blank(value):
            blockers.append(f"missing-{kind}:{name}")
            snapshot[snapshot_name] = "missing"
            continue
        if _is_secret_like(value):
            blockers.append(f"redacted-{kind}:{name}")
            snapshot[snapshot_name] = REDACTED
            continue

        snapshot[snapshot_name] = value
    return snapshot


def _requirement_name_blockers(kind: str, names: tuple[str, ...]) -> tuple[str, ...]:
    blockers: list[str] = []
    for name in names:
        if not isinstance(name, str):
            blockers.append(f"malformed-{kind}-requirement")
        elif _missing_or_blank(name):
            blockers.append(f"blank-{kind}-requirement")
    return tuple(blockers)


def _valid_requirement_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _missing_or_blank(value: object) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _is_secret_like(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in _SECRET_TERMS)


def _snapshot_name(name: str) -> str:
    if _is_secret_like(name):
        return REDACTED
    return name


def _redacted_names(names: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(_snapshot_name(name) if isinstance(name, str) else name for name in names)
