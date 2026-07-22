from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

try:
    from .message_models import StoredMessage
except ImportError:  # 允许独立运行旧数据迁移和测试
    from message_models import StoredMessage

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

                CREATE TABLE IF NOT EXISTS knowledge_topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_origin TEXT NOT NULL,
                    topic_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'collecting',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    first_message_at_ms INTEGER,
                    last_message_at_ms INTEGER,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE (group_origin, topic_key)
                );
                CREATE INDEX IF NOT EXISTS idx_topics_origin_status
                    ON knowledge_topics(group_origin, status, updated_at_ms DESC);

                CREATE TABLE IF NOT EXISTS message_classifications (
                    group_origin TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    topic_id INTEGER,
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL,
                    classified_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (group_origin, message_id),
                    FOREIGN KEY (topic_id) REFERENCES knowledge_topics(id)
                );

                CREATE TABLE IF NOT EXISTS topic_messages (
                    topic_id INTEGER NOT NULL,
                    group_origin TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    added_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (topic_id, message_id),
                    UNIQUE (group_origin, message_id),
                    FOREIGN KEY (topic_id) REFERENCES knowledge_topics(id)
                );
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

    def query_unclassified(
        self, origin: str, start_ms: int, end_ms: int, limit: int
    ) -> list[StoredMessage]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT gm.message_id, gm.sender_name, gm.sender_id,
                       gm.content, gm.created_at_ms
                FROM group_messages gm
                LEFT JOIN message_classifications mc
                  ON mc.group_origin = gm.origin AND mc.message_id = gm.message_id
                WHERE gm.origin = ?
                  AND gm.created_at_ms >= ? AND gm.created_at_ms < ?
                  AND gm.message_id IS NOT NULL AND gm.message_id != ''
                  AND mc.message_id IS NULL
                ORDER BY gm.created_at_ms ASC, gm.id ASC
                LIMIT ?
                """,
                (origin, start_ms, end_ms, limit),
            ).fetchall()
        return [StoredMessage(*row) for row in rows]

    def list_topics(self, origin: str, limit: int = 30) -> list[dict]:
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT * FROM knowledge_topics
                WHERE group_origin = ?
                ORDER BY updated_at_ms DESC
                LIMIT ?
                """,
                (origin, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_classifications(
        self,
        origin: str,
        decisions: list[dict],
        ready_threshold: int,
        classified_at_ms: int,
    ) -> dict:
        kept = discarded = 0
        touched_topics: set[int] = set()
        with closing(self._connect()) as connection, connection:
            for decision in decisions:
                message_id = decision["message_id"]
                if decision["decision"] == "discard":
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO message_classifications
                            (group_origin, message_id, decision, reason,
                             confidence, classified_at_ms)
                        VALUES (?, ?, 'discard', ?, ?, ?)
                        """,
                        (
                            origin,
                            message_id,
                            decision["reason"],
                            decision["confidence"],
                            classified_at_ms,
                        ),
                    )
                    discarded += cursor.rowcount
                    continue

                connection.execute(
                    """
                    INSERT INTO knowledge_topics
                        (group_origin, topic_key, title, status, created_at_ms, updated_at_ms)
                    VALUES (?, ?, ?, 'collecting', ?, ?)
                    ON CONFLICT(group_origin, topic_key) DO UPDATE SET
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        origin,
                        decision["topic_key"],
                        decision["topic_title"],
                        classified_at_ms,
                        classified_at_ms,
                    ),
                )
                topic_id = int(
                    connection.execute(
                        "SELECT id FROM knowledge_topics WHERE group_origin = ? AND topic_key = ?",
                        (origin, decision["topic_key"]),
                    ).fetchone()[0]
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO message_classifications
                        (group_origin, message_id, decision, topic_id, reason,
                         confidence, classified_at_ms)
                    VALUES (?, ?, 'keep', ?, ?, ?, ?)
                    """,
                    (
                        origin,
                        message_id,
                        topic_id,
                        decision["reason"],
                        decision["confidence"],
                        classified_at_ms,
                    ),
                )
                if cursor.rowcount:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO topic_messages
                            (topic_id, group_origin, message_id, added_at_ms)
                        VALUES (?, ?, ?, ?)
                        """,
                        (topic_id, origin, message_id, classified_at_ms),
                    )
                    kept += 1
                    touched_topics.add(topic_id)

            for topic_id in touched_topics:
                stats = connection.execute(
                    """
                    SELECT COUNT(*), MIN(gm.created_at_ms), MAX(gm.created_at_ms)
                    FROM topic_messages tm
                    JOIN group_messages gm
                      ON gm.origin = tm.group_origin AND gm.message_id = tm.message_id
                    WHERE tm.topic_id = ?
                    """,
                    (topic_id,),
                ).fetchone()
                status = "ready" if stats[0] >= ready_threshold else "collecting"
                connection.execute(
                    """
                    UPDATE knowledge_topics
                    SET message_count = ?, first_message_at_ms = ?, last_message_at_ms = ?,
                        status = CASE WHEN status = 'summarized' THEN status ELSE ? END,
                        updated_at_ms = ?
                    WHERE id = ?
                    """,
                    (stats[0], stats[1], stats[2], status, classified_at_ms, topic_id),
                )
        return {"kept": kept, "discarded": discarded, "topics": len(touched_topics)}

    def get_topic(self, origin: str, topic_id: int) -> dict | None:
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM knowledge_topics WHERE group_origin = ? AND id = ?",
                (origin, topic_id),
            ).fetchone()
        return dict(row) if row else None

    def get_topic_messages(self, origin: str, topic_id: int) -> list[StoredMessage]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT gm.message_id, gm.sender_name, gm.sender_id,
                       gm.content, gm.created_at_ms
                FROM topic_messages tm
                JOIN group_messages gm
                  ON gm.origin = tm.group_origin AND gm.message_id = tm.message_id
                WHERE tm.group_origin = ? AND tm.topic_id = ?
                ORDER BY gm.created_at_ms ASC, gm.id ASC
                """,
                (origin, topic_id),
            ).fetchall()
        return [StoredMessage(*row) for row in rows]

    def mark_topic_summarized(self, origin: str, topic_id: int, updated_at_ms: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE knowledge_topics SET status = 'summarized', updated_at_ms = ?
                WHERE group_origin = ? AND id = ?
                """,
                (updated_at_ms, origin, topic_id),
            )

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
