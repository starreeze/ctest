# -*- coding: utf-8 -*-
# @Date    : 2025-04-02 10:11:24
# @Author  : Shangyu.Xing (starreeze@foxmail.com)
"interface with the clash meta core & speedtest api"
import os
import subprocess
import time
from dataclasses import dataclass
from math import ceil, floor
from typing import cast
from urllib.parse import urlsplit

import requests
import speedtest
from func_timeout import FunctionTimedOut, func_set_timeout
from iterwrap import retry_dec

from common.args import config_args, logger, test_args

header = {"Authorization": f"Bearer {config_args.controller_password}"}
MEBIBYTE = 1024 * 1024


def get(*args, **kwargs):
    return requests.get(*args, **kwargs, headers=header)


def put(*args, **kwargs):
    return requests.put(*args, **kwargs, headers=header)


def post(*args, **kwargs):
    return requests.post(*args, **kwargs, headers=header)


@dataclass(frozen=True)
class DownloadSample:
    size_bytes: int
    body_seconds: float
    ttfb_ms: float
    goodput_mibps: float


def percentile(values: list[float], quantile: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sample."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = floor(position)
    upper = ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def validate_adaptive_speed_config() -> list[int]:
    sizes_mb = test_args.speed_http_sizes_mb
    if not sizes_mb or any(size <= 0 for size in sizes_mb):
        raise ValueError("speed_http_sizes_mb must contain positive sizes")
    if sizes_mb != sorted(set(sizes_mb)):
        raise ValueError("speed_http_sizes_mb must be unique and increasing")
    if test_args.speed_http_trials <= 0:
        raise ValueError("speed_http_trials must be positive")
    if not 0 <= test_args.speed_http_percentile <= 1:
        raise ValueError("speed_http_percentile must be between 0 and 1")
    if test_args.speed_http_min_duration <= 0 or test_args.speed_http_max_transfer_seconds <= 0:
        raise ValueError("adaptive speed durations must be positive")
    if test_args.speed_http_min_duration > test_args.speed_http_max_transfer_seconds:
        raise ValueError("speed_http_min_duration cannot exceed speed_http_max_transfer_seconds")
    if test_args.speed_http_connect_timeout <= 0 or test_args.speed_http_read_timeout <= 0:
        raise ValueError("adaptive speed timeouts must be positive")
    if test_args.speed_http_chunk_size_kb <= 0:
        raise ValueError("speed_http_chunk_size_kb must be positive")
    return [size * MEBIBYTE for size in sizes_mb]


def download_sample(size_bytes: int) -> DownloadSample:
    """Download exactly size_bytes through the selected local proxy and measure body goodput."""
    url_template = test_args.speed_test_url
    if urlsplit(url_template).scheme != "https":
        raise ValueError("adaptive speed_test_url must use HTTPS")
    headers = {"Accept-Encoding": "identity", "Cache-Control": "no-cache"}
    uses_range = "{bytes}" not in url_template
    if not uses_range:
        url = url_template.format(bytes=size_bytes)
    else:
        url = url_template
        headers["Range"] = f"bytes=0-{size_bytes - 1}"

    proxies = {"http": config_args.proxy_url, "https": config_args.proxy_url}
    request_started = time.perf_counter()
    with requests.get(
        url,
        stream=True,
        proxies=proxies,
        headers=headers,
        params={"cachebuster": str(time.time_ns())},
        timeout=(test_args.speed_http_connect_timeout, test_args.speed_http_read_timeout),
        allow_redirects=False,
    ) as response:
        headers_received = time.perf_counter()
        response.raise_for_status()
        if response.status_code // 100 != 2:
            raise ValueError(f"adaptive speed test returned HTTP {response.status_code}")
        if uses_range:
            expected_range = f"bytes 0-{size_bytes - 1}/"
            if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(
                expected_range
            ):
                raise ValueError("adaptive fixed-file endpoint did not honor the requested byte range")
        if urlsplit(response.url).scheme != "https":
            raise ValueError("adaptive speed test redirected to a non-HTTPS URL")
        downloaded = 0
        body_started = time.perf_counter()
        for chunk in response.iter_content(chunk_size=test_args.speed_http_chunk_size_kb * 1024):
            if time.perf_counter() - body_started > test_args.speed_http_max_transfer_seconds:
                raise TimeoutError(
                    f"adaptive download exceeded {test_args.speed_http_max_transfer_seconds:.1f}s"
                )
            if not chunk:
                continue
            downloaded += min(len(chunk), size_bytes - downloaded)
            if downloaded == size_bytes:
                break
        body_seconds = time.perf_counter() - body_started

    if downloaded != size_bytes:
        raise ValueError(f"adaptive download returned {downloaded} of {size_bytes} requested bytes")
    if body_seconds > test_args.speed_http_max_transfer_seconds:
        raise TimeoutError(f"adaptive download exceeded {test_args.speed_http_max_transfer_seconds:.1f}s")
    if body_seconds <= 0:
        raise ValueError("adaptive download body duration must be positive")
    return DownloadSample(
        size_bytes=size_bytes,
        body_seconds=body_seconds,
        ttfb_ms=(headers_received - request_started) * 1000,
        goodput_mibps=downloaded / body_seconds / MEBIBYTE,
    )


def try_download_sample(size_bytes: int) -> DownloadSample | None:
    try:
        sample = download_sample(size_bytes)
    except KeyboardInterrupt:
        raise
    except (requests.RequestException, TimeoutError, ValueError) as e:
        logger.warning(f"Adaptive download of {size_bytes / MEBIBYTE:.0f} MiB failed: {e}")
        return None
    logger.info(
        f"Adaptive download {size_bytes / MEBIBYTE:.0f} MiB: "
        f"{sample.goodput_mibps:.2f} MiB/s, TTFB {sample.ttfb_ms:.0f} ms, "
        f"body {sample.body_seconds:.2f} s"
    )
    return sample


def adaptive_download_speed() -> float:
    """Return availability-weighted lower-percentile goodput in MiB/s."""
    sizes = validate_adaptive_speed_config()
    selected_size = sizes[0]
    selected_samples: list[DownloadSample | None] = []
    for size_bytes in sizes:
        sample = try_download_sample(size_bytes)
        if sample is None:
            selected_size = size_bytes
            selected_samples = [None]
            break

        selected_size = size_bytes
        if sample.body_seconds >= test_args.speed_http_min_duration or size_bytes == sizes[-1]:
            selected_samples = [sample]
            break

    while len(selected_samples) < test_args.speed_http_trials:
        selected_samples.append(try_download_sample(selected_size))

    successful = [sample for sample in selected_samples if sample is not None]
    if not successful:
        return 0.0

    availability = len(successful) / len(selected_samples)
    lower_goodput = percentile(
        [sample.goodput_mibps for sample in successful], test_args.speed_http_percentile
    )
    median_ttfb = percentile([sample.ttfb_ms for sample in successful], 0.5)
    reliable_goodput = availability * lower_goodput
    logger.info(
        f"Adaptive result at {selected_size / MEBIBYTE:.0f} MiB: "
        f"availability {availability:.0%}, p{test_args.speed_http_percentile * 100:g} "
        f"{lower_goodput:.2f} MiB/s, median TTFB {median_ttfb:.0f} ms, "
        f"reliable goodput {reliable_goodput:.2f} MiB/s"
    )
    return reliable_goodput


@func_set_timeout(test_args.speedtest_call_timeout)
def call_adaptive_speedtest() -> float:
    return adaptive_download_speed()


def test_download_adaptive() -> float:
    try:
        return call_adaptive_speedtest()
    except FunctionTimedOut:
        logger.warning("Adaptive speed test exceeded the total call timeout")
        return 0.0


@retry_dec(test_args.speed_test_retry)
@func_set_timeout(test_args.speedtest_call_timeout)
def call_speedtest() -> float:
    st = speedtest.Speedtest()
    return st.download()


def test_download_speedtest() -> float:
    try:
        bps = call_speedtest()
    except KeyboardInterrupt as e:
        raise e
    except BaseException as e:
        logger.warning(f"Error during speedtest: {e}")
        return 0
    mib_per_second = cast(float, bps) / (MEBIBYTE * 8)
    return mib_per_second


def test_speed_single(name: str):
    url = config_args.controller_url + f"/proxies/{config_args.target_group}"
    response = put(url, json={"name": name})
    if response.status_code // 100 != 2 or response.text:
        logger.error(f"Failed to set proxy {name}: {response.text}")
        return 0.0
    if test_args.speed_test_mode == "sdk":
        return test_download_speedtest()
    if test_args.speed_test_mode == "adaptive":
        return test_download_adaptive()
    raise ValueError(f"Unsupported speed test mode: {test_args.speed_test_mode}")


@retry_dec(test_args.test_latency_retry)
@func_set_timeout(test_args.latency_call_timeout)
def get_latency_once(url: str, group_name) -> dict[str, int]:
    url = (
        config_args.controller_url
        + f"/group/{group_name}/delay?url={url}&timeout={test_args.latency_timeout}"
    )
    return get(url).json()


def get_latency(proxies: list[str], group_name: str) -> dict[str, int]:
    "return valid proxy names and their latency in ms"
    latency = {name: 0 for name in proxies}
    i, total = 1, test_args.latency_test_times * len(test_args.latency_test_urls)
    for _ in range(test_args.latency_test_times):
        for url in test_args.latency_test_urls:
            logger.info(f"[{i}/{total}] Testing latency for group '{group_name}'...")
            try:
                new_latency = cast(dict, get_latency_once(url, group_name))
            except KeyboardInterrupt:
                logger.warning("KeyboardInterrupt detected, exiting...")
                exit(1)
            except Exception as e:
                logger.error(f"Error during latency test: {e}")
                continue
            if "timeout" in new_latency.get("message", ""):
                logger.info(f"[{i}/{total}] valid_count=0")
                i += 1
                continue
            valid_count = sum(1 if value < test_args.latency_timeout else 0 for value in new_latency.values())
            logger.info(f"[{i}/{total}] {valid_count=}")
            for key, value in latency.items():
                latency[key] = max(value, new_latency.get(key, test_args.latency_timeout))
            i += 1
    return {key: value for key, value in latency.items() if 0 < value < test_args.latency_timeout}


def get_speed(latencies: list[tuple[str, int]]) -> dict[str, tuple[float, int]]:
    "return valid proxy names and their download speed in MiB/s and latency in ms"
    speed_latency: dict[str, tuple[float, int]] = {}
    num_success = 0
    try:
        for i, (name, latency) in enumerate(latencies):
            if test_args.test_speed:
                if num_success >= test_args.max_num:
                    break
                logger.debug(f"Testing proxy {name}. Latency: {latency}ms\n")
                logger.info(
                    f"Progress: Success - [{num_success}/{test_args.max_num}]; All - [{i+1}/{len(latencies)}]"
                )
                speed = test_speed_single(name)
                logger.info(f"Speed for {name}: {speed:.2f} MiB/s")
                if speed >= test_args.load_balance_thres:
                    num_success += 1
            else:
                try:
                    speed = float(name.split(" - ")[1])
                except (ValueError, IndexError):
                    speed = 0.0
            speed_latency[name] = (speed, latency)
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt detected, saving current results...")
    return speed_latency


@retry_dec(test_args.test_latency_retry)
@func_set_timeout(test_args.core_restart_timeout)
def restart_core():
    "send POST to restart clash meta core"
    resp = post(config_args.controller_url + "/restart")
    if resp.status_code != 200:
        logger.error(f"The response code is not successful when restarting the core: {resp.text}")
        raise RuntimeError()
    logger.info("Core restarted.")


class MetaLifecycle:
    def __init__(self):
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(config_args.meta_start_command, shell=True)
        time.sleep(10)

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait()
            self.process = None
        os.system("pkill -f mihomo")
