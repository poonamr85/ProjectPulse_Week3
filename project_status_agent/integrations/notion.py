"""Read-only Notion database connector using data-source pagination."""

import re

from project_status_agent.config import required
from project_status_agent.integrations.http import ServiceError, request_json


def notion_id(value):
    # Ignore view IDs in query strings and accept both dashed and compact IDs.
    value = value.split("?")[0].rstrip("/")
    matches = re.findall(
        r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}",
        value,
    )
    if not matches:
        raise ValueError("Expected a Notion database/data-source UUID or database URL.")
    return matches[-1]


def property_value(prop):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        if prop.get("has_more"):
            raise ValueError(
                "Truncated Notion property; shorten it or add property pagination."
            )
        return "".join(
            item.get("plain_text", item.get("text", {}).get("content", ""))
            for item in value or []
        )
    if kind in ("select", "status"):
        return (value or {}).get("name", "")
    if kind == "date":
        return (value or {}).get("start", "")
    if kind in ("url", "email", "phone_number"):
        return value or ""
    raise ValueError(
        f"Unsupported Notion property type: {kind}. Use the documented CSV field types."
    )


class NotionSource:
    def __init__(self, token, version="2025-09-03", transport=request_json):
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": version}
        self.transport = transport

    def read(self, database_id="", data_source_id=""):
        if not data_source_id:
            database = self.transport(
                f"https://api.notion.com/v1/databases/{notion_id(database_id)}",
                self.headers,
            )
            sources = database.get("data_sources", [])
            if len(sources) != 1:
                raise ValueError(
                    "Database must have one data source, or set its explicit DATA_SOURCE_ID in .env."
                )
            data_source_id = sources[0]["id"]
        source_id = notion_id(data_source_id)
        rows, cursor, seen = [], None, set()
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            page = self.transport(
                f"https://api.notion.com/v1/data_sources/{source_id}/query",
                self.headers,
                body,
            )
            if not isinstance(page.get("results"), list):
                raise ServiceError("Notion returned an invalid query response.")
            for record in page["results"]:
                if record.get("archived") or record.get("in_trash"):
                    continue
                row = {
                    name: property_value(prop)
                    for name, prop in record["properties"].items()
                }
                # Notion may contain an unused blank database page after import.
                # Only skip entirely empty rows; partially filled records must validate.
                if not any(value.strip() for value in row.values()):
                    continue
                row["_source_record_id"] = record["id"]
                row["_source_url"] = record.get("url", "")
                rows.append(row)
            if not page.get("has_more"):
                return rows
            cursor = page.get("next_cursor")
            if not cursor or cursor in seen:
                raise ServiceError(
                    "Notion pagination cursor missing or repeated; sync was not saved."
                )
            seen.add(cursor)

    def read_all(self, env):
        result = {}
        for table, label in (
            ("tasks", "TASKS"),
            ("sprints", "SPRINTS"),
            ("history", "HISTORY"),
        ):
            db = env.get(f"NOTION_{label}_DATABASE_ID", "")
            ds = env.get(f"NOTION_{label}_DATA_SOURCE_ID", "")
            if not db and not ds:
                required(f"NOTION_{label}_DATABASE_ID")
            result[table] = self.read(db, ds)
        return result
