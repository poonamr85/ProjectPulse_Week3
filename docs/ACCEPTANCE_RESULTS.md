# Acceptance results

Current evidence for PRJ-001 / Notion / November 10, 2026 reporting cutoff, using GLM-5.3-Flash and synthetic records. Selected live results and automated safeguards are distinguished below; this is not blanket application sign-off.

| Check | Result | Current evidence |
| --- | --- | --- |
| Project data and Mem0 | Last verified baseline: 20 tasks, 3 sprints, 200 observations; 200 memory deliveries sent and sample retrieval checked | `data/acceptance/data-sync-verification.json` |
| Grounding and historical follow-ups | Six selected answers PASS, with verified model/tool traces; shared-conversation evidence reuse is explicitly recorded | `data/acceptance/grounding-evaluations.json` |
| Recorded facts versus forecasts | Fresh-chat targeted retest PASS; no claim of guaranteed future narrative accuracy | Supplemental retest in `grounding-evaluations.json` |
| Missing-tool recovery | Controlled injected no-tool response recovered through real model/tool calls; unsupported initial text not saved | `data/acceptance/controlled-tool-selection-recovery.json` |
| Independent sprint/blocker/risk sample | Five of six completed; one provider timeout. This earlier sample is not a fully passed reliability benchmark | `data/acceptance/tool-selection-fix-live.json` |
| Latest report generation | RPT-012 reached Awaiting review with no validation issues after application-controlled evidence-reference assembly; not human-approved by this test | `data/acceptance/report-evidence-reference-retest.json` |
| Mem0 first-tool failure | F01 PASS: HTTP 401 memory search followed by successful history, blocker and sprint tools; answer disclosed unavailable memory | `data/acceptance/failure-recovery-evaluations.json` |
| Notion first-tool failure | F02 PASS: HTTP 401 refresh followed by successful saved sprint/blocker tools; answer disclosed failed refresh and November 10 cutoff | Same failure-recovery evidence; direct traces included |
| H01 refresh cancellation | PASS: approval interrupt recorded; Cancel blocked refresh; agent disclosed denial and used retained saved evidence | `data/acceptance/human-review-evaluations.json`; initial/resume trace links |
| H02 report revision and approval | PASS: RPT-013 v1 review → revision → v2 review → explicit approval → saved; factual sections unchanged | Same human-review evidence; generation/revision/approval trace links |
| Offline regression | 103 tests PASS after the latest report and UI code changes | `data/acceptance/regression-tests.txt` |
| Stale approval and checkpoint recovery | PASS in that suite: old-version approval rejected; fresh workflow instance restores saved review state and avoids duplicate saves | `tests/test_reports.py` |
| Review controls and exports | Automated approval/revision/rejection and document-content coverage | `tests/test_webui.py` |

## Reviewer documents

- [Project documentation — Word](Project_Pulse_Project_Documentation.docx) / [PDF](Project_Pulse_Project_Documentation.pdf): overview, architecture, datasets, coding prompts, iterations, learnings and acceptance evidence.
- [Acceptance tests and evidence — Word](Project_Pulse_Acceptance_Tests_and_Evidence.docx) / [PDF](Project_Pulse_Acceptance_Tests_and_Evidence.pdf): scenario cards, exact answer excerpts, reviewer assessments, calls and direct trace links.

Regenerate the acceptance Word/PDF with `python -m scripts.generate_acceptance_document`. No API calls are made. The build journal retains the development history; this index describes current results rather than repeating historical test counts.

## Remaining scope

Independent fresh-chat sprint/blocker/risk verification and final visual report/download review remain separate checks where not already evidenced. Existing automated stale-approval and checkpoint-recovery evidence is retained; another manual rerun is not required solely for the documentation cleanup. Prepare the Google Doc, GitHub submission and five-minute demonstration.

## Retention

Keep both runtime SQLite databases and their active sidecars. Preserve the original November 4 fixtures used by tests and the November 10 continuation. They are distinct datasets, not duplicates. Word/PDF pairs are intentional editable/shareable formats.

Superseded model/follow-up/contract/report outputs and the previous version of this index are preserved in `data/backups/superseded-acceptance-nov11.zip`. The completed one-time data migration remains in `data/backups/notion-extension-nov10.zip`. Current evidence stays in `data/acceptance/`; runtime state was not changed by cleanup.
