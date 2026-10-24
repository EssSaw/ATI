from typing import Any, Dict


def run_code_sandboxed(
    code: str,
    enabled: bool = False,
    timeout_sec: float = 3.0,
    max_output_chars: int = 10000,
) -> Dict[str, Any]:
    """Fail closed until a resource-limited, isolated execution worker exists."""
    return {
        "status": "unavailable" if enabled else "disabled",
        "message": (
            "Runtime execution is unavailable: no isolated worker is configured. "
            "A subprocess or write/network-only sandbox can still expose local secrets. "
            "Do not claim this code was executed."
            if enabled else "Code execution is disabled by default in settings."
        ),
        "stdout": "",
        "stderr": "",
        "exit_code": None,
    }
