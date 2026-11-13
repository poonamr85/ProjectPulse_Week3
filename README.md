# Project Pulse

A project-status agent that reads Notion sprint/task history, analyzes blockers and risks with tools, retrieves historical context from Mem0, and generates reports for human review. LangGraph manages state, clarification, approval, and recovery. Nebius hosts the model; LangSmith records traces.

## Run locally

From the project directory:

```sh
source .venv/bin/activate
python -m streamlit run app.py
```

Open http://127.0.0.1:8501. Select **Weekly reports** or **Ask the Agent** in the sidebar.

On a new machine, use Python 3.11+ and install the tested environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-tools.lock
```

Copy `.env.example` to `.env` only if `.env` does not already exist, then fill in your own credentials. Never commit `.env` or runtime databases. The workspace has been tested on Python 3.14.7.

## Code structure

```text
app.py                         Streamlit entry point
project_status_agent/
    config.py                  Shared settings and environment loading
    cli.py                     Terminal commands
    agent/                     ReAct graph, tools, run/resume/retry
    integrations/              Notion, Mem0, Nebius, HTTP transport
    data/                      Validation, SQLite storage, factual analysis
    reports/                   Report content, workflow, library, downloads
    ui/                        Report screen, chat screen, charts
scripts/                       Explicit maintenance and live verification
sample_project_data/           Original synthetic baseline through Nov 4
sample_project_data_nov10/     Synthetic continuation through Nov 10
tests/                         Offline regression tests
docs/                          Architecture, setup, usage, acceptance
```

Start with [the architecture guide](docs/ARCHITECTURE.md) for individual files and execution paths. No database migration is needed for the package reorganization. Existing reports, conversation checkpoints, memory delivery state, and CLI commands use the same storage.

## Check connections and data

```sh
python -m project_status_agent check-config
python -m project_status_agent sync --source notion
python -m project_status_agent status --source notion
python -m project_status_agent tools fetch_sprint_data --source notion --args '{"as_of_date":"2026-11-10"}'
```

Configuration checks show whether settings exist; they do not authenticate to services. Sync is read-only against Notion and validates the complete bundle before updating SQLite. Failed syncs preserve the last valid data. Owners are text labels; preserve the documented database column names and types.

The extended synthetic dataset has 20 tasks, 3 sprints, and 200 history records through November 10, 2026. The original fixture has 120 history records through November 4 and remains available for reproducible tests. Sources are isolated: selecting CSV does not combine its records with Notion.

For offline CSV data, run:

```sh
python -m project_status_agent sync --source csv --folder sample_project_data_nov10
```

History records status, sprint assignment, and blocker notes. Task ownership, priority, and deadlines are current attributes, not a historical change log. Old reports and chat cutoffs remain fixed; start a new chat or report to use newer data.

## Memory and model behavior

SQLite is the source for exact historical facts. Mem0 provides ranked contextual retrieval and is not an exhaustive historical query engine. Sync queues observations locally; uploading to Mem0 is an explicit separate action:

```sh
python -m project_status_agent memory-publish --source notion --limit 80
python -m project_status_agent memory-poll --source notion
```

Inspect status first. Do not blindly resend uncertain deliveries. The Notion dataset was synchronized to Mem0 with 200 sent records on November 10; later changes require another sync and memory publication.

Interactive Q&A uses a 60-second model timeout with no automatic provider retries and offers a manual Retry button after a timeout. If a model response selects no tool and no valid evidence exists, the graph allows one additional tool-selection decision before handing off. This is bounded reasoning recovery, separate from network retries. Reports use their separate model timeout/retry configuration. Human approval is required before an agent-requested sync or saving an approved report. Mitigation actions are proposals; approval does not modify tasks or execute actions.

## Validation

```sh
LANGSMITH_TRACING=false python -m unittest discover -s tests -v
python -m ruff check project_status_agent tests scripts app.py
```

Tests use isolated databases and model doubles. Real service checks spend provider credits and are separate; see [acceptance results](docs/ACCEPTANCE_RESULTS.md) and [maintenance scripts](scripts/README.md).

## Temporary Replit demo

For the isolated CSV demonstration with SQLite, see [Replit setup](docs/REPLIT_DEMO.md). The launcher initializes synthetic records without copying local databases.

## Project documentation

- [Project overview, architecture, tools, datasets and learnings — PDF](docs/Project_Pulse_Project_Documentation.pdf) · [Word](docs/Project_Pulse_Project_Documentation.docx)
- [Architecture flow diagram](docs/Project_Pulse_Architecture.png)

The synthetic CSV fixtures are included. Local credentials, runtime databases, backups and raw acceptance artifacts under `data/` are excluded. Published acceptance documents contain reviewed results; LangSmith trace links require access to the original workspace. Rebuilding the acceptance documents requires the local evidence JSON files used by the maintenance script.

## Guides

- [Architecture and module map](docs/ARCHITECTURE.md)
- [Notion database setup](sample_project_data/NOTION_SETUP.txt)
- [November 10 synthetic dataset](sample_project_data_nov10/README.md)
- [Model configuration](docs/MODEL_SETUP.md)
- [Agent workflow and human handoffs](docs/REACT_AGENT.md)
- [Analysis tools](docs/TOOLS.md)
- [Reports and review](docs/REPORTS.md)
- [UI guide](docs/UI_GUIDE.md)
- [Acceptance results](docs/ACCEPTANCE_RESULTS.md)
- [Acceptance tests and evidence summary (Word)](docs/Project_Pulse_Acceptance_Tests_and_Evidence.docx) · [PDF](docs/Project_Pulse_Acceptance_Tests_and_Evidence.pdf)
