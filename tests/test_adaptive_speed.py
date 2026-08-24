import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


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
        proxy_url="http://127.0.0.1:7890",
        target_group="select",
        global_group="GLOBAL",
        meta_start_command="mihomo -d profiles",
        min_speed_threshold_kbps=512,
        discard=True,
        profile_remote_url_path="urls.txt",
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
        max_num=3,
        load_balance_thres=0.5,
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
        self.args.max_num = 3
        self.args_module.config_args.min_speed_threshold_kbps = 512

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
        self.assertEqual(self.api.probe_time_limit_seconds(1 * mebi), 7.0)
        self.assertEqual(self.api.probe_time_limit_seconds(4 * mebi), 13.0)
        self.assertEqual(self.api.probe_time_limit_seconds(8 * mebi), 21.0)
        self.assertEqual(self.api.probe_time_limit_seconds(16 * mebi), 30.0)

    def test_config_rejects_invalid_ladder_and_duration(self):
        self.args.speed_http_sizes_mb = [1, 8, 4]
        with self.assertRaisesRegex(ValueError, "unique and increasing"):
            self.api.validate_adaptive_speed_config()

        self.args.speed_http_sizes_mb = [1, 4]
        self.args.speed_http_min_duration = 31.0
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
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
        self.assertEqual(kwargs["timeout"], (7.0, 7.0))
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

    def test_adaptive_all_failures_return_zero(self):
        with patch.object(self.api, "try_download_sample", return_value=None) as download:
            self.assertEqual(self.api.adaptive_download_speed(), 0.0)
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

    def test_adaptive_call_timeout_returns_zero(self):
        with patch.object(self.api, "call_adaptive_speedtest", side_effect=self.api.FunctionTimedOut("timeout")):
            self.assertEqual(self.api.test_download_adaptive(), 0.0)

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
            result = self.api.get_speed([("node-a", 120)])

        self.assertEqual(result["node-a"], (1.0, 120))
        self.assertEqual(mode.call_args_list[0].kwargs["json"], {"mode": "global"})
        self.assertEqual(mode.call_args_list[-1].kwargs["json"], {"mode": "rule"})
        selected_groups = [call.args[0].rsplit("/", 1)[-1] for call in select.call_args_list]
        self.assertEqual(selected_groups, ["GLOBAL", "select"])

    def test_persist_speed_failures_skips_only_when_every_node_aborted(self):
        self.args.speed_test_mode = "adaptive"
        self.assertTrue(self.speed.should_persist_speed_failures(10, 9))
        self.assertFalse(self.speed.should_persist_speed_failures(10, 10))
        self.assertTrue(self.speed.should_persist_speed_failures(10, 3))
        self.assertTrue(self.speed.should_persist_speed_failures(2, 2))
        self.assertFalse(self.speed.should_persist_speed_failures(10, 0))
        self.args.speed_test_mode = "sdk"
        self.assertTrue(self.speed.should_persist_speed_failures(10, 10))

    def test_keep_hosts_survive_latency_discard(self):
        timeout = self.args.latency_timeout
        keep = {"name": f"{timeout} - 0.00 - pin", "server": "10.0.0.1"}
        drop = {"name": f"{timeout} - 0.00 - other", "server": "10.0.0.2"}
        ok = {"name": "0100 - 1.50 - fast", "server": "10.0.0.3"}
        pinned = {"10.0.0.1"}
        self.assertTrue(self.speed.should_retain_proxy(keep, pinned))
        self.assertFalse(self.speed.should_retain_proxy(drop, pinned))
        self.assertTrue(self.speed.should_retain_proxy(ok, pinned))
        self.args_module.config_args.discard = False
        self.assertTrue(self.speed.should_retain_proxy(drop, set()))
        self.args_module.config_args.discard = True


if __name__ == "__main__":
    unittest.main()
