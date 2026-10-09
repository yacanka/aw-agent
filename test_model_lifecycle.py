import io
import json
import unittest
from unittest import mock

from agent import OfflineGemmaAgent, main
from terminal_ui import TerminalUI
from test_agent_loop import FakeModel, call, reply


class ResourceModel(FakeModel):
    def __init__(self, responses):
        super().__init__(responses)
        self.closed = False

    def close(self):
        self.closed = True

    def create_chat_completion(self, **kwargs):
        if self.closed:
            raise AssertionError("Closed model reused")
        response = super().create_chat_completion(**kwargs)
        if isinstance(response, BaseException):
            raise response
        return response


class ModelLifecycleTests(unittest.TestCase):
    def test_many_tasks_release_model_before_loading_next_and_keep_conversation(self):
        models = []

        def build():
            self.assertTrue(all(model.closed for model in models))
            model = ResourceModel([reply("Done")])
            models.append(model)
            return model

        with mock.patch("agent._build_llm", side_effect=build):
            agent = OfflineGemmaAgent(terminal=TerminalUI(io.StringIO()))
            for index in range(20):
                self.assertEqual(agent.chat(f"Request {index}"), "Done")
                self.assertTrue(models[-1].closed)
        self.assertEqual(len(models), 20)
        self.assertIn("Request 18", json.dumps(models[-1].history))

    def test_failure_releases_model_without_repeating_confirmed_create(self):
        creation = call(
            "jira_create_issue", {"project_key": "APP", "issue_type_id": "3", "summary": "Task"}
        )
        failed = ResourceModel([reply(calls=[creation]), OSError("device memory")])
        recovered = ResourceModel([reply("APP-12 exists")])
        executed = []

        def execute(**kwargs):
            executed.append(kwargs)
            return {"success": True, "key": "APP-12"}

        with mock.patch("agent._build_llm", side_effect=[failed, recovered]):
            agent = OfflineGemmaAgent(tool_executor=execute, terminal=TerminalUI(io.StringIO()))
            with self.assertRaisesRegex(OSError, "device memory"):
                agent.chat("Create a task")
            self.assertTrue(failed.closed)
            self.assertEqual(agent.chat("What happened?"), "APP-12 exists")
        self.assertEqual(len(executed), 1)
        self.assertIn("APP-12", json.dumps(recovered.history))
        self.assertEqual(agent._current_groups, [])
        self.assertEqual(agent._create_results, {})

    def test_injected_model_stays_caller_owned(self):
        model = ResourceModel([reply("One"), reply("Two")])
        agent = OfflineGemmaAgent(model, terminal=TerminalUI(io.StringIO()))
        agent.chat("First")
        agent.reset_conversation()
        self.assertEqual(agent.chat("Second"), "Two")
        self.assertFalse(model.closed)

    def test_reset_releases_loaded_model(self):
        model = ResourceModel([])
        with mock.patch("agent._build_llm", return_value=model):
            agent = OfflineGemmaAgent(terminal=TerminalUI(io.StringIO()))
            agent.reset_conversation()
            self.assertTrue(model.closed)

    def test_cli_exit_without_task_releases_model(self):
        for ending in ("exit", EOFError(), KeyboardInterrupt()):
            with self.subTest(ending=ending):
                model = ResourceModel([])
                with (
                    mock.patch("agent._build_llm", return_value=model),
                    mock.patch("builtins.input", side_effect=[ending]),
                    mock.patch("sys.stdout", new_callable=io.StringIO),
                ):
                    main()
                self.assertTrue(model.closed)

    def test_cleanup_failure_does_not_hide_inference_error(self):
        model = ResourceModel([OSError("device memory")])
        model.close = mock.Mock(side_effect=RuntimeError("close failed"))
        output = io.StringIO()
        with mock.patch("agent._build_llm", return_value=model):
            agent = OfflineGemmaAgent(terminal=TerminalUI(output))
            with self.assertRaisesRegex(OSError, "device memory"):
                agent.chat("Inspect")
        self.assertIn("model.close", output.getvalue())
        self.assertIn("close failed", output.getvalue())

    def test_reload_failure_can_be_followed_by_another_request(self):
        first = ResourceModel([reply("First")])
        last = ResourceModel([reply("Recovered")])
        with mock.patch("agent._build_llm", side_effect=[first, RuntimeError("load failed"), last]):
            agent = OfflineGemmaAgent(terminal=TerminalUI(io.StringIO()))
            agent.chat("First request")
            with self.assertRaisesRegex(RuntimeError, "load failed"):
                agent.chat("Second request")
            self.assertEqual(agent.chat("Third request"), "Recovered")
        self.assertTrue(last.closed)

    def test_interrupt_releases_model_and_repairs_tool_history(self):
        model = ResourceModel([reply(calls=[call()])])
        executor = mock.Mock(side_effect=KeyboardInterrupt())
        with mock.patch("agent._build_llm", return_value=model):
            agent = OfflineGemmaAgent(tool_executor=executor, terminal=TerminalUI(io.StringIO()))
            with self.assertRaises(KeyboardInterrupt):
                agent.chat("Inspect")
        self.assertTrue(model.closed)
        turn = agent._conversation[-1]
        self.assertEqual(turn[1]["tool_calls"][0]["id"], turn[2]["tool_call_id"])
        self.assertIn("Batch interrupted", turn[2]["content"])

    def test_model_reference_cycle_is_collected_after_turn(self):
        import weakref

        references = []

        def build():
            model = ResourceModel([reply("Done")])
            model.tokenizer = model
            references.append(weakref.ref(model))
            return model

        with mock.patch("agent._build_llm", side_effect=build):
            agent = OfflineGemmaAgent(terminal=TerminalUI(io.StringIO()))
            agent.chat("Inspect")
        self.assertIsNone(references[0]())

    def test_failed_model_cycle_is_collected_before_reload(self):
        import weakref

        references = []

        def build():
            if references:
                self.assertIsNone(references[0]())
            model = ResourceModel([OSError("device memory")] if not references else [reply("Done")])
            model.tokenizer = model
            references.append(weakref.ref(model))
            return model

        with mock.patch("agent._build_llm", side_effect=build):
            agent = OfflineGemmaAgent(terminal=TerminalUI(io.StringIO()))
            with self.assertRaisesRegex(OSError, "device memory"):
                agent.chat("Inspect")
            self.assertEqual(agent.chat("Try another task"), "Done")
