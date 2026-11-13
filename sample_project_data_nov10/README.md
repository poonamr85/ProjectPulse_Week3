# Synthetic project continuation through November 10, 2026

This is fictional demonstration data, not real delivery evidence. It extends the original 20-task, three-sprint dataset from November 4 through November 10. Original sample_project_data fixtures remain available unchanged for historical tests.

Added 80 observations: all 20 tasks on November 5, 6, 9, and 10 (weekdays only). The combined dataset contains 200 history records. Existing October–November 4 observations and sprint assignments are preserved. Owners, priorities, and due dates remain unchanged.

## Fictional changes

| Date | Scenario |
| --- | --- |
| Nov 5 | Baseline carried forward, providing another observation of persistent blockers. |
| Nov 6 | Payment integration unblocked and in progress; analytics completed; security review blocked awaiting scan evidence; launch announcement started. |
| Nov 9 | Payment integration and accessibility review completed; checkout tests resumed; receipt work remains blocked on provider webhook configuration; performance testing blocked on environment capacity. |
| Nov 10 | Checkout tests blocked again on webhook signature validation; security review resumed; enrollment testing and launch announcement completed; go-live review started. |

As of November 10: 15 Done, 3 Blocked, 2 In progress across the project. Sprint 3 has 12 assigned tasks: 7 Done, 3 Blocked, 2 In progress. Current blockers: T-008, T-013, T-014. T-008 remains blocked across multiple sprints. T-013 has interrupted blockage; do not describe its whole history as continuously blocked.

Historical observations show task status and sprint assignment. They do not establish exact completion times, uninterrupted blockage between observations, or changes in ownership. Current task attributes are not a historical ownership/deadline log.

## Useful Q&A tests

- What changed between November 4 and November 10?
- Which blockers were resolved, and which are new?
- Which tasks have been blocked across multiple sprints?
- What are the risks for Sprint 3 as of November 10?
- Why are receipts still blocked after payment integration was completed?

The one-time Notion extension scripts have been retired after successful migration. A compact local rollback archive, data/backups/notion-extension-nov10.zip, preserves the source scripts, original backup, schema, and write journal. The app's normal Notion connector remains read-only. Current synchronization evidence is consolidated in data/acceptance/data-sync-verification.json. The CSV files here are the reusable extended dataset.
