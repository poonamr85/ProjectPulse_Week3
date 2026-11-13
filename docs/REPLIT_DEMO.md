# Temporary Replit demonstration

This setup keeps SQLite and initializes the November 10 synthetic CSV dataset. It does not copy or alter your local reference databases. It is a temporary shared demonstration, not a durable multi-user deployment.

## Import and configure

1. Import `https://github.com/poonamr85/ProjectPulse_Week3` into a Python Replit project, or pull the latest commit into an existing import.
2. In the Replit Shell, install dependencies with `python -m pip install -r requirements-tools.lock`. Use Python 3.11 or newer; Linux dependency installation still needs verification on Replit.
3. Add these values in Replit Secrets / configurations, using your own credentials:

| Name | Purpose |
| --- | --- |
| `NEBIUS_API_KEY` | Required for Q&A and report narrative generation |
| `NEBIUS_MODEL` | `zai-org/GLM-5.3-Flash` |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1` |
| `LANGSMITH_API_KEY` | Optional trace access |
| `LANGSMITH_TRACING` | `true` to record traces |
| `LANGSMITH_PROJECT` | Use `project-pulse-replit-demo` to distinguish hosted evidence |
| `MEM0_API_KEY` / `MEMORY_USER_ID` | Optional memory retrieval; existing Notion-tagged memories are not CSV memories |

Notion credentials are unnecessary for the CSV walkthrough. Do not upload `.env`, runtime databases or private backups. Review published-app secrets separately if publishing.

## Start and load data

Press Run, or use `python -m scripts.run_replit_demo`.

- Seeds 20 tasks, 3 sprints and 200 historical observations into `data/replit_demo/project_status.sqlite3` on first startup.
- Uses `data/replit_demo/agent_checkpoints.sqlite3` for hosted conversations and reports.
- Defaults the UI to **Sample CSV snapshots**; the normal local launcher still defaults to Notion.
- Skips re-import when demo history is already present; does not delete saved reviews on an ordinary process restart with files intact.
- Queues observations locally but does not publish them to Mem0 or generate a paid report automatically.
- Binds Streamlit to `0.0.0.0:8501`; `.replit` maps this to the web port.

## Generate and review

1. Keep **Sample CSV snapshots** selected.
2. Set Reporting date to **November 10, 2026** and Compare from to **November 4, 2026**.
3. Click **Generate report**. This calls Nebius and uses credits.
4. Wait for **Awaiting review**. Request a revision or approve only after reviewing the displayed version.
5. Download Word, PDF or Markdown; test a sprint or historical question in Ask the Agent.

Local report labels such as RPT-013 are not imported: this hosted workspace starts its own report library. Mem0 search is source-isolated: it will not reuse Notion-tagged observations as CSV evidence. Structured history works without memory publication.

## Sharing and state

- Prefer restricted Replit access/preview for the walkthrough. The application has no individual account/role isolation; viewers share the project and can spend provider credits or review reports.
- Use one running deployment instance. Do not use multiple autoscaling instances with separate local SQLite files.
- Complete the walkthrough without republishing midway. A deployment reset/replacement can lose the databases; startup can reload CSV facts, but it cannot recreate past reports, approvals or conversations.
- Keep the local application/databases as the reference copy. Do not treat a temporary hosted restart result as durable storage certification.
- Publishing is a separate step in Replit. Confirm the selected runtime, access settings, secrets and port configuration before publishing. Neither dependency compatibility nor the published URL is verified by local tests.

References: [Replit configuration](https://docs.replit.com/features/project-setup/configuration), [ports](https://docs.replit.com/features/project-setup/ports), [Secrets](https://docs.replit.com/core-concepts/project-editor/app-setup/secrets).
