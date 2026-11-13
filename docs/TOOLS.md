# Project analysis tools

Eight real LangChain tools are registered in `create_tools` and used by the implemented ReAct agent and report workflow. Deterministic analysis tools can also be invoked directly without a model API key; remote integrations need their own credentials.

## Code structure

| File | Purpose |
| --- | --- |
| `project_status_agent/data/analysis.py` | Shared historical reader and seven deterministic analysis/context functions |
| `project_status_agent/agent/tools.py` | Small LangChain wrappers and the sync tool |
| `project_status_agent/cli.py` | Terminal entry point for listing and invoking tools |
| `tests/test_analysis_tools.py` | Known results, historical cutoffs, errors, isolation, and actual tool invocation |

`ProjectData` scopes all reads to one project and source, selects snapshots no
later than the reporting date, and attaches coverage information. `AnalysisService`
contains application logic. `create_tools` binds the trusted project/source and
creates a fresh SQLite connection per tool call; the model cannot choose another
project through its arguments. Expected errors become tool observations.

## Try the tools

Use the existing virtual environment from the project directory:

```sh
source .venv/bin/activate
python -m project_status_agent tools
python -m project_status_agent tools fetch_sprint_data --args '{"as_of_date":"2026-11-04"}'
python -m project_status_agent tools detect_blockers --args '{"as_of_date":"2026-11-04"}'
python -m project_status_agent tools risk_scorer --args '{"as_of_date":"2026-11-04"}'
python -m project_status_agent tools get_persistent_blockers --args '{"as_of_date":"2026-11-04"}'
python -m project_status_agent tools trend_analysis --args '{"as_of_date":"2026-11-04","start_date":"2026-10-30"}'
python -m project_status_agent tools query_sprint_history --args '{"as_of_date":"2026-11-04","task_id":"T-004","limit":3}'
python -m project_status_agent tools search_project_memory --args '{"query":"Why is payment integration blocked?","as_of_date":"2026-11-04"}'
```

The default source is `notion`. Add `--source csv` to analyze local fixture records
instead. Mem0 search is a remote read; all other analysis commands above are local.

On another machine, install the tools dependency group first:

```sh
python -m pip install -e '.[tools]'
```

For a real new observation, the sync tool can read Notion and save a local snapshot:

```sh
python -m project_status_agent tools sync_project_data --args '{"observed_on":"2026-11-05"}'
```

Use the actual observation date, not the example date on a later day. It must be
after imported history and within a defined sprint. New memory observations are
queued locally; this tool does not publish to Mem0 or modify Notion. Exact repeated
captures are idempotent. Current attributes and snapshots must satisfy validation.

## What each tool does

| Tool | Behavior |
| --- | --- |
| `sync_project_data` | Reads a complete Notion bundle, captures current task states, validates, and commits locally |
| `fetch_sprint_data` | Returns sprint details, assigned tasks, counts, and observed completion percentage |
| `detect_blockers` | Returns explicit Blocked tasks and transitive impacts through documented current dependency links |
| `risk_scorer` | Returns Critical, High, or Medium flags with reasons; includes overdue work |
| `trend_analysis` | Compares date boundaries or last N distinct observation dates |
| `get_persistent_blockers` | Requires Blocked observations in at least two distinct assigned sprints |
| `query_sprint_history` | Filters exact observations by task, assigned sprint, and date; newest first, with limit/offset pagination |
| `search_project_memory` | Searches Mem0 context and verifies project/source/task/snapshot references against local history |

`trend_analysis` requires exactly one of `start_date` or `last_n_snapshots`. The
latter counts distinct observation dates across the project, not individual task
rows or repeated imports. Historical query `limit` controls the last N individual
observations instead. Limits are 1–100; use `next_offset` for further history pages.

## Rules and evidence

- Overdue: incomplete task with current due date strictly before the reporting date.
- Critical: Critical-priority task that is blocked or overdue.
- High: another task that is blocked or overdue.
- Medium: In progress across at least two unchanged observations spanning seven days,
  with no higher risk. Duration ends at the latest observation, not an unobserved date.
- Persistent blocker: Blocked observations in two or more assigned sprints. By default
  it must still be blocked at the reporting cutoff. Set `currently_blocked_only=false`
  to include recovered work. Nonconsecutive periods are included and are not described
  as uninterrupted blockage.

Responses include snapshot IDs, actual observation dates, source URLs where available,
last successful sync information, and warnings. Results cap task lists at 100 and
report truncation. History supports pagination. Aggregates use the full matching set.

Snapshot membership/status/blocker notes are historical. Titles, owners, priorities,
deadlines, and dependencies are current attributes: do not claim their historical
values. Dependency links imply potential impact, not proof of a second blocked task.
Task counts are not story-point velocity. An unchanged status is not proof of no work.

Mem0 search is not exhaustive. Matching a source reference does not verify every word
in a memory. Missing or future source references are excluded, and the original source
observation is included for comparison. If Mem0 fails, its tool returns an error; the
future agent can use structured history or ask for help rather than invent context.

## Verification

```sh
python -m unittest discover -s tests -v
```

Known fixture results as of November 4, 2026: 12 current-sprint tasks; blockers
T-004/T-008/T-013; persistent blockers T-004/T-008; overdue incomplete tasks
T-004/T-006/T-008/T-011; newly observed Done since October 30: T-015/T-016.

Tools were exercised through LangChain `.invoke()` without an LLM. The tests use
isolated temporary databases and mocked Mem0 responses, and never write to Notion
or upload memories. Direct smoke checks can also use the already-synced Notion data.

## Data interpretation contract

All analysis responses include `data_contract` field/provenance metadata and shared interpretation warnings from `project_status_agent/data/contract.py`. Historical blocker notes and assigned sprint membership are queryable; ownership and other current attributes are not historical measurements even when shown beside old snapshots. Risk descriptions use the reporting cutoff, and exact sprint IDs such as SPR-03 are required when selecting explicitly. The same contract is supplied to Q&A and report narrative generation.
