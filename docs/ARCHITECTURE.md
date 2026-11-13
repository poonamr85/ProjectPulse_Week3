# Architecture and code guide

The application has five packages. UI modules display state and collect actions; workflow modules decide what happens; tools call analysis and integrations. Runtime data remains in the existing root data/ directory, separate from project_status_agent/data/ Python code.

## Module map

```text
app.py
project_status_agent/
├── __main__.py                 python -m entry point
├── config.py                   Shared workspace settings, environment helpers
├── cli.py                      Argument parsing and command dispatch
├── agent/
│   ├── workflow.py             Model → tools → model loop, human interrupts
│   ├── runner.py               Checkpoints, follow-ups, inspect/resume/retry
│   └── tools.py                Scoped LangChain tool definitions
├── integrations/
│   ├── notion.py               Read-only paginated Notion connector
│   ├── memory.py               Mem0 API, delivery queue publication/reconciliation
│   ├── model.py                Nebius model configuration and smoke check
│   └── http.py                 Shared bounded HTTP transport and safe errors
├── data/
│   ├── contract.py             Shared field meaning, provenance, interpretation rules
│   ├── validation.py           CSV loading and complete dataset validation
│   ├── store.py                SQLite facts, sync audit, memory queue, freshness
│   └── analysis.py             Sprint metrics, risks, blockers, trends, history
├── reports/
│   ├── content.py              Report schema, factual validation, rendering/save
│   ├── workflow.py             Collect → draft → validate → review → save
│   ├── library.py              Saved-report discovery and stable RPT numbers
│   └── exports.py              Word, PDF, Markdown and download controls
└── ui/
    ├── reports.py              Workspace layout and report review screen
    ├── chat.py                 Simple Q&A screen and plain-language messages
    └── charts.py               Shared interactive/static chart definitions
```

Package __init__.py files identify package boundaries; they do not load models, make API requests, or re-export duplicate implementations. Explicit imports show where each function lives. The removed flat modules are replaced by the paths above; internal imports and mock targets have been updated together.

## Follow a question through the code

1. app.py opens ui/reports.py, which loads settings and chooses the screen.
2. ui/chat.py passes the question to agent/runner.py.
3. The runner opens the existing checkpoint database and invokes agent/workflow.py.
4. The model selects tools from agent/tools.py. Those tools call data/analysis.py or integrations/memory.py. Tools return observations; data from external sources is not treated as instructions.
5. The graph calls the model again with the observations. It either answers, selects more tools, pauses for clarification/approval, or returns a failure requiring attention.
6. The UI renders the answer or human request. Retry after a model timeout retains the question and completed observations.

## Follow a report through the code

ui/reports.py calls reports/workflow.py. The workflow collects tool evidence, generates narrative/mitigation proposals, validates them against frozen facts, and pauses for review. Revisions require fresh approval. Version/hash guards reject stale approvals. Approved content is saved locally through reports/content.py. reports/exports.py renders the selected version without changing its approval state.

reports/library.py reads saved report checkpoints and assigns permanent display numbers. This storage logic is outside the UI. Charts share one implementation for screen and document exports.

## Configuration and persistence

config.Settings supplies the common database path, checkpoint path, project ID, and Notion API version. Entry points read settings at runtime, so environment overrides still work. Provider credentials are read through required() and never stored in Settings or printed. Integration-specific model timeout validation stays with the model adapter.

- data/project_status.sqlite3: exact records, sync audit, memory delivery, approved reports.
- data/agent_checkpoints.sqlite3: conversation/report workflow state and report display numbers.
- .env: local credentials, ignored by Git.
- docs/: maintained setup, architecture, usage, and acceptance guides.
- scripts/: explicitly invoked operations, separate from runtime services.

The refactor does not rename tables, thread identifiers, report identifiers, or state fields. Existing checkpoints can be inspected without constructing a model. All data remains synthetic unless explicitly labeled Real by the dataset.

## Boundaries to preserve

Keep arithmetic and temporal conclusions in data/analysis.py. Keep agent decisions in the graph and human approval guards in workflows/runners. Keep service request handling in integrations/. Keep display labels and Streamlit widgets in ui/. Avoid adding generic abstractions for a single use; extend the existing module responsible for the behavior.

The original sample_project_data is a November 4 regression baseline; sample_project_data_nov10 is the extended reusable dataset. Do not replace the original fixtures to make live counts match; select the appropriate source/dataset.

## Refactor verification

Run the offline suite with tracing disabled, lint the code, inspect a pre-existing report and chat checkpoint, compare database table counts, and exercise CLI help/status/tools. AppTest covers generation, revisions, stale forms, approvals, chat clarification/retry, and document exports. No live provider calls are needed merely to validate the module move.

## Shared data contract

`data/contract.py` is the single interpretation reference used by the agent system prompt, report narrative prompt, and analysis-tool result envelopes. Tools expose `data_contract` alongside `results`, `coverage`, and consistent `warnings`; existing result keys and stored schemas are retained.

Historical Status, Assigned Sprint ID, and Blocker Note are recorded at snapshot dates. Title, owner, priority, due date, dependencies, original plan, and Status Since are current task attributes, not historical change logs. Current-only values on old tool rows are joined from the current Tasks record; repeated owner labels do not establish historical ownership. Assigned sprint, observation sprint, and planned sprint have different meanings. Blank assignment means backlog.

Overdue is evaluated against the reporting cutoff, with a strict earlier-than comparison. Facts, hypotheses, and proposed actions must be distinguished. Similar wording is not proof of a dependency, shared cause, or provider responsibility. No critical-path schedule, exact completion timestamp, effort/capacity measurement, or guaranteed blocker-resolution date is recorded. A missing field on one page does not prove it is untracked.

This is a shared grounding contract, not a free-form narrative validator. New generation uses it; saved report contents and existing project data are not rewritten.

## Facts, interpretations, and actions

The shared contract includes ANSWER_GUIDANCE: recorded claims use retrieved evidence; interpretations are labeled as possible explanations or hypotheses with their uncertainties; recommendations are proposed next steps, not executed or guaranteed actions. Simple factual answers do not need a three-part template. The same guidance reaches Q&A and report narrative generation.

Tool task views now expose `depends_on_task_ids` from the current Tasks record. Similar note wording cannot create an edge. A shared upstream dependency does not prove a direct relationship, shared cause, or that one fix resolves both tasks. Existing snapshots and saved reports are not rewritten. Narrative grounding is guided by instructions and evidence, not a universal sentence-by-sentence validator.


## Memory scope and source provenance

- Notion and CSV observations are saved with separate source tags; Mem0 publication is explicit.
- Each memory links a project, source, task, observation date and snapshot reference.
- Mem0 retrieval filters to the selected source and reporting cutoff, then checks references against local history.
- Tool results remain in conversation checkpoints/traces; human feedback and decisions remain in report checkpoints/review history. Neither is automatically published to Mem0.
- Unified retrieval across source types is not implemented. Persistent source-tagged observation memory is implemented; broad cross-source synthesis is partial scope coverage.
