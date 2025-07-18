# -*- coding: utf-8 -*-
# @Date    : 2025-04-02 10:11:24
# @Author  : Shangyu.Xing (starreeze@foxmail.com)
"interface with the clash meta core & speedtest api"
import time
from typing import cast

import requests
import speedtest
from func_timeout import func_set_timeout
from iterwrap import retry_dec

from common.args import config_args, logger, test_args


def test_download_url(url, duration, window_size, proxies) -> float:
    start_time = time.time()
    downloaded = 0
    download_speeds = []

    try:
        with requests.get(url, stream=True, proxies=proxies) as response:
            response.raise_for_status()

            for data in response.iter_content(chunk_size=4096):
                downloaded += len(data)
                current_time = time.time()
                elapsed_time = current_time - start_time

                # Save the downloaded amount and timestamp at regular intervals
                download_speeds.append((elapsed_time, downloaded))
                logger.debug(f"current average speed: {downloaded / elapsed_time / (1024 * 1024):.2f} MB/s")

                # Stop downloading after the specified duration
                if elapsed_time > duration:
                    break
    except KeyboardInterrupt as e:
        raise e
    except Exception as e:
        logger.warning(f"Error during download: {e}")
        return 0

    # Calculate the maximum average speed over the specified window size
    max_avg_speed = 0
    for i in range(len(download_speeds)):
        window_end_time = download_speeds[i][0] + window_size
        window_data = [x for x in download_speeds if x[0] <= window_end_time]
        if len(window_data) > 1:
            time_span = window_data[-1][0] - window_data[0][0]
            data_span = window_data[-1][1] - window_data[0][1]
            avg_speed = data_span / time_span / (1024 * 1024)  # MB/s
            max_avg_speed = max(max_avg_speed, avg_speed)

    logger.info(f"Maximum average download speed over {window_size} seconds: {max_avg_speed:.2f} MB/s")
    return max_avg_speed


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
    MBps = cast(float, bps) / (1024 * 1024 * 8)
    return MBps


def test_speed_single(name: str):
    url = config_args.controller_url + "/proxies/🔰 节点选择"
    response = requests.put(url, json={"name": name})
    if response.status_code // 100 != 2 or response.text:
        logger.error(f"Failed to set proxy {name}: {response.text}")
        return 0.0
    else:
        # return test_download_url(test_args.speed_test_url, test_args.speed_duration, test_args.speed_window_size, test_args.proxies)
        return test_download_speedtest()


@retry_dec(test_args.test_latency_retry)
@func_set_timeout(test_args.latency_call_timeout)
def get_latency_once(url: str) -> dict[str, int]:
    url = (
        config_args.controller_url + f"/group/🔰 节点选择/delay?url={url}&timeout={test_args.latency_timeout}"
    )
    return requests.get(url).json()


def get_latency(proxies: list[str]) -> dict[str, int]:
    "return valid proxy names and their latency in ms"
    latency = {name: 0 for name in proxies}
    i, total = 1, test_args.latency_test_times * len(test_args.latency_test_urls)
    for _ in range(test_args.latency_test_times):
        for url in test_args.latency_test_urls:
            logger.info(f"[{i}/{total}] Testing latency...")
            try:
                new_latency = cast(dict, get_latency_once(url))
            except KeyboardInterrupt:
                logger.warning("KeyboardInterrupt detected, exiting...")
                exit(1)
            except Exception as e:
                logger.error(f"Error during latency test: {e}")
                continue
            for key, value in latency.items():
                latency[key] = max(value, new_latency.get(key, test_args.latency_timeout))
            i += 1
    return {key: value for key, value in latency.items() if value < test_args.latency_timeout}


def get_speed(latencies: list[tuple[str, int]]) -> dict[str, tuple[float, int]]:
    "return valid proxy names and their download speed in MB/s and latency in ms"
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
                logger.info(f"Speed for {name}: {speed:.2f} MB/s")
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
    resp = requests.post(config_args.controller_url + "/restart")
    if resp.status_code != 200:
        logger.error(f"The response code is not successful when restarting the core: {resp.text}")
        raise RuntimeError()
    logger.info("Core restarted.")
