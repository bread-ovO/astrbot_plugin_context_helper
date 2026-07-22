from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredMessage:
    message_id: str
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
        with closing(self._connect()) as connection, connection:
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

                CREATE TABLE IF NOT EXISTS extraction_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_origin TEXT NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    message_count INTEGER NOT NULL,
                    provider_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_origin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at_ms INTEGER NOT NULL,
                    reviewed_at_ms INTEGER,
                    reviewer_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_hash
                    ON knowledge_entries(group_origin, content_hash);
                CREATE INDEX IF NOT EXISTS idx_knowledge_status
                    ON knowledge_entries(group_origin, status, created_at_ms);
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
        with closing(self._connect()) as connection, connection:
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
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT message_id, sender_name, sender_id, content, created_at_ms
                FROM (
                    SELECT id, message_id, sender_name, sender_id, content, created_at_ms
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

    def create_job(
        self,
        origin: str,
        start_ms: int,
        end_ms: int,
        message_count: int,
        provider_id: str,
        created_at_ms: int,
    ) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO extraction_jobs
                    (group_origin, start_ms, end_ms, message_count,
                     provider_id, status, created_at_ms)
                VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (origin, start_ms, end_ms, message_count, provider_id, created_at_ms),
            )
            return int(cursor.lastrowid)

    def finish_job(self, job_id: int, status: str, error: str | None = None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE extraction_jobs SET status = ?, error = ? WHERE id = ?",
                (status, error, job_id),
            )

    def add_knowledge_entries(self, rows: list[tuple]) -> int:
        with closing(self._connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO knowledge_entries
                    (group_origin, title, content, category, keywords_json,
                     sources_json, confidence, content_hash, status, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                rows,
            )
            return connection.total_changes - before

    def list_knowledge(self, origin: str, status: str, limit: int = 10) -> list[dict]:
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT * FROM knowledge_entries
                WHERE group_origin = ? AND status = ?
                ORDER BY confidence DESC, created_at_ms ASC
                LIMIT ?
                """,
                (origin, status, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_knowledge(self, origin: str, entry_id: int) -> dict | None:
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM knowledge_entries WHERE group_origin = ? AND id = ?",
                (origin, entry_id),
            ).fetchone()
        return dict(row) if row else None

    def review_knowledge(
        self,
        origin: str,
        entry_id: int,
        status: str,
        reviewer_id: str,
        review_note: str,
        reviewed_at_ms: int,
    ) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_entries
                SET status = ?, reviewer_id = ?, review_note = ?, reviewed_at_ms = ?
                WHERE group_origin = ? AND id = ? AND status = 'pending'
                """,
                (status, reviewer_id, review_note, reviewed_at_ms, origin, entry_id),
            )
            return cursor.rowcount == 1

    def purge_before(self, cutoff_ms: int) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM group_messages WHERE created_at_ms < ?", (cutoff_ms,)
            )
            return cursor.rowcount
