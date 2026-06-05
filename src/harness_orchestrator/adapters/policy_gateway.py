"""Injectable policy gateway boundary for Harness decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable, Mapping, Protocol

from harness_orchestrator.contracts import GateDecision, GovernedWorkRequest


Record = Mapping[str, Any]


@dataclass(frozen=True)
class PolicyGatewayConfig:
    """Configuration for an inert policy evaluator boundary."""

    gate_name: str = "policy"
    default_blocker: str = "policy-denied"
    missing_client_blocker: str = "client-not-configured"
    redacted_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyGatewayRequest:
    """Plain request data passed only to an injected policy client."""

    request_id: str
    work_id: str
    operation: str
    payload: Record = field(default_factory=dict)
    metadata: Record = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _EvaluatingClient(Protocol):
    def evaluate(self, request: PolicyGatewayRequest) -> Any:
        """Evaluate an inert request envelope."""


Client = Callable[[PolicyGatewayRequest], Any] | _EvaluatingClient


class PolicyGateway:
    """Gate Harness work through an injected policy evaluator."""

    def __init__(
        self,
        config: PolicyGatewayConfig | None = None,
        client: Client | None = None,
    ) -> None:
        self.config = config or PolicyGatewayConfig()
        self._client = client

    def evaluate(
        self,
        work_request: GovernedWorkRequest | Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
        work_id: str | None = None,
        operation: str = "policy-evaluation",
        payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> GateDecision:
        """Evaluate a governed request, or fail closed when no client is configured."""

        request = self._request(
            work_request,
            request_id=request_id,
            work_id=work_id,
            operation=operation,
            payload=payload,
            metadata=metadata,
        )

        if self._client is None:
            return self._decision(
                request=request,
                passed=False,
                reason="Policy gateway has no client configured.",
                blockers=(self.config.missing_client_blocker,),
                status="not_configured",
            )

        try:
            response = self._call_client(request)
        except Exception:
            return self._decision(
                request=request,
                passed=False,
                reason="Policy gateway client error.",
                blockers=("client-error",),
                status="client_error",
            )

        return self._map_response(request=request, response=response)

    def _request(
        self,
        work_request: GovernedWorkRequest | Mapping[str, Any] | None,
        *,
        request_id: str | None,
        work_id: str | None,
        operation: str,
        payload: Mapping[str, Any] | None,
        metadata: Mapping[str, Any] | None,
    ) -> PolicyGatewayRequest:
        request_payload = self._plain_mapping(work_request)
        if payload:
            request_payload.update(self._plain_mapping(payload))

        resolved_work_id = str(
            work_id
            or request_payload.get("work_id")
            or request_payload.get("id")
            or "unknown-work"
        )
        resolved_request_id = str(
            request_id
            or self._value(work_request, "request_id")
            or request_payload.get("request_id")
            or resolved_work_id
        )
        resolved_operation = operation
        embedded_operation = request_payload.get("operation")
        if operation == "policy-evaluation" and embedded_operation is not None:
            resolved_operation = str(embedded_operation)

        request_metadata = self._metadata(work_request)
        request_metadata.update(self._plain_mapping(metadata))
        request_metadata.setdefault("operation", resolved_operation)
        if "channel" in request_payload:
            request_metadata.setdefault("channel", request_payload["channel"])
        if "policy_scope" in request_payload:
            request_metadata.setdefault("policy_scope", request_payload["policy_scope"])

        return PolicyGatewayRequest(
            request_id=resolved_request_id,
            work_id=resolved_work_id,
            operation=resolved_operation,
            payload=self._redacted_mapping(request_payload),
            metadata=self._redacted_mapping(request_metadata),
        )

    def _call_client(self, request: PolicyGatewayRequest) -> Any:
        client = self._client
        if callable(client):
            return client(request)
        return client.evaluate(request)

    def _map_response(
        self,
        *,
        request: PolicyGatewayRequest,
        response: Any,
    ) -> GateDecision:
        passed = self._response_passed(response)
        status = "allowed" if passed else "denied"
        fallback_reason = (
            "Policy gateway allowed the operation."
            if passed
            else "Policy gateway denied the operation."
        )
        reason = self._safe_text(
            self._value(response, "reason") or self._value(response, "message"),
            fallback_reason,
            response=response,
        )
        blockers = () if passed else self._safe_blockers(response)
        metadata = self._response_metadata(request=request, response=response, status=status)

        if self._response_malformed(response):
            passed = False
            reason = "Policy gateway response was not recognized."
            blockers = ("policy-response-invalid",)
            metadata["status"] = "invalid_response"

        return self._decision(
            request=request,
            passed=passed,
            reason=reason,
            blockers=blockers,
            status=str(metadata["status"]),
            metadata=metadata,
        )

    def _response_passed(self, response: Any) -> bool:
        for key in ("allowed", "passed"):
            value = self._value(response, key)
            if isinstance(value, bool):
                return value

        decision = self._value(response, "decision") or self._value(response, "status")
        if isinstance(decision, str):
            normalized = decision.strip().lower()
            if normalized in {"allow", "allowed", "approve", "approved", "pass", "passed"}:
                return True
            if normalized in {"deny", "denied", "block", "blocked", "fail", "failed"}:
                return False

        return False

    def _response_malformed(self, response: Any) -> bool:
        if response is None:
            return True
        if any(isinstance(self._value(response, key), bool) for key in ("allowed", "passed")):
            return False
        decision = self._value(response, "decision") or self._value(response, "status")
        if isinstance(decision, str):
            return decision.strip().lower() not in {
                "allow",
                "allowed",
                "approve",
                "approved",
                "pass",
                "passed",
                "deny",
                "denied",
                "block",
                "blocked",
                "fail",
                "failed",
            }
        return True

    def _safe_blockers(self, response: Any) -> tuple[str, ...]:
        value = self._value(response, "blockers")
        if value is None:
            value = self._value(response, "reasons")

        if isinstance(value, str):
            blockers = (self._redact_text(value, response=response),)
        elif isinstance(value, (list, tuple)):
            blockers = tuple(
                self._redact_text(str(item), response=response) for item in value
            )
        else:
            blockers = ()

        return blockers or (self.config.default_blocker,)

    def _response_metadata(
        self,
        *,
        request: PolicyGatewayRequest,
        response: Any,
        status: str,
    ) -> dict[str, Any]:
        response_metadata = self._plain_mapping(self._value(response, "metadata"))
        metadata = {
            "request_id": request.request_id,
            "operation": request.operation,
            "status": status,
        }
        for key in ("channel", "policy_scope", "trigger_type"):
            value = request.metadata.get(key) or request.payload.get(key)
            if value is not None:
                metadata[key] = value
        metadata.update(response_metadata)
        return self._redacted_mapping(metadata, response=response)

    def _decision(
        self,
        *,
        request: PolicyGatewayRequest,
        passed: bool,
        reason: str,
        blockers: tuple[str, ...],
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> GateDecision:
        decision_metadata = {
            "request_id": request.request_id,
            "operation": request.operation,
            "status": status,
        }
        decision_metadata.update(dict(metadata or {}))
        return GateDecision(
            decision_id=f"{self.config.gate_name}:{request.request_id}",
            work_id=request.work_id,
            gate_name=self.config.gate_name,
            passed=passed,
            reason=reason,
            blockers=blockers,
            metadata=self._redacted_mapping(decision_metadata),
        )

    def _metadata(self, value: Any) -> dict[str, Any]:
        metadata = self._value(value, "metadata")
        if isinstance(metadata, Mapping):
            return self._plain_mapping(metadata)
        return {}

    def _plain_mapping(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
        if is_dataclass(value):
            return asdict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            mapped = to_dict()
            if isinstance(mapped, Mapping):
                return {str(key): item for key, item in mapped.items()}
        return {}

    def _redacted_mapping(
        self,
        value: Mapping[str, Any],
        *,
        response: Any = None,
    ) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if self._sensitive_key(str(key)):
                redacted[str(key)] = "[redacted]"
            elif isinstance(item, Mapping):
                redacted[str(key)] = self._redacted_mapping(item, response=response)
            elif isinstance(item, (list, tuple)):
                redacted[str(key)] = tuple(
                    self._redacted_value(nested, response=response) for nested in item
                )
            else:
                redacted[str(key)] = self._redacted_value(item, response=response)
        return redacted

    def _redacted_value(self, value: Any, *, response: Any = None) -> Any:
        if isinstance(value, str):
            return self._redact_text(value, response=response)
        return value

    def _safe_text(self, value: Any, fallback: str, *, response: Any) -> str:
        if not isinstance(value, str) or not value:
            return fallback
        return self._redact_text(value, response=response)

    def _redact_text(self, value: str, *, response: Any = None) -> str:
        redacted = value
        for secret in self._redaction_values(response):
            if secret:
                redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def _redaction_values(self, response: Any = None) -> tuple[str, ...]:
        values = list(self.config.redacted_values)
        values.extend(self._sensitive_values(response))
        return tuple(str(value) for value in values if value)

    def _sensitive_values(self, value: Any) -> tuple[str, ...]:
        if isinstance(value, Mapping):
            found: list[str] = []
            for key, item in value.items():
                if self._sensitive_key(str(key)) and isinstance(item, str):
                    found.append(item)
                found.extend(self._sensitive_values(item))
            return tuple(found)
        if isinstance(value, (list, tuple)):
            found = []
            for item in value:
                found.extend(self._sensitive_values(item))
            return tuple(found)
        return ()

    def _value(self, value: Any, key: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(key)
        return getattr(value, key, None)

    def _sensitive_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(
            token in normalized
            for token in ("secret", "token", "api_key", "password", "credential")
        )


def evaluate(**kwargs: Any) -> GateDecision:
    """Convenience wrapper for the default fail-closed policy boundary."""

    return PolicyGateway().evaluate(**kwargs)
