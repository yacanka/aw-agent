"""Jira Server 8.16 REST v2 tools. Credentials never enter tool schemas."""

from __future__ import annotations

import json
import re
import ssl
import time
from http.client import HTTPException
from typing import Any
from urllib import error, parse, request

from jira_fields import RESERVED_FIELDS, validate_fields
from settings import load_settings, redact

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 30


class JiraError(ValueError):
    def __init__(self, code: str, message: str, field_errors: dict | None = None):
        super().__init__(message)
        self.code = code
        self.field_errors = field_errors


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value: str) -> str:
    parsed = parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise JiraError(
            "configuration", "JIRA_BASE_URL must be an HTTPS URL without credentials/query"
        )
    try:
        _ = parsed.port
    except ValueError:
        raise JiraError("configuration", "Invalid Jira port") from None
    path = parse.unquote(parsed.path)
    if any(part in {".", ".."} for part in path.split("/")) or "\\" in path:
        raise JiraError("configuration", "Invalid Jira context path")
    if any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise JiraError("configuration", "Invalid whitespace in Jira URL")
    return value.rstrip("/")


def _page(start_at: int, max_results: int) -> dict[str, int]:
    if type(start_at) is not int or start_at < 0:
        raise JiraError("invalid_arguments", "start_at must be a non-negative integer")
    if type(max_results) is not int or not 1 <= max_results <= 50:
        raise JiraError("invalid_arguments", "max_results must be between 1 and 50")
    return {"startAt": start_at, "maxResults": max_results}


def _issue_key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*", value):
        raise JiraError("invalid_arguments", "Expected a Jira issue key such as PROJECT-123")
    return value.upper()


def _text(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def _issue(item: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    fields = item.get("fields") or {}
    result = {
        "key": item.get("key"),
        "summary": _text(fields.get("summary"), 500),
        "status": (fields.get("status") or {}).get("name"),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
    }
    if detail:
        result.update(
            {
                "description": _text(fields.get("description"), 12000),
                "updated": fields.get("updated"),
                "issue_type": (fields.get("issuetype") or {}).get("name"),
                "issue_type_id": (fields.get("issuetype") or {}).get("id"),
                "is_subtask": (fields.get("issuetype") or {}).get("subtask"),
                "project": {
                    key: (fields.get("project") or {}).get(key) for key in ("id", "key", "name")
                },
                "priority": (fields.get("priority") or {}).get("name"),
                "parent": _issue(fields["parent"]) if fields.get("parent") else None,
                "subtasks": [_issue(child) for child in fields.get("subtasks", [])[:50]],
                "subtasks_truncated": len(fields.get("subtasks", [])) > 50,
            }
        )
    return result


class JiraClient:
    def __init__(
        self, settings_loader=load_settings, opener_factory=request.build_opener, sleeper=time.sleep
    ):
        self.settings_loader = settings_loader
        self.opener_factory = opener_factory
        self.sleeper = sleeper

    def _session(self, *, identity_only: bool = False) -> dict:
        settings = self.settings_loader()
        _base_url(settings.get("JIRA_BASE_URL", ""))
        session = settings.get("JIRA_JSESSIONID", "")
        if not session:
            raise JiraError("session_missing", "Set JIRA_JSESSIONID in .env or the environment")
        if not re.fullmatch(r"[\x21-\x7e]+", session) or any(c in session for c in ';"\\'):
            raise JiraError("configuration", "JIRA_JSESSIONID contains invalid cookie characters")
        identity = self._request("myself", settings=settings)
        if not identity.get("name") and not identity.get("key"):
            raise JiraError("session_expired", "Jira did not confirm an authenticated user")
        return identity if identity_only else settings

    def _get(self, endpoint: str, parameters: dict | None = None, settings: dict | None = None):
        if settings is None:
            if endpoint == "myself":
                return self._session(identity_only=True)
            settings = self._session()
        return self._request(endpoint, parameters, settings=settings)

    def _request(
        self,
        endpoint: str,
        parameters: dict | None = None,
        *,
        settings: dict,
        payload: dict | None = None,
    ):
        base = _base_url(settings.get("JIRA_BASE_URL", ""))
        session = settings["JIRA_JSESSIONID"]
        writing = payload is not None
        try:
            context = ssl.create_default_context(cafile=settings.get("JIRA_CA_FILE") or None)
        except (OSError, ssl.SSLError):
            raise JiraError("tls", "Could not load the configured CA file") from None
        opener = self.opener_factory(NoRedirect(), request.HTTPSHandler(context=context))
        url = base + "/rest/api/2/" + endpoint
        if parameters:
            url += "?" + parse.urlencode(parameters)
        body = (
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if writing else None
        )
        if body and len(body) > MAX_RESPONSE_BYTES:
            raise JiraError("invalid_arguments", "Create payload exceeds 2 MiB")
        req = request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Cookie": "JSESSIONID=" + session,
                **({"Content-Type": "application/json; charset=utf-8"} if writing else {}),
            },
            method="POST" if writing else "GET",
        )
        for attempt in range(1 if writing else 2):
            try:
                with opener.open(req, timeout=TIMEOUT_SECONDS) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise JiraError("response_too_large", "Jira response exceeds 2 MiB")
                    if "application/json" not in response.headers.get("Content-Type", "").lower():
                        raise JiraError(
                            "session_expired", "Jira returned a login page or non-JSON response"
                        )
                data = json.loads(raw)
                expected = list if endpoint == "project" else dict
                if not isinstance(data, expected):
                    raise ValueError("Unexpected response shape")
                return json.loads(json.dumps(data).replace(session, "[REDACTED]"))
            except error.HTTPError as exc:
                status = exc.code
                try:
                    if writing and status == 400:
                        self._field_error(exc, session)
                finally:
                    exc.close()
                if writing and status >= 500:
                    self._unknown_outcome()
                if not writing and status in {429, 502, 503, 504} and attempt == 0:
                    self.sleeper(1)
                    continue
                self._http_error(status)
            except (ssl.SSLError, error.URLError, TimeoutError, OSError, HTTPException) as exc:
                if writing:
                    self._unknown_outcome()
                if isinstance(getattr(exc, "reason", exc), ssl.SSLError):
                    raise JiraError(
                        "tls", "TLS verification failed; check the corporate CA"
                    ) from None
                if attempt == 0:
                    self.sleeper(1)
                    continue
                raise JiraError("network", "Jira connection failed or timed out") from None
            except (UnicodeError, ValueError) as exc:
                if writing:
                    self._unknown_outcome()
                if isinstance(exc, JiraError):
                    raise
                raise JiraError(
                    "invalid_response", "Jira returned an invalid JSON response"
                ) from None
        raise JiraError("network", "Jira request failed")

    @staticmethod
    def _unknown_outcome():
        raise JiraError(
            "outcome_unknown",
            "Creation may have succeeded. Do not retry automatically; "
            "check Jira with JQL before attempting another create.",
        ) from None

    @staticmethod
    def _field_error(response, session):
        try:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
            data = json.loads(raw)
            errors = data.get("errors") or {}
            if not isinstance(errors, dict):
                raise ValueError
            safe = {str(k)[:100]: str(v)[:1000] for k, v in list(errors.items())[:50]}
            messages = data.get("errorMessages") or []
            if isinstance(messages, list) and messages:
                safe["_request"] = "; ".join(str(message)[:500] for message in messages[:5])
            safe = json.loads(json.dumps(safe).replace(session, "[REDACTED]"))
        except (ValueError, AttributeError, OSError):
            safe = {}
        raise JiraError("validation", "Jira rejected the create fields", safe)

    @staticmethod
    def _http_error(status: int) -> None:
        errors = {
            400: ("invalid_query", "Jira rejected the JQL or request arguments"),
            401: ("session_expired", "Jira session expired; update JIRA_JSESSIONID"),
            403: ("forbidden", "The Jira session does not have permission"),
            404: ("not_found", "Jira issue or endpoint was not found or is not visible"),
            429: ("rate_limited", "Jira rate limit reached; retry later"),
        }
        if 300 <= status < 400:
            raise JiraError(
                "session_redirect",
                "Jira redirected the request; redirects are disabled, check the session",
            )
        code, message = errors.get(status, ("http_error", f"Jira returned HTTP {status}"))
        raise JiraError(code, message)

    def search(self, jql: str, start_at: int = 0, max_results: int = 20) -> dict:
        if not isinstance(jql, str) or not jql.strip() or len(jql) > 4000:
            raise JiraError("invalid_arguments", "jql must contain 1 to 4000 characters")
        params = _page(start_at, max_results)
        params.update(jql=jql, fields="summary,status,assignee")
        data = self._get("search", params)
        items = [_issue(item) for item in data.get("issues", [])[:max_results]]
        return self._pagination(data, "issues", items, start_at)

    def get_issue(self, issue_key: str) -> dict:
        data = self._get(
            "issue/" + _issue_key(issue_key),
            {
                "fields": "summary,description,status,assignee,updated,issuetype,project,parent,subtasks,priority",
            },
        )
        return {"success": True, "issue": _issue(data, detail=True)}

    def get_comments(self, issue_key: str, start_at: int = 0, max_results: int = 20) -> dict:
        data = self._get(
            "issue/" + _issue_key(issue_key) + "/comment", _page(start_at, max_results)
        )
        items = [
            {
                "id": item.get("id"),
                "author": (item.get("author") or {}).get("displayName"),
                "body": _text(item.get("body")),
                "created": item.get("created"),
            }
            for item in data.get("comments", [])[:max_results]
        ]
        return self._pagination(data, "comments", items, start_at)

    def list_projects(self, start_at: int = 0, max_results: int = 20) -> dict:
        _page(start_at, max_results)
        data = self._get("project")
        items = [
            {key: project.get(key) for key in ("id", "key", "name")}
            for project in data[start_at : start_at + max_results]
        ]
        return self._pagination({"total": len(data)}, "projects", items, start_at)

    @staticmethod
    def _project_key(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
            raise JiraError("invalid_arguments", "Expected a Jira project key")
        return value.upper()

    def _metadata(self, project_key: str, issue_type_id: str | None, settings: dict) -> list:
        params = {"projectKeys": self._project_key(project_key)}
        if issue_type_id is not None:
            if not isinstance(issue_type_id, str) or not re.fullmatch(r"[0-9]+", issue_type_id):
                raise JiraError("invalid_arguments", "Expected a numeric issue type ID string")
            params.update(issuetypeIds=issue_type_id, expand="projects.issuetypes.fields")
        data = self._get("issue/createmeta", params, settings=settings)
        for project in data.get("projects", []):
            if project.get("key") == project_key.upper():
                return project.get("issuetypes", [])
        raise JiraError("forbidden", "Project is unavailable or has no create permission")

    def get_create_metadata(
        self,
        project_key: str,
        issue_type_id: str | None = None,
        start_at: int = 0,
        max_results: int = 20,
        field_id: str | None = None,
    ) -> dict:
        """Select a type for fields, then a field_id for its paginated allowed values."""
        _page(start_at, max_results)
        if field_id is not None and issue_type_id is None:
            raise JiraError("invalid_arguments", "field_id requires issue_type_id")
        types = self._metadata(project_key, issue_type_id, self._session())
        if issue_type_id is None:
            items = [{k: t.get(k) for k in ("id", "name", "subtask")} for t in types]
            key = "issue_types"
        else:
            selected = self._select_type(types, issue_type_id)
            fields = selected.get("fields")
            if not isinstance(fields, dict):
                raise JiraError("invalid_response", "Missing create field metadata")
            if field_id is not None:
                if field_id not in fields:
                    raise JiraError("invalid_arguments", "Unknown field ID")
                items = [self._option(item) for item in fields[field_id].get("allowedValues", [])]
                key = "allowed_values"
            else:
                items = []
                for name, field in fields.items():
                    values = field.get("allowedValues") or []
                    items.append(
                        {
                            "id": name,
                            "name": field.get("name", name),
                            "required": bool(field.get("required")),
                            "has_default": bool(field.get("hasDefaultValue")),
                            "schema": field.get("schema"),
                            "allowed_values": [self._option(v) for v in values[:10]],
                            "allowed_values_total": len(values),
                        }
                    )
                key = "fields"
        return self._pagination(
            {"total": len(items)}, key, items[start_at : start_at + max_results], start_at
        )

    @staticmethod
    def _option(item: dict) -> dict:
        # Never expose autoCompleteUrl or follow a server-supplied URL with cookies.
        return {k: _text(item[k], 500) for k in ("id", "name", "value", "displayName") if k in item}

    @staticmethod
    def _select_type(types: list, issue_type_id: str) -> dict:
        for item in types:
            if item.get("id") == issue_type_id:
                return item
        raise JiraError("validation", "Issue type is unavailable for this project")

    def create_issue(
        self,
        project_key: str,
        issue_type_id: str,
        summary: str,
        description: str | None = None,
        fields: dict | None = None,
    ) -> dict:
        return self._create(
            project_key, issue_type_id, summary, description, fields, self._session()
        )

    def create_subtask(
        self,
        parent_key: str,
        issue_type_id: str,
        summary: str,
        description: str | None = None,
        fields: dict | None = None,
    ) -> dict:
        parent_key = _issue_key(parent_key)
        settings = self._session()
        parent = self._get("issue/" + parent_key, {"fields": "project,issuetype"}, settings)
        parent_fields = parent.get("fields") or {}
        if parent_fields.get("issuetype", {}).get("subtask") is not False:
            raise JiraError("validation", "Parent must be a verified non-subtask issue")
        project_key = (parent_fields.get("project") or {}).get("key")
        return self._create(
            project_key, issue_type_id, summary, description, fields, settings, parent_key
        )

    def _create(
        self, project_key, issue_type_id, summary, description, fields, settings, parent_key=None
    ) -> dict:
        project_key = self._project_key(project_key)
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 255:
            raise JiraError("invalid_arguments", "Summary must contain 1 to 255 characters")
        if description is not None and not isinstance(description, str):
            raise JiraError("invalid_arguments", "Description must be a string")
        fields = {} if fields is None else fields
        if not isinstance(fields, dict) or RESERVED_FIELDS.intersection(fields):
            raise JiraError(
                "invalid_arguments", "Extra fields must be an object without reserved fields"
            )
        selected = self._select_type(
            self._metadata(project_key, issue_type_id, settings), issue_type_id
        )
        if selected.get("subtask") is not bool(parent_key):
            raise JiraError("validation", "Issue type does not match normal issue/subtask creation")
        metadata = selected.get("fields")
        if not isinstance(metadata, dict) or not metadata:
            raise JiraError("invalid_response", "Missing create field metadata")
        payload = {
            **fields,
            "project": {"key": project_key},
            "issuetype": {"id": issue_type_id},
            "summary": summary,
        }
        if description is not None:
            payload["description"] = description
        if parent_key:
            payload["parent"] = {"key": parent_key}
        errors = validate_fields(payload, metadata)
        if errors:
            raise JiraError("validation", "Correct the create fields before retrying", errors)
        data = self._request("issue", settings=settings, payload={"fields": payload})
        try:
            key = _issue_key(data.get("key"))
        except JiraError:
            self._unknown_outcome()
        return {
            "success": True,
            "key": key,
            "id": data.get("id"),
            "url": _base_url(settings["JIRA_BASE_URL"]) + "/browse/" + key,
        }

    @staticmethod
    def _pagination(data: dict, key: str, items: list, start: int) -> dict:
        total = data.get("total", start + len(items))
        more = start + len(items) < total
        return {
            "success": True,
            key: items,
            "start_at": start,
            "total": total,
            "has_more": more,
            "next_start_at": start + len(items) if more and items else None,
        }


JIRA_TOOL_DEFINITIONS = []
for name, description, properties, required in (
    (
        "jira_search",
        "Read Jira issues using JQL; convert natural language to JQL.",
        {"jql": {"type": "string"}},
        ["jql"],
    ),
    (
        "jira_get_issue",
        "Read Jira issue details.",
        {"issue_key": {"type": "string"}},
        ["issue_key"],
    ),
    (
        "jira_get_comments",
        "Read paginated Jira issue comments.",
        {"issue_key": {"type": "string"}},
        ["issue_key"],
    ),
):
    if name != "jira_get_issue":
        properties.update(
            {
                "start_at": {"type": "integer", "minimum": 0},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
            }
        )
    JIRA_TOOL_DEFINITIONS.append(
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }
    )


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_PAGE_PROPERTIES = {
    "start_at": {"type": "integer", "minimum": 0},
    "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
}
JIRA_TOOL_DEFINITIONS.extend(
    [
        _tool(
            "jira_list_projects",
            "List accessible Jira projects, with pagination.",
            dict(_PAGE_PROPERTIES),
            [],
        ),
        _tool(
            "jira_get_create_metadata",
            "Discover project issue types; select issue_type_id for fields and requirements. "
            "Select field_id to page through allowed values. Use IDs, never guess required values.",
            {
                "project_key": {"type": "string"},
                "issue_type_id": {"type": "string"},
                "field_id": {"type": "string"},
                **_PAGE_PROPERTIES,
            },
            ["project_key"],
        ),
    ]
)
for _name, _parent in (("jira_create_issue", "project_key"), ("jira_create_subtask", "parent_key")):
    JIRA_TOOL_DEFINITIONS.append(
        _tool(
            _name,
            "Create a Jira issue only on an explicit user request. Discover metadata first. "
            "For subtasks provide a normal parent issue key. Extra fields use Jira Server JSON "
            "(options by id, users by name). Never automatically retry an unknown outcome.",
            {
                _parent: {"type": "string"},
                "issue_type_id": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "fields": {"type": "object", "additionalProperties": True},
            },
            [_parent, "issue_type_id", "summary"],
        )
    )

JIRA_TOOL_NAMES = frozenset(item["function"]["name"] for item in JIRA_TOOL_DEFINITIONS)
JIRA_CREATE_TOOLS = frozenset({"jira_create_issue", "jira_create_subtask"})


def execute_jira(name: str, arguments: dict, client: JiraClient | None = None) -> dict:
    client = client or JiraClient()
    handlers = {
        "jira_search": client.search,
        "jira_get_issue": client.get_issue,
        "jira_get_comments": client.get_comments,
        "jira_list_projects": client.list_projects,
        "jira_get_create_metadata": client.get_create_metadata,
        "jira_create_issue": client.create_issue,
        "jira_create_subtask": client.create_subtask,
    }
    try:
        return handlers[name](**arguments)
    except JiraError as exc:
        result = {"success": False, "error_code": exc.code, "error": redact(str(exc))}
        if exc.field_errors is not None:
            result["field_errors"] = json.loads(redact(json.dumps(exc.field_errors)))
        return result
    except (TypeError, ValueError, KeyError, AttributeError, OSError):
        return {
            "success": False,
            "error_code": "invalid_response_or_arguments",
            "error": "Check Jira arguments, configuration, and API response format",
        }
