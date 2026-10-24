import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.sessions import session_store


@pytest.fixture(autouse=True)
def clean_sessions():
    """Ensure in-memory sessions are cleared before each test."""
    session_store.clear()
    yield
    session_store.clear()


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    return TestClient(app)
