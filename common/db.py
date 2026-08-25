# -*- coding: utf-8 -*-
"""Host-level failure cooldown tracking."""

import os
import sqlite3
import time

from common.args import config_args, logger


SECONDS_PER_DAY = 24 * 60 * 60
SECONDS_PER_HOUR = 60 * 60


class ProxyFailureDB:
    """Track one failure streak and dynamic cooldown per proxy host."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config_args.failure_db_path
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS proxy_failures (
                    server TEXT NOT NULL PRIMARY KEY,
                    consecutive_failures INTEGER NOT NULL,
                    last_failure_time REAL NOT NULL,
                    cooldown_until REAL NOT NULL
                )
                """
            )
            conn.commit()
        logger.debug(f"Initialized failure database at {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @staticmethod
    def cooldown_seconds_for_count(count: int) -> int:
        cooldowns = config_args.failure_cooldown_days
        if not cooldowns or any(days < 0 for days in cooldowns):
            raise ValueError("failure_cooldown_days must contain non-negative day counts")
        head_start_hours = config_args.failure_cooldown_head_start_hours
        if head_start_hours < 0:
            raise ValueError("failure_cooldown_head_start_hours must be non-negative")
        days = cooldowns[min(max(count, 1), len(cooldowns)) - 1]
        return max(days * SECONDS_PER_DAY - head_start_hours * SECONDS_PER_HOUR, 0)

    def record_failures_batch(self, hosts: list[str], now: float | None = None) -> None:
        """Increment each host once and apply its 0/23/71/167-hour cooldown."""
        hosts = list(dict.fromkeys(str(host) for host in hosts))
        if not hosts:
            return
        current_time = time.time() if now is None else now
        placeholders = ",".join("?" for _ in hosts)
        with self._get_connection() as conn:
            existing = {
                server: count
                for server, count in conn.execute(
                    f"SELECT server, consecutive_failures FROM proxy_failures WHERE server IN ({placeholders})",
                    hosts,
                )
            }
            rows = []
            for host in hosts:
                count = existing.get(host, 0) + 1
                cooldown_until = current_time + self.cooldown_seconds_for_count(count)
                rows.append((host, count, current_time, cooldown_until))
            conn.executemany(
                """
                INSERT INTO proxy_failures
                    (server, consecutive_failures, last_failure_time, cooldown_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(server) DO UPDATE SET
                    consecutive_failures = excluded.consecutive_failures,
                    last_failure_time = excluded.last_failure_time,
                    cooldown_until = excluded.cooldown_until
                """,
                rows,
            )
            conn.commit()
        logger.info(f"Recorded one failed run for {len(hosts)} host(s)")

    def record_successes_batch(self, hosts: list[str]) -> None:
        """A successful representative clears the host failure streak."""
        hosts = list(dict.fromkeys(str(host) for host in hosts))
        if not hosts:
            return
        with self._get_connection() as conn:
            before = conn.total_changes
            conn.executemany("DELETE FROM proxy_failures WHERE server = ?", [(host,) for host in hosts])
            deleted = conn.total_changes - before
            conn.commit()
        if deleted:
            logger.info(f"Cleared cooldown state for {deleted} recovered host(s)")

    def should_filter(self, server: str, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM proxy_failures WHERE server = ?", (str(server),)
            ).fetchone()
        return bool(row and row[0] > current_time)

    def get_filtered_proxies(self, now: float | None = None) -> set[str]:
        """Return hosts whose cooldown has not expired."""
        current_time = time.time() if now is None else now
        with self._get_connection() as conn:
            return {
                server
                for (server,) in conn.execute(
                    "SELECT server FROM proxy_failures WHERE cooldown_until > ?", (current_time,)
                )
            }

    def get_failure_count(self, server: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT consecutive_failures FROM proxy_failures WHERE server = ?", (str(server),)
            ).fetchone()
        return int(row[0]) if row else 0

    def get_cooldown_until(self, server: str) -> float:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM proxy_failures WHERE server = ?", (str(server),)
            ).fetchone()
        return float(row[0]) if row else 0.0
