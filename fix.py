#!/bin/env python
# -*- coding: utf-8 -*-
# @Date    : 2024-06-18 09:33:04
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

from __future__ import annotations

from args import config_args as args
from args import logger


def get_unsupported_name(profile: list[str]) -> list[str]:
    return [line.split(",")[0].split(": ")[-1] for line in profile if any(u in line for u in args.unsupported_names)]


def fix():
    profile = open(args.profile_path, "r", encoding="utf-8").read().strip().splitlines()
    names = get_unsupported_name(profile)
    logger.info(f"Fixing unsupported names: {names}")
    fixed = filter(lambda line: not any(name in line for name in names), profile)

    with open(args.profile_path, "w", encoding="utf-8") as f:
        f.write("\n".join(fixed))


if __name__ == "__main__":
    fix()
