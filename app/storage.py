from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionState:
    user_id: str
    thread_id: Optional[str]
    active_turn_id: Optional[str]
    status: str
    status_detail: str
    updated_at: str


class GatewayStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    active_turn_id TEXT,
                    status TEXT NOT NULL DEFAULT 'idle',
                    status_detail TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT UNIQUE,
                    user_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_user_created
                    ON messages(user_id, created_at);
                """
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record_message(
        self,
        user_id: str,
        direction: str,
        content: str,
        external_id: Optional[str] = None,
    ) -> bool:
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO messages(external_id, user_id, direction, content, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (external_id, user_id, direction, content, _now()),
                )
                self._connection.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_session(self, user_id: str) -> SessionState:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                timestamp = _now()
                self._connection.execute(
                    "INSERT INTO sessions(user_id, updated_at) VALUES (?, ?)",
                    (user_id, timestamp),
                )
                self._connection.commit()
                return SessionState(user_id, None, None, "idle", "", timestamp)
            return SessionState(**dict(row))

    def set_thread(self, user_id: str, thread_id: Optional[str]) -> None:
        self._update_session(user_id, thread_id=thread_id, active_turn_id=None)

    def set_active_turn(self, user_id: str, turn_id: Optional[str]) -> None:
        self._update_session(user_id, active_turn_id=turn_id)

    def set_status(self, user_id: str, status: str, detail: str = "") -> None:
        self._update_session(user_id, status=status, status_detail=detail)

    def _update_session(self, user_id: str, **values: object) -> None:
        self.get_session(user_id)
        allowed = {"thread_id", "active_turn_id", "status", "status_detail"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported session fields: {sorted(unknown)}")
        assignments = [f"{name} = ?" for name in values]
        parameters = list(values.values())
        assignments.append("updated_at = ?")
        parameters.extend([_now(), user_id])
        with self._lock:
            self._connection.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE user_id = ?",
                parameters,
            )
            self._connection.commit()

