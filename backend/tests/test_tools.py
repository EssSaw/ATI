import json
from backend.app.tools.ast_tool import parse_ast
from backend.app.tools.linter_tool import run_linter
from backend.app.tools.definitions import execute_tool


def test_parse_ast_valid_code():
    code = """
import math
from os import path

class Calculator:
    def add(self, a, b):
        return a + b
"""
    res = parse_ast(code)
    assert res["valid_syntax"] is True
    assert len(res["classes"]) == 1
    assert res["classes"][0]["name"] == "Calculator"
    assert len(res["functions"]) == 1
    assert res["functions"][0]["name"] == "add"
    assert "math" in res["imports"]
    assert "os.path" in res["imports"]
    assert res["node_count"] > 0


def test_parse_ast_syntax_error():
    code = "def broken(:\n    pass"
    res = parse_ast(code)
    assert res["valid_syntax"] is False
    assert res["line"] == 1
    assert "error" in res


def test_run_linter_finds_undefined_and_unused():
    code = "import sys\nprint(undeclared_var)"
    res = run_linter(code)
    assert res["total_issues"] == 2
    messages = [issue["message"] for issue in res["issues"]]
    assert any("imported but unused" in m for m in messages)
    assert any("undefined name 'undeclared_var'" in m for m in messages)


def test_run_linter_clean_code():
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    res = run_linter(code)
    assert res["total_issues"] == 0
    assert len(res["issues"]) == 0


def test_execute_tool_enforces_request_code_not_model_args():
    # Model sends malicious code in arguments, but execute_tool MUST run on request_code!
    request_code = "print('legitimate student code')"
    model_args = json.dumps({"code": "import os; os.system('malicious')"})

    out_str, trace, is_submit, report = execute_tool(
        tool_name="parse_ast",
        raw_arguments=model_args,
        request_code=request_code,
        enable_code_execution=False,
    )
    assert is_submit is False
    assert trace.tool == "parse_ast"
    data = json.loads(out_str)
    assert data["valid_syntax"] is True
    # The AST analyzed is the request_code, which contains no imports!
    assert len(data["imports"]) == 0


def test_execute_tool_submit_report_valid():
    valid_report = {
        "summary": "Found 1 syntax error.",
        "bugs": [
            {
                "type": "syntax",
                "line": 1,
                "message": "Missing colon after function signature.",
                "suggestion": "Add a ':' at the end of line 1.",
            }
        ],
        "explanation": "In Python, compound statements like def require a colon.",
        "fixed_code": "def foo():\n    pass",
    }
    out_str, trace, is_submit, report = execute_tool(
        tool_name="submit_report",
        raw_arguments=json.dumps(valid_report),
        request_code="def foo()\n    pass",
    )
    assert is_submit is True
    assert report is not None
    assert report.summary == "Found 1 syntax error."
    assert len(report.bugs) == 1
    assert report.bugs[0].type == "syntax"
    assert trace.status == "success"


def test_execute_tool_submit_report_invalid_schema():
    invalid_report = {"summary": "incomplete"}
    out_str, trace, is_submit, report = execute_tool(
        tool_name="submit_report",
        raw_arguments=json.dumps(invalid_report),
        request_code="def foo(): pass",
    )
    assert is_submit is False
    assert report is None
    assert trace.status == "error"
    assert "Invalid report format" in out_str
