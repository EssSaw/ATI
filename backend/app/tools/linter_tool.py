import sys
from typing import Any, Dict, List
import pyflakes.api
from pyflakes import reporter


class _MemoryReporter(reporter.Reporter):
    """Custom pyflakes reporter collecting warnings/errors in memory without disk I/O."""

    def __init__(self) -> None:
        # Pass dummy streams to parent to satisfy reporter interface
        super().__init__(sys.stdout, sys.stderr)
        self.issues: List[Dict[str, Any]] = []

    def unexpectedError(self, filename: str, msg: str) -> None:
        self.issues.append({
            "type": "error",
            "line": None,
            "column": None,
            "message": str(msg),
        })

    def syntaxError(
        self, filename: str, msg: str, lineno: int, offset: int, text: str
    ) -> None:
        self.issues.append({
            "type": "syntax",
            "line": lineno,
            "column": offset,
            "message": str(msg),
            "text": (text or "").strip(),
        })

    def flake(self, message: Any) -> None:
        msg_str = (
            message.message % message.message_args
            if hasattr(message, "message_args") and message.message_args
            else str(message.message)
        )
        self.issues.append({
            "type": "style",
            "line": getattr(message, "lineno", None),
            "column": getattr(message, "col", None),
            "message": msg_str,
        })


def run_linter(code: str) -> Dict[str, Any]:
    """Run pyflakes static analysis on the Python code in memory."""
    if not isinstance(code, str):
        return {"issues": [{"type": "error", "line": None, "column": None, "message": "Code must be a string."}], "total_issues": 1}

    rep = _MemoryReporter()
    try:
        pyflakes.api.check(code, "student_code.py", rep)
    except Exception as err:
        return {
            "issues": [{
                "type": "error",
                "line": None,
                "column": None,
                "message": f"Linter failure: {err}",
            }],
            "total_issues": 1,
        }

    return {
        "issues": rep.issues,
        "total_issues": len(rep.issues),
    }
