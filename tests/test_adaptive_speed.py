import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class FakeResponse:
    def __init__(self, chunks, url="https://speed.example/download", status_code=200, headers=None):
        self.chunks = chunks
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

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
        meta_start_command="mihomo -d profiles",
    )
    module.test_args = SimpleNamespace(
        speed_test_mode="sdk",
        speed_test_url="https://speed.example/download?bytes={bytes}",
        speed_test_retry=1,
        speedtest_call_timeout=300,
        speed_http_sizes_mb=[1, 4, 8, 16, 32],
        speed_http_min_duration=3.0,
        speed_http_trials=3,
        speed_http_percentile=0.25,
        speed_http_connect_timeout=10.0,
        speed_http_read_timeout=15.0,
        speed_http_max_transfer_seconds=30.0,
        speed_http_chunk_size_kb=64,
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
        cls.api = importlib.import_module("common.api")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("common.api", None)
        cls.args_patch.stop()

    def setUp(self):
        self.args = self.args_module.test_args
        self.args.speed_test_mode = "sdk"
        self.args.speed_test_url = "https://speed.example/download?bytes={bytes}"
        self.args.speed_http_sizes_mb = [1, 4, 8, 16, 32]
        self.args.speed_http_min_duration = 3.0
        self.args.speed_http_trials = 3
        self.args.speed_http_percentile = 0.25
        self.args.speed_http_connect_timeout = 10.0
        self.args.speed_http_read_timeout = 15.0
        self.args.speed_http_max_transfer_seconds = 30.0
        self.args.speed_http_chunk_size_kb = 64

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
        timings = [10.0, 12.0, 12.0, 12.5, 14.0]

        with patch.object(self.api.requests, "get", return_value=response) as request, patch.object(
            self.api.time, "perf_counter", side_effect=timings
        ):
            sample = self.api.download_sample(size_bytes)

        self.assertEqual(sample.size_bytes, size_bytes)
        self.assertEqual(sample.ttfb_ms, 2000.0)
        self.assertEqual(sample.body_seconds, 2.0)
        self.assertEqual(sample.goodput_mibps, 0.5)
        _, kwargs = request.call_args
        self.assertEqual(
            kwargs["proxies"],
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertEqual(kwargs["timeout"], (10.0, 15.0))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertIn(str(size_bytes), request.call_args.args[0])

    def test_download_sample_rejects_short_body_and_https_downgrade(self):
        size_bytes = self.api.MEBIBYTE
        timings = [0.0, 0.1, 0.1, 0.2, 0.3]
        with patch.object(
            self.api.requests, "get", return_value=FakeResponse([b"short"])
        ), patch.object(self.api.time, "perf_counter", side_effect=timings):
            with self.assertRaisesRegex(ValueError, "returned 5"):
                self.api.download_sample(size_bytes)

        downgrade = FakeResponse([b"x" * size_bytes], url="http://speed.example/download")
        with patch.object(self.api.requests, "get", return_value=downgrade), patch.object(
            self.api.time, "perf_counter", side_effect=[0.0, 0.1]
        ):
            with self.assertRaisesRegex(ValueError, "non-HTTPS"):
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
            self.api.time, "perf_counter", side_effect=[0.0, 0.1, 0.1, 0.2, 1.1]
        ):
            self.api.download_sample(size_bytes)
        self.assertEqual(request.call_args.kwargs["headers"]["Range"], f"bytes=0-{size_bytes - 1}")

        ignored_range = FakeResponse([b"x" * size_bytes])
        with patch.object(self.api.requests, "get", return_value=ignored_range), patch.object(
            self.api.time, "perf_counter", side_effect=[0.0, 0.1]
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

        self.assertEqual(score, 6.0)
        called_sizes = [call.args[0] // self.api.MEBIBYTE for call in download.call_args_list]
        self.assertEqual(called_sizes, [1, 4, 8, 16, 16, 16])

    def test_adaptive_failure_is_retried_at_same_size_and_penalized(self):
        samples = [
            self.sample(1, 0.2, 5.0),
            None,
            self.sample(4, 1.0, 4.0),
            self.sample(4, 0.5, 8.0),
        ]
        with patch.object(self.api, "try_download_sample", side_effect=samples) as download:
            score = self.api.adaptive_download_speed()

        self.assertAlmostEqual(score, (2 / 3) * 5.0)
        called_sizes = [call.args[0] // self.api.MEBIBYTE for call in download.call_args_list]
        self.assertEqual(called_sizes, [1, 4, 4, 4])

    def test_speed_mode_dispatch_keeps_sdk_as_default(self):
        response = SimpleNamespace(status_code=204, text="")
        with patch.object(self.api, "put", return_value=response), patch.object(
            self.api, "test_download_speedtest", return_value=7.0
        ) as sdk, patch.object(self.api, "test_download_adaptive", return_value=9.0) as adaptive:
            self.assertEqual(self.api.test_speed_single("node"), 7.0)
            sdk.assert_called_once_with()
            adaptive.assert_not_called()

            self.args.speed_test_mode = "adaptive"
            self.assertEqual(self.api.test_speed_single("node"), 9.0)
            adaptive.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
