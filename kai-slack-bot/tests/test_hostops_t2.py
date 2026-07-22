import ast
from pathlib import Path


def test_hostops_gate_response_skips_generic_council_execution():
    source = Path(__file__).parents[1] / "main.py"
    module = ast.parse(source.read_text())
    helper = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_t2_requires_council_execution")
    body = ast.unparse(helper)
    assert 'response_data.get("kind") == "hostops_gate"' in body
    assert 'response_data.get("executed")' in body
