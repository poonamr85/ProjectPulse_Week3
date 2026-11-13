# Weekly reports with proposed risk mitigations

The checked report path is:

**Collect tool evidence → assemble draft → check facts/references → human review → save approved version.**

The `report` command uses a small LangGraph workflow built around the four required
analysis tools. It complements the general-purpose ReAct `ask` command. An `ask`
answer is still an unapproved narrative draft; use `report` for this versioned,
checked review-and-save process.

## Generate

```sh
source .venv/bin/activate
python -m project_status_agent report --as-of 2026-11-04
```

Defaults to the seven-day period ending on that date. Use `--start-date 2026-10-30`
for an explicit comparison boundary. Use `--source csv` for fixture-only analysis.
Generation uses Nebius credits, but does not read Notion live, publish to Mem0,
send messages, or change tasks. Sync data separately if needed.

The output contains `report_id`, `version`, `status`, `validation_issues`, structured
report JSON, and readable Markdown. A successful draft pauses at `awaiting_review`.

## What the mitigation plan contains

For every flagged project-wide risk:

- Task ID and severity, supplied by the Python risk tool.
- Proposed next action generated from the recorded risk and blocker context.
- Suggested owner taken from the current task owner, not an invented assignee.
- An observable success check.
- Snapshot IDs connecting the proposal to its risk evidence.

For example, T-004's missing provider credentials can motivate a proposed provider
follow-up and preparation using mocked responses. The evidence establishes the
missing credentials; it does not establish that the proposed workaround will be
feasible or successful. Suggested owners have not accepted an assignment. Approval
of the report does not execute or assign any mitigation action.

## Inspect and review

Copy the ID from the generated output. Inspect without model calls:

```sh
python -m project_status_agent report-show YOUR_REPORT_ID --markdown
```

Choose one decision for the exact version you reviewed:

```sh
python -m project_status_agent report-review YOUR_REPORT_ID --version 1 --decision approve
python -m project_status_agent report-review YOUR_REPORT_ID --version 1 --decision reject
python -m project_status_agent report-review YOUR_REPORT_ID --version 1 --decision revise --feedback "Make the proposed success checks easier to verify."
```

Revision generates a new draft using the same frozen evidence and the feedback;
it uses Nebius credits. The version increments and requires a new approval. Old
version approvals are rejected. Up to three versions and two generation attempts
per version are allowed. To refresh evidence, start a new report after syncing.

Approval/rejection itself needs no model request. Review decisions, feedback, time,
and the reviewed content hash are retained in LangGraph checkpoints. This is a
local single-user workflow, not an authenticated multi-user approval system.

## Validation and saving

Python builds counts, task facts, risk flags and trend results directly from the
tool outputs. The model writes the short summary and mitigation proposals only.
Checks cover matching scope/dates, unchanged facts, count totals, complete task
lists, one mitigation per risk, known task references, matching risk owners and
severity, evidence references, reporting cutoffs, and synthetic-data disclosure.
Schema and reference failures get a bounded correction attempt; unresolved issues
stop at `needs_attention` and cannot reach approval.

The checks do not prove every sentence in the summary or the practical feasibility
of the mitigation. Human review remains necessary. There is no numerical
probability of failure, invented mitigation deadline, or automatic task assignment.

The exact approved version is saved atomically as JSON and Markdown in the
`approved_reports` table of the project SQLite database. A content hash prevents
changes after approval. Repeating the same approval does not duplicate the saved
record. Rejection does not save an approved report. Draft state is checkpointed
separately so review survives application restarts. There is no external report
publication or automatic approved-summary upload to Mem0 in this milestone.

## Code

- `reports/content.py`: narrative schema, factual assembly, validation, Markdown, approved save.
- `reports/workflow.py`: collection, bounded generation/checking, interrupt, revision and save.
- `cli.py`: report creation, inspection and review commands.
- `tests/test_reports.py`: report errors, proposed-owner evidence, revisions, rejection,
  restart, stale approvals, hash protection, and duplicate-save checks.

Run `python -m unittest discover -s tests -v`. Tests use fake model responses and
temporary databases. A live GLM-5.2 draft was generated with five proposed plans,
passed the structured checks, and remains unapproved for the user to review.

Historical note: with an October 28 start, the closest available baseline is
October 23. The report explicitly labels that comparison approximate and shows
the actual observation dates. Current task attributes do not establish past owner,
deadline or dependency changes.


## Evidence references and review availability

The model writes a task ID, proposed action and success check for each flagged risk. The application attaches the exact current risk snapshot ID, owner and severity from the frozen tool evidence. The model does not generate evidence IDs. Validation still checks risk coverage, duplicates, task scope, reference integrity and reporting cutoff before review. This prevents reference-copying errors; it does not prove the semantic correctness or feasibility of proposed actions, which remain subject to human review.

The review controls (Changes to the report, Approve & save, Request revision and Reject report) appear only for Awaiting review reports. Needs attention reports show why checks failed and explain how to select a reviewable report or generate a new one. Existing failed and approved report versions retain their original state; this change applies to new generations and revisions.
