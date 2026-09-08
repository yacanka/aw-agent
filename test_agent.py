"""Explicit real-model acceptance test: python test_agent.py (not unit discovery)."""

import ast
import tempfile
from pathlib import Path

from agent import OfflineGemmaAgent
from agent_tools import execute_tool, run_python


def main() -> None:
    events = []

    def record_tool(**kwargs):
        result = execute_tool(**kwargs)
        events.append((kwargs["name"], result))
        return result

    with tempfile.TemporaryDirectory(prefix="gemma-acceptance-") as temporary:
        workspace = Path(temporary)
        agent = OfflineGemmaAgent(workspace=workspace, tool_executor=record_tool)
        agent.run(
            "Inspect the workspace. Create hello.py with a main() function and "
            'an if __name__ == "__main__" guard. Ask for a name and print '
            '"Merhaba <isim>". Read the file back and run_python with stdin '
            '"Yaşar\\n"; verify "Merhaba Yaşar". Never use PowerShell or Bash.'
        )
        script = workspace / "hello.py"
        assert script.is_file(), "Agent did not create hello.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        assert any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)
        assert any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and any(
                isinstance(item, ast.Constant) and item.value == "__main__"
                for item in node.test.comparators
            )
            for node in tree.body
        )
        names = [name for name, _ in events]
        for required in ("list_files", "write_file", "read_file", "run_python"):
            assert required in names, f"Missing required tool: {required}"
        result = run_python(workspace, "hello.py", stdin="Yaşar\n")
        assert result["success"], "Generated Python script failed"
        assert "Merhaba Yaşar" in result["stdout"], "Unexpected greeting"
    print("Real-model acceptance: OK")


if __name__ == "__main__":
    main()
