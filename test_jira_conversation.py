import io
import json
import unittest
from unittest import mock

from agent import OfflineGemmaAgent, _validated_calls
from terminal_ui import TerminalUI
from test_agent_loop import FakeModel, call, reply


class ConversationTests(unittest.TestCase):
    def agent(self, responses, executor=None):
        self.model = FakeModel(responses)
        self.executor = executor or mock.Mock(return_value={"success": True, "key": "APP-12"})
        return OfflineGemmaAgent(self.model, self.executor, terminal=TerminalUI(io.StringIO()))

    def test_chat_remembers_and_reset_forgets(self):
        agent = self.agent([reply("Which title?"), reply("Created."), reply("New conversation.")])
        agent.chat("Open a task in APP")
        agent.chat("Title: Offline support")
        history = json.dumps(self.model.history[-1])
        self.assertIn("Open a task in APP", history)
        self.assertIn("Which title?", history)
        agent.reset_conversation()
        agent.chat("Hello")
        self.assertNotIn("Offline support", json.dumps(self.model.history[-1]))

    def test_run_remains_independent(self):
        agent = self.agent([reply("First"), reply("Second")])
        agent.run("first-request")
        agent.run("second-request")
        self.assertNotIn("first-request", json.dumps(self.model.history[-1]))

    def test_repeated_create_in_same_turn_executes_once(self):
        creation = call(
            "jira_create_issue", {"project_key": "APP", "issue_type_id": "3", "summary": "Task"}
        )
        agent = self.agent([reply(calls=[creation]), reply(calls=[creation]), reply("Done")])
        agent.chat("Create task")
        self.assertEqual(self.executor.call_count, 1)
        tools = [m for m in self.model.history[-1] if m["role"] == "tool"]
        self.assertEqual(len(tools), 2)
        self.assertTrue(json.loads(tools[-1]["content"])["duplicate_prevented"])

    def test_unknown_create_result_is_not_retried(self):
        creation = call(
            "jira_create_issue", {"project_key": "APP", "issue_type_id": "3", "summary": "Task"}
        )
        executor = mock.Mock(return_value={"success": False, "error_code": "outcome_unknown"})
        agent = self.agent([reply(calls=[creation, creation]), reply("Check Jira")], executor)
        agent.chat("Create task")
        self.assertEqual(executor.call_count, 1)

    def test_completed_create_survives_later_model_failure(self):
        creation = call(
            "jira_create_issue", {"project_key": "APP", "issue_type_id": "3", "summary": "Task"}
        )
        agent = self.agent([reply(calls=[creation])])
        with self.assertRaises(StopIteration):
            agent.chat("Create task")
        self.model.responses = iter([reply("APP-12 already exists")])
        agent.chat("What happened?")
        self.assertIn("APP-12", json.dumps(self.model.history[-1]))
        self.assertEqual(self.executor.call_count, 1)

    def test_extra_fields_must_be_object(self):
        with self.assertRaisesRegex(ValueError, "object"):
            _validated_calls(
                {
                    "tool_calls": [
                        call(
                            "jira_create_issue",
                            {
                                "project_key": "APP",
                                "issue_type_id": "3",
                                "summary": "Task",
                                "fields": "not-an-object",
                            },
                        )
                    ]
                },
                "",
            )

    def test_equivalent_create_arguments_execute_once(self):
        for tool, key in (
            ("jira_create_issue", "project_key"),
            ("jira_create_subtask", "parent_key"),
        ):
            first = {
                key: "APP" if key == "project_key" else "APP-1",
                "issue_type_id": "3",
                "summary": "Task",
            }
            second = {**first, key: first[key].lower(), "fields": {}}
            agent = self.agent(
                [reply(calls=[call(tool, first), call(tool, second)]), reply("Done")]
            )
            agent.chat("Create one task")
            self.assertEqual(self.executor.call_count, 1)

    def test_truncated_http_post_through_dispatcher_is_not_repeated(self):
        from http.client import IncompleteRead

        from agent_tools import execute_tool
        from jira_client import JiraClient
        from test_jira import Response
        from test_jira_write import metadata

        response = Response({})
        response.read = mock.Mock(side_effect=IncompleteRead(b"partial"))
        opener = mock.Mock()
        opener.open.side_effect = [Response({"name": "tester"}), Response(metadata()), response]
        client = JiraClient(
            lambda: {
                "JIRA_BASE_URL": "https://jira.example.com",
                "JIRA_JSESSIONID": "synthetic-cookie",
            },
            lambda *args: opener,
        )
        creation = call(
            "jira_create_issue",
            {
                "project_key": "APP",
                "issue_type_id": "3",
                "summary": "Task",
                "fields": {"customfield_100": {"id": "10"}},
            },
        )
        agent = self.agent([reply(calls=[creation, creation]), reply("Check Jira")], execute_tool)
        with mock.patch("jira_client.JiraClient", return_value=client):
            agent.chat("Create one task")
        tools = [json.loads(m["content"]) for m in self.model.history[-1] if m["role"] == "tool"]
        self.assertEqual(tools[0]["error_code"], "outcome_unknown")
        self.assertTrue(tools[1]["duplicate_prevented"])
        self.assertEqual(sum(c.args[0].method == "POST" for c in opener.open.call_args_list), 1)

    def test_missing_field_followup_then_subtask_keeps_created_parent(self):
        create = call(
            "jira_create_issue",
            {
                "project_key": "APP",
                "issue_type_id": "3",
                "summary": "Offline support",
                "fields": {"customfield_100": {"id": "10"}},
            },
        )
        subtask = call(
            "jira_create_subtask",
            {"parent_key": "APP-12", "issue_type_id": "5", "summary": "Tests"},
        )
        executor = mock.Mock(
            side_effect=[
                {"success": True, "fields": [{"id": "customfield_100", "required": True}]},
                {"success": True, "key": "APP-12"},
                {"success": True, "key": "APP-13"},
            ]
        )
        agent = self.agent(
            [
                reply(
                    calls=[
                        call(
                            "jira_get_create_metadata", {"project_key": "APP", "issue_type_id": "3"}
                        )
                    ]
                ),
                reply("Which team?"),
                reply(calls=[create]),
                reply("Created APP-12"),
                reply(calls=[subtask]),
                reply("Created APP-13"),
            ],
            executor,
        )
        agent.chat("Create Offline support in APP")
        agent.chat("Platform")
        agent.chat("Create Tests under it")
        history = json.dumps(self.model.history[-1])
        self.assertIn("Which team?", history)
        self.assertIn("Platform", history)
        self.assertIn("APP-12", history)
        self.assertEqual(executor.call_args.kwargs["arguments"]["parent_key"], "APP-12")

    def test_budget_prunes_whole_previous_turns(self):
        agent = self.agent([reply("old answer"), reply("new answer")])
        agent.chat("old request " + "x" * 5000)
        agent.n_ctx = 3200
        agent.max_tokens = 100
        agent.chat("new request")
        messages = self.model.history[-1]
        self.assertNotIn("old request", json.dumps(messages))
        self.assertEqual(messages[-1]["content"], "new request")
