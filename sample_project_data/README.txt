PROJECT C DEMO DATA — ONLINE LEARNING PLATFORM

All names, tasks, dates, and histories are synthetic. These records are demo fixtures, not evidence of real project activity. Reporting as-of date: 2026-11-04. Sprint 3 is incomplete.

FILES
sprints.csv: 3 two-week sprint definitions.
tasks.csv: 20 current tasks as of the reporting date.
task_history.csv: 120 explicit historical snapshots (20 tasks x 6 observation dates).

NOTION IMPORT
Create a new page and use Import > CSV for each file, producing three tables. Use the human-readable task/sprint identifiers to join records in your agent; CSV imports do not automatically create Notion relations. Treat Task ID and Sprint ID as text. Set date columns to Date, and statuses/priorities to Select if desired. Owner is a fictional text label, not a real Notion user. Keep the observation dates as explicit properties; Notion creation/edit timestamps are import timestamps, not historical activity.

DATA MEANING
Planned Sprint ID is the original intended sprint, not proof of current sprint membership. Observation Sprint ID identifies the reporting period in which a snapshot was taken. All tasks are in the initial backlog, so To do snapshots may precede the planned sprint. Status Since means the first date the current status appears in these fixtures, not a complete activity log. An unchanged status does not prove no work occurred. Assigned Sprint ID now provides explicit synthetic membership at each observation. Blank means backlog before the planned sprint. Unfinished tasks carry into the next observed sprint; completed tasks retain their completion sprint. Current Sprint ID is the latest assigned sprint. Do not calculate story-point velocity: this dataset has no estimates or commitment baselines.

EXPECTED RESULTS AS OF 2026-11-04
20 tasks: 10 Done, 5 In progress, 3 Blocked, 2 To do.
4 overdue incomplete tasks: T-004, T-006, T-008, T-011 (4 tasks).
T-004 is explicitly Blocked in snapshots across all three sprint periods.
T-008 is explicitly Blocked in Sprint 2 and Sprint 3 snapshots.
T-013 is explicitly Blocked only in Sprint 3 snapshots.
T-006 and T-011 remain In progress from 2026-10-23 through 2026-11-04; flag as unchanged status, not confirmed blockers.
T-004 is a shared dependency of T-008 and T-013.
Done counts at each observation date: 0, 3, 4, 8, 8, 10. These are cumulative completion counts, not sprint velocity.

DEMO QUESTIONS
What is the current project status?
Which unfinished tasks are overdue as of November 4?
Which tasks were blocked across more than one sprint period?
Which tasks have had an unchanged In progress status since October 23?
What downstream work depends on payment integration?
What changed between the last two snapshots?

After connecting your agent, save new dated snapshots on each sync. Keep genuine observations separate from these synthetic fixtures. For a live demo, explicitly set the reporting date rather than silently using the computer date.

ADDED FIELDS
Project ID scopes all tables to PRJ-001. Depends On Task IDs explicitly links T-008 and T-013 to T-004. Assigned Sprint ID captures synthetic membership per observation. Current Sprint ID supports current-sprint filtering. These additions are authored demo facts, not recovered real history.
