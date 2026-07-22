from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

try:
    from .message_models import StoredMessage
except ImportError:  # 允许独立运行迁移脚本
    from message_models import StoredMessage


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plugin_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS group_messages (
    id BIGSERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    platform TEXT NOT NULL,
    group_id TEXT NOT NULL,
    message_id TEXT,
    sender_id TEXT NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    message_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_event JSONB,
    reply_to TEXT,
    created_at_ms BIGINT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_group_messages_origin_time
    ON group_messages(origin, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_group_messages_sender_time
    ON group_messages(origin, sender_id, created_at_ms DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_group_messages_deduplicate
    ON group_messages(origin, message_id)
    WHERE message_id IS NOT NULL AND message_id != '';

CREATE TABLE IF NOT EXISTS extraction_jobs (
    id BIGSERIAL PRIMARY KEY,
    group_origin TEXT NOT NULL,
    start_ms BIGINT NOT NULL,
    end_ms BIGINT NOT NULL,
    message_count INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at_ms BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id BIGSERIAL PRIMARY KEY,
    group_origin TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    sources_json JSONB NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    review_note TEXT NOT NULL DEFAULT '',
    created_at_ms BIGINT NOT NULL,
    reviewed_at_ms BIGINT,
    reviewer_id TEXT,
    UNIQUE (group_origin, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_status
    ON knowledge_entries(group_origin, status, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_keywords
    ON knowledge_entries USING GIN (keywords_json);

CREATE TABLE IF NOT EXISTS knowledge_topics (
    id BIGSERIAL PRIMARY KEY,
    group_origin TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'collecting'
        CHECK (status IN ('collecting', 'ready', 'summarized')),
    message_count INTEGER NOT NULL DEFAULT 0,
    first_message_at_ms BIGINT,
    last_message_at_ms BIGINT,
    created_at_ms BIGINT NOT NULL,
    updated_at_ms BIGINT NOT NULL,
    UNIQUE (group_origin, topic_key)
);
CREATE INDEX IF NOT EXISTS idx_topics_origin_status
    ON knowledge_topics(group_origin, status, updated_at_ms DESC);

CREATE TABLE IF NOT EXISTS message_classifications (
    group_origin TEXT NOT NULL,
    message_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('keep', 'discard')),
    topic_id BIGINT REFERENCES knowledge_topics(id),
    reason TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    classified_at_ms BIGINT NOT NULL,
    PRIMARY KEY (group_origin, message_id)
);

CREATE TABLE IF NOT EXISTS topic_messages (
    topic_id BIGINT NOT NULL REFERENCES knowledge_topics(id) ON DELETE CASCADE,
    group_origin TEXT NOT NULL,
    message_id TEXT NOT NULL,
    added_at_ms BIGINT NOT NULL,
    PRIMARY KEY (topic_id, message_id),
    UNIQUE (group_origin, message_id)
);

INSERT INTO plugin_schema_migrations(version) VALUES (1)
ON CONFLICT (version) DO NOTHING;
"""


def initialize_schema(connection) -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


class PostgresMessageStore:
    def __init__(
        self,
        database_url: str,
        min_size: int = 2,
        max_size: int = 10,
        timeout: float = 10,
    ):
        self.database_url = database_url
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    def open(self) -> None:
        self.pool.open(wait=True)
        with self.pool.connection() as connection:
            initialize_schema(connection)

    def close(self) -> None:
        self.pool.close()

    def add_message(self, **values: Any) -> bool:
        row = (
            values["origin"], values["platform"], values["group_id"],
            values["message_id"], values["sender_id"], values["sender_name"],
            values["content"], values["created_at_ms"],
        )
        return bool(self.add_messages([row]))

    def add_messages(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        with self.pool.connection() as connection:
            cursor = connection.executemany(
                """
                INSERT INTO group_messages
                    (origin, platform, group_id, message_id, sender_id,
                     sender_name, content, created_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                rows,
            )
            return max(cursor.rowcount, 0)

    def query(self, origin: str, start_ms: int, end_ms: int, limit: int) -> list[StoredMessage]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT message_id, sender_name, sender_id, content, created_at_ms
                FROM (
                    SELECT id, message_id, sender_name, sender_id, content, created_at_ms
                    FROM group_messages
                    WHERE origin = %s AND created_at_ms >= %s AND created_at_ms < %s
                    ORDER BY created_at_ms DESC, id DESC LIMIT %s
                ) recent
                ORDER BY created_at_ms ASC, id ASC
                """,
                (origin, start_ms, end_ms, limit),
            ).fetchall()
        return [StoredMessage(**row) for row in rows]

    def query_unclassified(
        self, origin: str, start_ms: int, end_ms: int, limit: int
    ) -> list[StoredMessage]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT gm.message_id, gm.sender_name, gm.sender_id,
                       gm.content, gm.created_at_ms
                FROM group_messages gm
                LEFT JOIN message_classifications mc
                  ON mc.group_origin = gm.origin AND mc.message_id = gm.message_id
                WHERE gm.origin = %s AND gm.created_at_ms >= %s AND gm.created_at_ms < %s
                  AND gm.message_id IS NOT NULL AND gm.message_id != ''
                  AND mc.message_id IS NULL
                ORDER BY gm.created_at_ms ASC, gm.id ASC LIMIT %s
                """,
                (origin, start_ms, end_ms, limit),
            ).fetchall()
        return [StoredMessage(**row) for row in rows]

    def create_job(
        self, origin: str, start_ms: int, end_ms: int, message_count: int,
        provider_id: str, created_at_ms: int,
    ) -> int:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO extraction_jobs
                    (group_origin, start_ms, end_ms, message_count,
                     provider_id, status, created_at_ms)
                VALUES (%s, %s, %s, %s, %s, 'running', %s) RETURNING id
                """,
                (origin, start_ms, end_ms, message_count, provider_id, created_at_ms),
            ).fetchone()
            return int(row["id"])

    def finish_job(self, job_id: int, status: str, error: str | None = None) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "UPDATE extraction_jobs SET status = %s, error = %s WHERE id = %s",
                (status, error, job_id),
            )

    def add_knowledge_entries(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        with self.pool.connection() as connection:
            cursor = connection.executemany(
                """
                INSERT INTO knowledge_entries
                    (group_origin, title, content, category, keywords_json,
                     sources_json, confidence, content_hash, status, created_at_ms)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, 'pending', %s)
                ON CONFLICT (group_origin, content_hash) DO NOTHING
                """,
                rows,
            )
            return max(cursor.rowcount, 0)

    def list_knowledge(self, origin: str, status: str, limit: int = 10) -> list[dict]:
        with self.pool.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM knowledge_entries
                WHERE group_origin = %s AND status = %s
                ORDER BY confidence DESC, created_at_ms ASC LIMIT %s
                """,
                (origin, status, limit),
            ).fetchall()

    def get_knowledge(self, origin: str, entry_id: int) -> dict | None:
        with self.pool.connection() as connection:
            return connection.execute(
                "SELECT * FROM knowledge_entries WHERE group_origin = %s AND id = %s",
                (origin, entry_id),
            ).fetchone()

    def review_knowledge(
        self, origin: str, entry_id: int, status: str, reviewer_id: str,
        review_note: str, reviewed_at_ms: int,
    ) -> bool:
        with self.pool.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_entries
                SET status = %s, reviewer_id = %s, review_note = %s, reviewed_at_ms = %s
                WHERE group_origin = %s AND id = %s AND status = 'pending'
                """,
                (status, reviewer_id, review_note, reviewed_at_ms, origin, entry_id),
            )
            return cursor.rowcount == 1

    def list_topics(self, origin: str, limit: int = 30) -> list[dict]:
        with self.pool.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM knowledge_topics WHERE group_origin = %s
                ORDER BY updated_at_ms DESC LIMIT %s
                """,
                (origin, limit),
            ).fetchall()

    def save_classifications(
        self, origin: str, decisions: list[dict], ready_threshold: int,
        classified_at_ms: int,
    ) -> dict:
        kept = discarded = 0
        touched_topics: set[int] = set()
        with self.pool.connection() as connection:
            for decision in decisions:
                if decision["decision"] == "discard":
                    cursor = connection.execute(
                        """
                        INSERT INTO message_classifications
                            (group_origin, message_id, decision, reason,
                             confidence, classified_at_ms)
                        VALUES (%s, %s, 'discard', %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (origin, decision["message_id"], decision["reason"],
                         decision["confidence"], classified_at_ms),
                    )
                    discarded += cursor.rowcount
                    continue
                row = connection.execute(
                    """
                    INSERT INTO knowledge_topics
                        (group_origin, topic_key, title, status, created_at_ms, updated_at_ms)
                    VALUES (%s, %s, %s, 'collecting', %s, %s)
                    ON CONFLICT (group_origin, topic_key) DO UPDATE
                        SET updated_at_ms = excluded.updated_at_ms
                    RETURNING id
                    """,
                    (origin, decision["topic_key"], decision["topic_title"],
                     classified_at_ms, classified_at_ms),
                ).fetchone()
                topic_id = int(row["id"])
                cursor = connection.execute(
                    """
                    INSERT INTO message_classifications
                        (group_origin, message_id, decision, topic_id, reason,
                         confidence, classified_at_ms)
                    VALUES (%s, %s, 'keep', %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (origin, decision["message_id"], topic_id, decision["reason"],
                     decision["confidence"], classified_at_ms),
                )
                if cursor.rowcount:
                    connection.execute(
                        """
                        INSERT INTO topic_messages(topic_id, group_origin, message_id, added_at_ms)
                        VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                        """,
                        (topic_id, origin, decision["message_id"], classified_at_ms),
                    )
                    kept += 1
                    touched_topics.add(topic_id)
            for topic_id in touched_topics:
                stats = connection.execute(
                    """
                    SELECT COUNT(*) AS count, MIN(gm.created_at_ms) AS first_ms,
                           MAX(gm.created_at_ms) AS last_ms
                    FROM topic_messages tm JOIN group_messages gm
                      ON gm.origin = tm.group_origin AND gm.message_id = tm.message_id
                    WHERE tm.topic_id = %s
                    """,
                    (topic_id,),
                ).fetchone()
                status = "ready" if stats["count"] >= ready_threshold else "collecting"
                connection.execute(
                    """
                    UPDATE knowledge_topics SET message_count = %s,
                        first_message_at_ms = %s, last_message_at_ms = %s,
                        status = CASE WHEN status = 'summarized' THEN status ELSE %s END,
                        updated_at_ms = %s WHERE id = %s
                    """,
                    (stats["count"], stats["first_ms"], stats["last_ms"],
                     status, classified_at_ms, topic_id),
                )
        return {"kept": kept, "discarded": discarded, "topics": len(touched_topics)}

    def get_topic(self, origin: str, topic_id: int) -> dict | None:
        with self.pool.connection() as connection:
            return connection.execute(
                "SELECT * FROM knowledge_topics WHERE group_origin = %s AND id = %s",
                (origin, topic_id),
            ).fetchone()

    def get_topic_messages(self, origin: str, topic_id: int) -> list[StoredMessage]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT gm.message_id, gm.sender_name, gm.sender_id,
                       gm.content, gm.created_at_ms
                FROM topic_messages tm JOIN group_messages gm
                  ON gm.origin = tm.group_origin AND gm.message_id = tm.message_id
                WHERE tm.group_origin = %s AND tm.topic_id = %s
                ORDER BY gm.created_at_ms ASC, gm.id ASC
                """,
                (origin, topic_id),
            ).fetchall()
        return [StoredMessage(**row) for row in rows]

    def mark_topic_summarized(self, origin: str, topic_id: int, updated_at_ms: int) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """
                UPDATE knowledge_topics SET status = 'summarized', updated_at_ms = %s
                WHERE group_origin = %s AND id = %s
                """,
                (updated_at_ms, origin, topic_id),
            )

    def purge_before(self, cutoff_ms: int) -> int:
        with self.pool.connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM group_messages gm
                WHERE gm.created_at_ms < %s
                  AND NOT EXISTS (
                      SELECT 1 FROM topic_messages tm
                      WHERE tm.group_origin = gm.origin AND tm.message_id = gm.message_id
                  )
                """,
                (cutoff_ms,),
            )
            return cursor.rowcount

    def stats(self, origin: str) -> dict:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM group_messages WHERE origin = %s) AS messages,
                    (SELECT COUNT(*) FROM message_classifications WHERE group_origin = %s) AS classified,
                    (SELECT COUNT(*) FROM knowledge_topics WHERE group_origin = %s) AS topics,
                    (SELECT COUNT(*) FROM knowledge_entries WHERE group_origin = %s) AS knowledge,
                    pg_database_size(current_database()) AS database_bytes
                """,
                (origin, origin, origin, origin),
            ).fetchone()
        row["pool"] = self.pool.get_stats()
        return row
