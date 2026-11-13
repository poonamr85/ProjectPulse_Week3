"""Streamlit conversation view over the checkpointed project-status agent."""

from datetime import date

import streamlit as st

from project_status_agent.agent.runner import run_agent, saved_messages
from project_status_agent.data.store import latest_observation_date


def friendly_date(value):
    return date.fromisoformat(value).strftime("%b %d, %Y").replace(" 0", " ")


def failure_notice(result):
    if result.get("retryable"):
        return "This is taking longer than expected. Please try again."
    return "I couldn't finish checking the project records. Please try a more specific question. If the problem continues, contact your project administrator."


def fallback_notice(trace):
    failed = {item.get("tool") for item in trace if item.get("outcome") == "error"}
    succeeded = {item.get("tool") for item in trace if item.get("outcome") == "success"}
    if not failed:
        return None
    if "search_project_memory" in failed and succeeded:
        return "Historical context couldn't be retrieved. This answer uses the available project records only."
    if "sync_project_data" in failed and succeeded:
        return (
            "Project data wasn't refreshed. This answer uses previously saved records."
        )
    return (
        "Some project information couldn't be retrieved. The answer may be incomplete."
    )


def render_qa(database_path, checkpoint_path, project_id, source, default_date, errors):
    st.header("Ask the Agent")
    st.caption(
        "Ask about sprints, risks, and blockers. The agent uses relevant tools to analyze current and historical project data."
    )
    if st.button("New chat"):
        st.query_params.pop("agent_thread", None)
        st.rerun()
    args = (database_path, checkpoint_path, project_id, source)
    selected = st.query_params.get("agent_thread", "")
    result = None
    request = None
    if selected:
        try:
            result = run_agent(*args, thread_id=selected, inspect_only=True)
        except errors:
            st.error("This chat could not be opened. Select New chat to continue.")
            return
        messages = saved_messages(checkpoint_path, selected)
        try:
            latest = latest_observation_date(
                database_path, project_id, source, result["as_of_date"]
            )
        except errors:
            latest = None
        for message in messages:
            if message.type == "human" or (
                message.type == "ai" and not message.tool_calls and message.content
            ):
                with st.chat_message(
                    "user" if message.type == "human" else "assistant"
                ):
                    st.markdown(message.content)
            elif message.type == "tool" and message.name == "ask_human":
                with st.chat_message("user"):
                    st.markdown(message.content)
        st.caption(
            f"Data updated through {friendly_date(latest)}"
            if latest
            else "No dated project records available for this question."
        )
        if result["status"] == "needs_human":
            st.warning(failure_notice(result))
            if result.get("retryable"):
                if st.button("Retry", type="primary"):
                    try:
                        with st.spinner("Checking project records…"):
                            run_agent(
                                *args, thread_id=selected, interactive=True, retry=True
                            )
                        st.rerun()
                    except errors:
                        st.error(
                            "We couldn’t retry just now. Please try again shortly."
                        )
            else:
                st.info("Address the issue above, then submit a clarification below.")
        notice = fallback_notice(result["tool_trace"])
        if notice:
            st.warning(notice)
        request = result.get("approval_request")
        if request:
            with st.chat_message("assistant"):
                st.info(
                    request.get("question")
                    or "Refresh project data from Notion? This updates saved project records without changing tasks in Notion."
                )
                if request.get("action") != "clarify":
                    approve, deny = st.columns(2)
                    approved = approve.button("Refresh data", type="primary")
                    denied = deny.button("Cancel")
                    if approved or denied:
                        try:
                            with st.spinner("Checking project records…"):
                                run_agent(
                                    *args,
                                    thread_id=selected,
                                    decision=approved,
                                    interactive=True,
                                )
                            st.rerun()
                        except errors:
                            st.error(
                                "We couldn’t continue just now. Please try again shortly."
                            )
                    return
    else:
        with st.chat_message("assistant"):
            st.write(
                "Hi! Ask me about your project. For example: **What's been blocked for more than one sprint?**"
            )
        try:
            latest = latest_observation_date(
                database_path, project_id, source, default_date.isoformat()
            )
        except errors:
            latest = None
        st.caption(
            f"Data updated through {friendly_date(latest)}"
            if latest
            else "No dated project records available yet."
        )
    suggested = None
    if not request:
        for column, label in zip(
            st.columns(3),
            [
                "Summarize this sprint",
                "Show current blockers",
                "Highlight project risks",
            ],
            strict=True,
        ):
            if column.button(label, width="stretch"):
                suggested = label
    typed = st.chat_input(
        "Type your clarification…" if request else "Ask a project question…"
    )
    question = typed or suggested
    if question:
        try:
            with st.spinner("Checking project records…"):
                if request:
                    output = run_agent(
                        *args,
                        thread_id=selected,
                        clarification=question,
                        interactive=True,
                    )
                else:
                    output = run_agent(
                        *args,
                        question=question,
                        interactive=True,
                        thread_id=selected or None,
                        as_of_date=None if selected else default_date.isoformat(),
                    )
            st.query_params["agent_thread"] = output["thread_id"]
            st.rerun()
        except errors:
            st.error(
                "We couldn’t answer just now. Please try again, or ask a more specific project question."
            )
