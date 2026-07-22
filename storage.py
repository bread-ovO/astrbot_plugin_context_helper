from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredMessage:
    sender_name: str
    sender_id: str
    content: str
    created_at_ms: int


class MessageStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    origin TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    message_id TEXT,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_group_messages_origin_time
                    ON group_messages(origin, created_at_ms);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_group_messages_deduplicate
                    ON group_messages(origin, message_id)
                    WHERE message_id IS NOT NULL AND message_id != '';
                """
            )

    def add_message(
        self,
        *,
        origin: str,
        platform: str,
        group_id: str,
        message_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        created_at_ms: int,
    ) -> bool:
        return bool(
            self.add_messages(
                [
                    (
                        origin,
                        platform,
                        group_id,
                        message_id,
                        sender_id,
                        sender_name,
                        content,
                        created_at_ms,
                    )
                ]
            )
        )

    def add_messages(self, rows: list[tuple]) -> int:
        with self._connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO group_messages
                    (origin, platform, group_id, message_id, sender_id,
                     sender_name, content, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return connection.total_changes - before

    def query(self, origin: str, start_ms: int, end_ms: int, limit: int) -> list[StoredMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sender_name, sender_id, content, created_at_ms
                FROM (
                    SELECT id, sender_name, sender_id, content, created_at_ms
                    FROM group_messages
                    WHERE origin = ? AND created_at_ms >= ? AND created_at_ms < ?
                    ORDER BY created_at_ms DESC, id DESC
                    LIMIT ?
                )
                ORDER BY created_at_ms ASC, id ASC
                """,
                (origin, start_ms, end_ms, limit),
            ).fetchall()
        return [StoredMessage(*row) for row in rows]

    def purge_before(self, cutoff_ms: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM group_messages WHERE created_at_ms < ?", (cutoff_ms,)
            )
            return cursor.rowcount
