import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def make_args_module():
    module = types.ModuleType("common.args")
    module.config_args = SimpleNamespace(
        consecutive_failure_threshold=3,
        failure_filter_duration_days=30,
        failure_dedup_hours=24,
        failure_db_path="",
        unsupported_names=["cipher: chacha20-poly1305"],
        target_group="select",
        max_proxies_per_group=100,
        load_balance_strategy="round-robin",
    )
    module.logger = logging.getLogger("test_host_identity")
    module.get_newest_profile = lambda: ""
    return module


def proxy(name: str, server: str, port: int, extra: dict | None = None) -> dict:
    item = {"name": name, "server": server, "port": port, "type": "ss"}
    if extra:
        item.update(extra)
    return item


class HostIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.args_module = make_args_module()
        cls.args_patch = patch.dict(sys.modules, {"common.args": cls.args_module})
        cls.args_patch.start()
        sys.modules.pop("common.db", None)
        sys.modules.pop("function.fix", None)
        cls.db_mod = importlib.import_module("common.db")
        cls.fix = importlib.import_module("function.fix")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("function.fix", None)
        sys.modules.pop("common.db", None)
        cls.args_patch.stop()

    def test_failure_filter_skips_keep_hosts(self):
        kept, dropped = self.fix.apply_failure_filter(
            [
                proxy("pin", "1.1.1.1", 443),
                proxy("fail", "2.2.2.2", 443),
                proxy("ok", "3.3.3.3", 443),
            ],
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1"},
        )
        self.assertEqual([p["name"] for p in kept], ["pin", "ok"])
        self.assertEqual(dropped, ["fail"])

    def test_dedup_keeps_first_proxy_per_host(self):
        kept, host_to_name, skipped = self.fix.handle_redundant_and_conflicts(
            [
                proxy("a", "1.1.1.1", 443),
                proxy("b", "1.1.1.1", 8443),
                proxy("c", "2.2.2.2", 443),
            ]
        )
        self.assertEqual([p["name"] for p in kept], ["a", "c"])
        self.assertEqual(host_to_name, {"1.1.1.1": "a", "2.2.2.2": "c"})
        self.assertEqual(skipped, {"b"})

    def test_duplicate_name_different_hosts_are_renamed(self):
        kept, host_to_name, skipped = self.fix.handle_redundant_and_conflicts(
            [
                proxy("n", "10.0.0.1", 443),
                proxy("n", "10.0.0.2", 443),
            ]
        )
        self.assertEqual(kept[0]["name"], "n")
        self.assertEqual(kept[1]["name"], "n [10.0.0.2]")
        self.assertEqual(host_to_name["10.0.0.1"], "n")
        self.assertEqual(host_to_name["10.0.0.2"], "n [10.0.0.2]")
        self.assertEqual(skipped, set())

    def test_ipv6_host_is_one_identity(self):
        kept, host_to_name, skipped = self.fix.handle_redundant_and_conflicts(
            [
                proxy("v6-a", "2001:db8::1", 443),
                proxy("v6-b", "2001:db8::1", 8443),
                proxy("v6-c", "2001:db8::2", 443),
            ]
        )
        self.assertEqual([p["name"] for p in kept], ["v6-a", "v6-c"])
        self.assertEqual(set(host_to_name), {"2001:db8::1", "2001:db8::2"})
        self.assertEqual(skipped, {"v6-b"})

    def test_empty_proxy_list(self):
        kept, host_to_name, skipped = self.fix.handle_redundant_and_conflicts([])
        self.assertEqual(kept, [])
        self.assertEqual(host_to_name, {})
        self.assertEqual(skipped, set())

    def test_group_references_follow_host_rename_and_drop(self):
        original = [
            proxy("keep", "1.1.1.1", 443),
            proxy("drop-port", "1.1.1.1", 8443),
            proxy("n", "2.2.2.2", 443),
        ]
        kept, host_to_name, skipped = self.fix.handle_redundant_and_conflicts(original)
        data = {"proxy-groups": [{"name": "g", "proxies": ["keep", "drop-port", "n", "DIRECT"]}]}
        self.fix.update_proxy_references(data, original, skipped, host_to_name)
        self.assertEqual(data["proxy-groups"][0]["proxies"], ["keep", "n", "DIRECT"])
        self.assertEqual([p["name"] for p in kept], ["keep", "n"])

    def test_failure_db_filters_every_port_on_the_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_mod.ProxyFailureDB(os.path.join(tmp, "failures.db"))
            now = time.time()
            with sqlite3.connect(db.db_path) as conn:
                conn.execute(
                    "INSERT INTO proxy_failures VALUES (?, ?, ?, ?)",
                    ("1.1.1.1", 3, now, now),
                )
                conn.commit()
            filtered = db.get_filtered_proxies()
            self.assertEqual(filtered, {"1.1.1.1"})
            self.assertTrue(db.should_filter("1.1.1.1"))
            self.assertFalse(db.should_filter("2.2.2.2"))

    def test_batch_record_dedupes_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_mod.ProxyFailureDB(os.path.join(tmp, "failures.db"))
            db.record_failures_batch(["a.example", "a.example", "b.example"])
            self.assertEqual(db.get_failure_count("a.example"), 1)
            with sqlite3.connect(db.db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM proxy_failures").fetchone()[0], 2)

    def test_migrates_server_port_rows_to_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "failures.db")
            now = time.time()
            older = now - 1000
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    CREATE TABLE proxy_failures (
                        server TEXT NOT NULL,
                        port INTEGER NOT NULL,
                        consecutive_failures INTEGER DEFAULT 0,
                        first_failure_time REAL DEFAULT 0,
                        last_failure_time REAL DEFAULT 0,
                        PRIMARY KEY (server, port)
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO proxy_failures VALUES (?, ?, ?, ?, ?)",
                    [
                        ("same.host", 443, 3, older, now),
                        ("same.host", 8443, 1, now, now),
                        ("other.host", 443, 2, now, now),
                    ],
                )
                conn.commit()

            db = self.db_mod.ProxyFailureDB(path)
            with sqlite3.connect(path) as conn:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(proxy_failures)")]
                self.assertNotIn("port", columns)
                rows = {
                    row[0]: row[1:]
                    for row in conn.execute(
                        "SELECT server, consecutive_failures, first_failure_time, last_failure_time FROM proxy_failures"
                    )
                }
            self.assertEqual(set(rows), {"same.host", "other.host"})
            self.assertEqual(rows["same.host"][0], 3)
            self.assertEqual(rows["same.host"][1], older)
            self.assertEqual(rows["same.host"][2], now)
            self.assertEqual(db.get_failure_count("same.host"), 3)
            self.assertTrue(db.should_filter("same.host"))
            self.assertFalse(db.should_filter("other.host"))


if __name__ == "__main__":
    unittest.main()
