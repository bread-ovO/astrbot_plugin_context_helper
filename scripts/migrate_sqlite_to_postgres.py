from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from postgres_storage import initialize_schema


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    )


def rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table_exists(connection, table):
        return []
    return connection.execute(f"SELECT * FROM {table}").fetchall()


def migrate(sqlite_path: Path, database_url: str) -> dict[str, int]:
    result: dict[str, int] = {}
    with closing(sqlite3.connect(sqlite_path)) as source:
        source.row_factory = sqlite3.Row
        with psycopg.connect(database_url) as target:
            initialize_schema(target)
            with target.cursor() as cursor:
                message_rows = rows(source, "group_messages")
                cursor.executemany(
                    """
                    INSERT INTO group_messages
                        (origin, platform, group_id, message_id, sender_id,
                         sender_name, content, created_at_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            row["origin"], row["platform"], row["group_id"],
                            row["message_id"], row["sender_id"], row["sender_name"],
                            row["content"], row["created_at_ms"],
                        )
                        for row in message_rows
                    ],
                )
                result["group_messages"] = max(cursor.rowcount, 0)

                topic_rows = rows(source, "knowledge_topics")
                cursor.executemany(
                    """
                    INSERT INTO knowledge_topics
                        (id, group_origin, topic_key, title, status, message_count,
                         first_message_at_ms, last_message_at_ms, created_at_ms, updated_at_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [tuple(row[key] for key in row.keys()) for row in topic_rows],
                )
                result["knowledge_topics"] = max(cursor.rowcount, 0)

                classification_rows = rows(source, "message_classifications")
                cursor.executemany(
                    """
                    INSERT INTO message_classifications
                        (group_origin, message_id, decision, topic_id, reason,
                         confidence, classified_at_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [tuple(row[key] for key in row.keys()) for row in classification_rows],
                )
                result["message_classifications"] = max(cursor.rowcount, 0)

                topic_message_rows = rows(source, "topic_messages")
                cursor.executemany(
                    """
                    INSERT INTO topic_messages(topic_id, group_origin, message_id, added_at_ms)
                    VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    [tuple(row[key] for key in row.keys()) for row in topic_message_rows],
                )
                result["topic_messages"] = max(cursor.rowcount, 0)

                knowledge_rows = rows(source, "knowledge_entries")
                cursor.executemany(
                    """
                    INSERT INTO knowledge_entries
                        (id, group_origin, title, content, category, keywords_json,
                         sources_json, confidence, content_hash, status, review_note,
                         created_at_ms, reviewed_at_ms, reviewer_id)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s,
                            %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [tuple(row[key] for key in row.keys()) for row in knowledge_rows],
                )
                result["knowledge_entries"] = max(cursor.rowcount, 0)

                job_rows = rows(source, "extraction_jobs")
                cursor.executemany(
                    """
                    INSERT INTO extraction_jobs
                        (id, group_origin, start_ms, end_ms, message_count,
                         provider_id, status, error, created_at_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [tuple(row[key] for key in row.keys()) for row in job_rows],
                )
                result["extraction_jobs"] = max(cursor.rowcount, 0)

                for table in (
                    "knowledge_topics",
                    "knowledge_entries",
                    "extraction_jobs",
                ):
                    cursor.execute(
                        f"""
                        SELECT setval(
                            pg_get_serial_sequence('{table}', 'id'),
                            GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 1), 1),
                            true
                        )
                        """
                    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移上下文助手 SQLite 数据到 PostgreSQL")
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--database-url", required=True)
    arguments = parser.parse_args()
    if not arguments.sqlite.is_file():
        raise SystemExit(f"SQLite 文件不存在：{arguments.sqlite}")
    migrated = migrate(arguments.sqlite, arguments.database_url)
    for table, count in migrated.items():
        print(f"{table}: 新增 {count} 条")


if __name__ == "__main__":
    main()
