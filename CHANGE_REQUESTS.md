# Change Requests

Date: 2026-06-03 Europe/Vienna
Status: proposed only; not implemented

## Validation Rule

- Each CR must be validated before a worker agent implements it.
- Each worker gets exact owned files and must not revert unrelated work.
- Tester/evaluator runs after implementation before milestone completion.

## Harness CRs

| ID | Status | Scope | Owned Files | Acceptance |
|---|---|---|---|---|
| CR-HAR-001 | proposed | Add shared interface records: `GovernedWorkRequest`, `EvidenceRequest`, `EvidenceBundle`, `MediaReleaseRequest`, `GateDecision` | `src/harness_orchestrator/contracts.py`, `tests/test_contracts.py` | dataclasses or pydantic records exist; no service calls |
| CR-HAR-002 | proposed | Add AI-Art safety gateway wrapper | `src/harness_orchestrator/adapters/ai_art_safety_gateway.py`, `tests/test_ai_art_safety_gateway.py` | URL/key configurable; no hard-coded social/publish action |
| CR-HAR-003 | proposed | Add MARACA evidence gateway wrapper | `src/harness_orchestrator/adapters/maraca_evidence_gateway.py`, `tests/test_maraca_evidence_gateway.py` | returns `EvidenceBundle`; does not mutate MARACA |
| CR-HAR-004 | proposed | Add gate state machine | `src/harness_orchestrator/gate_state_machine.py`, `tests/test_gate_state_machine.py` | blocks media/publish unless all gates pass |
| CR-HAR-005 | proposed | Add twice-daily scheduler | `src/harness_orchestrator/scheduler.py`, `tests/test_scheduler.py` | default 08:00/20:00; timezone/config injectable |
| CR-HAR-006 | proposed | Add blocked read-only social watch candidate interface | `src/harness_orchestrator/watch_social.py`, `tests/test_watch_social.py` | connectors injectable; real API calls disabled by default |
| CR-HAR-007 | proposed | Add run ledger/status persistence | `src/harness_orchestrator/run_ledger.py`, `tests/test_run_ledger.py` | records decisions, dependencies, audits, and unfinished tasks |

## MARACA CRs

| ID | Status | Scope | Owned Files | Acceptance |
|---|---|---|---|---|
| CR-MAR-001 | proposed | Add additive `EvidenceBundle` export adapter | `src/synthesis/evidence_bundle.py`, `tests/test_evidence_bundle_export.py` | retrieval behavior unchanged; adapter maps existing records only |
| CR-MAR-002 | proposed | Add source/social candidate mapping tests | `src/ingestion/social_source_candidates.py`, `tests/test_social_source_candidates.py` | uses existing source/freshness/ingestion records; no real social API calls |
| CR-MAR-003 | proposed | Add AI-Art-style connection registry only if LLM adapter is needed | `src/shared/connection_settings.py`, `tests/test_connection_settings.py`, `.env.example` | `deepseek-open-art` standard, alias policy, redaction, tests |
| CR-MAR-004 | proposed | Wire documented `QDRANT_COLLECTION` and `NEO4J_DATABASE` | `src/backend_app/health.py`, `src/storage/qdrant_runtime.py`, `src/storage/neo4j_runtime.py`, `tests/test_backend_health.py`, `tests/test_backend_adapters.py` | health/runtime use documented env vars |

## AI-Art CRs

| ID | Status | Scope | Owned Files | Acceptance |
|---|---|---|---|---|
| CR-AIA-001 | proposed | Add media release gate combining provenance, critic, security review, review status, and approval before publish | `backend/media_release_gate.py`, `tests/test_media_release_gate.py` | publish blocked unless all release checks pass |
| CR-AIA-002 | proposed | Add social-scout contract tests proving real APIs remain blocked | `tests/test_social_scout_contracts.py`, `workspaces/social-scout/AGENTS.md`, `workspaces/social-scout/TOOLS.md` | no scrape/API call path enabled without policy |
| CR-AIA-003 | proposed | Expose media release gate through adapter factory if CR-AIA-001 approved | `backend/adapter_factory.py`, `tests/test_adapter_factory.py` | factory/config boundary preserved |

## Blocked Items

- No code implementation approved.
- No social media credentials or compliance policy approved.
- No publish integration beyond existing AI-Art local/gated adapter approved.
