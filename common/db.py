# -*- coding: utf-8 -*-
# @Date    : 2025-12-02
# @Author  : Shangyu.Xing (starreeze@foxmail.com)
"""Persistent database for tracking proxy failures using SQLite"""

import os
import sqlite3
import time
from typing import Optional, Set

from common.args import config_args, logger


class ProxyFailureDB:
    """Database to track consecutive proxy failures and filter out unreliable proxies"""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the failure database.

        Args:
            db_path: Path to SQLite database file. If None, uses config_args.failure_db_path
        """
        self.db_path = db_path or config_args.failure_db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database and create tables if they don't exist"""
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS proxy_failures (
                    server TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    consecutive_failures INTEGER DEFAULT 0,
                    first_failure_time REAL DEFAULT 0,
                    last_failure_time REAL DEFAULT 0,
                    PRIMARY KEY (server, port)
                )
            """
            )
            # Migration: add first_failure_time column if it doesn't exist
            cursor.execute("PRAGMA table_info(proxy_failures)")
            columns = [row[1] for row in cursor.fetchall()]
            if "first_failure_time" not in columns:
                cursor.execute("ALTER TABLE proxy_failures ADD COLUMN first_failure_time REAL DEFAULT 0")
                # Initialize first_failure_time from last_failure_time for existing records
                cursor.execute(
                    "UPDATE proxy_failures SET first_failure_time = last_failure_time WHERE first_failure_time = 0"
                )
            conn.commit()
        logger.debug(f"Initialized failure database at {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection"""
        return sqlite3.connect(self.db_path)

    def record_failures_batch(self, proxies: list[tuple[str, int]]) -> None:
        """
        Record failures for multiple proxies in a single transaction.
        Much more efficient than calling record_failure() for each proxy.

        Args:
            proxies: List of (server, port) tuples
        """
        if not proxies:
            return

        current_time = time.time()
        dedup_window_seconds = config_args.failure_dedup_hours * 3600

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get all existing records in one query
            placeholders = ",".join(["(?, ?)"] * len(proxies))
            flat_params = [item for proxy in proxies for item in proxy]
            cursor.execute(
                f"""
                SELECT server, port, consecutive_failures, first_failure_time
                FROM proxy_failures
                WHERE (server, port) IN (VALUES {placeholders})
                """,
                flat_params,
            )
            existing = {(row[0], row[1]): (row[2], row[3]) for row in cursor.fetchall()}

            # Prepare batch operations
            updates_with_count = []  # (new_count, current_time, server, port)
            updates_time_only = []  # (current_time, server, port)
            inserts = []  # (server, port, 1, current_time, current_time)

            for server, port in proxies:
                if (server, port) in existing:
                    consecutive_failures, first_failure_time = existing[(server, port)]
                    time_since_first = current_time - first_failure_time
                    current_segment = int(time_since_first // dedup_window_seconds)
                    new_count = current_segment + 1

                    if new_count > consecutive_failures:
                        updates_with_count.append((new_count, current_time, server, port))
                        logger.debug(
                            f"Failure for {server}:{port} in segment {current_segment}, count updated to {new_count}"
                        )
                    else:
                        updates_time_only.append((current_time, server, port))
                        logger.debug(
                            f"Failure for {server}:{port} in segment {current_segment}, count unchanged ({consecutive_failures})"
                        )
                else:
                    inserts.append((server, port, 1, current_time, current_time))
                    logger.debug(f"First failure for {server}:{port}, count = 1")

            # Execute batch operations
            if updates_with_count:
                cursor.executemany(
                    "UPDATE proxy_failures SET consecutive_failures = ?, last_failure_time = ? WHERE server = ? AND port = ?",
                    updates_with_count,
                )
            if updates_time_only:
                cursor.executemany(
                    "UPDATE proxy_failures SET last_failure_time = ? WHERE server = ? AND port = ?",
                    updates_time_only,
                )
            if inserts:
                cursor.executemany(
                    "INSERT INTO proxy_failures (server, port, consecutive_failures, first_failure_time, last_failure_time) VALUES (?, ?, ?, ?, ?)",
                    inserts,
                )

            conn.commit()
            logger.info(
                f"Batch recorded {len(proxies)} failures: {len(inserts)} new, {len(updates_with_count)} count updates, {len(updates_time_only)} time-only updates"
            )

    def record_successes_batch(self, proxies: list[tuple[str, int]]) -> None:
        """
        Record successes for multiple proxies in a single transaction.
        Removes all specified entries from the failure database.

        Args:
            proxies: List of (server, port) tuples
        """
        if not proxies:
            return

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("DELETE FROM proxy_failures WHERE server = ? AND port = ?", proxies)
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info(f"Batch reset failure count for {deleted} proxies")

    def should_filter(self, server: str, port: int) -> bool:
        """
        Check if a proxy should be filtered out based on failure history.

        A proxy is filtered if:
        - It has >= consecutive_failure_threshold failures
        - The last failure was within failure_filter_duration_days

        Args:
            server: Proxy server address
            port: Proxy port

        Returns:
            True if proxy should be filtered, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT consecutive_failures, last_failure_time FROM proxy_failures WHERE server = ? AND port = ?",
                (server, port),
            )
            result = cursor.fetchone()

            if not result:
                return False

            consecutive_failures, last_failure_time = result

            # Check if we've reached the failure threshold
            if consecutive_failures < config_args.consecutive_failure_threshold:
                return False

            # Check if the failure is still within the filter duration
            current_time = time.time()
            duration_seconds = config_args.failure_filter_duration_days * 24 * 3600
            time_since_failure = current_time - last_failure_time

            if time_since_failure > duration_seconds:
                # Filter period has expired, remove from database
                logger.debug(f"Filter period expired for {server}:{port}, removing from database")
                cursor.execute("DELETE FROM proxy_failures WHERE server = ? AND port = ?", (server, port))
                conn.commit()
                return False

            return True

    def get_filtered_proxies(self) -> Set[str]:
        """
        Get set of all proxies that should be filtered.

        Returns:
            Set of "server:port" strings that should be filtered
        """
        filtered = set()
        current_time = time.time()
        duration_seconds = config_args.failure_filter_duration_days * 24 * 3600

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get all entries that meet the filter criteria
            cursor.execute(
                """
                SELECT server, port, last_failure_time FROM proxy_failures
                WHERE consecutive_failures >= ?
            """,
                (config_args.consecutive_failure_threshold,),
            )

            rows = cursor.fetchall()
            expired_entries = []

            for server, port, last_failure_time in rows:
                time_since_failure = current_time - last_failure_time

                if time_since_failure > duration_seconds:
                    # Filter period has expired
                    expired_entries.append((server, port))
                else:
                    filtered.add(f"{server}:{port}")

            # Clean up expired entries
            if expired_entries:
                cursor.executemany(
                    "DELETE FROM proxy_failures WHERE server = ? AND port = ?", expired_entries
                )
                conn.commit()
                logger.debug(
                    f"Cleaned up {len(expired_entries)} expired entries while getting filtered proxies"
                )

        return filtered

    def get_failure_count(self, server: str, port: int) -> int:
        """
        Get the consecutive failure count for a proxy.

        Args:
            server: Proxy server address
            port: Proxy port

        Returns:
            Number of consecutive failures, or 0 if not in database
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT consecutive_failures FROM proxy_failures WHERE server = ? AND port = ?",
                (server, port),
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
