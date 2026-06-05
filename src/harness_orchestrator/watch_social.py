"""Read-only candidate boundary for a disabled watch source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol


Record = Mapping[str, Any]


@dataclass(frozen=True)
class WatchCandidate:
    """Plain candidate record surfaced for human review."""

    candidate_id: str
    work_id: str
    source_name: str
    title: str = ""
    summary: str = ""
    reference: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Record = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WatchCandidateRequest:
    """Inert request data passed only to an injected candidate connector."""

    request_id: str
    work_id: str
    topics: tuple[str, ...] = ()
    connector_name: str = "manual"
    max_candidates: int = 10
    metadata: Record = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WatchCandidateResult:
    """Read-only candidate result with block state and normalized records."""

    request_id: str
    work_id: str
    connector_name: str
    status: str
    candidates: tuple[WatchCandidate, ...] = ()
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    metadata: Record = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WatchConfig:
    """Configuration for an inert, opt-in candidate connector boundary."""

    enabled: bool = False
    connector_name: str = "manual"
    allowed_connectors: tuple[str, ...] = ()
    max_candidates: int = 10
    local_candidates: tuple[Record, ...] = ()


class _CandidateClient(Protocol):
    def candidates(self, request: WatchCandidateRequest) -> Any:
        """Return candidate data for an inert request envelope."""


class _ListCandidateClient(Protocol):
    def list_candidates(self, request: WatchCandidateRequest) -> Any:
        """Return candidate data for an inert request envelope."""


Connector = (
    Callable[[WatchCandidateRequest], Any]
    | _CandidateClient
    | _ListCandidateClient
)


class SocialWatch:
    """Surface read-only candidate records through explicit opt-in connectors."""

    def __init__(
        self,
        config: WatchConfig | None = None,
        connector: Connector | None = None,
    ) -> None:
        self.config = config or WatchConfig()
        self._connector = connector

    def candidates(
        self,
        *,
        request_id: str,
        work_id: str,
        topics: Iterable[str] = (),
        connector_name: str | None = None,
        max_candidates: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WatchCandidateResult:
        """Return normalized read-only candidates, or a blocked result."""

        active_connector = connector_name or self.config.connector_name
        request = WatchCandidateRequest(
            request_id=request_id,
            work_id=work_id,
            topics=tuple(topics),
            connector_name=active_connector,
            max_candidates=max_candidates or self.config.max_candidates,
            metadata=dict(metadata or {}),
        )

        if not self.config.enabled:
            return self._blocked(
                request=request,
                blocker="watch-disabled",
                note="Candidate watch is disabled.",
            )

        if active_connector not in self.config.allowed_connectors:
            return self._blocked(
                request=request,
                blocker="connector-not-allowed",
                note="Candidate connector is not allow-listed.",
            )

        if self._connector is None:
            local_candidates = self._candidate_records(
                request=request,
                value=self.config.local_candidates,
            )
            if local_candidates:
                return WatchCandidateResult(
                    request_id=request.request_id,
                    work_id=request.work_id,
                    connector_name=request.connector_name,
                    status="local",
                    candidates=local_candidates,
                    notes=("Local read-only candidates returned.",),
                    metadata={"candidate_count": len(local_candidates)},
                )
            return self._blocked(
                request=request,
                blocker="connector-not-configured",
                note="Candidate connector is not configured.",
            )

        try:
            response = self._call_connector(request)
        except Exception:
            return self._blocked(
                request=request,
                blocker="connector-error",
                note="Candidate connector error.",
            )

        candidates = self._candidate_records(request=request, value=response)
        return WatchCandidateResult(
            request_id=request.request_id,
            work_id=request.work_id,
            connector_name=request.connector_name,
            status="candidates_available",
            candidates=candidates,
            notes=self._notes(response),
            metadata={"candidate_count": len(candidates)},
        )

    def evaluate(self, **kwargs: Any) -> WatchCandidateResult:
        """Compatibility alias for read-only candidate collection."""

        return self.candidates(**kwargs)

    def _call_connector(self, request: WatchCandidateRequest) -> Any:
        connector = self._connector
        if callable(connector):
            return connector(request)
        for method_name in ("candidates", "list_candidates"):
            method = getattr(connector, method_name, None)
            if callable(method):
                return method(request)
        raise TypeError("Candidate connector is not callable.")

    def _blocked(
        self,
        *,
        request: WatchCandidateRequest,
        blocker: str,
        note: str,
    ) -> WatchCandidateResult:
        return WatchCandidateResult(
            request_id=request.request_id,
            work_id=request.work_id,
            connector_name=request.connector_name,
            status="blocked",
            blockers=(blocker,),
            notes=(note,),
        )

    def _candidate_records(
        self,
        *,
        request: WatchCandidateRequest,
        value: Any,
    ) -> tuple[WatchCandidate, ...]:
        records = self._records(value)
        if not records:
            records = self._records(self._value(value, "candidates"))

        candidates: list[WatchCandidate] = []
        for index, record in enumerate(records[: request.max_candidates], start=1):
            candidates.append(
                WatchCandidate(
                    candidate_id=self._text(
                        record.get("candidate_id") or record.get("id"),
                        f"{request.request_id}:{index}",
                    ),
                    work_id=self._text(record.get("work_id"), request.work_id),
                    source_name=self._text(
                        record.get("source_name") or record.get("source"),
                        request.connector_name,
                    ),
                    title=self._text(record.get("title"), ""),
                    summary=self._text(
                        record.get("summary") or record.get("body") or record.get("text"),
                        "",
                    ),
                    reference=self._optional_text(
                        record.get("reference") or record.get("url")
                    ),
                    tags=self._strings(record.get("tags")),
                    metadata=self._metadata(record),
                )
            )
        return tuple(candidates)

    def _records(self, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, Mapping):
            if "candidates" in value and len(value) <= 3:
                return self._records(value.get("candidates"))
            return [self._plain_mapping(value)]
        if not isinstance(value, (list, tuple)):
            return []

        records: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, WatchCandidate):
                records.append(item.to_dict())
                continue
            mapped = self._plain_mapping(item)
            if mapped:
                records.append(mapped)
        return records

    def _plain_mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return {
                str(key): item
                for key, item in value.items()
                if not self._sensitive_key(str(key))
            }

        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            mapped = to_dict()
            if isinstance(mapped, Mapping):
                return self._plain_mapping(mapped)

        return {}

    def _metadata(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = record.get("metadata")
        if isinstance(value, Mapping):
            return self._plain_mapping(value)
        return {}

    def _notes(self, response: Any) -> tuple[str, ...]:
        return self._strings(
            self._value(response, "notes") or self._value(response, "validation_notes")
        )

    def _value(self, value: Any, key: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(key)
        return getattr(value, key, None)

    def _strings(self, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        return ()

    def _optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    def _text(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback
        return str(value)

    def _sensitive_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(token in normalized for token in ("secret", "token", "api_key"))


def candidates(**kwargs: Any) -> WatchCandidateResult:
    """Convenience wrapper for the default disabled boundary."""

    return SocialWatch().candidates(**kwargs)
