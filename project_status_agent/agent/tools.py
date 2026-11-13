"""LangChain tool definitions for a single configured project and source.

The small wrappers delegate calculations to AnalysisService. A fresh SQLite
connection per invocation also allows future agent workers to use these tools.
"""

import os
import sqlite3
from collections.abc import Callable
from contextlib import closing

from langchain.tools import tool
from langchain_core.tools import BaseTool, ToolException

from project_status_agent.config import Settings, required
from project_status_agent.data.analysis import AnalysisService, Result, parse_date
from project_status_agent.data.store import Store
from project_status_agent.data.validation import capture_current
from project_status_agent.integrations.http import ServiceError
from project_status_agent.integrations.memory import ProjectMemory
from project_status_agent.integrations.notion import NotionSource


def create_tools(
    database_path: str,
    project_id: str,
    source: str = "notion",
    memory_factory: Callable[[], ProjectMemory] | None = None,
    sync_fetcher: Callable[[], dict] | None = None,
) -> list[BaseTool]:
    """Bind tool access to a trusted project/source, not model-supplied scope.

    Call load_env before constructing tools if using local .env credentials.
    A supplied sync_fetcher must return a complete, unsaved project bundle.
    """
    if not project_id.strip() or source not in {"csv", "notion"}:
        raise ValueError("Provide a project ID and source csv or notion.")

    def run(
        operation: Callable[[AnalysisService], Result], needs_memory: bool = False
    ) -> Result:
        try:
            memory = None
            if needs_memory:
                memory = (
                    memory_factory()
                    if memory_factory
                    else ProjectMemory(
                        required("MEM0_API_KEY"), required("MEMORY_USER_ID"), project_id
                    )
                )
            with closing(Store(database_path)) as store:
                service = AnalysisService(store, project_id, source, memory)
                return operation(service)
        except (ValueError, ServiceError) as error:
            raise ToolException(str(error)) from None
        except (OSError, sqlite3.Error):
            raise ToolException(
                "Local storage operation failed. Check the database path and access permissions."
            ) from None

    @tool
    def sync_project_data(observed_on: str) -> Result:
        """Read Notion and atomically save a current snapshot dated YYYY-MM-DD.

        Use only when fresh data is needed. Use the actual observation date,
        after imported history and within a defined sprint. Does not modify
        Notion or upload Mem0 memories. CSV mode needs an injected sync_fetcher.
        """

        def sync(service: AnalysisService) -> Result:
            parse_date(observed_on)

            def fetch() -> dict:
                if sync_fetcher:
                    bundle = sync_fetcher()
                elif source == "notion":
                    connector = NotionSource(
                        required("NOTION_TOKEN"),
                        Settings.from_env().notion_version,
                    )
                    bundle = connector.read_all(os.environ)
                else:
                    raise ValueError("CSV sync tool requires an explicit sync_fetcher.")
                return capture_current(bundle, observed_on)

            counts = service.store.sync(project_id, source, fetch)
            return {
                "project_id": project_id,
                "source": source,
                "observed_on": observed_on,
                "results": {"synced": counts},
                "warnings": [
                    "Memory observations are queued locally, not uploaded to Mem0."
                ],
            }

        return run(sync)

    @tool
    def fetch_sprint_data(as_of_date: str, sprint_id: str | None = None) -> Result:
        """Read saved sprint details, assignees, and status counts as of YYYY-MM-DD.

        Uses historical membership/status, not a live Notion read. If sprint_id
        is omitted, choose the sprint containing as_of_date. Explicit IDs must
        match records exactly (for example SPR-03, not 3). Owners are current
        attributes; Assigned Sprint ID provides historical membership.
        """
        return run(lambda service: service.fetch_sprint_data(as_of_date, sprint_id))

    @tool
    def detect_blockers(as_of_date: str) -> Result:
        """Find explicitly blocked tasks and potential downstream dependency impacts.

        Searches the whole project as of YYYY-MM-DD. An unchanged status alone
        does not qualify as a confirmed blocker. Blocker notes are dated history;
        owner and dependency information are current attributes.
        depends_on_task_ids lists recorded current dependencies. Similar blocker
        wording and potential affected tasks do not establish a shared cause.
        """
        return run(lambda service: service.detect_blockers(as_of_date))

    @tool
    def risk_scorer(as_of_date: str) -> Result:
        """Flag project-wide Critical, High, or Medium risks as of YYYY-MM-DD.

        Applies explicit rules to blockage, overdue tasks, priority, and observed
        unchanged In progress status. Returns reasons, not failure probabilities.
        Overdue means incomplete with a due date strictly before as_of_date.
        No critical-path calculation or guaranteed resolution date is available.
        """
        return run(lambda service: service.risk_scorer(as_of_date))

    @tool
    def trend_analysis(
        as_of_date: str,
        start_date: str | None = None,
        last_n_snapshots: int | None = None,
    ) -> Result:
        """Compare status, completions, and potential stalls between observations.

        Supply exactly one of start_date (YYYY-MM-DD) or last_n_snapshots (2–100
        distinct observation dates). Reports actual evidence dates and gaps.
        For changes in blocker notes or assigned sprints, use query_sprint_history.
        """
        return run(
            lambda service: service.trend_analysis(
                as_of_date, start_date, last_n_snapshots
            )
        )

    @tool
    def get_persistent_blockers(
        as_of_date: str,
        currently_blocked_only: bool = True,
        minimum_sprints: int = 2,
    ) -> Result:
        """Find tasks observed blocked in at least two distinct assigned sprints.

        Counts sprint IDs, not repeated sync runs. Set currently_blocked_only
        false to include historical blockers that have recovered.
        """
        return run(
            lambda service: service.get_persistent_blockers(
                as_of_date, currently_blocked_only, minimum_sprints
            )
        )

    @tool
    def query_sprint_history(
        as_of_date: str,
        task_id: str | None = None,
        sprint_id: str | None = None,
        start_date: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Result:
        """Read exact dated snapshots, newest first, with task/assigned-sprint filters.

        Dates use YYYY-MM-DD. limit is 1–100; use next_offset to retrieve another
        page. For the last N observations, set limit=N with offset=0.
        Historical Status, Assigned Sprint ID, and Blocker Note are available.
        Historical owner, priority, due-date, and dependency changes are not.
        """
        return run(
            lambda service: service.query_sprint_history(
                as_of_date, task_id, sprint_id, start_date, limit, offset
            )
        )

    @tool
    def search_project_memory(query: str, as_of_date: str, limit: int = 10) -> Result:
        """Search Mem0 for relevant explanations and verify their local source references.

        Use for context and reasons, not exhaustive blocker lists or exact counts.
        Reporting date is YYYY-MM-DD and limit is 1–100. Requires Mem0 credentials.
        """
        return run(
            lambda service: service.search_project_memory(query, as_of_date, limit),
            needs_memory=True,
        )

    tools = [
        sync_project_data,
        fetch_sprint_data,
        detect_blockers,
        risk_scorer,
        trend_analysis,
        get_persistent_blockers,
        query_sprint_history,
        search_project_memory,
    ]
    for registered_tool in tools:
        # Errors become tool observations the future ReAct agent can respond to.
        registered_tool.handle_tool_error = True
        registered_tool.handle_validation_error = (
            "Invalid tool arguments; check the tool schema and supply required fields."
        )
    return tools
