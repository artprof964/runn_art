# New Chat Handoff Prompt

Use this file to continue Harness implementation in a fresh chat or after context pressure.

## Hard Rules

1. Start by checking context/window usage and git status for Harness, MARACA, and AI-Art.
2. Read these source-of-truth files before proposing work:
   - `NEW_CHAT_HANDOFF_PROMPT.md`
   - `CURRENT_PROCESS_STATUS.md`
   - `PROJECT_ORCHESTRATION_TRACKER.md`
   - `CHANGE_REQUESTS.md`
   - `DETAILED_REPOSITORY_SUMMARY.md`
   - `PROJECT_FILE_FUNCTION_MODULE_OVERVIEW.md`
3. If context/window usage is above 50 percent, above 250000 tokens, or cannot be measured confidently, do not spawn OA2 and do not implement code. Recheck git state, update status/handoff docs only, then stop.
4. Do not implement anything beyond CR-HAR-058 until a fresh next CR is proposed by OA2, dependency-checked, validator-reviewed, and assigned exact owned files.
5. Do not edit MARACA or AI-Art code unless a fresh approved CR explicitly authorizes exact owned files there.
6. Preserve user/prior-agent changes. Never revert unrelated work.
7. Implement only one CR at a time.
8. Each implementation CR requires OA2 proposal, pre-code validator GREEN, worker implementation in exact owned files, post-worker validator, evaluator, and reviewer/remediation loops when needed.
9. After each CR, run focused tests, useful full tests, compileall, and scope/side-effect checks.
10. Update tracker, status, change-request, summary/overview, and handoff docs after each completed CR.

## Project Goal

Harness at `C:\Users\fredo\git_repos\Harness_age_mem_v02` is the governed orchestration control plane.

MARACA at `C:\Users\fredo\git_repos\MARACA\maraca_V02` supplies source-backed evidence.

AI-Art at `C:\Users\fredo\git_repos\AI-Art\AI-Artist` produces and publishes media only after policy, validation, provenance, review, approval, and release gates pass.

The system should remain modular, generalized, standardized, and easy to change. Manual starts, planned twice-daily starts, and policy-approved replaceable watch connectors remain future composition goals; real service behavior is blocked until exact CR authorization.

## Current Baseline

- Harness CR-HAR-001 through CR-HAR-058 are complete locally.
- MARACA CR-MAR-001 through CR-MAR-004 are complete locally.
- AI-Art CR-AIA-001 through CR-AIA-006 are complete locally.
- Latest completed Harness unit: CR-HAR-058, release publish execution handoff readiness receipt verification.
- Latest verification baseline:
  - Focused CR-HAR-058 tests: 16 OK
  - Adjacent CR-HAR-056/057/058 chain: 63 OK
  - Full Harness unittest discovery: 663 OK
  - `compileall src tests`: OK
  - Final validator/evaluator: GREEN
- Direct MARACA runtime execution remains blocked.
- Direct AI-Art runtime execution remains blocked.
- Real scheduler/watch/social/publishing behavior, credentials, env/package probing, subprocess/network/service construction, hidden persistence, random IDs, and wall-clock-dependent behavior remain blocked until a future exact CR authorizes them.

## Current Git State

Latest recorded recheck:

- Harness `C:\Users\fredo\git_repos\Harness_age_mem_v02`: `main...origin/main [ahead 2]`
  - Modified project docs include `CHANGE_REQUESTS.md`, `CURRENT_PROCESS_STATUS.md`, `DETAILED_REPOSITORY_SUMMARY.md`, `NEW_CHAT_HANDOFF_PROMPT.md`, `PROJECT_FILE_FUNCTION_MODULE_OVERVIEW.md`, and `PROJECT_ORCHESTRATION_TRACKER.md`.
  - Untracked CR-HAR-056/057/058 files:
    - `src/harness_orchestrator/release_publish_execution_handoff_readiness.py`
    - `src/harness_orchestrator/release_publish_execution_handoff_readiness_ledger.py`
    - `src/harness_orchestrator/release_publish_execution_handoff_readiness_receipt.py`
    - `tests/test_release_publish_execution_handoff_readiness.py`
    - `tests/test_release_publish_execution_handoff_readiness_ledger.py`
    - `tests/test_release_publish_execution_handoff_readiness_receipt.py`
- MARACA `C:\Users\fredo\git_repos\MARACA\maraca_V02`: clean on `main...origin/main`; latest recorded HEAD/origin is `84bdbfa1dd50ab92ee2492fffae457216c5667cd`.
- AI-Art `C:\Users\fredo\git_repos\AI-Art\AI-Artist`: clean on `main...origin/main [ahead 1]`.

Always recheck git state before acting.

## Contingent Next CR

CR-HAR-059 is a contingent candidate only, not an authorized implementation CR.

Candidate title: Add Harness-only release publish execution handoff acceptance boundary.

Contingent owned files:
- `src/harness_orchestrator/release_publish_execution_handoff_acceptance.py`
- `tests/test_release_publish_execution_handoff_acceptance.py`

Expected shape if freshly authorized:
- Consume only explicit CR-HAR-058 verification data.
- Return frozen/plain JSON-safe acceptance data.
- Fail closed for malformed, tampered, unsafe, secret-like, action-intent, bad `to_dict()`, bool/float strictness, and caller-mutation cases.
- Add no MARACA/AI-Art imports, executor, publishing, scheduler/watch/social, network, subprocess, credentials, env/package probing, filesystem IO, `RunLedger` mutation/construction/save/load, hidden persistence, random IDs, wall-clock behavior, or service/client construction.

Before any CR-HAR-059 edit, a fresh context-safe OA2 proposal and separate pre-code validator pass must return GREEN.

## Fresh Chat Prompt

```text
Resume finalization for Harness_age_mem_v02.

First check context/window usage. If usage is above 50 percent, above 250000 tokens, or cannot be measured confidently, do not open a new task, do not spawn OA2, and do not implement code. Recheck git status for Harness, MARACA, and AI-Art/AI-Artist; update CURRENT_PROCESS_STATUS.md and NEW_CHAT_HANDOFF_PROMPT.md with a concise status note; then stop.

If context is measurable and safe:
1. Recheck git status for:
   - Harness: C:\Users\fredo\git_repos\Harness_age_mem_v02
   - MARACA: C:\Users\fredo\git_repos\MARACA\maraca_V02
   - AI-Art: C:\Users\fredo\git_repos\AI-Art\AI-Artist
2. Read NEW_CHAT_HANDOFF_PROMPT.md, CURRENT_PROCESS_STATUS.md, CHANGE_REQUESTS.md, PROJECT_ORCHESTRATION_TRACKER.md, DETAILED_REPOSITORY_SUMMARY.md, and PROJECT_FILE_FUNCTION_MODULE_OVERVIEW.md.
3. Use gpt-5.5 high for OA2 and gpt-5.5 medium for validator, reviewer, worker, and evaluator agents.
4. Spawn OA2 to propose the next CR after CR-HAR-058, validate dependency order, assign exact owned files, and verify no blocked files will be edited.
5. If OA2 returns GREEN for an exact CR, run the validator -> worker -> validator -> evaluator workflow.
6. If validator or evaluator returns RED, route through reviewer -> worker -> validator -> evaluator for exact approved remediation scope only.
7. When all checks are GREEN, update project status/tracking/handoff files and stop if context becomes unsafe.

Current baseline:
- CR-HAR-001 through CR-HAR-058 are complete locally.
- CR-MAR-001 through CR-MAR-004 are complete locally.
- CR-AIA-001 through CR-AIA-006 are complete locally.
- No code beyond reviewed CR-HAR-001 through CR-HAR-058, CR-MAR-001 through CR-MAR-004, and CR-AIA-001 through CR-AIA-006 is authorized.
```

## Latest Continuation Note

- On 2026-06-07, the user asked to read handoff/status files and send OA2.
- Required source-of-truth files were re-read.
- Context/window check reported `88589` tokens used, with no remaining-token report and no exact context-window percentage.
- Because context could not be measured confidently, OA2 was not spawned and no CR-HAR-059 implementation was started.
- `CURRENT_PROCESS_STATUS.md` and this file were summarized to keep future handoffs concise.
