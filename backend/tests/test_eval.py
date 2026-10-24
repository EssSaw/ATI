import pytest
from backend.eval.fixtures import EVAL_FIXTURES
from backend.eval.run_eval import evaluate_fixture, run_all_evaluations


def test_eval_fixture_count():
    assert len(EVAL_FIXTURES) == 12, "Must contain exactly 12 evaluation fixtures"


@pytest.mark.parametrize("fixture", EVAL_FIXTURES, ids=[f.id for f in EVAL_FIXTURES])
def test_each_eval_fixture(fixture):
    result = evaluate_fixture(fixture)
    assert result["passed"] is True or result["skipped"] is True, f"Fixture {fixture.id} failed: {result['details']}"


def test_eval_suite_summary_100_percent():
    summary = run_all_evaluations()
    assert summary["total_fixtures"] == 12
    assert summary["passed_fixtures"] == 6
    assert summary["skipped_fixtures"] == 6
    assert summary["failed_fixtures"] == 0
    assert summary["score_percent"] == 100.0
