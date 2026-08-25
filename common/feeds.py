# -*- coding: utf-8 -*-
import os
from datetime import datetime
from typing import Iterable

import requests

from common.utils import github_feed_parts, load_raw_clash_yaml, rewrite_github_feed_url

direct_session = requests.Session()
direct_session.trust_env = False


def parse_feed_list(lines: Iterable[str]) -> list[tuple[str, bool]]:
    """Return (url, pinned) for enabled urls.txt lines. A leading ! pins the feed's hosts."""
    feeds: list[tuple[str, bool]] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pinned = line.startswith("!")
        if pinned:
            line = line[1:].strip()
            if not line or line.startswith("#"):
                raise ValueError(f"pin marker is missing a feed URL: {raw!r}")
        feeds.append((line, pinned))
    return feeds


def load_enabled_feeds(url_list_path: str) -> list[tuple[str, bool]]:
    with open(url_list_path, encoding="utf-8") as f:
        return parse_feed_list(f)


def local_path_for_github_feed(url: str, url_list_path: str) -> str | None:
    """Map a GitHub/jsDelivr feed to a repo-relative file sitting next to urls.txt."""
    parts = github_feed_parts(url)
    if parts is None:
        return None
    rel = parts[3]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    base = os.path.dirname(os.path.abspath(url_list_path)) or "."
    candidate = os.path.join(base, rel)
    if os.path.isfile(candidate):
        return candidate
    return None


def _read_keep_feed(url: str, url_list_path: str) -> str:
    local = local_path_for_github_feed(url, url_list_path)
    if local is not None:
        with open(local, encoding="utf-8") as f:
            return f.read()
    rewritten = rewrite_github_feed_url(url)
    response = direct_session.get(
        rewritten,
        timeout=30,
    )
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def hosts_from_keep_feed(url: str, url_list_path: str) -> set[str]:
    content = _read_keep_feed(url, url_list_path)
    data = load_raw_clash_yaml(content)
    return {str(proxy["server"]) for proxy in data["proxies"]}


def load_keep_hosts(url_list_path: str) -> set[str]:
    hosts: set[str] = set()
    for url, keep in load_enabled_feeds(url_list_path):
        if not keep:
            continue
        url = datetime.strftime(datetime.now(), url)
        hosts |= hosts_from_keep_feed(url, url_list_path)
    return hosts
