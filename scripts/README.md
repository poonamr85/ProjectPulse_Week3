# Maintenance scripts

Run from the project root. These scripts are separate from normal app operation.

| Command | Purpose | External effects |
| --- | --- | --- |
| `python -m scripts.generate_acceptance_document` | Rebuild the current acceptance Word/PDF using retained evidence | None; writes only the two current document files |
| `python -m scripts.verify_live_traces` | Run a real agent question and report, then verify LangSmith spans | Uses provider credits; creates an unapproved report; saves a timestamped verification folder |

Use the regular project CLI for sync, memory publication, questions, and report review. The live verification script uses its explicit November 4 fixture cutoff; normal UI questions use the latest saved data.

The completed November 10 migration scripts and their migration-only tests have been retired from the active source tree. Their source, rollback data, and write journal are preserved in the ignored local archive `data/backups/notion-extension-nov10.zip`. They are not required to run the application.
