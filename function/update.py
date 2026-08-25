# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:36:29
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

import os
from datetime import datetime

import requests
import yaml

from common.args import config_args as args
from common.args import get_newest_profile, logger
from common.feeds import load_enabled_feeds
from common.utils import decode_subconverter_body, load_raw_clash_yaml, rewrite_github_feed_url

META_PROXY_TYPES = {"vless", "hysteria", "hysteria2", "tuic", "wireguard", "anytls", "ssh", "mieru"}
direct_session = requests.Session()
direct_session.trust_env = False


def log_proxy_inventory(proxies: list[dict], label: str) -> None:
    counts: dict[str, int] = {}
    reality = 0
    for proxy in proxies:
        kind = str(proxy.get("type", "unknown"))
        counts[kind] = counts.get(kind, 0) + 1
        if proxy.get("reality-opts"):
            reality += 1
    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    logger.info(f"{label}: {len(proxies)} proxies ({summary}; reality-opts={reality})")


def fetch_converted_profile(feed_urls: list[str], config_url: str) -> str:
    last_error: Exception | None = None
    fallback_content: str | None = None
    merged = "|".join(feed_urls)
    for base_url in args.subconvert_base_urls:
        logger.info(f"Fetching converted profile from {base_url} ({len(feed_urls)} feeds)")
        try:
            response = direct_session.get(
                base_url,
                params={"url": merged, "config": config_url, "emoji": "true"},
                timeout=args.subconvert_timeout,
                headers={"User-Agent": args.subconvert_user_agent},
            )
            response.raise_for_status()
            content, replaced = decode_subconverter_body(response.content)
            if replaced:
                logger.warning(
                    f"Subconverter body is not valid UTF-8; replaced {replaced} invalid sequences"
                )
            parsed = load_raw_clash_yaml(content)
            log_proxy_inventory(parsed["proxies"], f"Subconverter {base_url}")
            types = {str(proxy.get("type")) for proxy in parsed["proxies"]}
            if types & META_PROXY_TYPES:
                return content
            if fallback_content is None:
                fallback_content = content
            logger.warning(
                f"Backend {base_url} omitted Mihomo protocol types; trying next backend"
            )
        except (requests.RequestException, ValueError, yaml.YAMLError, UnicodeDecodeError) as e:
            last_error = e
            logger.warning(f"Backend {base_url} failed: {e}")
    if fallback_content is not None:
        logger.warning("No subconverter backend emitted Mihomo protocol types; using first valid conversion")
        return fallback_content
    raise RuntimeError(f"All subconvert backends failed, last error: {last_error}") from last_error


def update():
    urls = []
    for url, keep in load_enabled_feeds(args.profile_remote_url_path):
        url = datetime.strftime(datetime.now(), url)
        rewritten = rewrite_github_feed_url(url)
        if rewritten != url:
            logger.info(f"Rewrote GitHub feed to jsDelivr: {url} -> {rewritten}")
        if keep:
            logger.info(f"Pinning keep-feed hosts from {url}")
        urls.append(rewritten)
    config_url = rewrite_github_feed_url(args.subconvert_config_url)
    content = fetch_converted_profile(urls, config_url)

    profile = get_newest_profile()
    logger.info(f"Updating newest profile {profile} ...")
    tmp_path = f"{profile}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, profile)
