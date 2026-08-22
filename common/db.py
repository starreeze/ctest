# -*- coding: utf-8 -*-
# @Date    : 2025-12-02
# @Author  : Shangyu.Xing (starreeze@foxmail.com)
"""Persistent database for tracking proxy failures using SQLite"""

import os
import sqlite3
import time

from common.args import config_args, logger


class ProxyFailureDB:
    """Database to track consecutive proxy failures and filter out unreliable proxies"""

    def __init__(self, db_path: str | None = None):
        """
        Initialize the failure database.

        Args:
            db_path: Path to SQLite database file. If None, uses config_args.failure_db_path
        """
        self.db_path = db_path or config_args.failure_db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database and create tables if they don't exist"""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS proxy_failures (
                    server TEXT NOT NULL PRIMARY KEY,
                    consecutive_failures INTEGER DEFAULT 0,
                    first_failure_time REAL DEFAULT 0,
                    last_failure_time REAL DEFAULT 0
                )
            """
            )
            self._migrate_schema(cursor)
            conn.commit()
        logger.debug(f"Initialized failure database at {self.db_path}")

    def _migrate_schema(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(proxy_failures)")
        columns = [row[1] for row in cursor.fetchall()]
        if "first_failure_time" not in columns:
            cursor.execute("ALTER TABLE proxy_failures ADD COLUMN first_failure_time REAL DEFAULT 0")
            cursor.execute(
                "UPDATE proxy_failures SET first_failure_time = last_failure_time WHERE first_failure_time = 0"
            )
            columns.append("first_failure_time")
        if "port" not in columns:
            return

        before = cursor.execute("SELECT COUNT(*) FROM proxy_failures").fetchone()[0]
        cursor.execute(
            """
            CREATE TABLE proxy_failures_host (
                server TEXT NOT NULL PRIMARY KEY,
                consecutive_failures INTEGER DEFAULT 0,
                first_failure_time REAL DEFAULT 0,
                last_failure_time REAL DEFAULT 0
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO proxy_failures_host (server, consecutive_failures, first_failure_time, last_failure_time)
            SELECT
                server,
                MAX(consecutive_failures),
                MIN(CASE WHEN first_failure_time = 0 THEN last_failure_time ELSE first_failure_time END),
                MAX(last_failure_time)
            FROM proxy_failures
            GROUP BY server
            """
        )
        after = cursor.execute("SELECT COUNT(*) FROM proxy_failures_host").fetchone()[0]
        cursor.execute("DROP TABLE proxy_failures")
        cursor.execute("ALTER TABLE proxy_failures_host RENAME TO proxy_failures")
        logger.info(f"Migrated failure database primary key from (server, port) to server: {before} rows -> {after} hosts")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection"""
        return sqlite3.connect(self.db_path)

    def record_failures_batch(self, hosts: list[str]) -> None:
        """
        Record failures for multiple proxy hosts in a single transaction.

        Args:
            hosts: Proxy server addresses
        """
        if not hosts:
            return
        hosts = list(dict.fromkeys(str(host) for host in hosts))

        current_time = time.time()
        dedup_window_seconds = config_args.failure_dedup_hours * 3600

        with self._get_connection() as conn:
            cursor = conn.cursor()

            placeholders = ",".join(["?"] * len(hosts))
            cursor.execute(
                f"""
                SELECT server, consecutive_failures, first_failure_time
                FROM proxy_failures
                WHERE server IN ({placeholders})
                """,
                hosts,
            )
            existing = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

            updates_with_count = []
            updates_time_only = []
            inserts = []

            for host in hosts:
                if host in existing:
                    consecutive_failures, first_failure_time = existing[host]
                    time_since_first = current_time - first_failure_time
                    current_segment = int(time_since_first // dedup_window_seconds)
                    new_count = current_segment + 1

                    if new_count > consecutive_failures:
                        updates_with_count.append((new_count, current_time, host))
                        logger.debug(
                            f"Failure for {host} in segment {current_segment}, count updated to {new_count}"
                        )
                    else:
                        updates_time_only.append((current_time, host))
                        logger.debug(
                            f"Failure for {host} in segment {current_segment}, count unchanged ({consecutive_failures})"
                        )
                else:
                    inserts.append((host, 1, current_time, current_time))
                    logger.debug(f"First failure for {host}, count = 1")

            if updates_with_count:
                cursor.executemany(
                    "UPDATE proxy_failures SET consecutive_failures = ?, last_failure_time = ? WHERE server = ?",
                    updates_with_count,
                )
            if updates_time_only:
                cursor.executemany(
                    "UPDATE proxy_failures SET last_failure_time = ? WHERE server = ?",
                    updates_time_only,
                )
            if inserts:
                cursor.executemany(
                    "INSERT INTO proxy_failures (server, consecutive_failures, first_failure_time, last_failure_time) VALUES (?, ?, ?, ?)",
                    inserts,
                )

            conn.commit()
            logger.info(
                f"Batch recorded {len(hosts)} failures: {len(inserts)} new, {len(updates_with_count)} count updates, {len(updates_time_only)} time-only updates"
            )

    def record_successes_batch(self, hosts: list[str]) -> None:
        """
        Record successes for multiple proxy hosts in a single transaction.
        Removes all specified entries from the failure database.

        Args:
            hosts: Proxy server addresses
        """
        if not hosts:
            return
        hosts = list(dict.fromkeys(str(host) for host in hosts))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("DELETE FROM proxy_failures WHERE server = ?", [(host,) for host in hosts])
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info(f"Batch reset failure count for {deleted} proxies")

    def should_filter(self, server: str) -> bool:
        """
        Check if a proxy host should be filtered out based on failure history.

        A host is filtered if:
        - It has >= consecutive_failure_threshold failures
        - The last failure was within failure_filter_duration_days
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT consecutive_failures, last_failure_time FROM proxy_failures WHERE server = ?",
                (server,),
            )
            result = cursor.fetchone()

            if not result:
                return False

            consecutive_failures, last_failure_time = result

            if consecutive_failures < config_args.consecutive_failure_threshold:
                return False

            current_time = time.time()
            duration_seconds = config_args.failure_filter_duration_days * 24 * 3600
            time_since_failure = current_time - last_failure_time

            if time_since_failure > duration_seconds:
                logger.debug(f"Filter period expired for {server}, removing from database")
                cursor.execute("DELETE FROM proxy_failures WHERE server = ?", (server,))
                conn.commit()
                return False

            return True

    def get_filtered_proxies(self) -> set[str]:
        """
        Get set of all proxy hosts that should be filtered.

        Returns:
            Set of server addresses that should be filtered
        """
        filtered: set[str] = set()
        current_time = time.time()
        duration_seconds = config_args.failure_filter_duration_days * 24 * 3600

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT server, last_failure_time FROM proxy_failures
                WHERE consecutive_failures >= ?
            """,
                (config_args.consecutive_failure_threshold,),
            )

            rows = cursor.fetchall()
            expired_entries = []

            for server, last_failure_time in rows:
                time_since_failure = current_time - last_failure_time

                if time_since_failure > duration_seconds:
                    expired_entries.append((server,))
                else:
                    filtered.add(server)

            if expired_entries:
                cursor.executemany("DELETE FROM proxy_failures WHERE server = ?", expired_entries)
                conn.commit()
                logger.debug(
                    f"Cleaned up {len(expired_entries)} expired entries while getting filtered proxies"
                )

        return filtered

    def get_failure_count(self, server: str) -> int:
        """
        Get the consecutive failure count for a proxy host.

        Returns:
            Number of consecutive failures, or 0 if not in database
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT consecutive_failures FROM proxy_failures WHERE server = ?",
                (server,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def cleanup_expired(self) -> None:
        """Remove expired entries from the database"""
        current_time = time.time()
        duration_seconds = config_args.failure_filter_duration_days * 24 * 3600
        cutoff_time = current_time - duration_seconds

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM proxy_failures WHERE last_failure_time < ?", (cutoff_time,))
            deleted_count = cursor.rowcount
            conn.commit()

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired entries from failure database")
