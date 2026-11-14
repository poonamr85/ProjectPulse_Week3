# Nebius model connection

The project uses `langchain-openai` as an OpenAI-compatible protocol adapter,
with the Nebius API key, model ID, and base URL explicitly supplied. Requests go
to Nebius, not OpenAI. An OpenAI API key is not required.

Configuration in the private `.env`:

```dotenv
NEBIUS_API_KEY=your_private_key
NEBIUS_MODEL=zai-org/GLM-5.3-Flash
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1
```

Use the endpoint appropriate to your account if Nebius specifies a regional URL.
The client uses chat completions, a configurable 120-second request timeout, one retry, and a
4096-token output limit. This limit includes reasoning on models that account for
it as output. It is a starting configuration, not a guarantee for longer tasks.

## Verify

```sh
source .venv/bin/activate
python -m project_status_agent check-config
python -m project_status_agent model-check --as-of 2026-11-04
```

The check spends Nebius credits on at most two logical model turns (a transient
request may be retried once). It exposes only `fetch_sprint_data`, with automatic
tool choice, validates the requested date and tool, executes it against saved
SQLite records, and sends compact metrics back for a final answer. It does not
sync Notion, modify tasks, upload memories, or save reports. It verifies one-tool
selection, not selection among the full toolset or a production agent loop.

The output includes structured metrics and usage. Compare model-generated prose
against the metrics; this check does not validate every statement in the answer.
Qualitative claims such as project momentum require further evidence.

Live check succeeded with GLM-5.2: SPR-03, 12 tasks, 3 Blocked, 5 In progress,
2 Done, 2 To do. The next milestone is the full bounded ReAct/LangGraph workflow,
including evidence validation and human review.

Code: `project_status_agent/integrations/model.py`; offline tests: `tests/test_model.py`.
On another machine, install `python -m pip install -e '.[model]'` or use the pinned
`requirements-tools.lock` environment. Tests use fake model responses and do not
spend credits. Running `model-check` makes real requests.

References:
- [Nebius model guide](https://github.com/nebius/token-factory-cookbook/blob/main/models/glm-5.2.md)
- [LangChain tool binding](https://reference.langchain.com/python/langchain-openai/chat_models/base/BaseChatOpenAI/bind_tools)

Timeouts: `NEBIUS_TIMEOUT_SECONDS` defaults to 120 and accepts 15–180 seconds for reports and non-interactive model use. One automatic retry remains enabled for those requests. Interactive UI Q&A instead uses `NEBIUS_QA_TIMEOUT_SECONDS` (default 60, accepts 15–180) with zero automatic retries. After a Q&A timeout, a Retry button continues the saved model step without adding the question again or replaying completed tools. Timeouts apply per model request, not to the whole investigation. Restart Streamlit after changing model settings.

## Model availability recovery — November 10, 2026

A live Q&A request failed with HTTP 404 before any tools ran. The configured endpoint's model catalog no longer listed zai-org/GLM-5.2. Tested zai-org/GLM-5.3-Flash with the exact blocker question: completed in two model calls and two successful tool calls, returning T-008, T-013, and T-014. Updated the local environment and example configuration to the tested model. Model availability can change; check the provider catalog before changing the identifier again. A successful check verifies this question, not every model behavior or pricing equivalence. Project records and saved reports were not changed by the model configuration update.

The older model-recovery artifact was retired during cleanup; current-model evidence is in data/acceptance/grounding-evaluations.json. Start a new UI chat after restarting Streamlit and repeat the blocker question. Historical failed investigations remain in their recorded state.


## Report output-limit handling — November 11, 2026

A failed report trace showed two empty GLM-5.3-Flash responses: each exhausted 4,096 completion tokens entirely on reasoning (`finish_reason=length`). This was not missing project data or a proven mitigation-coverage error. Report generation now binds a 16,384-token completion allowance, including reasoning; interactive Q&A retains its existing configuration. This is a ceiling, not a fixed token charge, and can permit longer or more expensive responses. A truncated report stops with a specific output-limit message instead of repeating the same schema-repair request. Empty responses without a length finish reason get a specific message and at most one retry. Schema, evidence validation and human approval remain required. Regression tests cover both failure cases.


## LangSmith agent destination

The configured tracing endpoint is `https://api.smith.langchain.com`, agent ID `intelligent-project-status`, environment `development`. The UI resolves environment settings before importing integrations; when a complete agent address is present, startup removes `LANGSMITH_PROJECT`, `LANGCHAIN_PROJECT` and `LANGCHAIN_SESSION` from the process to avoid mixed-destination errors. Project-only configurations remain supported when no complete agent address is supplied. API keys stay in ignored `.env` or platform Secrets. The Replit launcher sets this agent destination explicitly. Restart the running process after updating code or settings; previous missing traces cannot be recovered retroactively.
