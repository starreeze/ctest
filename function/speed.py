# -*- coding: utf-8 -*-
from typing import Iterable

import yaml

from common.api import get_latency, get_speed
from common.args import config_args, get_newest_profile, logger, test_args


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

    valid = get_latency(proxies)
    logger.info(f"get {len(valid)} / {len(proxies)} valid proxies.")
    valid = list(sorted(valid.items(), key=lambda x: x[1]))

    name2ls = get_speed(valid)

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
    for start, group in zip(test_args.group_proxy_start, config["proxy-groups"]):
        if start == -1:
            continue
        if start == -2:
            names = list(
                filter(lambda x: float(x.split(" - ")[1]) > test_args.load_balance_thres, replaced_names)
            )
            group["proxies"] = names if names else [replaced_names[0]]
            continue
        group["proxies"][start:] = replaced_names

    with open(profile_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    test_latency_speed()
