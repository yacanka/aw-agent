import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent import OfflineGemmaAgent
from terminal_ui import TerminalUI


def reply(content="", calls=None, finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": content,
                    "tool_calls": calls,
                },
            }
        ]
    }


def call(name="list_files", arguments=None):
    return {"function": {"name": name, "arguments": arguments or {}}}


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.history = []

    def tokenize(self, value):
        return list(range(len(value) // 4))

    def create_chat_completion(self, **kwargs):
        self.history.append(kwargs["messages"])
        return next(self.responses)


class AgentLoopTests(unittest.TestCase):
    def run_agent(self, responses, executor=None, **kwargs):
        model = FakeModel(responses)
        executor = executor or mock.Mock(return_value={"success": True})
        agent = OfflineGemmaAgent(model, executor, **kwargs)
        with redirect_stdout(io.StringIO()):
            result = agent.run("Inspect the workspace")
        return result, model, executor

    def test_structured_and_native_calls_share_dispatch(self):
        for response in (
            reply(calls=[call()]),
            reply('<|tool_call>call:list_files{directory:<|"|>.<|"|>}<tool_call|>'),
        ):
            result, model, executor = self.run_agent([response, reply("Inspected.")])
            self.assertEqual(result, "Inspected.")
            executor.assert_called_once()
            messages = model.history[1]
            self.assertEqual(messages[-1]["tool_call_id"], messages[-2]["tool_calls"][0]["id"])

    def test_invalid_calls_recover_without_execution(self):
        bad = [
            reply("<|tool_call>call:list_files{broken<tool_call|>"),
            reply(calls=[call("Bash")]),
            reply(calls=[call("read_file")]),
            reply(calls=[call("list_files", {"unexpected": True})]),
            reply(calls=[call("list_files", {"directory": 42})]),
            reply(""),
            reply("partial", finish="length"),
        ]
        for response in bad:
            with self.subTest(response=response):
                result, _, executor = self.run_agent(
                    [response, reply("Could not perform an action.")]
                )
                executor.assert_not_called()
                self.assertTrue(result)

    def test_invalid_limit_and_counter_reset(self):
        with self.assertRaisesRegex(RuntimeError, "consecutive"):
            self.run_agent([reply(""), reply(""), reply("")])
        _, _, executor = self.run_agent(
            [
                reply(""),
                reply(""),
                reply(calls=[call()]),
                reply(""),
                reply("Done"),
            ]
        )
        executor.assert_called_once()

    def test_context_removes_complete_groups(self):
        model = FakeModel([])
        agent = OfflineGemmaAgent(model, n_ctx=3200, max_tokens=50)
        initial = [{"role": "system", "content": "system"}, {"role": "user", "content": "task"}]
        groups = [
            [
                {"role": "assistant", "tool_calls": [{"id": str(index)}]},
                {"role": "tool", "tool_call_id": str(index), "content": "x" * 2000},
            ]
            for index in range(4)
        ]
        messages = agent._messages(initial, groups)
        self.assertLess(len(groups), 4)
        self.assertEqual(messages[0:2], initial)
        for index in range(2, len(messages), 2):
            self.assertEqual(
                messages[index]["tool_calls"][0]["id"], messages[index + 1]["tool_call_id"]
            )

    def test_oversize_initial_request_stops_before_inference(self):
        model = FakeModel([])
        agent = OfflineGemmaAgent(model, n_ctx=600, max_tokens=100)
        with self.assertRaisesRegex(RuntimeError, "Context budget"):
            agent.run("x" * 10000)
        self.assertEqual(model.history, [])

    def test_credential_not_exposed_to_model_or_logs(self):
        with mock.patch.dict("os.environ", {"JIRA_JSESSIONID": "test-session-value"}):
            executor = mock.Mock(return_value={"description": "test-session-value"})
            output = io.StringIO()
            _, model, _ = self.run_agent(
                [reply(calls=[call()]), reply("Done")], executor, terminal=TerminalUI(output)
            )
            self.assertNotIn("test-session-value", json.dumps(model.history))
            self.assertNotIn("test-session-value", output.getvalue())
            self.assertIn("TOOL RESULT", output.getvalue())

    def test_validation_error_identifies_tool_parameter_and_retry(self):
        output = io.StringIO()
        _, _, executor = self.run_agent(
            [reply(calls=[call("list_files", {"directory": 42})]), reply("Recovered")],
            terminal=TerminalUI(output),
        )
        executor.assert_not_called()
        rendered = output.getvalue()
        self.assertIn("step=1 model.validate", rendered)
        self.assertIn("list_files.directory: Expected string, received int", rendered)
        self.assertIn("(1/3)", rendered)

    def test_tool_exception_reports_stage_and_does_not_disappear(self):
        output = io.StringIO()
        executor = mock.Mock(side_effect=OSError("synthetic execution failure"))
        with self.assertRaisesRegex(OSError, "synthetic execution failure"):
            self.run_agent([reply(calls=[call()])], executor, terminal=TerminalUI(output))
        self.assertIn("ERROR step=1 tool.execute list_files call_", output.getvalue())
        self.assertNotIn("[TAMAMLANDI]", output.getvalue())

    def test_inference_exception_reports_stage(self):
        output = io.StringIO()
        model = FakeModel([])
        model.create_chat_completion = mock.Mock(side_effect=RuntimeError("inference failed"))
        agent = OfflineGemmaAgent(model, terminal=TerminalUI(output))
        with self.assertRaisesRegex(RuntimeError, "inference failed"):
            agent.run("Inspect")
        self.assertIn("ERROR step=1 model.inference", output.getvalue())

    def test_terminal_shows_progress_thoughts_tool_summary_and_final_answer(self):
        output = io.StringIO()
        model = FakeModel(
            [
                reply(
                    "<|channel>thought\nÖnce dosyaları kontrol et.<channel|>"
                    '<|tool_call>call:list_files{directory:<|"|>.<|"|>}<tool_call|>'
                ),
                reply("<|channel>thought\nKanıt yeterli.<channel|>Bitti."),
            ]
        )
        executor = mock.Mock(return_value={"directory": ".", "files": [{"name": "a.py"}]})
        agent = OfflineGemmaAgent(
            model,
            executor,
            terminal=TerminalUI(output),
            show_model_thoughts=True,
        )

        result = agent.run("Inspect the workspace", max_steps=4)

        rendered = output.getvalue()
        self.assertEqual(result, "Bitti.")
        self.assertIn("[ADIM 1/4]", rendered)
        self.assertIn("Modelin düşüncesi", rendered)
        self.assertIn("Önce dosyaları kontrol et.", rendered)
        self.assertIn("[ARAÇ 1/1] Dosya listesi", rendered)
        self.assertIn("[OK] 1 öğe bulundu.", rendered)
        self.assertIn("[TAMAMLANDI] 2 adımda", rendered)
        self.assertIn("[YANIT]\nBitti.", rendered)

    def test_terminal_can_hide_model_thoughts(self):
        output = io.StringIO()
        agent = OfflineGemmaAgent(
            FakeModel([reply("<|channel>thought\nGizli not.<channel|>Bitti.")]),
            terminal=TerminalUI(output),
            show_model_thoughts=False,
        )

        agent.run("Inspect the workspace")

        self.assertNotIn("Gizli not", output.getvalue())
