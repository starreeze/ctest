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

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS proxy_failures (
                        server TEXT NOT NULL,
                        port INTEGER NOT NULL,
                        consecutive_failures INTEGER DEFAULT 0,
                        last_failure_time REAL DEFAULT 0,
                        PRIMARY KEY (server, port)
                    )
                """
                )
                conn.commit()
            logger.debug(f"Initialized failure database at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize failure database: {e}")
            raise

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection"""
        return sqlite3.connect(self.db_path)

    def record_failure(self, server: str, port: int) -> None:
        """
        Record a failure for a proxy. Increments consecutive failure count.

        Args:
            server: Proxy server address
            port: Proxy port
        """
        current_time = time.time()

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Use UPSERT (INSERT OR REPLACE with increment)
                cursor.execute(
                    """
                    INSERT INTO proxy_failures (server, port, consecutive_failures, last_failure_time)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(server, port) DO UPDATE SET
                        consecutive_failures = consecutive_failures + 1,
                        last_failure_time = ?
                """,
                    (server, port, current_time, current_time),
                )
                conn.commit()

                # Get the new failure count for logging
                cursor.execute(
                    "SELECT consecutive_failures FROM proxy_failures WHERE server = ? AND port = ?",
                    (server, port),
                )
                result = cursor.fetchone()
                if result:
                    logger.debug(f"Recorded failure for {server}:{port}: {result[0]} consecutive failures")
        except Exception as e:
            logger.error(f"Failed to record failure for {server}:{port}: {e}")

    def record_success(self, server: str, port: int) -> None:
        """
        Record a success for a proxy. Resets consecutive failure count by removing entry.

        Args:
            server: Proxy server address
            port: Proxy port
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM proxy_failures WHERE server = ? AND port = ?", (server, port))
                if cursor.rowcount > 0:
                    logger.debug(f"Reset failure count for {server}:{port}")
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record success for {server}:{port}: {e}")

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
        try:
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
        except Exception as e:
            logger.error(f"Failed to check filter status for {server}:{port}: {e}")
            return False

    def get_filtered_proxies(self) -> Set[str]:
        """
        Get set of all proxies that should be filtered.

        Returns:
            Set of "server:port" strings that should be filtered
        """
        filtered = set()
        current_time = time.time()
        duration_seconds = config_args.failure_filter_duration_days * 24 * 3600

        try:
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

        except Exception as e:
            logger.error(f"Failed to get filtered proxies: {e}")

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
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT consecutive_failures FROM proxy_failures WHERE server = ? AND port = ?",
                    (server, port),
                )
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Failed to get failure count for {server}:{port}: {e}")
            return 0

    def cleanup_expired(self) -> None:
        """Remove expired entries from the database"""
        current_time = time.time()
        duration_seconds = config_args.failure_filter_duration_days * 24 * 3600
        cutoff_time = current_time - duration_seconds

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM proxy_failures WHERE last_failure_time < ?", (cutoff_time,))
                deleted_count = cursor.rowcount
                conn.commit()

                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} expired entries from failure database")
        except Exception as e:
            logger.error(f"Failed to cleanup expired entries: {e}")

    def save(self) -> None:
        """
        Compatibility method - SQLite auto-saves on commit.
        This method exists for API compatibility with the old JSON-based implementation.
        """
        # SQLite automatically saves data on commit, so this is a no-op
        logger.debug("Save called (SQLite auto-commits, no action needed)")

    def load(self) -> None:
        """
        Compatibility method - SQLite loads data on demand.
        This method exists for API compatibility with the old JSON-based implementation.
        """
        # SQLite loads data on demand, so this is a no-op
        logger.debug("Load called (SQLite loads on demand, no action needed)")
