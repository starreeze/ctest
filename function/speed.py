# -*- coding: utf-8 -*-
from typing import Iterable

import yaml

from common.api import get_latency, get_speed
from common.args import apply_runtime_proxy_env, config_args, get_newest_profile, logger, test_args
from common.db import ProxyFailureDB
from common.utils import dump_yaml


def should_persist_speed_failures(tested_count: int, abort_count: int) -> bool:
    """Persist nodes with no successful sample unless adaptive mode looks like a shared-endpoint outage."""
    if not test_args.test_speed or abort_count == 0:
        return False
    if test_args.speed_test_mode != "adaptive":
        return True
    if test_args.speed_outage_min_samples <= 0:
        raise ValueError("speed_outage_min_samples must be positive")
    if not 0 <= test_args.speed_outage_fail_ratio <= 1:
        raise ValueError("speed_outage_fail_ratio must be between 0 and 1")
    if tested_count >= test_args.speed_outage_min_samples and abort_count == tested_count:
        logger.warning(
            f"Not persisting {abort_count}/{tested_count} aborted speed test(s): "
            "failure rate looks like a shared measurement-endpoint outage"
        )
        return False
    return True


def replace_name(names: Iterable[str], info: dict[str, tuple[float, int]]) -> list[str]:
    new_names = []
    for name in names:
        if name in info:
            new_names.append(f"{info[name][1]:04d} - {info[name][0]:.2f} - {name.split(' - ')[-1]}")
        else:
            new_names.append(f"{test_args.latency_timeout} - 0.00 - {name.split(' - ')[-1]}")
    return new_names
    # return sorted(new_names, key=lambda x: int(x.split(" - ")[1]), reverse=True)


def sl_from_name(name: str) -> tuple[float, int]:
    latency, speed = name.split(" - ")[0:2]
    return -float(speed), int(latency)


def convert_to_str(config: dict) -> dict:
    for proxy in config["proxies"]:
        if not isinstance(proxy["name"], str):
            proxy["name"] = str(proxy["name"])
    for group in config["proxy-groups"]:
        for i in range(len(group["proxies"])):
            if not isinstance(group["proxies"][i], str):
                group["proxies"][i] = str(group["proxies"][i])
    return config


def test_latency_speed():
    # in speedtest mode, use the latest profile
    profile_path = get_newest_profile()
    config = yaml.safe_load(open(profile_path, "r", encoding="utf-8"))
    config = convert_to_str(config)
    proxies = [p["name"] for p in config["proxies"]]

    # Find all "节点选择" groups (created by fix.py)
    selection_groups = [
        group
        for group in config.get("proxy-groups", [])
        if group["name"].startswith(f"{config_args.target_group} Group")
    ]

    if not selection_groups:
        raise ValueError(f"No '{config_args.target_group}' groups found")

    logger.info(f"Found {len(selection_groups)} proxy selection group(s) to test")

    # Test each group separately and merge results
    all_valid = {}
    for i, group in enumerate(selection_groups, 1):
        group_name = group["name"]
        proxy_group = group.get("proxies", [])
        logger.info(
            f"Testing group {i}/{len(selection_groups)}: {group_name} with {len(proxy_group)} proxies"
        )
        valid = get_latency(proxy_group, group_name)
        logger.info(f"Group {i}/{len(selection_groups)}: got {len(valid)} / {len(proxy_group)} valid proxies")
        all_valid.update(valid)

    logger.info(f"Total: got {len(all_valid)} / {len(proxies)} valid proxies across all groups.")
    valid = list(sorted(all_valid.items(), key=lambda x: x[1]))

    # Update failure database - collect all failures first, then batch update
    failure_db = ProxyFailureDB()
    failed_proxies = []
    for proxy_dict in config["proxies"]:
        proxy_name = proxy_dict["name"]
        server = proxy_dict["server"]
        port = proxy_dict["port"]

        # Check if this proxy failed (not in valid results)
        if proxy_name not in all_valid:
            failed_proxies.append((server, port))
            logger.debug(f"Will record failure for {proxy_name} ({server}:{port})")

    # Batch record all failures at once
    if failed_proxies:
        failure_db.record_failures_batch(failed_proxies)

    name2ls = get_speed(valid)

    # Record failures for proxies that produced no successful throughput sample.
    # Completed-but-slow results are left in the profile ranking and are not persisted.
    name_to_proxy = {proxy["name"]: proxy for proxy in config["proxies"]}
    abort_failures = []
    for proxy_name, (speed, latency) in name2ls.items():
        if speed == 0:
            if proxy_name in name_to_proxy:
                proxy_dict = name_to_proxy[proxy_name]
                server = proxy_dict["server"]
                port = proxy_dict["port"]
                abort_failures.append((server, port))
                logger.debug(f"Will record failure for {proxy_name} ({server}:{port}): no successful speed sample")

    if should_persist_speed_failures(len(name2ls), len(abort_failures)):
        failure_db.record_failures_batch(abort_failures)

    replaced_names = replace_name(proxies, name2ls)
    for new_name, proxy in zip(replaced_names, config["proxies"]):
        proxy["name"] = new_name
    if config_args.discard:  # filter out latency >= timeout
        config["proxies"] = filter(
            lambda x: sl_from_name(x["name"])[1] < test_args.latency_timeout, config["proxies"]
        )
    config["proxies"] = sorted(config["proxies"], key=lambda x: sl_from_name(x["name"]))

    if config_args.discard:
        replaced_names = filter(lambda x: sl_from_name(x)[1] < test_args.latency_timeout, replaced_names)
    replaced_names = sorted(replaced_names, key=sl_from_name)
    new_groups = []
    for start, group in zip(test_args.group_proxy_start, config["proxy-groups"]):
        if start == -1:
            new_groups.append(group)
            continue
        if start == -2:
            names = list(
                filter(lambda x: float(x.split(" - ")[1]) >= test_args.load_balance_thres, replaced_names)
            )
            group["proxies"] = names if names else [replaced_names[0]]
            group["strategy"] = config_args.load_balance_strategy
            group.pop("tolerance", None)
            new_groups.append(group)
            continue
        group["proxies"][start:] = replaced_names
        new_groups.append(group)

    config["proxy-groups"] = new_groups

    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(dump_yaml(config))


if __name__ == "__main__":
    apply_runtime_proxy_env()
    test_latency_speed()
