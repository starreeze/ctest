# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:36:29
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

import os
import urllib.parse
from datetime import datetime

import requests
import yaml

from common.args import config_args as args
from common.args import get_newest_profile, logger
from common.utils import load_raw_clash_yaml


def fetch_converted_profile(merged_urls: str, config_url: str) -> str:
    last_error: Exception | None = None
    for base_url in args.subconvert_base_urls:
        final_url = f"{base_url}&url={merged_urls}&config={config_url}&emoji=true"
        logger.info(f"Fetching converted profile from {base_url}")
        try:
            response = requests.get(
                final_url,
                timeout=args.subconvert_timeout,
                headers={"User-Agent": args.subconvert_user_agent},
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            content = response.content.decode("utf-8")
            load_raw_clash_yaml(content)
            return content
        except (requests.RequestException, ValueError, yaml.YAMLError, UnicodeDecodeError) as e:
            last_error = e
            logger.warning(f"Backend {base_url} failed: {e}")
    raise RuntimeError(f"All subconvert backends failed, last error: {last_error}") from last_error


def update():
    urls = []
    with open(args.profile_remote_url_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                line = datetime.strftime(datetime.now(), line)
                urls.append(line)
    encoded_urls = [urllib.parse.quote(url, safe="") for url in urls]
    merged_urls = "|".join(encoded_urls)
    config_url = urllib.parse.quote(args.subconvert_config_url, safe="")
    content = fetch_converted_profile(merged_urls, config_url)

    profile = get_newest_profile()
    logger.info(f"Updating newest profile {profile} ...")
    tmp_path = f"{profile}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, profile)
