# -*- coding: utf-8 -*-
# @Date    : 2025-04-02 10:11:24
# @Author  : Shangyu.Xing (starreeze@foxmail.com)
"interface with the clash meta core & speedtest api"
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from math import ceil, floor
from typing import cast
from urllib.parse import quote, urljoin, urlsplit

import requests
import speedtest
import yaml
from func_timeout import FunctionTimedOut, func_set_timeout
from iterwrap import retry_dec
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from common.args import config_args, logger, test_args
from common.utils import dump_yaml

header = {"Authorization": f"Bearer {config_args.controller_password}"}
controller_session = requests.Session()
controller_session.trust_env = False
MEBIBYTE = 1024 * 1024
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def get(*args, **kwargs):
    kwargs.setdefault("timeout", config_args.controller_timeout)
    return controller_session.get(*args, **kwargs, headers=header)


def put(*args, **kwargs):
    kwargs.setdefault("timeout", config_args.controller_timeout)
    return controller_session.put(*args, **kwargs, headers=header)


def post(*args, **kwargs):
    kwargs.setdefault("timeout", config_args.controller_timeout)
    return controller_session.post(*args, **kwargs, headers=header)


def patch(*args, **kwargs):
    kwargs.setdefault("timeout", config_args.controller_timeout)
    return controller_session.patch(*args, **kwargs, headers=header)


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
    if test_args.speed_http_connect_overhead < 0:
        raise ValueError("speed_http_connect_overhead cannot be negative")
    if config_args.min_speed_threshold_kbps <= 0:
        raise ValueError("min_speed_threshold_kbps must be positive")
    if test_args.speed_http_chunk_size_kb <= 0:
        raise ValueError("speed_http_chunk_size_kb must be positive")
    if not 0 < test_args.speed_http_ramp_fail_factor <= 1:
        raise ValueError("speed_http_ramp_fail_factor must be in (0, 1]")
    return [size * MEBIBYTE for size in sizes_mb]


def probe_time_limit_seconds(size_bytes: int) -> float:
    """Wall-clock budget: connect/TTFB overhead plus time to finish at the failure-DB speed floor."""
    if size_bytes <= 0:
        raise ValueError("probe size must be positive")
    rate_bytes = config_args.min_speed_threshold_kbps * 1024
    return min(
        test_args.speed_http_connect_overhead + size_bytes / rate_bytes,
        test_args.speed_http_max_transfer_seconds,
    )


def _set_socket_timeout(response: requests.Response, seconds: float) -> None:
    connection = getattr(response.raw, "connection", None)
    sock = getattr(connection, "sock", None) if connection is not None else None
    if sock is not None:
        sock.settimeout(max(seconds, 0.001))


def open_adaptive_download(
    url: str, headers: dict[str, str], time_limit: float, started_at: float
) -> requests.Response:
    """GET url through the local proxy, following at most one HTTPS redirect."""
    remaining = time_limit - (time.perf_counter() - started_at)
    if remaining <= 0:
        raise TimeoutError(f"adaptive download exceeded {time_limit:.1f}s")
    proxies = {"http": config_args.proxy_url, "https": config_args.proxy_url}
    read_timeout = min(test_args.speed_http_read_timeout, remaining)
    connect_timeout = min(test_args.speed_http_connect_timeout, remaining)
    request_kwargs = {
        "stream": True,
        "proxies": proxies,
        "headers": headers,
        "params": {"cachebuster": str(time.time_ns())},
        "timeout": (connect_timeout, read_timeout),
        "allow_redirects": False,
    }
    response = requests.get(url, **request_kwargs)
    if response.status_code not in REDIRECT_STATUSES:
        return response
    location = response.headers.get("Location")
    current_url = response.url
    response.close()
    if not location:
        raise ValueError("adaptive speed test redirect missing Location")
    redirect_url = urljoin(current_url, location)
    if urlsplit(redirect_url).scheme != "https":
        raise ValueError("adaptive speed test redirected to a non-HTTPS URL")
    remaining = time_limit - (time.perf_counter() - started_at)
    if remaining <= 0:
        raise TimeoutError(f"adaptive download exceeded {time_limit:.1f}s")
    request_kwargs["timeout"] = (
        min(test_args.speed_http_connect_timeout, remaining),
        min(test_args.speed_http_read_timeout, remaining),
    )
    response = requests.get(redirect_url, **request_kwargs)
    if response.status_code in REDIRECT_STATUSES:
        response.close()
        raise ValueError("adaptive speed test exceeded one redirect")
    return response


def download_sample(size_bytes: int) -> DownloadSample:
    """Download exactly size_bytes through the selected local proxy and measure body goodput."""
    url_template = test_args.speed_test_url
    parsed_template = urlsplit(url_template)
    if parsed_template.scheme != "https":
        raise ValueError("adaptive speed_test_url must use HTTPS")
    headers = {
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Referer": f"{parsed_template.scheme}://{parsed_template.netloc}/",
    }
    uses_range = "{bytes}" not in url_template
    if not uses_range:
        url = url_template.format(bytes=size_bytes)
    else:
        url = url_template
        headers["Range"] = f"bytes=0-{size_bytes - 1}"

    request_started = time.perf_counter()
    time_limit = probe_time_limit_seconds(size_bytes)
    response = open_adaptive_download(url, headers, time_limit, request_started)
    try:
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
        chunk_size = test_args.speed_http_chunk_size_kb * 1024
        while downloaded < size_bytes:
            remaining = time_limit - (time.perf_counter() - request_started)
            if remaining <= 0:
                raise TimeoutError(f"adaptive download exceeded {time_limit:.1f}s")
            _set_socket_timeout(response, min(test_args.speed_http_read_timeout, remaining))
            chunk = response.raw.read(min(chunk_size, size_bytes - downloaded))
            if not chunk:
                break
            downloaded += len(chunk)
        body_seconds = time.perf_counter() - body_started
    finally:
        response.close()

    if downloaded != size_bytes:
        raise ValueError(f"adaptive download returned {downloaded} of {size_bytes} requested bytes")
    if (headers_received - request_started) + body_seconds > time_limit:
        raise TimeoutError(f"adaptive download exceeded {time_limit:.1f}s")
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
    except (requests.RequestException, Urllib3HTTPError, TimeoutError, ValueError, OSError) as e:
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
    last_success: DownloadSample | None = None
    ramp_failed = False
    for size_bytes in sizes:
        sample = try_download_sample(size_bytes)
        if sample is None:
            if last_success is not None:
                selected_size = last_success.size_bytes
                selected_samples = [last_success]
                ramp_failed = True
                logger.info(
                    f"Adaptive ramp failed at {size_bytes / MEBIBYTE:.0f} MiB; "
                    f"falling back to {selected_size / MEBIBYTE:.0f} MiB"
                )
            else:
                selected_size = size_bytes
                selected_samples = [None]
            break

        last_success = sample
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
    if ramp_failed:
        reliable_goodput *= test_args.speed_http_ramp_fail_factor
    logger.info(
        f"Adaptive result at {selected_size / MEBIBYTE:.0f} MiB: "
        f"availability {availability:.0%}, p{test_args.speed_http_percentile * 100:g} "
        f"{lower_goodput:.2f} MiB/s, median TTFB {median_ttfb:.0f} ms, "
        f"ramp-fail factor {test_args.speed_http_ramp_fail_factor if ramp_failed else 1:g}, "
        f"reliable goodput {reliable_goodput:.2f} MiB/s"
    )
    return reliable_goodput


@func_set_timeout(test_args.speedtest_call_timeout)
def call_adaptive_speedtest() -> float:
    return adaptive_download_speed()


def test_download_adaptive() -> float:
    try:
        return call_adaptive_speedtest()
    except KeyboardInterrupt:
        raise
    except FunctionTimedOut:
        logger.warning("Adaptive speed test exceeded the total call timeout")
        return 0.0
    except Exception as e:
        logger.warning(f"Adaptive speed test failed: {e}")
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


def get_core_mode() -> str:
    response = get(config_args.controller_url + "/configs")
    response.raise_for_status()
    return response.json()["mode"]


def set_core_mode(mode: str) -> None:
    response = patch(config_args.controller_url + "/configs", json={"mode": mode})
    if response.status_code // 100 != 2:
        raise RuntimeError(f"Failed to set core mode {mode}: {response.text}")
    logger.info(f"Core mode set to {mode}")


def select_proxy(name: str, group: str) -> bool:
    url = config_args.controller_url + f"/proxies/{quote(group, safe='')}"
    response = put(url, json={"name": name})
    if response.status_code // 100 != 2:
        logger.error(f"Failed to set proxy {name} on {group}: {response.text}")
        return False
    return True


def test_speed_single(name: str):
    if not select_proxy(name, config_args.global_group):
        return 0.0
    if not select_proxy(name, config_args.target_group):
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


def get_speed(
    latencies: list[tuple[str, int]], name_to_host: dict[str, str]
) -> dict[str, tuple[float, int]]:
    """Return the first positive-throughput endpoint per host, tried by latency."""
    candidates: dict[str, list[tuple[str, int]]] = {}
    for name, latency in sorted(latencies, key=lambda item: item[1]):
        candidates.setdefault(name_to_host[name], []).append((name, latency))
    speed_latency: dict[str, tuple[float, int]] = {}
    previous_mode: str | None = None
    if test_args.test_speed:
        previous_mode = get_core_mode()
        if previous_mode != "global":
            set_core_mode("global")
    try:
        for host_index, (host, endpoints) in enumerate(candidates.items(), 1):
            logger.info(
                f"Testing host {host_index}/{len(candidates)} ({host}) with {len(endpoints)} latency-valid endpoint(s)"
            )
            for name, latency in endpoints:
                if test_args.test_speed:
                    speed = test_speed_single(name)
                    logger.info(f"Speed for {name}: {speed:.2f} MiB/s")
                    if speed <= 0:
                        continue
                else:
                    speed = 0.0
                speed_latency[name] = (speed, latency)
                break
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt detected, saving current results...")
    finally:
        if previous_mode is not None and previous_mode != "global":
            set_core_mode(previous_mode)
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
        self.test_profile_path: str | None = None

    def start(self, profile_path: str) -> None:
        with open(profile_path, encoding="utf-8") as profile_file:
            profile = yaml.safe_load(profile_file)

        proxy_url = urlsplit(config_args.proxy_url)
        controller_url = urlsplit(config_args.controller_url)
        if proxy_url.scheme != "http" or not proxy_url.port:
            raise ValueError("meta mode requires an http proxy_url with an explicit port")
        if controller_url.scheme != "http" or not controller_url.hostname or not controller_url.port:
            raise ValueError("meta mode requires an http controller_url with an explicit host and port")

        for key in ("mixed-port", "socks-port", "redir-port", "tproxy-port"):
            profile[key] = 0
        profile["port"] = proxy_url.port
        profile["allow-lan"] = False
        profile["bind-address"] = "127.0.0.1"
        profile["external-controller"] = f"{controller_url.hostname}:{controller_url.port}"
        profile["secret"] = config_args.controller_password
        if isinstance(profile.get("dns"), dict):
            profile["dns"]["enable"] = False
        if isinstance(profile.get("tun"), dict):
            profile["tun"]["enable"] = False

        fd, self.test_profile_path = tempfile.mkstemp(prefix="clash-test-core-", suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as test_profile:
            test_profile.write(dump_yaml(profile))

        self.process = subprocess.Popen(
            [*shlex.split(config_args.meta_start_command), "-f", self.test_profile_path],
            start_new_session=True,
        )
        deadline = time.time() + max(test_args.core_restart_timeout, 30)
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"mihomo exited with code {self.process.returncode} before the controller was ready")
            try:
                resp = controller_session.get(
                    f"{config_args.controller_url}/version", headers=header, timeout=1
                )
                if resp.ok:
                    logger.info("Mihomo controller is ready")
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError("mihomo did not become ready")

    def stop(self) -> None:
        process = self.process
        self.process = None
        try:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        finally:
            if self.test_profile_path is not None:
                os.unlink(self.test_profile_path)
                self.test_profile_path = None
