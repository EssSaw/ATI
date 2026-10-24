import time
import uuid
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from .schemas import Report


@dataclass
class SessionData:
    session_id: str
    code: str
    question: str
    report: Report
    created_at: float
    last_accessed_at: float
    messages: List[Dict[str, str]] = field(default_factory=list)


class SessionStore:
    """In-memory, bounded, TTL-based session store for follow-up student chats."""

    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 500):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: Dict[str, SessionData] = {}

    def cleanup(self) -> None:
        """Remove expired sessions and enforce maximum session count."""
        now = time.time()
        expired_ids = [
            sid for sid, s in self._sessions.items()
            if now - s.last_accessed_at > self.ttl_seconds
        ]
        for sid in expired_ids:
            self._sessions.pop(sid, None)

        # Enforce max sessions (evict oldest accessed)
        if len(self._sessions) > self.max_sessions:
            sorted_by_access = sorted(
                self._sessions.items(),
                key=lambda item: item[1].last_accessed_at
            )
            excess = len(self._sessions) - self.max_sessions
            for sid, _ in sorted_by_access[:excess]:
                self._sessions.pop(sid, None)

    def create_session(self, code: str, question: str, report: Report) -> str:
        self.cleanup()
        session_id = str(uuid.uuid4())
        now = time.time()
        self._sessions[session_id] = SessionData(
            session_id=session_id,
            code=code,
            question=question,
            report=report,
            created_at=now,
            last_accessed_at=now,
            messages=[],
        )
        self.cleanup()
        return session_id

    def get_session(self, session_id: str) -> Optional[SessionData]:
        self.cleanup()
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = time.time()
        if now - session.last_accessed_at > self.ttl_seconds:
            self._sessions.pop(session_id, None)
            return None
        session.last_accessed_at = now
        return session

    def add_message(self, session_id: str, role: str, content: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False
        session.messages.append({"role": role, "content": content[:20000]})
        session.messages[:] = session.messages[-20:]
        return True

    def clear(self) -> None:
        """Clear all sessions (useful for testing)."""
        self._sessions.clear()


# Global in-memory singleton
session_store = SessionStore()
