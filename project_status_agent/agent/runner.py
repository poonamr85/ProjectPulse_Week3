"""Persist, inspect, and resume one agent investigation per thread ID."""

from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, Overwrite

from project_status_agent.agent.tools import create_tools
from project_status_agent.agent.workflow import (
    build_agent,
    count_reusable_evidence,
    initial_state,
)
from project_status_agent.integrations.model import create_model


def run_agent(
    database_path: str,
    checkpoint_path: str,
    project_id: str,
    source: str,
    question: str | None = None,
    as_of_date: str | None = None,
    thread_id: str | None = None,
    decision: bool | None = None,
    inspect_only: bool = False,
    model=None,
    tools=None,
    clarification: str | None = None,
    interactive: bool = False,
    retry: bool = False,
) -> dict:
    """Run a new investigation or inspect/resume its checkpoint after restart."""
    if question is None and not thread_id:
        raise ValueError(
            "A thread ID is required to inspect or resume an investigation."
        )
    thread_id = thread_id or str(uuid4())
    if str(Path(database_path).resolve()) == str(Path(checkpoint_path).resolve()):
        raise ValueError(
            "Use separate files for project records and agent checkpoints."
        )
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 40,
        "run_name": "Project Pulse - Agent investigation",
        "tags": ["project-pulse", "agent"],
        "metadata": {
            "project_id": project_id,
            "source": source,
            "reporting_date": as_of_date,
        },
    }
    with SqliteSaver.from_conn_string(checkpoint_path) as saver:
        # Inspection does not construct a model or make network requests.
        saved = saver.get_tuple(config)
        values = saved.checkpoint.get("channel_values", {}) if saved else {}
        if values and (
            values.get("project_id") != project_id or values.get("source") != source
        ):
            raise ValueError("Thread belongs to a different project/source.")
        if inspect_only:
            if not values:
                raise ValueError("Unknown investigation thread.")
            pauses = [
                pause
                for _, channel, items in (saved.pending_writes or [])
                if channel == "__interrupt__"
                for pause in items
            ]
            output = summarize(thread_id, values, pending=bool(pauses))
            if pauses:
                output["approval_request"] = pauses[0].value
            return output
        if retry:
            if (
                question is not None
                or decision is not None
                or clarification is not None
            ):
                raise ValueError(
                    "Retry cannot be combined with a new question or human response."
                )
            if values.get("status") != "needs_human" or not values.get("retryable"):
                raise ValueError("Only a timed-out model request can be retried.")
            if values.get("model_calls", 0) >= 6:
                raise ValueError(
                    "Investigation limit reached. Start a new chat or narrow the question."
                )
            # Continue at the model with existing observations; do not replay tools
            # or append another copy of the user's question.
            invocation = {"status": "running", "answer": "", "retryable": False}
        elif question is not None:
            if values and any(
                channel == "__interrupt__"
                for _, channel, _ in saved.pending_writes or []
            ):
                raise ValueError(
                    "Resolve the pending human approval before asking a follow-up."
                )
            if values:
                if as_of_date and as_of_date != values["as_of_date"]:
                    raise ValueError(
                        "Start a new conversation to change the reporting date."
                    )
                as_of_date = values["as_of_date"]
            if as_of_date is None:
                raise ValueError(
                    "New investigations require an explicit reporting date."
                )
            invocation = initial_state(question, project_id, source, as_of_date)
            if values:
                # Retain messages, but give each follow-up its own bounded budget.
                invocation["trace"] = Overwrite([])
                invocation["reused_evidence_count"] = count_reusable_evidence(
                    values.get("messages", []), project_id, source, as_of_date
                )
        else:
            if not values:
                raise ValueError("Unknown investigation thread.")
            if decision is None and clarification is None:
                raise ValueError("Resume requires approve or deny.")
            pauses = [
                pause
                for _, channel, items in saved.pending_writes or []
                if channel == "__interrupt__"
                for pause in items
            ]
            action = pauses[0].value.get("action") if pauses else None
            if action == "clarify":
                if not clarification or not clarification.strip():
                    raise ValueError("Provide the requested clarification.")
                invocation = Command(resume=clarification.strip())
            else:
                if not isinstance(decision, bool):
                    raise ValueError("Choose approve or deny for this action.")
                invocation = Command(resume=decision)
        configured_model = (
            model if model is not None else create_model(interactive=interactive)
        )
        graph = build_agent(
            configured_model,
            tools
            if tools is not None
            else create_tools(database_path, project_id, source),
            project_id,
            source,
            saver,
            interactive_timeout=getattr(configured_model, "request_timeout", 60)
            if interactive
            else None,
        )
        if question is None and not retry:
            snapshot = graph.get_state(config)
            if not snapshot.interrupts:
                raise ValueError("This thread has no pending human approval.")
        result = graph.invoke(invocation, config)
        interruptions = result.get("__interrupt__", [])
        output = summarize(thread_id, result, pending=bool(interruptions))
        if interruptions:
            output["approval_request"] = interruptions[0].value
        return output


def summarize(thread_id: str, values: dict, pending: bool = False) -> dict:
    """Return public run state, excluding raw model reasoning and conversation."""
    return {
        "thread_id": thread_id,
        "status": "awaiting_approval" if pending else values.get("status", "unknown"),
        "project_id": values.get("project_id"),
        "source": values.get("source"),
        "as_of_date": values.get("as_of_date"),
        "answer": values.get("answer", ""),
        "model_calls": values.get("model_calls", 0),
        "tool_calls": values.get("tool_calls", 0),
        "tool_errors": values.get("tool_errors", 0),
        "tool_trace": values.get("trace", []),
        "retryable": values.get("retryable", False),
        "reused_evidence_count": values.get("reused_evidence_count", 0),
        "evidence_retries": values.get("evidence_retries", 0),
        "note": "Answers and reports are drafts. Narrative claims are not yet independently validated or published.",
    }


def saved_messages(checkpoint_path, thread_id):
    """Read the active chat after run_agent has validated its project scope."""
    config = {"configurable": {"thread_id": thread_id}}
    with SqliteSaver.from_conn_string(checkpoint_path) as saver:
        saved = saver.get_tuple(config)
        return (
            saved.checkpoint.get("channel_values", {}).get("messages", [])
            if saved
            else []
        )
