"""Read-only Jira Server/Data Center API. Credentials never enter tool schemas."""

from __future__ import annotations

import json
import re
import ssl
import time
from typing import Any
from urllib import error, parse, request

from settings import load_settings, redact

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 30


class JiraError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
    if parsed.path not in {"", "/"}:
        raise JiraError("configuration", "JIRA_BASE_URL must specify only the Jira origin")
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

    def _get(
        self, endpoint: str, parameters: dict | None = None, settings: dict | None = None
    ) -> dict:
        settings = self.settings_loader() if settings is None else settings
        base = _base_url(settings.get("JIRA_BASE_URL", "https://jira.example.com"))
        session = settings.get("JIRA_JSESSIONID", "")
        if not session:
            raise JiraError("session_missing", "Set JIRA_JSESSIONID in .env or the environment")
        if not re.fullmatch(r"[\x21-\x7e]+", session) or any(c in session for c in ';"\\'):
            raise JiraError("configuration", "JIRA_JSESSIONID contains invalid cookie characters")
        if endpoint != "myself":
            identity = self._get("myself", settings=settings)
            if not identity.get("name") and not identity.get("key"):
                raise JiraError("session_expired", "Jira did not confirm an authenticated user")
        try:
            context = ssl.create_default_context(cafile=settings.get("JIRA_CA_FILE") or None)
        except (OSError, ssl.SSLError):
            raise JiraError("tls", "Could not load the configured CA file") from None
        # No cookie jar, login flow, or redirect following. Proxy handling uses
        # urllib's standard environment/system configuration.
        opener = self.opener_factory(NoRedirect(), request.HTTPSHandler(context=context))
        url = base + "/rest/api/2/" + endpoint
        if parameters:
            url += "?" + parse.urlencode(parameters)
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Cookie": "JSESSIONID=" + session,
            },
            method="GET",
        )
        for attempt in range(2):
            try:
                with opener.open(req, timeout=TIMEOUT_SECONDS) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise JiraError("response_too_large", "Jira response exceeds 2 MiB")
                    if "application/json" not in response.headers.get("Content-Type", "").lower():
                        raise JiraError(
                            "session_expired",
                            "Jira returned a login page or non-JSON response; check the session",
                        )
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                # Drop a credential even if a server error or field reflects it.
                return json.loads(json.dumps(data).replace(session, "[REDACTED]"))
            except error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code in {429, 502, 503, 504} and attempt == 0:
                    self.sleeper(1)
                    continue
                self._http_error(code)
            except (ssl.SSLError, error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, ssl.SSLError):
                    raise JiraError(
                        "tls", "TLS verification failed; check the corporate CA"
                    ) from None
                if attempt == 0:
                    self.sleeper(1)
                    continue
                raise JiraError("network", "Jira connection failed or timed out") from None
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                if isinstance(exc, JiraError):
                    raise
                raise JiraError(
                    "invalid_response", "Jira returned an invalid JSON response"
                ) from None
        raise JiraError("network", "Jira request failed")

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
                "fields": "summary,description,status,assignee,updated,issuetype",
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


def execute_jira(name: str, arguments: dict, client: JiraClient | None = None) -> dict:
    client = client or JiraClient()
    handlers = {
        "jira_search": client.search,
        "jira_get_issue": client.get_issue,
        "jira_get_comments": client.get_comments,
    }
    try:
        return handlers[name](**arguments)
    except JiraError as exc:
        return {"success": False, "error_code": exc.code, "error": redact(str(exc))}
    except (TypeError, ValueError, KeyError, AttributeError, OSError):
        return {
            "success": False,
            "error_code": "invalid_response_or_arguments",
            "error": "Check Jira arguments, configuration, and API response format",
        }
