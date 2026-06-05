"""Injectable MARACA evidence gateway wrapper."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol

from harness_orchestrator.contracts import EvidenceBundle, EvidenceRequest


@dataclass(frozen=True)
class MaracaEvidenceGatewayConfig:
    """Configuration for an inert MARACA connector boundary."""

    connector_name: str = "maraca"
    base_url: str | None = None
    path: str | None = None
    api_key_env_var: str | None = None


@dataclass(frozen=True)
class MaracaEvidenceGatewayRequest:
    """Plain request data passed only to an injected evidence client."""

    request_id: str
    work_id: str
    query: str
    connector_name: str = "maraca"
    required_sources: tuple[str, ...] = ()
    excluded_sources: tuple[str, ...] = ()
    freshness: str = "current"
    max_items: int = 10
    metadata: Mapping[str, Any] = field(default_factory=dict)
    base_url: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _CollectingClient(Protocol):
    def collect(self, request: MaracaEvidenceGatewayRequest) -> Any:
        """Collect evidence from an inert request envelope."""


class _EvaluatingClient(Protocol):
    def evaluate(self, request: MaracaEvidenceGatewayRequest) -> Any:
        """Evaluate an inert request envelope."""


class _QueryingClient(Protocol):
    def query(self, request: MaracaEvidenceGatewayRequest) -> Any:
        """Query evidence from an inert request envelope."""


Client = (
    Callable[[MaracaEvidenceGatewayRequest], Any]
    | _CollectingClient
    | _EvaluatingClient
    | _QueryingClient
)

_EVIDENCE_RECORD_KEYS = ("evidence_items", "evidence", "candidates", "items")


class MaracaEvidenceGateway:
    """Collect source-backed evidence through an injectable MARACA client."""

    def __init__(
        self,
        config: MaracaEvidenceGatewayConfig | None = None,
        client: Client | None = None,
    ) -> None:
        self.config = config or MaracaEvidenceGatewayConfig()
        self._client = client

    def collect(
        self,
        evidence_request: EvidenceRequest | None = None,
        *,
        request_id: str | None = None,
        work_id: str | None = None,
        query: str | None = None,
        connector_name: str | None = None,
        required_sources: Iterable[str] = (),
        excluded_sources: Iterable[str] = (),
        freshness: str = "current",
        max_items: int = 10,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceBundle:
        """Return a Harness evidence bundle from an injected client."""

        request = self._gateway_request(
            evidence_request=evidence_request,
            request_id=request_id,
            work_id=work_id,
            query=query,
            connector_name=connector_name,
            required_sources=required_sources,
            excluded_sources=excluded_sources,
            freshness=freshness,
            max_items=max_items,
            metadata=metadata,
        )

        if self._client is None:
            return self._empty_bundle(
                request=request,
                status="not_configured",
                note="MARACA evidence gateway has no client configured.",
            )

        try:
            response = self._call_client(request)
        except Exception:
            return self._empty_bundle(
                request=request,
                status="client_error",
                note="MARACA evidence gateway client error.",
            )

        return self._bundle_from_response(request=request, response=response)

    def evaluate(
        self,
        evidence_request: EvidenceRequest | None = None,
        **kwargs: Any,
    ) -> EvidenceBundle:
        """Compatibility alias for collect-style evidence clients."""

        return self.collect(evidence_request, **kwargs)

    def _gateway_request(
        self,
        *,
        evidence_request: EvidenceRequest | None,
        request_id: str | None,
        work_id: str | None,
        query: str | None,
        connector_name: str | None,
        required_sources: Iterable[str],
        excluded_sources: Iterable[str],
        freshness: str,
        max_items: int,
        metadata: Mapping[str, Any] | None,
    ) -> MaracaEvidenceGatewayRequest:
        if evidence_request is not None:
            return MaracaEvidenceGatewayRequest(
                request_id=evidence_request.request_id,
                work_id=evidence_request.work_id,
                query=evidence_request.query,
                connector_name=evidence_request.connector_name,
                required_sources=tuple(evidence_request.required_sources),
                excluded_sources=tuple(evidence_request.excluded_sources),
                freshness=evidence_request.freshness,
                max_items=evidence_request.max_items,
                metadata=dict(evidence_request.metadata),
                base_url=self.config.base_url,
                path=self.config.path,
            )

        return MaracaEvidenceGatewayRequest(
            request_id=request_id or "",
            work_id=work_id or "",
            query=query or "",
            connector_name=connector_name or self.config.connector_name,
            required_sources=tuple(required_sources),
            excluded_sources=tuple(excluded_sources),
            freshness=freshness,
            max_items=max_items,
            metadata=dict(metadata or {}),
            base_url=self.config.base_url,
            path=self.config.path,
        )

    def _call_client(self, request: MaracaEvidenceGatewayRequest) -> Any:
        client = self._client
        if callable(client):
            return client(request)
        for method_name in ("collect", "evaluate", "query"):
            method = getattr(client, method_name, None)
            if callable(method):
                return method(request)
        raise TypeError("MARACA evidence client is not callable.")

    def _bundle_from_response(
        self,
        *,
        request: MaracaEvidenceGatewayRequest,
        response: Any,
    ) -> EvidenceBundle:
        metadata = self._metadata(response)
        metadata.setdefault("status", "collected")

        return EvidenceBundle(
            bundle_id=self._safe_text(
                self._value(response, "bundle_id"),
                f"{request.connector_name}:{request.request_id}",
            ),
            request_id=request.request_id,
            work_id=request.work_id,
            connector_name=request.connector_name,
            evidence_items=self._records(
                self._first_value(response, _EVIDENCE_RECORD_KEYS)
            ),
            source_ids=self._source_ids(response),
            validation_notes=self._notes(response),
            metadata=metadata,
        )

    def _empty_bundle(
        self,
        *,
        request: MaracaEvidenceGatewayRequest,
        status: str,
        note: str,
    ) -> EvidenceBundle:
        return EvidenceBundle(
            bundle_id=f"{request.connector_name}:{request.request_id}:empty",
            request_id=request.request_id,
            work_id=request.work_id,
            connector_name=request.connector_name,
            validation_notes=(note,),
            metadata={"status": status},
        )

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

    def _source_ids(self, response: Any) -> tuple[str, ...]:
        value = self._value(response, "source_ids")
        if value is None:
            value = self._derived_source_ids(response)
        return self._strings(value)

    def _derived_source_ids(self, response: Any) -> tuple[str, ...]:
        identifiers: list[str] = []
        for record in self._records(
            self._first_value(response, _EVIDENCE_RECORD_KEYS)
        ):
            for key in ("source_id", "source", "id"):
                value = record.get(key)
                if value is not None:
                    identifiers.append(str(value))
                    break
        return tuple(identifiers)

    def _notes(self, response: Any) -> tuple[str, ...]:
        return self._strings(
            self._first_value(response, ("validation_notes", "validator_notes", "notes"))
        )

    def _metadata(self, response: Any) -> dict[str, Any]:
        value = self._value(response, "metadata")
        record = self._plain_mapping(value)
        return dict(record or {})

    def _plain_mapping(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, Mapping):
            return self._safe_mapping(value)

        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            mapped = to_dict()
            if isinstance(mapped, Mapping):
                return self._safe_mapping(mapped)

        return None

    def _safe_mapping(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            str(key): self._safe_value(str(key), item)
            for key, item in value.items()
            if not self._sensitive_key(str(key))
        }

    def _safe_value(self, key: str, value: Any) -> Any:
        if self._sensitive_key(key):
            return "[redacted]"
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, Mapping):
            return self._safe_mapping(value)
        if isinstance(value, tuple):
            return tuple(self._safe_value("", item) for item in value)
        if isinstance(value, list):
            return [self._safe_value("", item) for item in value]
        return value

    def _strings(self, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (self._redact(value),)
        if isinstance(value, (list, tuple)):
            return tuple(self._redact(str(item)) for item in value)
        return ()

    def _safe_text(self, value: Any, fallback: str) -> str:
        if isinstance(value, str) and value:
            return self._redact(value)
        return self._redact(fallback)

    def _redact(self, value: str) -> str:
        for marker in self._redaction_markers():
            value = value.replace(marker, "[redacted]")
        return value

    def _redaction_markers(self) -> tuple[str, ...]:
        return tuple(
            marker
            for marker in (
                self.config.base_url,
                self.config.path,
                self.config.api_key_env_var,
            )
            if marker
        )

    def _sensitive_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(token in normalized for token in ("secret", "token", "api_key"))

    def _first_value(self, response: Any, keys: tuple[str, ...]) -> Any:
        for key in keys:
            value = self._value(response, key)
            if value is not None:
                return value
        return None

    def _value(self, response: Any, key: str) -> Any:
        if isinstance(response, Mapping):
            return response.get(key)
        return getattr(response, key, None)
