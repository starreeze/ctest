# -*- coding: utf-8 -*-
import yaml
import time
import requests
from typing import Iterable
import speedtest
from func_timeout import func_set_timeout
from iterwrap import retry_dec
from args import logger, config_args, test_args


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
    except KeyboardInterrupt:
        exit(1)
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
    except KeyboardInterrupt:
        exit(1)
    except BaseException as e:
        logger.warning(f"Error during speedtest: {e}")
        return 0
    MBps = bps / (1024 * 1024 * 8)
    logger.info(f"speedtest download speed: {MBps:.2f} MB/s")
    return MBps


def get_speed(name: str):
    url = config_args.controller_url + f"/proxies/🔰 节点选择"
    response = requests.put(url, json={"name": name})
    if response.status_code // 100 != 2 or response.text:
        logger.error(f"Failed to set proxy {name}: {response.text}")
        return 0
    else:
        # return test_download_url(test_args.speed_test_url, test_args.speed_duration, test_args.speed_window_size, test_args.proxies)
        return test_download_speedtest()


@func_set_timeout(test_args.latency_call_timeout)
def get_latency_once() -> dict[str, int]:
    url = (
        config_args.controller_url
        + f"/group/🔰 节点选择/delay?url={test_args.latency_test_url}&timeout={test_args.latency_timeout}"
    )
    return requests.get(url).json()


def get_latency(proxies: list[str]) -> dict[str, int]:
    "return valid proxy names and their latency in ms"
    latency = {name: 0 for name in proxies}
    for i in range(test_args.latency_test_times):
        logger.info(f"[{i+1}/{test_args.latency_test_times}] Testing latency...")
        try:
            new_latency = get_latency_once()
        except BaseException as e:
            logger.error(f"Error during latency test: {e}")
            continue
        for key, value in latency.items():
            latency[key] = max(value, new_latency.get(key, test_args.latency_timeout))
    return {key: value for key, value in latency.items() if value < test_args.latency_timeout}


def replace_name(names: Iterable[str], info: dict[str, tuple[float, int]]) -> list[str]:
    new_names = []
    for name in names:
        if name in info:
            new_names.append(f"{info[name][1]:04d} - {info[name][0]:.2f} - {name.split(' - ')[-1]}")
        else:
            new_names.append(f"{test_args.latency_timeout} - 0.00 - {name.split(' - ')[-1]}")
    return new_names
    # return sorted(new_names, key=lambda x: int(x.split(" - ")[1]), reverse=True)


def main():
    with open(config_args.profile_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    proxies = [p["name"] for p in config["proxies"]]
    valid = get_latency(proxies)
    logger.info(f"get {len(valid)} valid proxies.")
    if len(valid) > test_args.max_num_proxy_tested:
        valid = dict(sorted(valid.items(), key=lambda x: x[1])[: test_args.max_num_proxy_tested])

    info: dict[str, tuple[float, int]] = {}
    for i, (name, latency) in enumerate(valid.items()):
        logger.info(f"[{i+1}/{len(valid)}] Testing speed for proxy {name}. Latency: {latency}ms")
        info[name] = (get_speed(name), latency)

    replaced_names = replace_name(proxies, info)
    for new_name, proxy in zip(replaced_names, config["proxies"]):
        proxy["name"] = new_name
    config["proxies"] = sorted(config["proxies"], key=lambda x: float(x["name"].split(" - ")[1]), reverse=True)

    replaced_names = sorted(replaced_names, key=lambda x: float(x.split(" - ")[1]), reverse=True)
    for start, group in zip(test_args.group_proxy_start, config["proxy-groups"]):
        if start == -1:
            continue
        if start == -2:
            names = list(filter(lambda x: float(x.split(" - ")[1]) > test_args.load_balance_thres, replaced_names))
            group["proxies"] = names if names else [replaced_names[0]]
            continue
        group["proxies"][start:] = replaced_names

    with open(config_args.profile_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main()
