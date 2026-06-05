"""Injectable safety gateway wrapper for AI-Art evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
from typing import Any, Callable, Mapping, Protocol

from harness_orchestrator.contracts import GateDecision


@dataclass(frozen=True)
class AIArtSafetyGatewayConfig:
    """Configuration for an external AI-Art safety evaluator."""

    base_url: str = "https://deepseek-open-art.example.invalid"
    api_key_env_var: str = "deepseek-open-art"
    safety_path: str = "/safety/evaluate"
    gate_name: str = "ai-art-safety"


@dataclass(frozen=True)
class SafetyGatewayRequest:
    """Inert request data passed to an injected safety client."""

    url: str
    path: str
    payload: Mapping[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


class _EvaluatingClient(Protocol):
    def evaluate(self, request: SafetyGatewayRequest) -> Any:
        """Evaluate an inert request envelope."""


Client = Callable[[SafetyGatewayRequest], Any] | _EvaluatingClient


class AIArtSafetyGateway:
    """Gate AI-Art work through an injectable safety evaluator."""

    def __init__(
        self,
        config: AIArtSafetyGatewayConfig | None = None,
        client: Client | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config or AIArtSafetyGatewayConfig()
        self._client = client
        self._env = env if env is not None else environ

    def evaluate(
        self,
        *,
        request_id: str,
        work_id: str,
        operation: str = "safety-evaluation",
        payload: Mapping[str, Any] | None = None,
    ) -> GateDecision:
        """Evaluate work with an injected client, or block inertly by default."""

        if self._client is None:
            return self._decision(
                request_id=request_id,
                work_id=work_id,
                passed=False,
                reason="AI-Art safety gateway has no client configured.",
                blockers=("client-not-configured",),
                metadata={
                    "request_id": request_id,
                    "operation": operation,
                    "status": "not_configured",
                },
            )

        request = SafetyGatewayRequest(
            url=self._endpoint_url(),
            path=self.config.safety_path,
            payload={
                "request_id": request_id,
                "work_id": work_id,
                "operation": operation,
                "payload": dict(payload or {}),
            },
            headers=self._headers(),
        )

        try:
            response = self._call_client(request)
        except Exception:
            return self._decision(
                request_id=request_id,
                work_id=work_id,
                passed=False,
                reason="AI-Art safety gateway client error.",
                blockers=("client-error",),
                metadata={
                    "request_id": request_id,
                    "operation": operation,
                    "status": "client_error",
                    "path": self.config.safety_path,
                },
            )

        return self._map_response(
            request_id=request_id,
            work_id=work_id,
            operation=operation,
            response=response,
        )

    def _call_client(self, request: SafetyGatewayRequest) -> Any:
        if callable(self._client):
            return self._client(request)
        return self._client.evaluate(request)

    def _endpoint_url(self) -> str:
        base_url = self.config.base_url.rstrip("/")
        safety_path = self.config.safety_path
        if not safety_path.startswith("/"):
            safety_path = f"/{safety_path}"
        return f"{base_url}{safety_path}"

    def _headers(self) -> Mapping[str, str]:
        api_key = self._env.get(self.config.api_key_env_var)
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def _map_response(
        self,
        *,
        request_id: str,
        work_id: str,
        operation: str,
        response: Any,
    ) -> GateDecision:
        passed = self._response_passed(response)
        if passed:
            reason = self._safe_text(
                self._response_value(response, "reason"),
                "AI-Art safety gateway allowed the operation.",
            )
            blockers: tuple[str, ...] = ()
            status = "allowed"
        else:
            reason = self._safe_text(
                self._response_value(response, "reason"),
                "AI-Art safety gateway denied the operation.",
            )
            blockers = self._safe_blockers(response)
            status = "denied"

        return self._decision(
            request_id=request_id,
            work_id=work_id,
            passed=passed,
            reason=reason,
            blockers=blockers,
            metadata={
                "request_id": request_id,
                "operation": operation,
                "status": status,
                "path": self.config.safety_path,
            },
        )

    def _response_passed(self, response: Any) -> bool:
        for key in ("allowed", "passed"):
            value = self._response_value(response, key)
            if isinstance(value, bool):
                return value

        value = self._response_value(response, "decision")
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"allow", "allowed", "pass", "passed", "approved"}:
                return True
            if normalized in {"deny", "denied", "block", "blocked", "failed"}:
                return False

        return False

    def _safe_blockers(self, response: Any) -> tuple[str, ...]:
        value = self._response_value(response, "blockers")
        if value is None:
            value = self._response_value(response, "reasons")

        if isinstance(value, str):
            blockers = (self._redact(value),)
        elif isinstance(value, (list, tuple)):
            blockers = tuple(self._redact(str(item)) for item in value)
        else:
            blockers = ()

        return blockers or ("safety-policy",)

    def _safe_text(self, value: Any, fallback: str) -> str:
        if not isinstance(value, str) or not value:
            return fallback
        return self._redact(value)

    def _redact(self, value: str) -> str:
        api_key = self._env.get(self.config.api_key_env_var)
        if api_key:
            return value.replace(api_key, "[redacted]")
        return value

    def _response_value(self, response: Any, key: str) -> Any:
        if isinstance(response, Mapping):
            return response.get(key)
        return getattr(response, key, None)

    def _decision(
        self,
        *,
        request_id: str,
        work_id: str,
        passed: bool,
        reason: str,
        blockers: tuple[str, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> GateDecision:
        return GateDecision(
            decision_id=f"{self.config.gate_name}:{request_id}",
            work_id=work_id,
            gate_name=self.config.gate_name,
            passed=passed,
            reason=reason,
            blockers=blockers,
            metadata=dict(metadata or {}),
        )
