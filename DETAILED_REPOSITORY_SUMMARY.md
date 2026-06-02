# Detailed Repository Summary

Date: 2026-06-03 Europe/Vienna
Scope: read-only summary of Harness, MARACA, and AI-Art

## Assumptions

- `C:\Users\fredo\git_repos\Harness_age_mem_v02` is the additive Harness scaffold target.
- MARACA and AI-Art/AI-Artist must be rechecked before any future edits; current main-workspace status is clean.
- `deepseek-open-art` is the canonical local LLM API secret name because AI-Art already standardizes it.
- Social media connectors must be isolated behind replaceable functions/classes/config variables.

## Inventory Counts

| Project | Python files | Files with symbols | Classes | Functions | Methods |
|---|---:|---:|---:|---:|---:|
| Harness | 0 | 0 | 0 | 0 | 0 |
| MARACA | 68 | 65 | 169 | 545 | 321 |
| AI-Art/AI-Artist | 175 | 167 | 151 | 860 | 149 |

Full symbol list: `PROJECT_FILE_FUNCTION_MODULE_OVERVIEW.md`.

## Harness

- Role: control plane for governed orchestration.
- Current state: initialized git repo on branch `main` with documentation/tracker files only.
- Target GitHub repo: `git@github.com:artprof964/runn_art.git`.
- Required future modules: contracts, run ledger, policy client, MARACA evidence gateway, AI-Art media gateway, gate state machine, scheduler, social watch supervisor, approval inbox.
- Current implementation status: no code implemented.

## MARACA

- Role: trustworthy evidence service.
- GitHub remote: `git@github.com:artprof964/maraca_v02.git`.
- Git status: clean on `main...origin/main` at final recheck.
- Source role: provide source-backed retrieval, source registry, freshness, validation, ranking, synthesis, and orchestration-compatible evidence workflows.
- Key files:
  - `README.md`: identifies MARACA as an agent-orchestrated hybrid retrieval backend.
  - `src/planning/orchestration_runtime.py`: defines orchestration capabilities, status, runtime config, health checks, run results, protocol adapter, and LangGraph-compatible fallback.
  - `src/retrieval/execution.py`: provides vector, keyword, graph, hybrid retrieval and access filtering.
  - `src/source_registry/registry.py`: source metadata and source policy boundary.
  - `src/shared/contracts.py`, `src/shared/records.py`, `src/shared/policies.py`: shared DTO and policy/error/log record patterns.
  - `src/storage/adapters.py`: backend adapter/status/capability conventions.

## MARACA Constraints

- Do not make MARACA a publisher.
- Do not add social connectors directly into retrieval flow.
- Do not add DeepSeek/OpenAI-compatible LLM usage until an additive connection registry CR is approved.
- Preserve existing adapter/result/status style.

## AI-Art

- Role: gated media/action service.
- GitHub remote: `https://github.com/artprof964/AI-Artist.git`.
- Git status: clean on `main...origin/main` at final recheck.
- Source role: generate, validate, review, approve, audit, and publish media only after gates pass.
- Key files:
  - `backend/connection_settings.py`: defines `DEEPSEEK_OPEN_ART_ENV_VAR = "deepseek-open-art"`, provider URL/model defaults, secret aliases, and service URLs.
  - `backend/llm_api_smoke.py`: builds OpenAI-compatible client with `OpenAI(api_key=..., base_url=...)`.
  - `backend/adapter_factory.py`: central factory for Slack, GitHub, ComfyUI, publishing, and LLM smoke clients/adapters.
  - `backend/execution_gate.py`: validates operation, target, allow/valid flags, human approval, signature, and expiry.
  - `backend/publishing_adapter.py`: blocks publish without approved execution envelope.
  - `backend/publishing.py`: wraps publish action with side-effect audit recording.
  - `backend/source_ingestion.py`: imports approved-domain sources with hash/version/provenance metadata.
  - `backend/orchestrator.py`: mock multi-agent output path with knowledge, image-planner, and critic-curator sub-agent conventions.
  - `workspaces/*/AGENTS.md`: workspace-specific agent role conventions.

## AI-Art Constraints

- Do not bypass execution envelopes.
- Do not publish without human approval.
- Do not add real social or publishing actions until policy, credentials, compliance, provenance, audit, and approval gates exist.
- Keep service connections replaceable through factory/config boundaries.

## Target Flow

1. Scheduled, watch, or manual trigger enters Harness.
2. Harness creates governed work envelope.
3. Harness calls AI-Art safety/policy service.
4. Harness requests MARACA evidence bundle.
5. Harness applies source, freshness, validation, media-plan, provenance, critic, security, human-review, and publish gates.
6. AI-Art generates media only after execution envelope passes.
7. AI-Art records provenance, critic/review/security state, human approval, publish envelope, publishing result, and audit.

## Scheduler And Watch

- Twice daily default: 08:00 and 20:00 Europe/Vienna unless config overrides.
- Watch mode: read-only candidate ingestion only.
- Social connectors: Telegram, RSS, web/API, Slack, GitHub, or other services must be independent replaceable modules.
- Real API/scrape execution: blocked until policy/compliance credentials and rate limits are approved.

## DeepSeek/Open-Art Standard

- Standard env key: `deepseek-open-art`.
- Compatibility alias in AI-Art: `DEEPSEEK_API_KEY`.
- Default AI-Art API URL: `https://api.deepseek.com`.
- Harness should call through a connector/factory boundary and avoid hard-coded provider logic.

## Immediate Status

- Discovery done.
- Documentation/tracker setup complete.
- Evaluator/tester review passed.
- Harness git initialized and origin configured.
- Implementation blocked pending validator-approved CR.
