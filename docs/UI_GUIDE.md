# Review and download reports in the UI

## Start the application

From the project directory:

```sh
source .venv/bin/activate
python -m streamlit run app.py
```

Open **http://localhost:8501**. Keep the terminal running while using the app.
Stop it with Ctrl+C when finished. The server binds to this computer's loopback
address. No hosting deployment is needed.

On another machine, install the UI dependencies first:

```sh
python -m pip install -e '.[ui]'
```

The existing private `.env` supplies Notion, Mem0, Nebius, project, and database
settings. Keys are never displayed in the UI or included in report downloads.

## Review the current draft

The app automatically opens the latest report in the selected project/source.
The existing weekly draft is available under **Notion · saved snapshots**.
Opening or switching reports does not make model requests.

- **Overview:** prominent summary bullets, before/after status bars, observed-change bars, and an interactive sprint task status map. Switch to Workload by owner for a stacked bar chart.
- **Risks & mitigation:** each proposed action, suggested owner, success check,
  and supporting snapshot IDs.
- **Source details & review history:** an optional collapsed section with observation dates, freshness, limitations, evidence references, and version decisions. The duplicate Full Report tab has been removed; downloads contain the full report.

Sprint-specific metrics and project-wide risk counts are labeled separately.
Proposed owners are current task owners; no assignment or action is executed.

## Generate a report

1. Select the data source in the sidebar.
2. Choose the reporting date and comparison date.
3. Click **Generate report**.

For the prepared dataset, use reporting date **November 4, 2026**. The default
comparison starts seven days earlier. Available snapshots make that comparison
approximate; the actual dates are shown in the report.

Generation reads already-synced data and uses Nebius credits. It does not sync
Notion, send messages, publish to Mem0, or modify tasks. A failed validation is
displayed as needing attention, with the specific issues.

## Approve, reject, or revise

The **Review this report** panel appears for a checked draft. It changes report wording and proposed mitigation plans, not project task data. Correct task statuses, owners or deadlines in Notion, sync those updates, and generate a new report.

Review options:

- **Approve & save:** saves the exact displayed version locally.
- **Request revision:** requires feedback, generates a new version using Nebius,
  and pauses for another approval.
- **Reject report:** ends review without saving it as approved.

Review uses the existing version/hash checks and durable LangGraph checkpoint.
The report library lets you reopen the latest draft or saved version after an app
restart. This is the existing local single-user workflow; saving a report does
not execute its mitigation proposals.

## Download

At the bottom of the report, choose:

| Format | Use |
| --- | --- |
| Word (.docx) | Editable, styled document with headings and page numbering |
| PDF (.pdf) | Paginated, selectable-text document for review and sharing |
| Markdown (.md) | Plain source for project documentation |

All downloads use the reviewed structured report. The filename includes project,
reporting date, version, and draft/approved state. Document content and page footers
also show approval state. A rejected report is labeled not approved. Downloading
a draft does not approve it. Exports are unavailable while validation issues remain.

## Implementation and verification

- `app.py`: Streamlit entry point.
- `project_status_agent/ui/reports.py`: generation, report library, review, charts,
  metrics, and downloads; calls the existing report workflow.
- `project_status_agent/reports/exports.py`: Word/PDF/Markdown generation from one report.
- `.streamlit/config.toml`: theme and local server settings.
- `tests/test_webui.py`: Streamlit interaction tests plus Word/PDF content checks.

Tests use fake model replies and temporary databases. They cover opening an
existing report without regeneration, generating, approving, rejecting, revising,
scope isolation, and three download controls. Word files are reopened to check
content; PDF text and page counts are checked with a PDF reader. A live local
server health check is separate from these tests. Browser pixels and actual
browser download dialogs were not automated.

## Reading progress

The top metrics describe the current sprint (except the explicitly labeled project risk count). Progress graphs show project-wide counts for the same comparable task population at both actual snapshot boundaries, plus newly completed, newly blocked, and unchanged In progress counts. The sprint status map shows one point per task, with status colors and owner/deadline details on hover. Workload by owner uses stacked task counts; it does not measure hours or capacity. Summary bullets are formatting of the existing text, so saved report data and approval hashes are preserved.

The download section now uses one dropdown. Selecting Download Word, Download PDF, or Download Markdown starts that download directly; there is no second button.

Word and PDF downloads now embed the progress comparison, observed-change graph, and sprint workload-by-owner graph. They use the same frozen facts as the UI, with actual observation dates. Markdown includes SVG images as embedded data URLs; image display depends on the Markdown viewer. Download again to get the updated layout; no new model request or approval is needed for this formatting change.

## Selecting reports

The report selector is in the full-width main area under Report library. Labels use a friendly report number, reporting date, version, and a readable review status. Selection opens immediately and updates the URL. The selected report title shows its date and version; internal IDs are hidden from the screen. The separate Open selected report button has been removed.

Reports now have permanent friendly display numbers (RPT-001, RPT-002, ...). The same number is retained across revisions and application restarts. The dropdown and report heading use the friendly number; UUIDs remain in existing direct links and CLI commands, but are hidden from the screen. Display-number metadata is stored separately in the checkpoint database, without altering approved report content.
# Ask the Agent

Interactive Q&A uses a 60-second timeout per model request and no automatic retries. Set `NEBIUS_QA_TIMEOUT_SECONDS` in `.env` to override it (15–180 seconds), then restart Streamlit. This is separate from report-generation timeout and retry settings. Multiple model/tool steps can take longer than 60 seconds overall.

After a model timeout, select **Retry** to continue the saved investigation. The retry preserves the question, messages, and completed tool observations rather than submitting a duplicate question or replaying completed tools. Successful syncs with identical arguments are guarded against repeated execution within the same investigation. Retry is user initiated and remains subject to the investigation's execution limits. Choosing **New chat** starts a separate investigation.

In the sidebar, choose **Workspace → Ask the Agent**. Type a question in the chat box. New chats automatically use the latest saved observation date. Try “What has been blocked for more than one sprint?” or “What changed since last week?”

The agent chooses the relevant tools and returns an answer with task/snapshot references when available. Execution details, tool arguments, and thread IDs stay out of the chat; developers can inspect traces in LangSmith. Memory provides context; structured snapshot tools provide historical facts.

Ask a follow-up in the same conversation to retain prior messages. The reporting cutoff stays fixed; select **New chat** to begin again using the latest saved date. The active chat stays in the page URL and can be reopened after a restart. There is no conversation dropdown. Browsing saved messages does not make model calls.

If the agent needs clarification, reply in the same chat box. For a fresh sync request, review the proposed action and select **Refresh data** or **Cancel**. A denied sync may still allow an answer from saved evidence. Report approval remains in the Weekly reports workspace.

When a provider fails or the investigation reaches its limits, the panel displays the reason. Fix the issue or narrow the question and submit it on the same conversation. Each follow-up gets a fresh bounded execution budget. The UI displays tool failures even when another tool enables a useful answer. Answers remain drafts, and evidence citations in narrative answers are model generated rather than independently validated.


The Q&A offers starter prompts for sprint summaries, current blockers, and project risks. While processing, it shows “Checking project records…”. Data freshness appears as “Data updated through Nov 4, 2026” (using the actual latest available date within the question cutoff). Errors and fallback notices use plain language. The chat does not show a separate Sources section. New model answers are instructed to use stakeholder language; previously saved answers retain their original wording.

Use the report library to open reports; the sidebar no longer offers an Open by report ID section. Existing direct report links still work.

## Overview presentation

The sidebar identifies the project above the data-source selector. The main heading is Project Pulse Overview, with a small synthetic-data caption below the report selector when the workspace contains only synthetic tasks. Internal report IDs and the Technical details section are hidden. Task charts are titled Sprint task progress. Comparison dates come from the selected report’s frozen facts; generating a newer report updates its graphs and dates together. Older saved reports retain their original dates. Provider-credit captions, the local-key caption, the approximate-comparison caption, and the download-format explanation have been removed from the screen. Evidence limitations remain in the report’s source/review details and exported report.
