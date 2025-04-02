# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:36:29
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

import urllib.parse

import requests

from common.args import config_args as args
from common.args import get_newest_profile, logger


def update():
    with open(args.profile_remote_url_path, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]
    encoded_urls = [urllib.parse.quote(url, safe="") for url in urls]
    merged_urls = "|".join(encoded_urls)
    config_url = urllib.parse.quote(args.subconvert_config_url, safe="")
    final_url = f"{args.subconvert_base_url}&url={merged_urls}&config={config_url}&emoji=true"

    content = requests.get(final_url).content.decode("utf-8")
    profile = get_newest_profile()
    logger.info(f"Updating newest profile {profile} ...")
    with open(profile, "w", encoding="utf-8") as f:
        f.write(content)
