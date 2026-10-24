import json
import time
from typing import Any, Dict, List, Tuple
from pydantic import ValidationError

from ..schemas import BugItem, Report, TraceItem
from .ast_tool import parse_ast
from .linter_tool import run_linter
from .sandbox_tool import run_code_sandboxed


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "parse_ast",
            "description": "Parse the submitted student Python code to inspect AST structure (functions, classes, imports) and check for syntax errors.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_linter",
            "description": "Run pyflakes static analysis on the submitted student Python code to detect undefined variables, unused imports, and style/flake warnings.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code_sandboxed",
            "description": "Execute the submitted student Python code in a secure restricted sandbox with timeout to observe output and runtime errors (only if execution is enabled).",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_report",
            "description": "Submit the final structured code analysis report for the student.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Concise summary of findings, code quality, and issues found.",
                    },
                    "bugs": {
                        "type": "array",
                        "description": "List of discovered bugs, categorized by type (syntax, style, runtime, logic).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["syntax", "style", "runtime", "logic"],
                                },
                                "line": {
                                    "type": ["integer", "null"],
                                    "description": "1-based line number where the issue occurs, or null if general.",
                                },
                                "message": {
                                    "type": "string",
                                    "description": "Description of the issue.",
                                },
                                "suggestion": {
                                    "type": "string",
                                    "description": "Actionable suggestion to fix the issue.",
                                },
                            },
                            "required": ["type", "message", "suggestion"],
                            "additionalProperties": False,
                        },
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Educational explanation of concepts, why the bugs happened, and how to improve.",
                    },
                    "fixed_code": {
                        "type": "string",
                        "description": "Complete, working, corrected version of the code.",
                    },
                },
                "required": ["summary", "bugs", "explanation", "fixed_code"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_tool(
    tool_name: str,
    raw_arguments: str,
    request_code: str,
    enable_code_execution: bool = False,
) -> Tuple[str, TraceItem, bool, Any]:
    """Execute a tool called by the LLM.

    Security rule:
    Always enforce the student code from the request, NEVER arbitrary code passed
    in tool arguments from the model.

    Returns:
        (tool_output_json, trace_item, is_submit_report, report_object_or_none)
    """
    start_time = time.perf_counter()

    if tool_name == "parse_ast":
        result = parse_ast(request_code)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        status = "success" if result.get("valid_syntax") else "error"
        detail = (
            f"AST parsed: {result.get('node_count', 0)} nodes, "
            f"{len(result.get('functions', []))} functions"
            if result.get("valid_syntax")
            else f"Syntax error at line {result.get('line')}: {result.get('error')}"
        )
        trace = TraceItem(
            tool="parse_ast",
            status=status,
            detail=detail,
            duration_ms=round(elapsed_ms, 2),
        )
        return json.dumps(result), trace, False, None

    elif tool_name == "run_linter":
        result = run_linter(request_code)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        count = result.get("total_issues", 0)
        trace = TraceItem(
            tool="run_linter",
            status="success",
            detail=f"Linter found {count} issue(s)",
            duration_ms=round(elapsed_ms, 2),
        )
        return json.dumps(result), trace, False, None

    elif tool_name == "run_code_sandboxed":
        result = run_code_sandboxed(request_code, enabled=enable_code_execution)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        status_str = result.get("status", "unknown")
        if status_str == "executed":
            status = "success" if result.get("exit_code") == 0 else "error"
            detail = f"Executed (exit code {result.get('exit_code')})"
        elif status_str == "disabled":
            status = "skipped"
            detail = "Execution disabled by configuration"
        elif status_str == "timeout":
            status = "error"
            detail = result.get("message", "Timed out")
        else:
            status = "skipped"
            detail = result.get("message", "Unavailable")

        trace = TraceItem(
            tool="run_code_sandboxed",
            status=status,
            detail=detail,
            duration_ms=round(elapsed_ms, 2),
        )
        return json.dumps(result), trace, False, None

    elif tool_name == "submit_report":
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        try:
            parsed_args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            if not isinstance(parsed_args, dict):
                raise ValueError("Arguments must be a JSON object.")
            report = Report(**parsed_args)
            trace = TraceItem(
                tool="submit_report",
                status="success",
                detail=f"Report submitted with {len(report.bugs)} bug(s)",
                duration_ms=round(elapsed_ms, 2),
            )
            return json.dumps({"status": "accepted"}), trace, True, report
        except (json.JSONDecodeError, ValidationError, ValueError) as err:
            trace = TraceItem(
                tool="submit_report",
                status="error",
                detail=f"Invalid report arguments: {err}",
                duration_ms=round(elapsed_ms, 2),
            )
            return json.dumps({"error": f"Invalid report format: {err}"}), trace, False, None

    else:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace = TraceItem(
            tool=tool_name,
            status="error",
            detail=f"Unknown tool '{tool_name}'",
            duration_ms=round(elapsed_ms, 2),
        )
        return json.dumps({"error": f"Unknown tool '{tool_name}'"}), trace, False, None
