# New Chat Handoff Prompt

Use this prompt to continue in a new chat if context usage exceeds 50 percent or work must be handed off.

## Prompt

Build a local agent system where Harness orchestrates governed work, `C:\Users\fredo\git_repos\MARACA\maraca_V02` supplies trustworthy source-backed evidence, and `C:\Users\fredo\git_repos\AI-Art` produces and publishes media only after policy, validation, provenance, review, and approval gates pass. It should start twice daily and also run while watching social media such as Telegram or other services. Keep the project modular, generalized, standardized, and easy to change. Maintain overview, tracker, current status, detailed summary, change requests, and handoff files.

Rules:
- First check context/window usage. If above 50 percent, do not start a task/new agent. Update tracker/status/handoff and wrap up.
- Do not change existing MARACA or AI-Art code without an approved, validator-checked change request.
- Do not change implemented tasks.
- Use project tracker with milestones, current status, open issues, and finished issues.
- Check whether needed functions already exist before adding anything.
- Use `deepseek-open-art` as local LLM API access through an API boundary; AI-Art already has this standard.
- Keep social media and service connections as separate functions/classes/variables/parameters for replacement.
- Orchestrator must not move ahead if a dependency is unfinished.
- Always run evaluator/tester review before finishing a task.

Current state:
- Harness root `C:\Users\fredo\git_repos\Harness_age_mem_v02` is initialized on branch `main`.
- Harness origin: `git@github.com:artprof964/runn_art.git`.
- MARACA and AI-Art/AI-Artist were clean on `main...origin/main` at final recheck. Recheck before future edits.
- MARACA GitHub remote confirmed: `git@github.com:artprof964/maraca_v02.git`.
- AI-Art GitHub remote confirmed: `https://github.com/artprof964/AI-Artist.git`.
- Created Harness docs:
  - `CURRENT_PROCESS_STATUS.md`
  - `PROJECT_ORCHESTRATION_TRACKER.md`
  - `PROJECT_FILE_FUNCTION_MODULE_OVERVIEW.md`
  - `DETAILED_REPOSITORY_SUMMARY.md`
  - `CHANGE_REQUESTS.md`
  - `NEW_CHAT_HANDOFF_PROMPT.md`
- Function inventory:
  - Harness: 0 Python files.
  - MARACA: 68 Python files, 169 classes, 545 functions, 321 methods.
  - AI-Art/AI-Artist: 175 Python files, 151 classes, 860 functions, 149 methods.
- Read-only agents completed:
  - gpt-5.5 xhigh outline agent.
  - gpt-5.5 medium validation/rules agent.
- Final evaluator/tester agent passed after remediation.
- No code-worker was started because no CR is approved.
- Next step: select one CR from `CHANGE_REQUESTS.md`, run validator on exact scope, then assign a worker only if validation passes.

Do not implement code until a specific CR from `CHANGE_REQUESTS.md` is selected, validated, assigned to a worker with exact file ownership, implemented, and tested.
