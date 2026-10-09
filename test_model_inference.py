import io
import threading
import unittest
from unittest import mock

from agent import OfflineGemmaAgent
from terminal_ui import TerminalUI
from test_agent_loop import FakeModel, call, reply


def chunk(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class StreamingModel(FakeModel):
    def create_chat_completion(self, **kwargs):
        self.history.append(kwargs["messages"])
        return iter(next(self.responses))


class InferenceTests(unittest.TestCase):
    def test_stream_assembles_tool_arguments_before_execution_and_final_text(self):
        model = StreamingModel(
            [
                [
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "list_files",
                                        "arguments": '{"directory":',
                                    },
                                }
                            ]
                        }
                    ),
                    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"."}'}}]}),
                    chunk({}, "tool_calls"),
                ],
                [chunk({"content": "All "}), chunk({"content": "done."}), chunk({}, "stop")],
            ]
        )
        executed = []
        agent = OfflineGemmaAgent(
            model,
            lambda **kw: executed.append(kw) or {"success": True},
            terminal=TerminalUI(io.StringIO()),
        )
        self.assertEqual(agent.run("Inspect"), "All done.")
        self.assertEqual(executed[0]["arguments"], {"directory": "."})
        self.assertEqual(len(executed), 1)

    def test_stream_without_finish_does_not_execute_partial_tool(self):
        model = StreamingModel(
            [
                [
                    chunk(
                        {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "list_files", "arguments": "{}"}}
                            ]
                        }
                    )
                ],
                [chunk({"content": "Recovered"}), chunk({}, "stop")],
            ]
        )
        executed = []
        agent = OfflineGemmaAgent(
            model, lambda **kw: executed.append(kw), terminal=TerminalUI(io.StringIO())
        )
        self.assertEqual(agent.run("Inspect"), "Recovered")
        self.assertEqual(executed, [])

    def test_timeout_keeps_confirmed_results_and_does_not_retry(self):
        model = FakeModel([reply(calls=[call()])])
        original = model.create_chat_completion
        closed = []

        def slow_stream():
            try:
                with mock.patch("model_inference.time.monotonic", return_value=10**12):
                    yield chunk({"content": "partial"})
            finally:
                closed.append(True)

        def completion(**kwargs):
            if model.history:
                return slow_stream()
            return original(**kwargs)

        model.create_chat_completion = completion
        output = io.StringIO()
        agent = OfflineGemmaAgent(
            model,
            lambda **kw: {"success": True, "files": ["evidence"]},
            terminal=TerminalUI(output),
        )
        with self.assertRaises(TimeoutError):
            agent.chat("Inspect")
        self.assertEqual(closed, [True])
        self.assertIn("evidence", str(agent._conversation))
        self.assertNotIn("[TAMAMLANDI]", output.getvalue())

    def test_wait_log_before_first_chunk_and_monitor_stops_after_completion(self):
        reported = threading.Event()
        output = io.StringIO()

        class ReportingUI(TerminalUI):
            def debug(self, event, details):
                super().debug(event, details)
                if event.startswith("WAIT"):
                    reported.set()

        class WaitingModel(FakeModel):
            def create_chat_completion(self, **kwargs):
                if not reported.wait(7):
                    raise AssertionError("No progress report while model was blocked")
                return iter([chunk({"content": "Done"}), chunk({}, "stop")])

        agent = OfflineGemmaAgent(WaitingModel([]), terminal=ReportingUI(output))
        self.assertEqual(agent.run("Inspect"), "Done")
        self.assertIn('"chunks_received": 0', output.getvalue())
        self.assertFalse(any(t.name == "inference-progress" for t in threading.enumerate()))

    def test_native_tool_markup_and_reasoning_survive_chunk_boundaries(self):
        output = io.StringIO()
        model = StreamingModel(
            [
                [
                    chunk({"content": "<|tool_call>call:list_"}),
                    chunk({"content": "files{}<tool_call|>"}),
                    chunk({}, "stop"),
                ],
                [
                    chunk({"reasoning_content": "Enough "}),
                    chunk({"reasoning_content": "evidence.", "content": "Done"}),
                    chunk({}, "stop"),
                ],
            ]
        )
        executed = []
        agent = OfflineGemmaAgent(
            model,
            lambda **kw: executed.append(kw) or {"success": True},
            terminal=TerminalUI(output),
        )
        self.assertEqual(agent.run("Inspect"), "Done")
        self.assertEqual(len(executed), 1)
        self.assertIn("Enough evidence.", output.getvalue())
