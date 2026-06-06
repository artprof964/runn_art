"""Inert AI-Art media release gateway boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import re
from typing import Any, Callable, Mapping, Protocol

from harness_orchestrator.contracts import GateDecision, MediaReleaseRequest


GATE_NAME = "ai-art-media-release"


@dataclass(frozen=True)
class AIArtMediaGatewayRequest:
    """Plain Harness request data passed only to an injected media client."""

    request_id: str
    work_id: str
    media_items: tuple[Mapping[str, Any], ...]
    target_channels: tuple[str, ...] = ()
    required_gates: tuple[str, ...] = ()
    evidence_bundle_id: str | None = None
    connector_name: str = "ai-art"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _EvaluatingClient(Protocol):
    def evaluate(self, request: AIArtMediaGatewayRequest) -> Any:
        """Evaluate an inert media release request."""


class _CheckingClient(Protocol):
    def check(self, request: AIArtMediaGatewayRequest) -> Any:
        """Check an inert media release request."""


Client = Callable[[AIArtMediaGatewayRequest], Any] | _EvaluatingClient | _CheckingClient

_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "password",
    "cre" + "dential",
    "author" + "ization",
    "auth",
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|cre"
    r"dential|author"
    r"ization|auth)"
    r"\s*[:=]\s*[^,\s;]+"
)
_ID_KEYS = ("id", "media_id", "asset_id", "artifact_id")
_MEDIA_ID_KEYS = ("media_id", "media_ids", "asset_id", "asset_ids")
_ARTIFACT_ID_KEYS = ("artifact_id", "artifact_ids")


class AIArtMediaGateway:
    """Map injected AI-Art-like release results to Harness gate decisions."""

    def __init__(
        self,
        *,
        client: Client | None = None,
        result_data: Any = None,
    ) -> None:
        self._client = client
        self._result_data = deepcopy(result_data)

    def evaluate(
        self,
        media_request: MediaReleaseRequest,
        *,
        result_data: Any = None,
    ) -> GateDecision:
        """Evaluate a release request without importing or running AI-Art."""

        request = self._gateway_request(media_request)
        has_result_data = result_data is not None or self._result_data is not None
        if not has_result_data and self._client is None:
            return self._decision(
                request=request,
                passed=False,
                reason="AI-Art media release gateway has no client or result configured.",
                blockers=("client-or-result-not-configured",),
                metadata={"status": "not_configured"},
            )

        if has_result_data:
            response = deepcopy(result_data if result_data is not None else self._result_data)
        else:
            try:
                response = self._call_client(request)
            except Exception:
                return self._decision(
                    request=request,
                    passed=False,
                    reason="AI-Art media release gateway client error.",
                    blockers=("client-error",),
                    metadata={"status": "client_error"},
                )

        result = self._plain_mapping(response)
        if result is None:
            return self._decision(
                request=request,
                passed=False,
                reason="AI-Art media release gateway result is malformed.",
                blockers=("malformed-result",),
                metadata={"status": "malformed"},
            )

        identity_blockers = self._identity_blockers(request=request, result=result)
        if identity_blockers:
            return self._decision(
                request=request,
                passed=False,
                reason="AI-Art media release gateway result identity mismatch.",
                blockers=identity_blockers,
                metadata={
                    "status": "identity_mismatch",
                    "result": self._result_metadata(result),
                },
            )

        state = self._result_state(result)
        if state is None:
            return self._decision(
                request=request,
                passed=False,
                reason="AI-Art media release gateway result is malformed.",
                blockers=("malformed-result",),
                metadata={"status": "malformed", "result": self._result_metadata(result)},
            )

        passed, blocked_checks = state
        blockers = () if passed else self._blockers(result, blocked_checks)
        reason = self._reason(result, passed=passed)
        return self._decision(
            request=request,
            passed=passed,
            reason=reason,
            blockers=blockers,
            metadata={
                "status": "allowed" if passed else "blocked",
                "result": self._result_metadata(result),
                "blocked_checks": tuple(
                    self._check_name(check, "blocked-check") for check in blocked_checks
                ),
            },
        )

    def _gateway_request(
        self, media_request: MediaReleaseRequest
    ) -> AIArtMediaGatewayRequest:
        return AIArtMediaGatewayRequest(
            request_id=media_request.request_id,
            work_id=media_request.work_id,
            media_items=tuple(
                self._safe_mapping(dict(item)) if isinstance(item, Mapping) else {}
                for item in media_request.media_items
            ),
            target_channels=tuple(media_request.target_channels),
            required_gates=tuple(media_request.required_gates),
            evidence_bundle_id=media_request.evidence_bundle_id,
            connector_name=media_request.connector_name,
            metadata=self._safe_mapping(dict(media_request.metadata)),
        )

    def _call_client(self, request: AIArtMediaGatewayRequest) -> Any:
        client = self._client
        if callable(client):
            return client(request)
        for method_name in ("evaluate", "check"):
            method = getattr(client, method_name, None)
            if callable(method):
                return method(request)
        raise TypeError("AI-Art media release client is not callable.")

    def _result_state(
        self, result: Mapping[str, Any]
    ) -> tuple[bool, tuple[Mapping[str, Any], ...]] | None:
        allowed = result.get("allowed")
        blocked = result.get("blocked")
        blocked_checks = self._blocked_checks(result)

        if isinstance(allowed, bool) and isinstance(blocked, bool):
            if allowed == blocked:
                return None
            return (allowed and not blocked and not blocked_checks, blocked_checks)
        if isinstance(allowed, bool):
            return (allowed and not blocked_checks, blocked_checks)
        if isinstance(blocked, bool):
            return (not blocked and not blocked_checks, blocked_checks)
        if self._first_value(result, ("checks", "blocked_checks")) is not None:
            return (not blocked_checks, blocked_checks)
        return None

    def _blocked_checks(
        self, result: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        blocked: list[Mapping[str, Any]] = []
        for check in self._records(result.get("blocked_checks")):
            blocked.append(check)

        for check in self._records(result.get("checks")):
            if self._check_is_blocked(check):
                blocked.append(check)

        return tuple(blocked)

    def _check_is_blocked(self, check: Mapping[str, Any]) -> bool:
        if isinstance(check.get("blocked"), bool):
            return bool(check["blocked"])
        for key in ("allowed", "passed"):
            if isinstance(check.get(key), bool):
                return not bool(check[key])
        status = check.get("status")
        if isinstance(status, str):
            return status.strip().lower() in {
                "block",
                "blocked",
                "deny",
                "denied",
                "fail",
                "failed",
            }
        return False

    def _blockers(
        self,
        result: Mapping[str, Any],
        blocked_checks: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, ...]:
        blockers = list(self._strings(self._first_value(result, ("blockers", "reasons"))))
        for check in blocked_checks:
            blockers.extend(self._strings(check.get("blockers")))
            blocker = check.get("blocker")
            if blocker is not None:
                blockers.append(self._safe_text(str(blocker), "blocked-check"))
            if not blockers or blocker is None:
                blockers.append(self._check_name(check, "blocked-check"))
        return tuple(dict.fromkeys(blockers or ["media-release-blocked"]))

    def _reason(self, result: Mapping[str, Any], *, passed: bool) -> str:
        fallback = (
            "AI-Art media release gateway allowed the release."
            if passed
            else "AI-Art media release gateway blocked the release."
        )
        return self._safe_text(result.get("reason"), fallback)

    def _identity_blockers(
        self,
        *,
        request: AIArtMediaGatewayRequest,
        result: Mapping[str, Any],
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        result_work_id = self._safe_optional_text(result.get("work_id"))
        if result_work_id is not None and result_work_id != request.work_id:
            blockers.append("work-identity-mismatch")

        result_evidence_id = self._safe_optional_text(result.get("evidence_bundle_id"))
        if (
            result_evidence_id is not None
            and request.evidence_bundle_id is not None
            and result_evidence_id != request.evidence_bundle_id
        ):
            blockers.append("evidence-identity-mismatch")

        expected_media_ids, expected_artifact_ids = self._request_id_sets(request)
        supplied_media_ids = self._result_ids(result, _MEDIA_ID_KEYS)
        supplied_artifact_ids = self._result_ids(result, _ARTIFACT_ID_KEYS)
        if supplied_media_ids and expected_media_ids and not supplied_media_ids <= expected_media_ids:
            blockers.append("media-identity-mismatch")
        if (
            supplied_artifact_ids
            and expected_artifact_ids
            and not supplied_artifact_ids <= expected_artifact_ids
        ):
            blockers.append("artifact-identity-mismatch")
        return tuple(dict.fromkeys(blockers))

    def _request_id_sets(
        self, request: AIArtMediaGatewayRequest
    ) -> tuple[set[str], set[str]]:
        media_ids: set[str] = set()
        artifact_ids: set[str] = set()
        for item in request.media_items:
            for key in _ID_KEYS:
                value = item.get(key)
                if value is not None:
                    media_ids.add(str(value))
            artifact = item.get("artifact_id")
            if artifact is not None:
                artifact_ids.add(str(artifact))
        return media_ids, artifact_ids

    def _result_ids(self, result: Mapping[str, Any], keys: tuple[str, ...]) -> set[str]:
        ids: set[str] = set()
        for source in (result, self._metadata(result)):
            for key in keys:
                ids.update(self._identity_strings(source.get(key)))

        for check in self._records(result.get("checks")) + self._records(
            result.get("blocked_checks")
        ):
            metadata = self._metadata(check)
            for source in (check, metadata):
                for key in keys:
                    ids.update(self._identity_strings(source.get(key)))
        return ids

    def _identity_strings(self, value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            return {value}
        if isinstance(value, (list, tuple, set)):
            return {str(item) for item in value if item is not None}
        return {str(value)}

    def _result_metadata(self, result: Mapping[str, Any]) -> dict[str, Any]:
        metadata = self._metadata(result)
        for key in (
            "work_id",
            "evidence_bundle_id",
            "media_id",
            "media_ids",
            "artifact_id",
            "artifact_ids",
            "checks",
        ):
            if key in result:
                metadata.setdefault(key, result[key])
        return self._safe_mapping(metadata)

    def _metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        metadata = value.get("metadata")
        record = self._plain_mapping(metadata)
        return dict(record or {})

    def _records(self, value: Any) -> tuple[Mapping[str, Any], ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            value = (value,)
        if not isinstance(value, (list, tuple)):
            return ()
        records: list[Mapping[str, Any]] = []
        for item in value:
            record = self._plain_mapping(item)
            if record is not None:
                records.append(record)
        return tuple(records)

    def _plain_mapping(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, Mapping):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            mapped = to_dict()
            if isinstance(mapped, Mapping):
                return dict(mapped)
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        return None

    def _first_value(self, value: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            item = value.get(key)
            if item is not None:
                return item
        return None

    def _strings(self, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (self._safe_text(value, ""),)
        if isinstance(value, (list, tuple)):
            return tuple(self._safe_text(str(item), "") for item in value)
        return ()

    def _check_name(self, check: Mapping[str, Any], fallback: str) -> str:
        return self._safe_text(
            self._first_value(check, ("blocker", "name", "check", "id", "reason")),
            fallback,
        )

    def _safe_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    def _safe_text(self, value: Any, fallback: str) -> str:
        if not isinstance(value, str) or not value:
            return self._redact_text(fallback)
        return self._redact_text(value)

    def _safe_mapping(self, value: Mapping[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if self._sensitive_key(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = self._safe_value(item)
        return redacted

    def _safe_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, Mapping):
            return self._safe_mapping(value)
        if isinstance(value, tuple):
            return tuple(self._safe_value(item) for item in value)
        if isinstance(value, list):
            return [self._safe_value(item) for item in value]
        return value

    def _redact_text(self, value: str) -> str:
        return _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}=[redacted]", value)

    def _sensitive_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(part in normalized for part in _SENSITIVE_KEY_PARTS)

    def _decision(
        self,
        *,
        request: AIArtMediaGatewayRequest,
        passed: bool,
        reason: str,
        blockers: tuple[str, ...],
        metadata: Mapping[str, Any],
    ) -> GateDecision:
        decision_metadata = {
            "request_id": request.request_id,
            "status": metadata.get("status", "allowed" if passed else "blocked"),
        }
        decision_metadata.update(dict(metadata))
        return GateDecision(
            decision_id=f"{GATE_NAME}:{request.request_id}",
            work_id=request.work_id,
            gate_name=GATE_NAME,
            passed=passed,
            reason=self._safe_text(reason, ""),
            blockers=tuple(self._safe_text(blocker, "media-release-blocked") for blocker in blockers),
            evidence_bundle_id=request.evidence_bundle_id,
            metadata=self._safe_mapping(decision_metadata),
        )
