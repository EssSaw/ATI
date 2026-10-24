from unittest.mock import patch
from backend.app.tools.sandbox_tool import run_code_sandboxed


def test_sandbox_disabled_by_default():
    assert run_code_sandboxed("print('hello')")["status"] == "disabled"


def test_sandbox_fails_closed_even_when_requested():
    with patch("subprocess.Popen") as process:
        result = run_code_sandboxed("raise RuntimeError('must not execute')", enabled=True)
    process.assert_not_called()
    assert result["status"] == "unavailable"
    assert result["exit_code"] is None
    assert result["stdout"] == ""
