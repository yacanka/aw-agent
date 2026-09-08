import io
import json
import ssl
import unittest
from unittest import mock
from urllib import error

from jira_client import MAX_RESPONSE_BYTES, JiraClient, NoRedirect, execute_jira


class Response(io.BytesIO):
    def __init__(self, data=None, raw=None, content_type="application/json"):
        super().__init__(raw if raw is not None else json.dumps(data).encode())
        self.headers = {"Content-Type": content_type}


class JiraTests(unittest.TestCase):
    def client(self, *responses):
        self.opener = mock.Mock()
        sequence = []
        for response in responses:
            if isinstance(response, Response):
                sequence.append(Response({"name": "test-user"}))
            sequence.append(response)
        self.opener.open.side_effect = sequence
        self.settings = {
            "JIRA_BASE_URL": "https://jira.example.com",
            "JIRA_JSESSIONID": "test-session",
        }
        self.sleep = mock.Mock()
        return JiraClient(lambda: self.settings, lambda *args: self.opener, self.sleep)

    def test_search_pagination_and_minimum_fields(self):
        client = self.client(
            Response(
                {
                    "total": 3,
                    "issues": [
                        {
                            "key": "APP-1",
                            "fields": {
                                "summary": "First",
                                "status": {"name": "Open"},
                                "assignee": None,
                                "secret_extra": "not-returned",
                            },
                        }
                    ],
                }
            )
        )
        result = client.search("project = APP", max_results=1)
        self.assertEqual(result["next_start_at"], 1)
        self.assertTrue(result["has_more"])
        self.assertNotIn("secret_extra", str(result))
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.get_header("Cookie"), "JSESSIONID=test-session")
        self.assertIn("/rest/api/2/search?", req.full_url)
        self.assertNotIn("test-session", req.full_url)

    def test_issue_and_session_refresh(self):
        client = self.client(
            Response({"key": "APP-1", "fields": {"description": "test-session"}}),
            Response({"key": "APP-1", "fields": {}}),
        )
        result = client.get_issue("app-1")
        self.assertEqual(result["issue"]["description"], "[REDACTED]")
        self.settings["JIRA_JSESSIONID"] = "renewed-session"
        client.get_issue("APP-1")
        self.assertEqual(
            self.opener.open.call_args.args[0].get_header("Cookie"), "JSESSIONID=renewed-session"
        )

    def test_comments(self):
        client = self.client(
            Response(
                {
                    "total": 1,
                    "comments": [
                        {"id": "1", "body": "A comment", "author": {"displayName": "Tester"}}
                    ],
                }
            )
        )
        result = client.get_comments("APP-1")
        self.assertEqual(result["comments"][0]["body"], "A comment")
        self.assertFalse(result["has_more"])

    def test_http_failures_are_distinct_and_do_not_echo_headers(self):
        expected = {
            400: "invalid_query",
            401: "session_expired",
            403: "forbidden",
            404: "not_found",
            302: "session_redirect",
        }
        for status, code in expected.items():
            with self.subTest(status=status):
                client = self.client(
                    error.HTTPError(
                        "https://jira.example.com", status, "sensitive response", {}, None
                    )
                )
                result = execute_jira("jira_get_issue", {"issue_key": "APP-1"}, client)
                self.assertEqual(result["error_code"], code)
                self.assertNotIn("sensitive response", str(result))
                self.assertEqual(self.opener.open.call_count, 1)

    def test_html_size_json_and_tls_failures(self):
        for response, code in (
            (Response(raw=b"<html>Login</html>", content_type="text/html"), "session_expired"),
            (Response(raw=b"x" * (MAX_RESPONSE_BYTES + 1)), "response_too_large"),
            (Response(raw=b"{broken"), "invalid_response"),
            (error.URLError(ssl.SSLCertVerificationError("certificate")), "tls"),
        ):
            with self.subTest(code=code):
                result = execute_jira(
                    "jira_get_issue", {"issue_key": "APP-1"}, self.client(response)
                )
                self.assertEqual(result["error_code"], code)

    def test_retry_is_bounded(self):
        client = self.client(error.URLError("unavailable"), error.URLError("unavailable"))
        result = execute_jira("jira_get_issue", {"issue_key": "APP-1"}, client)
        self.assertEqual(result["error_code"], "network")
        self.assertEqual(self.opener.open.call_count, 2)
        self.sleep.assert_called_once_with(1)

    def test_forbidden_origins_and_arguments_never_connect(self):
        client = self.client()
        for url in (
            "http://jira.example.com",
            "https://user:pass@jira.example.com",
            "https://jira.example.com/path",
            "https://jira.example.com#other",
        ):
            self.settings["JIRA_BASE_URL"] = url
            result = execute_jira("jira_get_issue", {"issue_key": "APP-1"}, client)
            self.assertEqual(result["error_code"], "configuration")
        for arguments in (
            {"issue_key": "../APP-1"},
            {"issue_key": "APP-1", "url": "https://other"},
        ):
            self.assertFalse(execute_jira("jira_get_issue", arguments, client)["success"])
        self.opener.open.assert_not_called()
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other"))

    def test_missing_cookie_and_invalid_page(self):
        client = self.client()
        self.settings["JIRA_JSESSIONID"] = ""
        self.assertEqual(
            execute_jira("jira_get_issue", {"issue_key": "APP-1"}, client)["error_code"],
            "session_missing",
        )
        for size in (0, 51, True, 1.5):
            self.assertEqual(
                execute_jira("jira_search", {"jql": "project=APP", "max_results": size}, client)[
                    "error_code"
                ],
                "invalid_arguments",
            )

    def test_anonymous_identity_is_not_accepted(self):
        client = self.client()
        self.opener.open.side_effect = [Response({})]
        result = execute_jira("jira_get_issue", {"issue_key": "APP-1"}, client)
        self.assertEqual(result["error_code"], "session_expired")
        self.assertEqual(self.opener.open.call_count, 1)
