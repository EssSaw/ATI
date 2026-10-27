import json
from unittest.mock import patch
import pytest

from backend.app.project_tools import (
    PROJECT_TOOL_DEFINITIONS,
    ProjectChatTraceItem,
    execute_project_tool,
    handle_analyze_python,
    handle_check_conflicts,
    handle_validate_json,
)


# ---------------------------------------------------------------------------
# Definition Schema Checks
# ---------------------------------------------------------------------------


def test_project_tool_definitions_include_diagnostics():
    tool_names = [t["function"]["name"] for t in PROJECT_TOOL_DEFINITIONS]
    assert "analyze_python" in tool_names
    assert "validate_json" in tool_names
    assert "check_conflicts" in tool_names

    # Verify analyze_python schema
    analyze_def = next(t for t in PROJECT_TOOL_DEFINITIONS if t["function"]["name"] == "analyze_python")
    params = analyze_def["function"]["parameters"]
    assert "path" in params["properties"]
    assert params["required"] == ["path"]
    assert params["additionalProperties"] is False

    # Verify validate_json schema
    val_def = next(t for t in PROJECT_TOOL_DEFINITIONS if t["function"]["name"] == "validate_json")
    params_val = val_def["function"]["parameters"]
    assert "path" in params_val["properties"]
    assert params_val["required"] == ["path"]
    assert params_val["additionalProperties"] is False

    # Verify check_conflicts schema
    conf_def = next(t for t in PROJECT_TOOL_DEFINITIONS if t["function"]["name"] == "check_conflicts")
    params_conf = conf_def["function"]["parameters"]
    assert "glob" in params_conf["properties"]
    assert params_conf["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: analyze_python
# ---------------------------------------------------------------------------


def test_analyze_python_valid_code():
    code = """
import os
from math import sqrt

class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b

async def fetch_data(url: str):
    return {"status": "ok"}
"""
    snapshot = {"src/calc.py": code}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/calc.py"})

    assert status == "success"
    assert out["path"] == "src/calc.py"
    assert out["valid_syntax"] is True
    assert "ast" in out
    assert "linter" in out

    ast_data = out["ast"]
    assert len(ast_data["classes"]) == 1
    assert ast_data["classes"][0]["name"] == "Calculator"

    fn_names = [f["name"] for f in ast_data["functions"]]
    assert "add" in fn_names
    assert "fetch_data" in fn_names

    assert "os" in ast_data["imports"]
    assert "math.sqrt" in ast_data["imports"]
    assert ast_data["node_count"] > 0
    assert "valid syntax" in detail.lower()


def test_analyze_python_syntax_error():
    broken_code = "def invalid_syntax(\n    return 42\n"
    snapshot = {"src/bad.py": broken_code}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/bad.py"})

    assert status == "success"
    assert out["valid_syntax"] is False
    assert out["ast"]["valid_syntax"] is False
    assert out["syntax_error"]["line"] == 1
    assert "syntax error" in detail.lower()


def test_analyze_python_pyflakes_issues():
    # Code with unused import and undefined variable
    code = "import sys\nresult = undefined_variable + 1\n"
    snapshot = {"src/lint_err.py": code}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/lint_err.py"})

    assert status == "success"
    assert out["valid_syntax"] is True
    linter_data = out["linter"]
    assert linter_data["total_issues"] >= 2

    messages = [issue["message"] for issue in linter_data["issues"]]
    assert any("imported but unused" in m for m in messages)
    assert any("undefined name" in m for m in messages)


def test_analyze_python_clean_code():
    code = "def multiply(x: int, y: int) -> int:\n    return x * y\n"
    snapshot = {"src/clean.py": code}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/clean.py"})

    assert status == "success"
    assert out["valid_syntax"] is True
    assert out["total_linter_issues"] == 0
    assert len(out["linter"]["issues"]) == 0


def test_analyze_python_empty_file():
    snapshot = {"src/empty.py": ""}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/empty.py"})

    assert status == "success"
    assert out["valid_syntax"] is True
    assert len(out["ast"]["functions"]) == 0
    assert len(out["ast"]["classes"]) == 0


def test_analyze_python_non_existent():
    snapshot = {"src/main.py": "x = 1\n"}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/missing.py"})

    assert status == "error"
    assert "not found" in out["error"].lower()


def test_analyze_python_unsafe_path():
    snapshot = {"src/main.py": "x = 1\n"}
    out, status, detail = handle_analyze_python(snapshot, {"path": "../outside.py"})

    assert status == "error"
    assert "unsafe" in out["error"].lower()


def test_analyze_python_excluded_path():
    snapshot = {".env": "SECRET=abc\n"}
    out, status, detail = handle_analyze_python(snapshot, {"path": ".env"})

    assert status == "error"
    assert "excluded" in out["error"].lower()


def test_analyze_python_invalid_argument_types():
    snapshot = {"src/main.py": "x = 1\n"}

    out, status, detail = handle_analyze_python(snapshot, {})
    assert status == "error"
    assert "missing" in out["error"].lower()

    out, status, detail = handle_analyze_python(snapshot, {"path": 12345})
    assert status == "error"
    assert "invalid" in out["error"].lower() or "string" in out["error"].lower()


def test_analyze_python_file_size_cap():
    big_code = "# Comment\n" * 3500  # ~35,000 characters (> 30k)
    snapshot = {"src/huge.py": big_code}
    out, status, detail = handle_analyze_python(snapshot, {"path": "src/huge.py"})

    assert status == "error"
    assert "30,000" in out["error"]


def test_analyze_python_no_code_execution():
    # Verify that code with side-effects is statically analyzed and NEVER executed
    marker = {"executed": False}

    def evil_hook(*args, **kwargs):
        marker["executed"] = True

    malicious_code = """
import os
os.system('echo pwned')
"""
    snapshot = {"src/malicious.py": malicious_code}
    with patch("os.system", side_effect=evil_hook):
        out, status, detail = handle_analyze_python(snapshot, {"path": "src/malicious.py"})

    assert marker["executed"] is False
    assert status == "success"
    assert out["valid_syntax"] is True


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: validate_json
# ---------------------------------------------------------------------------


def test_validate_json_valid_object():
    content = json.dumps({"name": "ATI", "version": "1.0.0", "settings": {"enabled": True, "count": 42}})
    snapshot = {"config.json": content}
    out, status, detail = handle_validate_json(snapshot, {"path": "config.json"})

    assert status == "success"
    assert out["valid"] is True
    assert out["type"] == "dict"
    assert out["keys_count"] == 3
    assert out["has_duplicate_keys"] is False
    assert len(out["warnings"]) == 0


def test_validate_json_valid_array():
    content = json.dumps([1, 2, 3, {"nested": "item"}, None, True])
    snapshot = {"items.json": content}
    out, status, detail = handle_validate_json(snapshot, {"path": "items.json"})

    assert status == "success"
    assert out["valid"] is True
    assert out["type"] == "list"
    assert out["item_count"] == 6


def test_validate_json_syntax_error_line_column():
    bad_json = '{\n  "name": "test",\n  "broken": \n}'
    snapshot = {"broken.json": bad_json}
    out, status, detail = handle_validate_json(snapshot, {"path": "broken.json"})

    assert status == "success"
    assert out["valid"] is False
    assert "error" in out
    assert out["line"] == 4
    assert isinstance(out["column"], int)
    assert "line 4" in detail.lower()


def test_validate_json_reject_nan():
    nan_json = '{\n  "value": NaN\n}'
    snapshot = {"nan.json": nan_json}
    out, status, detail = handle_validate_json(snapshot, {"path": "nan.json"})

    assert status == "success"
    assert out["valid"] is False
    assert "nan" in out["error"].lower()
    assert out["line"] == 2
    assert out["column"] == 12


def test_validate_json_reject_infinity():
    inf_json = '{\n  "value": Infinity\n}'
    snapshot = {"inf.json": inf_json}
    out, status, detail = handle_validate_json(snapshot, {"path": "inf.json"})

    assert status == "success"
    assert out["valid"] is False
    assert "infinity" in out["error"].lower()
    assert out["line"] == 2


def test_validate_json_reject_negative_infinity():
    neg_inf_json = '{\n  "value": -Infinity\n}'
    snapshot = {"neg_inf.json": neg_inf_json}
    out, status, detail = handle_validate_json(snapshot, {"path": "neg_inf.json"})

    assert status == "success"
    assert out["valid"] is False
    assert "infinity" in out["error"].lower()
    assert out["line"] == 2


def test_validate_json_duplicate_keys_detection():
    dup_json = '{\n  "title": "first",\n  "count": 1,\n  "title": "second"\n}'
    snapshot = {"dup.json": dup_json}
    out, status, detail = handle_validate_json(snapshot, {"path": "dup.json"})

    assert status == "success"
    assert out["valid"] is True
    assert out["has_duplicate_keys"] is True
    assert "title" in out["duplicate_keys"]
    assert any("title" in w for w in out["warnings"])
    assert "duplicate key" in detail.lower()


def test_validate_json_nested_duplicate_keys():
    nested_dup = json.dumps({"outer": {"inner": 1, "inner": 2}})
    # Direct text with duplicated inner key
    nested_dup_text = '{"outer": {"inner": 1, "inner": 2}}'
    snapshot = {"nested_dup.json": nested_dup_text}
    out, status, detail = handle_validate_json(snapshot, {"path": "nested_dup.json"})

    assert status == "success"
    assert out["has_duplicate_keys"] is True
    assert "inner" in out["duplicate_keys"]


def test_validate_json_non_existent():
    snapshot = {"data.json": "{}"}
    out, status, detail = handle_validate_json(snapshot, {"path": "missing.json"})

    assert status == "error"
    assert "not found" in out["error"].lower()


def test_validate_json_unsafe_path():
    snapshot = {"data.json": "{}"}
    out, status, detail = handle_validate_json(snapshot, {"path": "/etc/config.json"})

    assert status == "error"
    assert "unsafe" in out["error"].lower()


def test_validate_json_excluded_path():
    snapshot = {".env": "FOO=bar"}
    out, status, detail = handle_validate_json(snapshot, {"path": ".env"})

    assert status == "error"
    assert "excluded" in out["error"].lower()


def test_validate_json_file_size_cap():
    huge_json = '{"a": "' + ("x" * 30005) + '"}'
    snapshot = {"huge.json": huge_json}
    out, status, detail = handle_validate_json(snapshot, {"path": "huge.json"})

    assert status == "error"
    assert "30,000" in out["error"]


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: check_conflicts
# ---------------------------------------------------------------------------


def test_check_conflicts_clean_snapshot():
    snapshot = {
        "src/main.py": "def hello():\n    return 'clean'\n",
        "README.md": "# No conflicts here\n",
    }
    out, status, detail = handle_check_conflicts(snapshot, {})

    assert status == "success"
    assert out["has_conflicts"] is False
    assert out["count"] == 0
    assert out["matches"] == []
    assert out["files_count"] == 0
    assert "no conflict" in detail.lower()


def test_check_conflicts_finds_anchored_markers():
    conflict_content = """# Header
<<<<<<< HEAD
def feature():
    return 'local'
=======
def feature():
    return 'remote'
>>>>>>> feature/awesome
# Footer
"""
    snapshot = {
        "src/feature.py": conflict_content,
        "src/clean.py": "print('ok')\n",
    }
    out, status, detail = handle_check_conflicts(snapshot, {})

    assert status == "success"
    assert out["has_conflicts"] is True
    assert out["count"] == 3
    assert out["files_count"] == 1
    assert "src/feature.py" in out["files_with_conflicts"]

    lines = [m["line"] for m in out["matches"]]
    assert lines == [2, 5, 8]

    markers = [m["marker"] for m in out["matches"]]
    assert markers == ["<<<<<<<", "=======", ">>>>>>>"]


def test_check_conflicts_diff3_markers():
    diff3_content = """<<<<<<< HEAD
local_val = 1
||||||| base
base_val = 0
=======
remote_val = 2
>>>>>>> branch-a
"""
    snapshot = {"diff3.py": diff3_content}
    out, status, detail = handle_check_conflicts(snapshot, {})

    assert status == "success"
    assert out["count"] == 4
    markers = [m["marker"] for m in out["matches"]]
    assert "|||||||" in markers


def test_check_conflicts_ignores_unanchored_and_sub_strings():
    # Indented markers or lines that resemble markers but are not anchored git merge markers
    non_conflict = """
    <<<<<<< indented marker
    ======= indented separator
    >>>>>>> indented end
<= not a marker
==== not 7 chars
======== 8 equals not git conflict
"""
    snapshot = {"notes.txt": non_conflict}
    out, status, detail = handle_check_conflicts(snapshot, {})

    assert status == "success"
    assert out["has_conflicts"] is False
    assert out["count"] == 0


def test_check_conflicts_with_glob_filter():
    conflict = "<<<<<<< HEAD\n1\n=======\n2\n>>>>>>> branch\n"
    snapshot = {
        "src/app.py": conflict,
        "docs/guide.md": conflict,
    }
    out, status, detail = handle_check_conflicts(snapshot, {"glob": "*.py"})

    assert status == "success"
    assert out["count"] == 3
    assert out["files_count"] == 1
    assert out["files_with_conflicts"] == ["src/app.py"]
    assert out["glob"] == "*.py"


def test_check_conflicts_invalid_glob_arg():
    snapshot = {"src/app.py": "x = 1"}
    out, status, detail = handle_check_conflicts(snapshot, {"glob": 123})

    assert status == "error"
    assert "glob" in out["error"].lower()


def test_check_conflicts_skips_excluded_files():
    # If conflict marker is inside .env, it should NOT be scanned/reported
    snapshot = {
        ".env": "<<<<<<< HEAD\nSECRET=1\n=======\nSECRET=2\n>>>>>>> br\n",
        "clean.py": "x = 1\n",
    }
    out, status, detail = handle_check_conflicts(snapshot, {})

    assert status == "success"
    assert out["has_conflicts"] is False
    assert out["count"] == 0


def test_check_conflicts_max_100_cap():
    # Generate 120 conflict markers across files
    lines = []
    for _ in range(40):
        lines.append("<<<<<<< HEAD")
        lines.append("=======")
        lines.append(">>>>>>> br")
    snapshot = {"large_conflicts.txt": "\n".join(lines)}

    out, status, detail = handle_check_conflicts(snapshot, {})

    assert status == "success"
    assert out["count"] == 100
    assert out["capped"] is True
    assert "capped at 100" in detail


# ---------------------------------------------------------------------------
# Output Bounding Tests
# ---------------------------------------------------------------------------


def test_diagnostics_output_bounded_at_15k():
    # Create Python file with 200 functions to verify output bounds
    fns = "\n".join(f"def func_{i}():\n    pass\n" for i in range(250))
    snapshot = {"big_ast.py": fns}

    out, status, detail = handle_analyze_python(snapshot, {"path": "big_ast.py"})
    assert status == "success"
    serialized = json.dumps(out)
    assert len(serialized) <= 15000


# ---------------------------------------------------------------------------
# execute_project_tool Dispatch Tests
# ---------------------------------------------------------------------------


def test_execute_project_tool_analyze_python_dispatch():
    snapshot = {"main.py": "def foo():\n    return 42\n"}
    out_str, trace, prop = execute_project_tool("analyze_python", json.dumps({"path": "main.py"}), snapshot)

    assert prop is None
    assert trace.tool == "analyze_python"
    assert trace.status == "success"
    assert trace.duration_ms >= 0

    data = json.loads(out_str)
    assert data["valid_syntax"] is True


def test_execute_project_tool_validate_json_dispatch():
    snapshot = {"data.json": '{"key": "value"}'}
    out_str, trace, prop = execute_project_tool("validate_json", json.dumps({"path": "data.json"}), snapshot)

    assert prop is None
    assert trace.tool == "validate_json"
    assert trace.status == "success"

    data = json.loads(out_str)
    assert data["valid"] is True


def test_execute_project_tool_check_conflicts_dispatch():
    snapshot = {"file.txt": "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> br\n"}
    out_str, trace, prop = execute_project_tool("check_conflicts", json.dumps({}), snapshot)

    assert prop is None
    assert trace.tool == "check_conflicts"
    assert trace.status == "success"

    data = json.loads(out_str)
    assert data["has_conflicts"] is True
    assert data["count"] == 3


def test_execute_project_tool_malformed_arguments_no_500():
    snapshot = {"main.py": "x = 1"}

    # Invalid JSON syntax
    out_str, trace, prop = execute_project_tool("analyze_python", "{malformed_json", snapshot)
    assert trace.status == "error"
    data = json.loads(out_str)
    assert "error" in data

    # Non-dictionary / string argument
    out_str, trace, prop = execute_project_tool("validate_json", "not a json object", snapshot)
    assert trace.status == "error"
    data = json.loads(out_str)
    assert "error" in data

    # Path not string
    out_str, trace, prop = execute_project_tool("analyze_python", {"path": None}, snapshot)
    assert trace.status == "error"
    data = json.loads(out_str)
    assert "error" in data


def test_output_cap_includes_large_scalar_fields():
    import json
    from backend.app.project_tools import _bound_output
    output = _bound_output({'error': 'x' * 30000}, max_chars=15000)
    assert len(json.dumps(output)) <= 15000
    assert output['truncated'] is True
