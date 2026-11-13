"""Nebius model configuration and a bounded, read-only tool-calling check."""

import json
import math
import os
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIError, APITimeoutError

from project_status_agent.agent.tools import create_tools
from project_status_agent.config import required
from project_status_agent.data.analysis import parse_date
from project_status_agent.integrations.http import ServiceError


def describe_model_error(error: APIError) -> str:
    """Explain failures without exposing request headers or provider bodies."""
    if isinstance(error, APITimeoutError):
        return "Nebius exceeded the configured request timeout. Check NEBIUS_TIMEOUT_SECONDS (default 120), provider latency, and your network. The request already has one automatic retry; avoid repeated blind retries."
    if isinstance(error, APIConnectionError):
        return "Could not connect to Nebius; no HTTP response was received. Check internet access, VPN/proxy settings, the endpoint, and Python HTTPS certificates."
    status = getattr(error, "status_code", None)
    details = {
        401: "Check the Nebius API key.",
        403: "Check Nebius project permissions and model access.",
        404: "Check NEBIUS_MODEL availability and NEBIUS_BASE_URL.",
        429: "Check Nebius quota, credits, and rate limits.",
    }
    return f"Nebius request failed (HTTP {status or 'unknown'}). " + details.get(
        status, "Check Nebius service availability and model request compatibility."
    )


def create_model(interactive: bool = False) -> ChatOpenAI:
    """Use Nebius's chat-completions endpoint with explicit bounded settings."""
    base_url = required("NEBIUS_BASE_URL").rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.query
    ):
        raise ValueError(
            "NEBIUS_BASE_URL must be an HTTPS API endpoint without credentials or query parameters."
        )
    timeout_key = (
        "NEBIUS_QA_TIMEOUT_SECONDS" if interactive else "NEBIUS_TIMEOUT_SECONDS"
    )
    default_timeout = "60" if interactive else "120"
    try:
        timeout = float(os.environ.get(timeout_key, default_timeout))
    except ValueError:
        raise ValueError(
            f"{timeout_key} must be a number between 15 and 180."
        ) from None
    if not math.isfinite(timeout) or not 15 <= timeout <= 180:
        raise ValueError(f"{timeout_key} must be between 15 and 180.")
    return ChatOpenAI(
        model=required("NEBIUS_MODEL"),
        api_key=required("NEBIUS_API_KEY"),
        base_url=base_url,
        use_responses_api=False,
        timeout=timeout,
        max_retries=0 if interactive else 1,
        max_tokens=4096,
    )


def check_model(
    database_path: str,
    project_id: str,
    source: str,
    as_of_date: str,
    model=None,
) -> dict:
    """Check one natural tool selection, local execution, and a final response.

    Two model turns at most; no live Notion sync, memory publication, or report
    saving. Only the compact sprint metrics are sent back to the model.
    """
    parse_date(as_of_date)
    configured_model = model if model is not None else create_model()
    tool = next(
        tool
        for tool in create_tools(database_path, project_id, source)
        if tool.name == "fetch_sprint_data"
    )
    messages = [
        SystemMessage(
            content=(
                "You are testing a project-status tool connection. Use the provided "
                "fetch_sprint_data tool to answer. Do not invent project facts. "
                "After its result, give a short summary with task count and status counts."
            )
        ),
        HumanMessage(content=f"What is the current sprint status as of {as_of_date}?"),
    ]
    try:
        first = configured_model.bind_tools([tool], tool_choice="auto").invoke(messages)
        if first.invalid_tool_calls or len(first.tool_calls) != 1:
            raise ServiceError(
                "Model did not return exactly one valid tool call. Check model tool support and token limits."
            )
        call = first.tool_calls[0]
        if call["name"] != tool.name or call["args"].get("as_of_date") != as_of_date:
            raise ServiceError(
                "Model selected an unexpected tool or reporting date; no tool was executed."
            )
        if call["args"].get("sprint_id") is not None:
            raise ServiceError(
                "Model invented an explicit sprint ID; test requires automatic sprint selection."
            )
        result = tool.invoke(call["args"])
        if not isinstance(result, dict):
            raise ServiceError(f"Sprint tool could not run: {result}")
        compact_result = {
            "as_of_date": as_of_date,
            "sprint": result["results"]["sprint"],
            "task_count": result["results"]["task_count"],
            "status_counts": result["results"]["status_counts"],
            "coverage": result["coverage"],
            "warnings": result["warnings"],
        }
        final = configured_model.invoke(
            [
                *messages,
                first,
                ToolMessage(
                    content=json.dumps(compact_result), tool_call_id=call["id"]
                ),
            ]
        )
        if final.tool_calls or not final.content:
            raise ServiceError(
                "Model did not finish with an answer after the tool result."
            )
        return {
            "connection": "verified",
            "tool_calling": "verified",
            "model": getattr(configured_model, "model_name", "test-model"),
            "tool": call["name"],
            "arguments": call["args"],
            "tool_result": compact_result,
            "answer": final.content,
            "usage": {
                "tool_selection": first.usage_metadata,
                "answer": final.usage_metadata,
            },
            "note": "A two-turn smoke check, not the full ReAct/LangGraph workflow. Answer wording still requires evaluation.",
        }
    except APIError as error:
        raise ServiceError(describe_model_error(error)) from None
