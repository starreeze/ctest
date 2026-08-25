import importlib
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml


class FakeRaw:
    def __init__(self, chunks):
        self._buf = b"".join(chunks)
        self._offset = 0

    def read(self, amt: int = -1) -> bytes:
        if amt is None or amt < 0:
            data = self._buf[self._offset :]
            self._offset = len(self._buf)
            return data
        data = self._buf[self._offset : self._offset + amt]
        self._offset += len(data)
        return data


class FakeResponse:
    def __init__(self, chunks, url="https://speed.example/download", status_code=200, headers=None):
        self.chunks = chunks
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""
        self.raw = FakeRaw(chunks)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def close(self):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self.chunks


def make_args_module():
    module = types.ModuleType("common.args")
    module.config_args = SimpleNamespace(
        controller_password="secret",
        controller_url="http://127.0.0.1:9090",
        controller_timeout=10.0,
        proxy_url="http://127.0.0.1:7890",
        target_group="select",
        global_group="GLOBAL",
        meta_start_command="mihomo -d profiles",
        discard=True,
        profile_remote_url_path="urls.txt",
        load_balance_strategy="round-robin",
        failure_cooldown_days=[0, 1, 3, 7],
        failure_cooldown_head_start_hours=1,
        failure_db_path="",
    )
    module.test_args = SimpleNamespace(
        speed_test_mode="sdk",
        speed_test_url="https://speed.example/download?bytes={bytes}",
        speed_test_retry=1,
        speedtest_call_timeout=300,
        speed_http_sizes_mb=[1, 4, 8, 16],
        speed_http_min_duration=3.0,
        speed_http_trials=2,
        speed_http_percentile=0.25,
        speed_http_connect_overhead=5.0,
        speed_http_connect_timeout=10.0,
        speed_http_read_timeout=15.0,
        speed_http_max_transfer_seconds=30.0,
        speed_http_deadline_rate_kibps=0,
        speed_http_chunk_size_kb=4096,
        speed_http_ramp_fail_factor=0.85,
        speed_outage_min_samples=5,
        speed_outage_fail_ratio=0.8,
        test_latency_retry=1,
        latency_call_timeout=300,
        core_restart_timeout=10,
        latency_timeout=5000,
        latency_test_times=1,
        latency_test_urls=["https://example.com"],
        test_speed=True,
        speed_retain_min_mibps=0.0,
        speed_avoid_cooldown_min_mibps=0.0,
        speed_load_balance_min_mibps=0.5,
    )
    module.logger = MagicMock()
    module.apply_runtime_proxy_env = MagicMock()
    module.get_newest_profile = MagicMock()
    return module


class AdaptiveSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.args_module = make_args_module()
        speedtest_module = types.ModuleType("speedtest")
        speedtest_module.Speedtest = MagicMock
        cls.args_patch = patch.dict(
            sys.modules, {"common.args": cls.args_module, "speedtest": speedtest_module}
        )
        cls.args_patch.start()
        sys.modules.pop("common.api", None)
        sys.modules.pop("function.speed", None)
        cls.api = importlib.import_module("common.api")
        cls.speed = importlib.import_module("function.speed")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("function.speed", None)
        sys.modules.pop("common.api", None)
        cls.args_patch.stop()

    def setUp(self):
        self.args = self.args_module.test_args
        self.args.speed_test_mode = "sdk"
        self.args.speed_test_url = "https://speed.example/download?bytes={bytes}"
        self.args.speed_http_sizes_mb = [1, 4, 8, 16]
        self.args.speed_http_min_duration = 3.0
        self.args.speed_http_trials = 2
        self.args.speed_http_percentile = 0.25
        self.args.speed_http_connect_overhead = 5.0
        self.args.speed_http_connect_timeout = 10.0
        self.args.speed_http_read_timeout = 15.0
        self.args.speed_http_max_transfer_seconds = 30.0
        self.args.speed_http_chunk_size_kb = 4096
        self.args.speed_http_ramp_fail_factor = 0.85
        self.args.speed_outage_min_samples = 5
        self.args.speed_outage_fail_ratio = 0.8
        self.args.test_speed = True
        self.args.speed_http_deadline_rate_kibps = 0
        self.args.speed_retain_min_mibps = 0.0
        self.args.speed_avoid_cooldown_min_mibps = 0.0
        self.args.speed_load_balance_min_mibps = 0.5

    def sample(self, size_mb, body_seconds, goodput, ttfb_ms=100):
        return self.api.DownloadSample(
            size_bytes=size_mb * self.api.MEBIBYTE,
            body_seconds=body_seconds,
            ttfb_ms=ttfb_ms,
            goodput_mibps=goodput,
        )

    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(self.api.percentile([4.0, 8.0, 12.0], 0.25), 6.0)
        self.assertEqual(self.api.percentile([12.0, 4.0, 8.0], 0.5), 8.0)

    def test_probe_time_limit_is_overhead_plus_size_at_speed_floor(self):
        mebi = self.api.MEBIBYTE
        with patch.object(self.args, "speed_http_deadline_rate_kibps", 512):
            self.assertEqual(self.api.probe_time_limit_seconds(1 * mebi), 7.0)
            self.assertEqual(self.api.probe_time_limit_seconds(4 * mebi), 13.0)
            self.assertEqual(self.api.probe_time_limit_seconds(8 * mebi), 21.0)
            self.assertEqual(self.api.probe_time_limit_seconds(16 * mebi), 30.0)

    def test_probe_time_limit_uses_bounded_cap_when_speed_floor_is_disabled(self):
        self.assertEqual(
            self.api.probe_time_limit_seconds(self.api.MEBIBYTE),
            self.args.speed_http_max_transfer_seconds,
        )

    def test_config_rejects_invalid_ladder_and_duration(self):
        self.args.speed_http_sizes_mb = [1, 8, 4]
        with self.assertRaisesRegex(ValueError, "unique and increasing"):
            self.api.validate_adaptive_speed_config()

        self.args.speed_http_sizes_mb = [1, 4]
        self.args.speed_http_min_duration = 31.0
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            self.api.validate_adaptive_speed_config()

        self.args.speed_http_min_duration = 3.0
        self.args.speed_http_deadline_rate_kibps = -1
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            self.api.validate_adaptive_speed_config()

    def test_download_sample_separates_ttfb_and_body_and_caps_bytes(self):
        size_bytes = self.api.MEBIBYTE
        response = FakeResponse([b"x" * (size_bytes + 128)])
        timings = [10.0, 10.0, 12.0, 12.0, 12.5, 14.0]

        with patch.object(self.api.requests, "get", return_value=response) as request, patch.object(
            self.api.time, "perf_counter", side_effect=timings
        ):
            sample = self.api.download_sample(size_bytes)

        self.assertEqual(sample.size_bytes, size_bytes)
        self.assertEqual(sample.ttfb_ms, 2000.0)
        self.assertEqual(sample.body_seconds, 2.0)
        self.assertEqual(sample.goodput_mibps, 0.5)
        self.assertEqual(response.raw._offset, size_bytes)
        _, kwargs = request.call_args
        self.assertEqual(
            kwargs["proxies"],
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertEqual(kwargs["headers"]["Referer"], "https://speed.example/")
        self.assertEqual(kwargs["timeout"], (10.0, 15.0))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertIn(str(size_bytes), request.call_args.args[0])

    def test_download_sample_rejects_short_body_and_https_downgrade(self):
        size_bytes = self.api.MEBIBYTE
        timings = [i * 0.1 for i in range(20)]
        with patch.object(
            self.api.requests, "get", return_value=FakeResponse([b"short"])
        ), patch.object(self.api.time, "perf_counter", side_effect=timings):
            with self.assertRaisesRegex(ValueError, "returned 5"):
                self.api.download_sample(size_bytes)

        downgrade = FakeResponse([b"x" * size_bytes], url="http://speed.example/download")
        with patch.object(self.api.requests, "get", return_value=downgrade), patch.object(
            self.api.time, "perf_counter", side_effect=[0.0, 0.1, 0.2]
        ):
            with self.assertRaisesRegex(ValueError, "non-HTTPS"):
                self.api.download_sample(size_bytes)

    def test_download_sample_follows_one_https_redirect(self):
        size_bytes = self.api.MEBIBYTE
        redirect = FakeResponse(
            [],
            url="https://speed.example/download?bytes=1",
            status_code=301,
            headers={"Location": "https://speed.example/final"},
        )
        final = FakeResponse([b"x" * size_bytes], url="https://speed.example/final")
        with patch.object(self.api.requests, "get", side_effect=[redirect, final]) as request, patch.object(
            self.api.time, "perf_counter", side_effect=[0.0, 0.1, 0.15, 0.2, 0.25, 1.1, 1.2]
        ):
            sample = self.api.download_sample(size_bytes)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0], "https://speed.example/final")
        self.assertEqual(sample.size_bytes, size_bytes)

    def test_download_sample_rejects_http_redirect_and_second_hop(self):
        size_bytes = self.api.MEBIBYTE
        http_redirect = FakeResponse(
            [],
            status_code=301,
            headers={"Location": "http://speed.example/download"},
        )
        with patch.object(self.api.requests, "get", return_value=http_redirect):
            with self.assertRaisesRegex(ValueError, "non-HTTPS"):
                self.api.download_sample(size_bytes)

        first = FakeResponse([], status_code=301, headers={"Location": "https://speed.example/b"})
        second = FakeResponse([], url="https://speed.example/b", status_code=302, headers={"Location": "https://speed.example/c"})
        with patch.object(self.api.requests, "get", side_effect=[first, second]):
            with self.assertRaisesRegex(ValueError, "exceeded one redirect"):
                self.api.download_sample(size_bytes)

    def test_fixed_file_requires_an_exact_range_response(self):
        size_bytes = self.api.MEBIBYTE
        self.args.speed_test_url = "https://speed.example/file.bin"
        response = FakeResponse(
            [b"x" * size_bytes],
            status_code=206,
            headers={"Content-Range": f"bytes 0-{size_bytes - 1}/4194304"},
        )
        with patch.object(self.api.requests, "get", return_value=response) as request, patch.object(
            self.api.time, "perf_counter", side_effect=[0.0, 0.1, 0.1, 0.2, 0.3, 1.1]
        ):
            self.api.download_sample(size_bytes)
        self.assertEqual(request.call_args.kwargs["headers"]["Range"], f"bytes=0-{size_bytes - 1}")

        ignored_range = FakeResponse([b"x" * size_bytes])
        with patch.object(self.api.requests, "get", return_value=ignored_range), patch.object(
            self.api.time, "perf_counter", side_effect=[0.0, 0.1, 0.2]
        ):
            with self.assertRaisesRegex(ValueError, "did not honor"):
                self.api.download_sample(size_bytes)

    def test_adaptive_ladder_selects_first_long_enough_body(self):
        samples = [
            self.sample(1, 0.2, 5.0),
            self.sample(4, 0.8, 5.0),
            self.sample(8, 1.6, 5.0),
            self.sample(16, 3.2, 4.0),
            self.sample(16, 2.0, 8.0),
            self.sample(16, 1.4, 12.0),
        ]
        with patch.object(self.api, "try_download_sample", side_effect=samples) as download:
            score = self.api.adaptive_download_speed()

        self.assertEqual(score, 5.0)
        called_sizes = [call.args[0] // self.api.MEBIBYTE for call in download.call_args_list]
        self.assertEqual(called_sizes, [1, 4, 8, 16, 16])

    def test_adaptive_failure_falls_back_to_last_success_and_penalizes(self):
        samples = [
            self.sample(1, 0.2, 5.0),
            None,
            self.sample(1, 0.2, 4.0),
            self.sample(1, 0.2, 8.0),
        ]
        with patch.object(self.api, "try_download_sample", side_effect=samples) as download:
            score = self.api.adaptive_download_speed()

        self.assertAlmostEqual(score, 0.85 * 4.25)
        called_sizes = [call.args[0] // self.api.MEBIBYTE for call in download.call_args_list]
        self.assertEqual(called_sizes, [1, 4, 1])

    def test_adaptive_all_failures_return_na(self):
        with patch.object(self.api, "try_download_sample", return_value=None) as download:
            self.assertIsNone(self.api.adaptive_download_speed())
        called_sizes = [call.args[0] // self.api.MEBIBYTE for call in download.call_args_list]
        self.assertEqual(called_sizes, [1, 1])

    def test_adaptive_last_rung_uses_short_body(self):
        self.args.speed_http_sizes_mb = [1, 4]
        samples = [
            self.sample(1, 0.2, 5.0),
            self.sample(4, 1.0, 8.0),
            self.sample(4, 1.1, 6.0),
            self.sample(4, 0.9, 10.0),
        ]
        with patch.object(self.api, "try_download_sample", side_effect=samples) as download:
            score = self.api.adaptive_download_speed()
        self.assertEqual(score, 6.5)
        called_sizes = [call.args[0] // self.api.MEBIBYTE for call in download.call_args_list]
        self.assertEqual(called_sizes, [1, 4, 4])

    def test_adaptive_call_timeout_returns_na(self):
        with patch.object(self.api, "call_adaptive_speedtest", side_effect=self.api.FunctionTimedOut("timeout")):
            self.assertIsNone(self.api.test_download_adaptive())

    def test_try_download_sample_treats_urllib3_timeout_as_failure(self):
        from urllib3.exceptions import ReadTimeoutError

        with patch.object(
            self.api,
            "download_sample",
            side_effect=ReadTimeoutError(None, None, "Read timed out."),
        ):
            self.assertIsNone(self.api.try_download_sample(self.api.MEBIBYTE))

    def test_speed_mode_dispatch_uses_adaptive_by_default(self):
        response = SimpleNamespace(status_code=204, text="")
        self.args.speed_test_mode = "adaptive"
        with patch.object(self.api, "put", return_value=response), patch.object(
            self.api, "test_download_speedtest", return_value=7.0
        ) as sdk, patch.object(self.api, "test_download_adaptive", return_value=9.0) as adaptive:
            self.assertEqual(self.api.test_speed_single("node"), 9.0)
            adaptive.assert_called_once_with()
            sdk.assert_not_called()

            self.args.speed_test_mode = "sdk"
            self.assertEqual(self.api.test_speed_single("node"), 7.0)
            sdk.assert_called_once_with()

    def test_controller_session_ignores_proxy_environment(self):
        self.assertFalse(self.api.controller_session.trust_env)

    def test_select_proxy_accepts_nonempty_success_response(self):
        response = SimpleNamespace(status_code=200, text='{"ok":true}')
        with patch.object(self.api, "put", return_value=response):
            self.assertTrue(self.api.select_proxy("node", "select"))

    def test_meta_stop_terminates_only_owned_process_group(self):
        process = MagicMock(pid=1234)
        process.poll.return_value = None
        lifecycle = self.api.MetaLifecycle()
        lifecycle.process = process
        with patch.object(self.api.os, "killpg") as killpg:
            lifecycle.stop()
        killpg.assert_called_once_with(1234, self.api.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=10)
        self.assertIsNone(lifecycle.process)

    def test_meta_start_uses_isolated_test_listeners(self):
        profile = {
            "port": 7000,
            "socks-port": 7891,
            "mixed-port": 7002,
            "allow-lan": True,
            "bind-address": "*",
            "external-controller": "0.0.0.0:9999",
            "dns": {"enable": True, "listen": "0.0.0.0:53"},
            "proxies": [{"name": "node", "type": "direct"}],
        }
        process = MagicMock(pid=1234)
        process.poll.return_value = None
        ready = SimpleNamespace(ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "profile.yaml")
            with open(source, "w", encoding="utf-8") as profile_file:
                yaml.safe_dump(profile, profile_file)
            lifecycle = self.api.MetaLifecycle()
            with patch.object(self.api.subprocess, "Popen", return_value=process) as popen, patch.object(
                self.api.controller_session, "get", return_value=ready
            ), patch.object(self.api.os, "killpg"):
                lifecycle.start(source)
                test_path = lifecycle.test_profile_path
                with open(test_path, encoding="utf-8") as test_file:
                    isolated = yaml.safe_load(test_file)
                self.assertEqual(isolated["port"], 7890)
                self.assertEqual(isolated["socks-port"], 0)
                self.assertEqual(isolated["mixed-port"], 0)
                self.assertFalse(isolated["allow-lan"])
                self.assertEqual(isolated["bind-address"], "127.0.0.1")
                self.assertFalse(isolated["dns"]["enable"])
                self.assertEqual(popen.call_args.args[0][-2:], ["-f", test_path])
                lifecycle.stop()
            self.assertFalse(os.path.exists(test_path))

    def test_get_speed_switches_to_global_and_restores(self):
        config_response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"mode": "rule"},
            raise_for_status=lambda: None,
        )
        ok = SimpleNamespace(status_code=204, text="")
        with patch.object(self.api, "get", return_value=config_response), patch.object(
            self.api, "patch", return_value=ok
        ) as mode, patch.object(self.api, "put", return_value=ok) as select, patch.object(
            self.api, "test_download_speedtest", return_value=1.0
        ):
            result = self.api.get_speed([("node-a", 120)], {"node-a": "host-a"})

        self.assertEqual(result, ({"node-a": (1.0, 120)}, {"host-a"}))
        self.assertEqual(mode.call_args_list[0].kwargs["json"], {"mode": "global"})
        self.assertEqual(mode.call_args_list[-1].kwargs["json"], {"mode": "rule"})
        selected_groups = [call.args[0].rsplit("/", 1)[-1] for call in select.call_args_list]
        self.assertEqual(selected_groups, ["GLOBAL", "select"])

    def test_get_speed_uses_inclusive_retain_threshold(self):
        config_response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"mode": "rule"},
            raise_for_status=lambda: None,
        )
        ok = SimpleNamespace(status_code=204, text="")
        with patch.object(self.api, "get", return_value=config_response), patch.object(
            self.api, "patch", return_value=ok
        ), patch.object(self.api, "put", return_value=ok), patch.object(
            self.api, "test_download_speedtest", side_effect=[0.25, 0.0]
        ) as download:
            result = self.api.get_speed(
                [("slow-latency", 300), ("fast-latency", 100), ("other", 50)],
                {"slow-latency": "same", "fast-latency": "same", "other": "other"},
            )
        self.assertEqual(
            result,
            ({"other": (0.25, 50), "fast-latency": (0.0, 100)}, {"other", "same"}),
        )
        self.assertEqual(download.call_count, 2)

    def test_get_speed_stops_at_na_and_avoids_cooldown(self):
        config_response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"mode": "rule"},
            raise_for_status=lambda: None,
        )
        ok = SimpleNamespace(status_code=204, text="")
        self.args.speed_retain_min_mibps = 1.0
        self.args.speed_avoid_cooldown_min_mibps = 1.0
        with patch.object(self.api, "get", return_value=config_response), patch.object(
            self.api, "patch", return_value=ok
        ), patch.object(self.api, "put", return_value=ok), patch.object(
            self.api, "test_download_speedtest", side_effect=[None, 5.0]
        ) as download:
            result = self.api.get_speed(
                [("first", 50), ("second", 100)],
                {"first": "same", "second": "same"},
            )
        self.assertEqual(result, ({"first": (None, 50)}, {"same"}))
        download.assert_called_once_with()

    def test_numeric_below_retain_can_avoid_cooldown(self):
        config_response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"mode": "rule"},
            raise_for_status=lambda: None,
        )
        ok = SimpleNamespace(status_code=204, text="")
        self.args.speed_retain_min_mibps = 2.0
        self.args.speed_avoid_cooldown_min_mibps = 0.5
        with patch.object(self.api, "get", return_value=config_response), patch.object(
            self.api, "patch", return_value=ok
        ), patch.object(self.api, "put", return_value=ok), patch.object(
            self.api, "test_download_speedtest", side_effect=[0.75, 1.5]
        ):
            result = self.api.get_speed(
                [("first", 50), ("second", 100)],
                {"first": "same", "second": "same"},
            )
        self.assertEqual(result, ({}, {"same"}))

    def test_speed_thresholds_must_be_finite_and_non_negative(self):
        self.args.speed_retain_min_mibps = float("nan")
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            self.api.get_speed([], {})

    def test_invalid_numeric_measurement_fails_fast(self):
        config_response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"mode": "global"},
            raise_for_status=lambda: None,
        )
        ok = SimpleNamespace(status_code=204, text="")
        with patch.object(self.api, "get", return_value=config_response), patch.object(
            self.api, "put", return_value=ok
        ), patch.object(self.api, "test_download_speedtest", return_value=float("nan")):
            with self.assertRaisesRegex(ValueError, "finite and non-negative"):
                self.api.get_speed([("node", 50)], {"node": "host"})

    def test_persist_speed_failures_uses_configured_ratio(self):
        self.args.speed_test_mode = "adaptive"
        self.assertFalse(self.speed.should_persist_speed_failures(10, 9))
        self.assertFalse(self.speed.should_persist_speed_failures(10, 10))
        self.assertTrue(self.speed.should_persist_speed_failures(10, 3))
        self.assertTrue(self.speed.should_persist_speed_failures(2, 2))
        self.assertFalse(self.speed.should_persist_speed_failures(10, 0))
        self.args.speed_test_mode = "sdk"
        self.assertTrue(self.speed.should_persist_speed_failures(10, 10))

    def test_failure_cooldown_uses_run_anchor_not_outcome_write_time(self):
        run_started_at = 1_000_000.0
        outcome_write_time = run_started_at + 10 * 60
        next_daily_run = run_started_at + 24 * 60 * 60
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = os.path.join(tmp, "profile.yaml")
            failure_db = self.speed.ProxyFailureDB(os.path.join(tmp, "failures.db"))
            failure_db.record_failures_batch(["dead.example"], now=run_started_at - 24 * 60 * 60)
            with open(profile_path, "w", encoding="utf-8") as profile_file:
                yaml.safe_dump(
                    {
                        "proxies": [
                            {
                                "name": "dead",
                                "type": "ss",
                                "server": "dead.example",
                                "port": 443,
                                "cipher": "aes-128-gcm",
                                "password": "secret",
                            }
                        ],
                        "proxy-groups": [
                            {"name": "select Group 1", "type": "select", "proxies": ["dead"]}
                        ],
                    },
                    profile_file,
                    allow_unicode=True,
                    sort_keys=False,
                )
            with (
                patch.object(self.speed, "get_newest_profile", return_value=profile_path),
                patch.object(self.speed, "get_latency", return_value={}),
                patch.object(self.speed, "get_speed", return_value=({}, set())),
                patch.object(self.speed, "load_keep_hosts", return_value=set()),
                patch.object(self.speed, "ProxyFailureDB", return_value=failure_db),
                patch.object(self.speed.time, "time", return_value=outcome_write_time),
            ):
                self.speed.test_latency_speed(failure_cooldown_anchor=run_started_at)

            self.assertEqual(
                failure_db.get_cooldown_until("dead.example"),
                run_started_at + 23 * 60 * 60,
            )
            self.assertFalse(failure_db.should_filter("dead.example", now=next_daily_run))
            self.assertLess(
                failure_db.get_cooldown_until("dead.example"),
                outcome_write_time + 23 * 60 * 60,
            )

    def test_semantic_group_rebuild_ignores_order_and_removes_test_groups(self):
        old = {"old-a", "old-b"}
        final = ["0100 - 2.00 - new-a", "0200 - 0.25 - new-b"]
        config = {
            "proxy-groups": [
                {"name": "static", "type": "select", "proxies": ["DIRECT"]},
                {"name": "select Group 1", "type": "select", "proxies": ["old-a"]},
                {"name": "service", "type": "select", "proxies": ["static", "old-b"]},
                {"name": "balance", "type": "load-balance", "proxies": ["old-a", "old-b"]},
            ]
        }
        self.speed.rebuild_proxy_groups(
            config,
            old,
            final,
            {final[0]: 2.0, final[1]: 0.25},
        )
        self.assertEqual([group["name"] for group in config["proxy-groups"]], ["static", "service", "balance"])
        self.assertEqual(config["proxy-groups"][1]["proxies"], ["static", *final])
        self.assertEqual(config["proxy-groups"][2]["proxies"], [final[0]])

    def test_na_is_stripped_on_rerun_sorted_last_and_excluded_from_load_balance(self):
        old = {"old-a", "old-b"}
        numeric = "0100 - 0.00 - numeric"
        unavailable = "0050 - N/A - unavailable"
        config = {
            "proxy-groups": [
                {"name": "select", "type": "select", "proxies": ["old-a", "old-b"]},
                {"name": "balance", "type": "load-balance", "proxies": ["old-a", "old-b"]},
            ]
        }
        self.args.speed_load_balance_min_mibps = 0.0
        self.speed.rebuild_proxy_groups(
            config,
            old,
            [numeric, unavailable],
            {numeric: 0.0, unavailable: None},
        )
        self.assertEqual(config["proxy-groups"][0]["proxies"], [numeric, unavailable])
        self.assertEqual(config["proxy-groups"][1]["proxies"], [numeric])
        self.assertEqual(self.speed.original_name("0050 - N/A - original"), "original")
        self.assertGreater(
            self.speed.score_from_name(unavailable),
            self.speed.score_from_name(numeric),
        )

    def test_all_na_load_balance_falls_back_to_direct(self):
        unavailable = "0050 - N/A - unavailable"
        config = {
            "proxy-groups": [
                {"name": "balance", "type": "load-balance", "proxies": ["old"]},
            ]
        }
        self.speed.rebuild_proxy_groups(config, {"old"}, [unavailable], {unavailable: None})
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["DIRECT"])

    def test_load_balance_uses_raw_speed_not_rounded_name(self):
        rounded_up = "0050 - 1.00 - rounded-up"
        qualifies = "0060 - 1.00 - qualifies"
        config = {
            "proxy-groups": [
                {"name": "balance", "type": "load-balance", "proxies": ["old"]},
            ]
        }
        self.args.speed_load_balance_min_mibps = 1.0
        self.speed.rebuild_proxy_groups(
            config,
            {"old"},
            [rounded_up, qualifies],
            {rounded_up: 0.996, qualifies: 1.0},
        )
        self.assertEqual(config["proxy-groups"][0]["proxies"], [qualifies])

    def test_semantic_group_rebuild_rejects_dangling_reference(self):
        config = {"proxy-groups": [{"name": "g", "type": "select", "proxies": ["missing"]}]}
        with self.assertRaisesRegex(ValueError, "dangling"):
            self.speed.rebuild_proxy_groups(config, set(), [], {})


if __name__ == "__main__":
    unittest.main()
