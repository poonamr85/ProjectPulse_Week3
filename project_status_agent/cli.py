"""Run with python -m project_status_agent. No remote writes during sync."""

import argparse
import json
import os
import sys
from datetime import date

from project_status_agent.config import Settings, load_env, required
from project_status_agent.data.store import Store
from project_status_agent.data.validation import capture_current, read_csv_bundle
from project_status_agent.integrations.http import ServiceError
from project_status_agent.integrations.memory import (
    ProjectMemory,
    publish_pending,
    reconcile_submitted,
)
from project_status_agent.integrations.notion import NotionSource


def main():
    parser = argparse.ArgumentParser(
        description="Project status data and memory foundation"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "check-config", help="Show presence of credentials; never their values"
    )
    sync = sub.add_parser(
        "sync", help="Fetch, validate, and atomically save project records"
    )
    sync.add_argument("--source", choices=["csv", "notion"], default="csv")
    sync.add_argument("--folder", default="sample_project_data")
    sync.add_argument(
        "--observed-on",
        help="Explicit date for a new current-task snapshot; must follow imported history",
    )
    status = sub.add_parser("status")
    status.add_argument("--source", choices=["csv", "notion"], default="csv")
    publish = sub.add_parser(
        "memory-publish", help="Explicitly publish queued source observations to Mem0"
    )
    publish.add_argument("--source", choices=["csv", "notion"], default="csv")
    publish.add_argument("--limit", type=int, default=10)
    poll = sub.add_parser("memory-poll", help="Refresh asynchronous delivery receipts")
    poll.add_argument("--source", choices=["csv", "notion"], default="csv")
    search = sub.add_parser("memory-search")
    search.add_argument("query")
    search.add_argument("--source", choices=["csv", "notion"], default="csv")
    search.add_argument("--as-of", required=True)
    tools = sub.add_parser(
        "tools", help="List tools or invoke one directly without a model"
    )
    tools.add_argument("name", nargs="?", help="Omit to list available tool schemas")
    tools.add_argument("--source", choices=["csv", "notion"], default="notion")
    tools.add_argument(
        "--args",
        default="{}",
        help='JSON object, for example {"as_of_date":"2026-11-04"}',
    )
    model_check = sub.add_parser(
        "model-check", help="Test Nebius tool calling with two bounded model requests"
    )
    model_check.add_argument("--source", choices=["csv", "notion"], default="notion")
    model_check.add_argument("--as-of", required=True)
    ask = sub.add_parser(
        "ask", help="Run the bounded ReAct agent on a project question"
    )
    ask.add_argument("question")
    ask.add_argument("--as-of", required=True)
    ask.add_argument("--source", choices=["csv", "notion"], default="notion")
    ask.add_argument("--thread-id")
    resume = sub.add_parser("agent-resume", help="Approve or deny a paused sync")
    resume.add_argument("thread_id")
    resume.add_argument("--decision", choices=["approve", "deny"], required=True)
    resume.add_argument("--source", choices=["csv", "notion"], default="notion")
    inspect = sub.add_parser(
        "agent-status", help="Inspect saved agent state without model calls"
    )
    inspect.add_argument("thread_id")
    inspect.add_argument("--source", choices=["csv", "notion"], default="notion")
    report = sub.add_parser(
        "report", help="Generate a checked weekly draft with proposed mitigations"
    )
    report.add_argument("--as-of", required=True)
    report.add_argument("--start-date")
    report.add_argument("--source", choices=["csv", "notion"], default="notion")
    report.add_argument("--report-id")
    report_review = sub.add_parser(
        "report-review", help="Review one exact report version"
    )
    report_review.add_argument("report_id")
    report_review.add_argument("--version", type=int, required=True)
    report_review.add_argument(
        "--decision", choices=["approve", "reject", "revise"], required=True
    )
    report_review.add_argument("--feedback", default="")
    report_review.add_argument("--source", choices=["csv", "notion"], default="notion")
    report_show = sub.add_parser(
        "report-show", help="Show a report draft or approved report without model calls"
    )
    report_show.add_argument("report_id")
    report_show.add_argument("--source", choices=["csv", "notion"], default="notion")
    report_show.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    store = None
    try:
        load_env()
        settings = Settings.from_env()
        if args.command == "check-config":
            result = {
                key: bool(os.environ.get(key, "").strip())
                for key in (
                    "NOTION_TOKEN",
                    "NOTION_TASKS_DATABASE_ID",
                    "NOTION_SPRINTS_DATABASE_ID",
                    "NOTION_HISTORY_DATABASE_ID",
                    "MEM0_API_KEY",
                    "MEMORY_USER_ID",
                    "OPENAI_API_KEY",
                    "LANGSMITH_API_KEY",
                    "NEBIUS_API_KEY",
                    "NEBIUS_MODEL",
                    "NEBIUS_BASE_URL",
                )
            }
        elif args.command in {"report", "report-review", "report-show"}:
            from project_status_agent.reports.workflow import run_report

            if args.command == "report" or (
                args.command == "report-review" and args.decision == "revise"
            ):
                print(
                    "Generating a checked report draft using Nebius credits. The workflow will pause for your review.",
                    file=sys.stderr,
                )
            result = run_report(
                database_path=settings.database_path,
                checkpoint_path=settings.checkpoint_path,
                project_id=settings.project_id,
                source=args.source,
                as_of_date=args.as_of if args.command == "report" else None,
                start_date=args.start_date if args.command == "report" else None,
                report_id=args.report_id,
                decision=args.decision if args.command == "report-review" else None,
                version=args.version if args.command == "report-review" else None,
                feedback=args.feedback if args.command == "report-review" else "",
                inspect_only=args.command == "report-show",
            )
            if args.command == "report-show" and args.markdown:
                print(result["markdown"])
                return
        elif args.command in {"ask", "agent-resume", "agent-status"}:
            from project_status_agent.agent.runner import run_agent

            if args.command != "agent-status":
                print(
                    "Running the ReAct agent using Nebius credits. Execution is bounded; sync requests pause for human approval.",
                    file=sys.stderr,
                )
            result = run_agent(
                database_path=settings.database_path,
                checkpoint_path=settings.checkpoint_path,
                project_id=settings.project_id,
                source=args.source,
                question=args.question if args.command == "ask" else None,
                as_of_date=args.as_of if args.command == "ask" else None,
                thread_id=args.thread_id,
                decision=(args.decision == "approve")
                if args.command == "agent-resume"
                else None,
                inspect_only=args.command == "agent-status",
            )
        elif args.command == "model-check":
            from project_status_agent.integrations.model import check_model

            print(
                "Testing Nebius model selection and a read-only local tool. Two model requests at most; requests use your Nebius credits.",
                file=sys.stderr,
            )
            result = check_model(
                settings.database_path,
                settings.project_id,
                args.source,
                args.as_of,
            )
        elif args.command == "tools":
            from project_status_agent.agent.tools import create_tools

            registered = create_tools(
                settings.database_path,
                settings.project_id,
                args.source,
            )
            by_name = {tool.name: tool for tool in registered}
            if args.name:
                if args.name not in by_name:
                    raise ValueError(f"Unknown tool. Choose from: {', '.join(by_name)}")
                arguments = json.loads(args.args)
                if not isinstance(arguments, dict):
                    raise ValueError("--args must be a JSON object.")
                tool_result = by_name[args.name].invoke(arguments)
                result = {"tool": args.name, "result": tool_result}
            else:
                result = {
                    tool.name: {"description": tool.description, "arguments": tool.args}
                    for tool in registered
                }
        else:
            project = settings.project_id
            if args.command == "memory-search":
                date.fromisoformat(args.as_of)
            if args.command == "memory-publish" and not 1 <= args.limit <= 120:
                raise ValueError("--limit must be between 1 and 120.")
            store = Store(settings.database_path)
            if args.command == "sync":
                if args.source == "csv":
                    fetch = lambda: read_csv_bundle(args.folder)
                else:
                    print(
                        "Reading Notion databases. Network requests can take up to 30 seconds per attempt; temporary failures retry up to three times.",
                        file=sys.stderr,
                    )
                    notion = NotionSource(
                        required("NOTION_TOKEN"),
                        settings.notion_version,
                    )
                    fetch = lambda: notion.read_all(os.environ)
                original_fetch = fetch
                if args.observed_on:
                    fetch = lambda: capture_current(original_fetch(), args.observed_on)
                result = {
                    "synced": store.sync(project, args.source, fetch),
                    "memory": "queued locally; not uploaded",
                }
            elif args.command == "status":
                result = store.summary(project, args.source)
            else:
                memory = ProjectMemory(
                    required("MEM0_API_KEY"), required("MEMORY_USER_ID"), project
                )
                if args.command == "memory-publish":
                    result = {
                        "published_or_submitted": publish_pending(
                            store, memory, project, args.source, args.limit
                        )
                    }
                elif args.command == "memory-poll":
                    reconcile_submitted(store, memory, project, args.source)
                    result = store.summary(project, args.source)
                else:
                    result = {
                        "results": memory.search(args.query, args.source, args.as_of),
                        "note": "Semantic context only; verify temporal conclusions against exact snapshots.",
                    }
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        parser.exit(
            130,
            "\nCancelled with Ctrl+C. The operation did not finish. Check sync status before retrying; do not blindly retry a memory upload.\n",
        )
    except (ValueError, OSError, ServiceError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    main()
