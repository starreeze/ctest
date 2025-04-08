#!/bin/env python
# -*- coding: utf-8 -*-
# @Date    : 2024-06-18 09:33:04
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

from __future__ import annotations

from collections import defaultdict
from functools import reduce

import yaml

from common.args import config_args as args
from common.args import get_newest_profile, logger


def get_unsupported_name(profile: str) -> list[str]:
    return [
        line.split(",")[0].split(": ")[-1]
        for line in profile.splitlines()
        if any(u in line for u in args.unsupported_names)
    ]


def filter_redundant_names(profile: str) -> list[str]:
    "proxy -> name, server, port"
    server2name = defaultdict(list)
    for proxy in yaml.safe_load(profile)["proxies"]:
        server2name[f"{proxy['server']}:{proxy['port']}"].append(proxy["name"])
    return reduce(lambda s, x: s + x[1:], server2name.values(), [])


def fix(profile_path: str):
    logger.info(f"Fixing {profile_path}")
    profile = open(profile_path, "r", encoding="utf-8").read().strip()
    profile = profile.replace("!<str>", "!!str")
    unsupported_names = get_unsupported_name(profile)
    logger.info(f"Fixing unsupported names: {unsupported_names}")
    redundant_names = filter_redundant_names(profile)
    logger.info(f"Fixing redundant names: {redundant_names}")
    fixed = filter(
        lambda line: not any(name in line for name in unsupported_names + redundant_names),
        profile.splitlines(),
    )

    with open(profile_path, "w", encoding="utf-8") as f:
        f.write("\n".join(fixed))


if __name__ == "__main__":
    fix(get_newest_profile())
