import io
import json
import unittest
from unittest import mock
from urllib import error

from jira_client import JiraClient, execute_jira
from test_jira import Response

FIELDS = {
    "project": {"required": True, "schema": {"type": "project"}},
    "issuetype": {"required": True, "schema": {"type": "issuetype"}},
    "summary": {"required": True, "name": "Summary", "schema": {"type": "string"}},
    "description": {"required": False, "schema": {"type": "string"}},
    "customfield_100": {
        "required": True,
        "name": "Team",
        "schema": {"type": "option"},
        "allowedValues": [{"id": "10", "value": "Platform"}],
    },
}


def metadata(subtask=False, fields=None):
    return {
        "projects": [
            {
                "key": "APP",
                "issuetypes": [
                    {
                        "id": "5" if subtask else "3",
                        "name": "Alt iş" if subtask else "Görev",
                        "subtask": subtask,
                        "fields": FIELDS if fields is None else fields,
                    }
                ],
            }
        ]
    }


class JiraWriteTests(unittest.TestCase):
    def client(self, *responses):
        self.opener = mock.Mock()
        self.opener.open.side_effect = [Response({"name": "tester"}), *responses]
        return JiraClient(
            lambda: {
                "JIRA_BASE_URL": "https://jira.example.com/jira",
                "JIRA_JSESSIONID": "synthetic-cookie",
            },
            lambda *args: self.opener,
            mock.Mock(),
        )

    def test_projects_array_and_context_path(self):
        client = self.client(
            Response(
                [
                    {"id": "1", "key": "APP", "name": "Application"},
                    {"id": "2", "key": "OPS", "name": "Operations"},
                ]
            )
        )
        result = execute_jira("jira_list_projects", {"max_results": 1}, client)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["projects"][0]["key"], "APP")
        self.assertEqual(result["next_start_at"], 1)
        self.assertEqual(
            self.opener.open.call_args.args[0].full_url,
            "https://jira.example.com/jira/rest/api/2/project",
        )

    def test_create_validated_payload_and_url(self):
        client = self.client(Response(metadata()), Response({"id": "12", "key": "APP-12"}))
        result = execute_jira(
            "jira_create_issue",
            {
                "project_key": "APP",
                "issue_type_id": "3",
                "summary": "Türkçe görev",
                "fields": {"customfield_100": {"id": "10"}},
            },
            client,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["url"], "https://jira.example.com/jira/browse/APP-12")
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.method, "POST")
        payload = json.loads(req.data)["fields"]
        self.assertEqual(payload["summary"], "Türkçe görev")
        self.assertEqual(payload["issuetype"], {"id": "3"})
        self.assertEqual(payload["customfield_100"], {"id": "10"})

    def test_missing_required_field_prevents_post(self):
        client = self.client(Response(metadata()))
        result = execute_jira(
            "jira_create_issue",
            {"project_key": "APP", "issue_type_id": "3", "summary": "Task"},
            client,
        )
        self.assertEqual(result["error_code"], "validation")
        self.assertIn("customfield_100", result["field_errors"])
        self.assertTrue(
            all(call.args[0].method == "GET" for call in self.opener.open.call_args_list)
        )

    def test_invalid_option_and_reserved_override_prevent_post(self):
        for fields in ({"customfield_100": {"id": "999"}}, {"project": {"key": "OTHER"}}):
            client = self.client(Response(metadata()))
            result = execute_jira(
                "jira_create_issue",
                {"project_key": "APP", "issue_type_id": "3", "summary": "Task", "fields": fields},
                client,
            )
            self.assertFalse(result["success"])
            self.assertTrue(
                all(call.args[0].method == "GET" for call in self.opener.open.call_args_list)
            )

    def test_subtask_uses_parent_project(self):
        parent = {
            "key": "APP-1",
            "fields": {"project": {"key": "APP"}, "issuetype": {"subtask": False}},
        }
        client = self.client(
            Response(parent), Response(metadata(True)), Response({"id": "13", "key": "APP-13"})
        )
        result = execute_jira(
            "jira_create_subtask",
            {
                "parent_key": "APP-1",
                "issue_type_id": "5",
                "summary": "Test",
                "fields": {"customfield_100": {"id": "10"}},
            },
            client,
        )
        self.assertTrue(result["success"], result)
        payload = json.loads(self.opener.open.call_args.args[0].data)["fields"]
        self.assertEqual(payload["parent"], {"key": "APP-1"})
        self.assertEqual(payload["project"], {"key": "APP"})

    def test_ambiguous_post_never_retried(self):
        for failure in (
            error.URLError("connection lost"),
            error.HTTPError("https://jira.example.com", 503, "", {}, None),
            Response(raw=b"{broken"),
        ):
            client = self.client(Response(metadata()), failure)
            result = execute_jira(
                "jira_create_issue",
                {
                    "project_key": "APP",
                    "issue_type_id": "3",
                    "summary": "Task",
                    "fields": {"customfield_100": {"id": "10"}},
                },
                client,
            )
            self.assertEqual(result["error_code"], "outcome_unknown", result)
            posts = [c for c in self.opener.open.call_args_list if c.args[0].method == "POST"]
            self.assertEqual(len(posts), 1)

    def test_field_errors_redacted(self):
        failure = error.HTTPError(
            "https://jira.example.com",
            400,
            "",
            {},
            io.BytesIO(json.dumps({"errors": {"summary": "Invalid synthetic-cookie"}}).encode()),
        )
        client = self.client(Response(metadata()), failure)
        result = execute_jira(
            "jira_create_issue",
            {
                "project_key": "APP",
                "issue_type_id": "3",
                "summary": "Task",
                "fields": {"customfield_100": {"id": "10"}},
            },
            client,
        )
        self.assertEqual(result["error_code"], "validation", result)
        self.assertNotIn("synthetic-cookie", str(result))
        self.assertIn("summary", result["field_errors"])

    def test_metadata_pagination(self):
        client = self.client(Response(metadata()))
        result = execute_jira(
            "jira_get_create_metadata",
            {"project_key": "APP", "issue_type_id": "3", "max_results": 2},
            client,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["fields"]), 2)
        self.assertTrue(result["has_more"])

    def test_default_does_not_allow_explicit_empty_required_field(self):
        fields = {
            **FIELDS,
            "customfield_100": {**FIELDS["customfield_100"], "hasDefaultValue": True},
        }
        client = self.client(Response(metadata(fields=fields)))
        result = execute_jira(
            "jira_create_issue",
            {
                "project_key": "APP",
                "issue_type_id": "3",
                "summary": "Task",
                "fields": {"customfield_100": None},
            },
            client,
        )
        self.assertEqual(result["error_code"], "validation", result)
        self.assertIn("customfield_100", result["field_errors"])

    def test_server_error_messages_are_reported_without_cookie(self):
        failure = error.HTTPError(
            "https://jira.example.com",
            400,
            "",
            {},
            io.BytesIO(
                json.dumps({"errorMessages": ["Workflow rejected synthetic-cookie"]}).encode()
            ),
        )
        client = self.client(Response(metadata()), failure)
        result = execute_jira(
            "jira_create_issue",
            {
                "project_key": "APP",
                "issue_type_id": "3",
                "summary": "Task",
                "fields": {"customfield_100": {"id": "10"}},
            },
            client,
        )
        self.assertIn("Workflow rejected", str(result))
        self.assertNotIn("synthetic-cookie", str(result))

    def test_subtask_parent_and_type_must_match(self):
        scenarios = [
            [
                Response(
                    {
                        "key": "APP-1",
                        "fields": {"project": {"key": "APP"}, "issuetype": {"subtask": True}},
                    }
                )
            ],
            [
                Response(
                    {
                        "key": "APP-1",
                        "fields": {"project": {"key": "APP"}, "issuetype": {"subtask": False}},
                    }
                ),
                Response(metadata()),
            ],
        ]
        for responses in scenarios:
            client = self.client(*responses)
            result = execute_jira(
                "jira_create_subtask",
                {"parent_key": "APP-1", "issue_type_id": "3", "summary": "Task"},
                client,
            )
            self.assertEqual(result["error_code"], "validation")
            self.assertTrue(all(c.args[0].method == "GET" for c in self.opener.open.call_args_list))

    def test_metadata_types_and_option_pagination(self):
        client = self.client(Response(metadata()))
        result = execute_jira("jira_get_create_metadata", {"project_key": "APP"}, client)
        self.assertEqual(result["issue_types"][0]["name"], "Görev")
        self.assertFalse(result["issue_types"][0]["subtask"])
        client = self.client(Response(metadata()))
        result = execute_jira(
            "jira_get_create_metadata",
            {"project_key": "APP", "issue_type_id": "3", "field_id": "customfield_100"},
            client,
        )
        self.assertEqual(result["allowed_values"], [{"id": "10", "value": "Platform"}])

    def test_issue_relations(self):
        client = self.client(
            Response(
                {
                    "key": "APP-1",
                    "fields": {
                        "project": {"key": "APP"},
                        "issuetype": {"id": "5", "subtask": True},
                        "parent": {"key": "APP-2", "fields": {"summary": "Parent"}},
                        "subtasks": [{"key": "APP-3", "fields": {"summary": "Child"}}],
                        "priority": {"name": "High"},
                    },
                }
            )
        )
        issue = client.get_issue("APP-1")["issue"]
        self.assertEqual(issue["parent"]["key"], "APP-2")
        self.assertEqual(issue["subtasks"][0]["key"], "APP-3")
        self.assertEqual(issue["project"]["key"], "APP")
        self.assertTrue(issue["is_subtask"])

    def test_myself_is_checked_once(self):
        client = self.client()
        self.assertEqual(client._get("myself")["name"], "tester")
        self.assertEqual(self.opener.open.call_count, 1)
