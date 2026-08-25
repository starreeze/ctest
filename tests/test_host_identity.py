import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def make_args_module():
    module = types.ModuleType("common.args")
    module.config_args = SimpleNamespace(
        failure_cooldown_days=[1, 3, 7],
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
            [proxy("pin", "1.1.1.1", 443), proxy("fail", "2.2.2.2", 443)],
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1"},
        )
        self.assertEqual([item["name"] for item in kept], ["pin"])
        self.assertEqual(dropped, ["fail"])

    def test_deprecated_dns_geosite_is_migrated(self):
        profile = {
            "dns": {
                "fallback": ["tls://1.0.0.1:853"],
                "fallback-filter": {"geoip": True, "geosite": ["gfw"]},
                "nameserver-policy": {"geosite:cn": ["223.5.5.5"]},
            }
        }
        self.fix.migrate_deprecated_dns_geosite(profile)
        self.assertNotIn("geosite", profile["dns"]["fallback-filter"])
        self.assertEqual(
            profile["dns"]["nameserver-policy"]["geosite:gfw"],
            ["tls://1.0.0.1:853"],
        )

    def test_dedup_uses_host_and_port(self):
        kept, endpoint_to_name, skipped = self.fix.handle_redundant_and_conflicts(
            [
                proxy("a", "EXAMPLE.com.", 443),
                proxy("same", "example.com", 443, {"type": "vmess"}),
                proxy("other-port", "example.com", 8443),
            ]
        )
        self.assertEqual([item["name"] for item in kept], ["a", "other-port"])
        self.assertEqual(
            endpoint_to_name,
            {("example.com", 443): "a", ("example.com", 8443): "other-port"},
        )
        self.assertEqual(skipped, {"same"})

    def test_duplicate_name_different_endpoints_is_renamed(self):
        kept, endpoint_to_name, skipped = self.fix.handle_redundant_and_conflicts(
            [proxy("n", "10.0.0.1", 443), proxy("n", "10.0.0.1", 8443)]
        )
        self.assertEqual([item["name"] for item in kept], ["n", "n [10.0.0.1]"])
        self.assertEqual(endpoint_to_name[("10.0.0.1", 8443)], "n [10.0.0.1]")
        self.assertEqual(skipped, set())

    def test_ipv6_ports_are_distinct_candidates(self):
        kept, _, _ = self.fix.handle_redundant_and_conflicts(
            [proxy("a", "2001:db8::1", 443), proxy("b", "2001:db8::1", 8443)]
        )
        self.assertEqual([item["name"] for item in kept], ["a", "b"])

    def test_group_reference_for_duplicate_endpoint_uses_kept_name(self):
        original = [proxy("keep", "1.1.1.1", 443), proxy("duplicate", "1.1.1.1", 443)]
        kept, endpoint_to_name, _ = self.fix.handle_redundant_and_conflicts(original)
        data = {"proxy-groups": [{"name": "g", "proxies": ["duplicate", "DIRECT"]}]}
        self.fix.update_proxy_references(data, original, endpoint_to_name)
        self.assertEqual(data["proxy-groups"][0]["proxies"], ["keep", "DIRECT"])
        self.assertEqual([item["name"] for item in kept], ["keep"])

    def test_dynamic_cooldown_progression_and_success_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_mod.ProxyFailureDB(os.path.join(tmp, "failures.db"))
            day = self.db_mod.SECONDS_PER_DAY
            now = 1_000_000.0
            for expected_count, expected_days in [(1, 1), (2, 3), (3, 7), (4, 7)]:
                db.record_failures_batch(["host", "host"], now=now)
                self.assertEqual(db.get_failure_count("host"), expected_count)
                self.assertEqual(db.get_cooldown_until("host"), now + expected_days * day)
                self.assertTrue(db.should_filter("host", now=now))
                self.assertFalse(db.should_filter("host", now=now + expected_days * day))
                now += expected_days * day
            db.record_successes_batch(["host"])
            self.assertEqual(db.get_failure_count("host"), 0)
            self.assertFalse(db.should_filter("host", now=now))

    def test_schema_is_host_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_mod.ProxyFailureDB(os.path.join(tmp, "failures.db"))
            db.record_failures_batch(["a.example", "a.example", "b.example"], now=1.0)
            with sqlite3.connect(db.db_path) as conn:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(proxy_failures)")]
                count = conn.execute("SELECT COUNT(*) FROM proxy_failures").fetchone()[0]
            self.assertEqual(
                columns,
                ["server", "consecutive_failures", "last_failure_time", "cooldown_until"],
            )
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
