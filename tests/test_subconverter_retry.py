import importlib
import logging
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests


META_YAML = "proxies:\n- {name: meta, type: vless, server: example.com, port: 443}\n"
LEGACY_YAML = "proxies:\n- {name: legacy, type: ss, server: example.com, port: 443}\n"


class SubconverterRetryTest(unittest.TestCase):
    def setUp(self):
        args_module = types.ModuleType("common.args")
        args_module.config_args = SimpleNamespace(
            subconvert_attempts=3,
            subconvert_base_urls=[
                "https://primary.example/sub?token=secret",
                "https://legacy.example/sub",
            ],
            subconvert_timeout=10,
            subconvert_user_agent="test",
        )
        args_module.get_newest_profile = lambda: ""
        args_module.logger = logging.getLogger("test_subconverter_retry")
        self.args_patch = patch.dict(sys.modules, {"common.args": args_module})
        self.args_patch.start()
        sys.modules.pop("function.update", None)
        self.update = importlib.import_module("function.update")

    def tearDown(self):
        sys.modules.pop("function.update", None)
        self.args_patch.stop()

    @staticmethod
    def response(content: str, error: Exception | None = None):
        response = MagicMock()
        response.content = content.encode()
        response.raise_for_status.side_effect = error
        return response

    def test_retries_primary_until_mihomo_conversion_succeeds(self):
        self.update.direct_session.get = MagicMock(
            side_effect=[
                self.response("<html>bad</html>"),
                self.response("", requests.ConnectionError("secret request URL")),
                self.response(META_YAML),
            ]
        )

        with self.assertLogs("test_subconverter_retry", level="WARNING") as logs:
            result = self.update.fetch_converted_profile(
                ["https://feed.example/private"], "https://config.example"
            )

        self.assertEqual(result, META_YAML)
        self.assertEqual(self.update.direct_session.get.call_count, 3)
        logged = "\n".join(logs.output)
        self.assertNotIn("secret", logged)
        self.assertNotIn("feed.example", logged)

    def test_exhausts_primary_before_trying_fallback(self):
        self.update.direct_session.get = MagicMock(
            side_effect=[
                self.response("not: [valid"),
                self.response("<html>bad</html>"),
                self.response("", requests.Timeout("timed out")),
                self.response(META_YAML),
            ]
        )

        result = self.update.fetch_converted_profile(["https://feed.example"], "https://config.example")

        self.assertEqual(result, META_YAML)
        called_backends = [call.args[0] for call in self.update.direct_session.get.call_args_list]
        self.assertEqual(called_backends[:3], [self.update.args.subconvert_base_urls[0]] * 3)
        self.assertEqual(called_backends[3], self.update.args.subconvert_base_urls[1])

    def test_retries_valid_legacy_result_before_fallback(self):
        self.update.direct_session.get = MagicMock(
            side_effect=[
                self.response(LEGACY_YAML),
                self.response(LEGACY_YAML),
                self.response(LEGACY_YAML),
                self.response(META_YAML),
            ]
        )

        result = self.update.fetch_converted_profile(["https://feed.example"], "https://config.example")

        self.assertEqual(result, META_YAML)
        self.assertEqual(self.update.direct_session.get.call_count, 4)

    def test_first_valid_legacy_result_is_used_only_after_all_attempts(self):
        later_legacy = LEGACY_YAML.replace("legacy", "later")
        self.update.direct_session.get = MagicMock(
            side_effect=[self.response(LEGACY_YAML)]
            + [self.response(later_legacy) for _ in range(5)]
        )

        result = self.update.fetch_converted_profile(
            ["https://feed.example"], "https://config.example"
        )

        self.assertEqual(result, LEGACY_YAML)
        self.assertEqual(self.update.direct_session.get.call_count, 6)

    def test_rejects_nonpositive_attempt_count(self):
        self.update.args.subconvert_attempts = 0
        with self.assertRaisesRegex(ValueError, "at least 1"):
            self.update.fetch_converted_profile(["https://feed.example"], "https://config.example")

    def test_terminal_failure_does_not_expose_or_chain_backend_error(self):
        self.update.direct_session.get = MagicMock(
            side_effect=[requests.ConnectionError("token=secret") for _ in range(6)]
        )

        with self.assertLogs("test_subconverter_retry", level="WARNING") as logs:
            with self.assertRaisesRegex(RuntimeError, "All subconverter backends failed") as raised:
                self.update.fetch_converted_profile(
                    ["https://feed.example/private"], "https://config.example/token=secret"
                )

        self.assertEqual(self.update.direct_session.get.call_count, 6)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        logged = "\n".join(logs.output)
        self.assertNotIn("secret", logged)
        self.assertNotIn("feed.example", logged)


if __name__ == "__main__":
    unittest.main()
