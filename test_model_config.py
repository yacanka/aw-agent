import runpy
import unittest
from pathlib import Path
from unittest import mock

import agent


class ModelConfigTests(unittest.TestCase):
    def test_batch_settings_validate_limits(self):
        cases = [
            ({}, (128, 64)),
            ({"GEMMA_N_BATCH": "32", "GEMMA_N_UBATCH": "16"}, (32, 16)),
            ({"GEMMA_N_BATCH": "0"}, None),
            ({"GEMMA_N_UBATCH": "-1"}, None),
            ({"GEMMA_N_BATCH": "invalid"}, None),
            ({"GEMMA_N_BATCH": "32", "GEMMA_N_UBATCH": "64"}, None),
            ({"GEMMA_N_BATCH": "32768"}, None),
        ]
        for values, expected in cases:
            with (
                self.subTest(values=values),
                mock.patch("settings.load_settings", return_value=values),
            ):
                if expected is None:
                    with self.assertRaises(ValueError):
                        runpy.run_path(str(Path(agent.__file__).with_name("config.py")))
                else:
                    config = runpy.run_path(str(Path(agent.__file__).with_name("config.py")))
                    self.assertEqual((config["N_BATCH"], config["N_UBATCH"]), expected)

    def test_model_builder_passes_memory_limits(self):
        backend = mock.Mock(__version__="0.3.35")
        with (
            mock.patch.dict("sys.modules", {"llama_cpp": backend}),
            mock.patch("agent.MODEL_PATH") as path,
            mock.patch("agent.WORKSPACE"),
            mock.patch("agent.N_BATCH", 32),
            mock.patch("agent.N_UBATCH", 16),
        ):
            path.is_file.return_value = True
            agent._build_llm()
        self.assertEqual(backend.Llama.call_args.kwargs["n_batch"], 32)
        self.assertEqual(backend.Llama.call_args.kwargs["n_ubatch"], 16)
