import time
from backend.app.schemas import BugItem, Report
from backend.app.sessions import SessionStore


def make_dummy_report():
    return Report(
        summary="Test summary",
        bugs=[
            BugItem(
                type="syntax",
                line=1,
                message="syntax err",
                suggestion="fix it",
            )
        ],
        explanation="Test explanation",
        fixed_code="print('fixed')",
    )


def test_session_create_and_get():
    store = SessionStore(ttl_seconds=100, max_sessions=10)
    sid = store.create_session("print(1)", "why?", make_dummy_report())
    assert sid is not None

    session = store.get_session(sid)
    assert session is not None
    assert session.code == "print(1)"
    assert session.question == "why?"
    assert session.report.summary == "Test summary"


def test_session_ttl_expiration():
    store = SessionStore(ttl_seconds=1, max_sessions=10)
    sid = store.create_session("x = 1", "", make_dummy_report())

    # Simulate time passing
    session = store.get_session(sid)
    assert session is not None

    session.last_accessed_at = time.time() - 10
    expired = store.get_session(sid)
    assert expired is None


def test_session_max_limit_eviction():
    store = SessionStore(ttl_seconds=1000, max_sessions=2)
    s1 = store.create_session("code 1", "", make_dummy_report())
    time.sleep(0.01)
    s2 = store.create_session("code 2", "", make_dummy_report())
    time.sleep(0.01)
    s3 = store.create_session("code 3", "", make_dummy_report())

    # s1 should have been evicted
    assert store.get_session(s1) is None
    assert store.get_session(s2) is not None
    assert store.get_session(s3) is not None


def test_session_add_message():
    store = SessionStore(ttl_seconds=100, max_sessions=10)
    sid = store.create_session("code", "", make_dummy_report())

    ok = store.add_message(sid, "user", "What does this mean?")
    assert ok is True
    ok2 = store.add_message(sid, "assistant", "It means X.")
    assert ok2 is True

    session = store.get_session(sid)
    assert len(session.messages) == 2
    assert session.messages[0]["role"] == "user"
    assert session.messages[1]["role"] == "assistant"
