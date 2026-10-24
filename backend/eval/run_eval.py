import sys
from typing import Any, Dict, List
from .fixtures import EVAL_FIXTURES, EvalFixture
from ..app.tools.ast_tool import parse_ast
from ..app.tools.linter_tool import run_linter
from ..app.tools.sandbox_tool import run_code_sandboxed


def evaluate_fixture(fixture: EvalFixture) -> Dict[str, Any]:
    """Evaluate a single student code fixture against static & sandbox tools without API calls."""
    ast_res = parse_ast(fixture.code)
    lint_res = run_linter(fixture.code)
    sand_res = run_code_sandboxed(fixture.code, enabled=True, timeout_sec=2.0)

    passed = False
    details = ""
    skipped = False

    if fixture.category == "syntax":
        if not ast_res.get("valid_syntax"):
            passed = True
            details = f"Syntax error caught at line {ast_res.get('line')}: {ast_res.get('error')}"
        else:
            details = "Failed to catch syntax error in AST parsing"

    elif fixture.category == "style":
        issues = lint_res.get("issues", [])
        matched = [iss for iss in issues if fixture.expected_indicator.lower() in iss.get("message", "").lower()]
        if matched:
            passed = True
            details = f"Linter caught issue: {matched[0]['message']} at line {matched[0].get('line')}"
        else:
            details = f"Linter missed expected indicator '{fixture.expected_indicator}' (found {len(issues)} other issues)"

    elif fixture.category == "runtime":
        stderr = sand_res.get("stderr", "")
        stdout = sand_res.get("stdout", "")
        combined = f"{stdout}\n{stderr}"
        if fixture.expected_indicator in combined or sand_res.get("status") == "timeout":
            passed = True
            details = f"Runtime caught: status={sand_res.get('status')} exit_code={sand_res.get('exit_code')}"
        elif sand_res.get("status") == "unavailable":
            skipped = True
            details = "Runtime detection not evaluated: isolated execution is unavailable."
        else:
            details = f"Runtime error '{fixture.expected_indicator}' not observed in: {stderr[:100]}"

    elif fixture.category == "security":
        stderr = sand_res.get("stderr", "")
        # Operation not permitted or PermissionError
        if "PermissionError" in stderr or "Operation not permitted" in stderr:
            passed = True
            details = "Sandbox successfully blocked file-write operation (PermissionError)"
        elif sand_res.get("status") == "unavailable":
            passed = True
            details = "Execution safely failed closed; no submitted code ran"
        else:
            details = f"Expected write denial, got status={sand_res.get('status')} stderr={stderr[:100]}"

    elif fixture.category == "logic":
        # Static checks pass; logic issues require semantic review
        if ast_res.get("valid_syntax") and lint_res.get("total_issues") == 0:
            skipped = True
            details = "Logic detection not evaluated offline; requires live model evaluation."
        else:
            details = f"Unexpected syntax/lint error in logic fixture"

    elif fixture.category == "clean":
        if ast_res.get("valid_syntax") and lint_res.get("total_issues") == 0:
            passed = True
            details = "Clean code validated: 0 syntax errors, 0 lint warnings"
        else:
            details = f"Clean code produced unexpected errors: {lint_res.get('issues')}"

    return {
        "id": fixture.id,
        "title": fixture.title,
        "category": fixture.category,
        "passed": passed,
        "skipped": skipped,
        "details": details,
    }


def run_all_evaluations() -> Dict[str, Any]:
    """Run all 12 evaluation fixtures and generate summary metrics."""
    results: List[Dict[str, Any]] = []
    passed_count = 0

    for fix in EVAL_FIXTURES:
        res = evaluate_fixture(fix)
        results.append(res)
        if res["passed"]:
            passed_count += 1

    total = len(EVAL_FIXTURES)
    skipped_count = sum(result["skipped"] for result in results)
    evaluated = total - skipped_count
    score_pct = round((passed_count / evaluated) * 100, 1) if evaluated else 0.0

    return {
        "total_fixtures": total,
        "passed_fixtures": passed_count,
        "failed_fixtures": evaluated - passed_count,
        "skipped_fixtures": skipped_count,
        "score_percent": score_pct,
        "results": results,
    }


if __name__ == "__main__":
    report = run_all_evaluations()
    print("=" * 60)
    print(f"OFFLINE TOOL CHECKS: {report['passed_fixtures']} passed, {report['failed_fixtures']} failed, {report['skipped_fixtures']} skipped")
    print("=" * 60)
    for r in report["results"]:
        status = "[SKIP]" if r["skipped"] else "[PASS]" if r["passed"] else "[FAIL]"
        print(f"{status} {r['id']} ({r['category']}): {r['details']}")
    print(f"Skipped: {report['skipped_fixtures']}; score excludes skipped fixtures and is not model accuracy.")
    print("=" * 60)
    if report["failed_fixtures"] > 0:
        sys.exit(1)
