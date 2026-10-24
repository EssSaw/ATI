import ast
from typing import Any, Dict, List


def parse_ast(code: str) -> Dict[str, Any]:
    """Parse Python code using the standard ast library.

    Detects syntax errors and extracts structural elements (functions, classes, imports).
    """
    if not isinstance(code, str):
        return {"valid_syntax": False, "error": "Code must be a string."}

    try:
        tree = ast.parse(code)
    except SyntaxError as err:
        return {
            "valid_syntax": False,
            "error": err.msg or "SyntaxError",
            "line": err.lineno,
            "column": err.offset,
            "text": (err.text or "").strip(),
        }
    except Exception as err:
        return {
            "valid_syntax": False,
            "error": str(err),
            "line": None,
            "column": None,
            "text": "",
        }

    functions: List[Dict[str, Any]] = []
    classes: List[Dict[str, Any]] = []
    imports: List[str] = []
    node_count = 0

    for node in ast.walk(tree):
        node_count += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args_count": len(node.args.args),
            })
        elif isinstance(node, ast.ClassDef):
            classes.append({
                "name": node.name,
                "line": node.lineno,
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}" if mod else alias.name)

    return {
        "valid_syntax": True,
        "functions": functions,
        "classes": classes,
        "imports": sorted(list(set(imports))),
        "node_count": node_count,
    }
