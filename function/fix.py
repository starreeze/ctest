#!/bin/env python
# -*- coding: utf-8 -*-
# @Date    : 2024-06-18 09:33:04
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict

from common.args import config_args as args
from common.args import get_newest_profile, logger
from common.db import ProxyFailureDB
from common.feeds import load_keep_hosts
from common.utils import dump_yaml, load_raw_clash_yaml, mihomo_accepts_vless_encryption


def create_proxy_groups(proxy_names: list[str], max_size: int) -> list[dict]:
    """
    Create proxy groups with at most max_size proxies each.

    Args:
        proxy_names: List of proxy names
        max_size: Maximum number of proxies per group

    Returns:
        List of proxy group dictionaries for clash config
    """
    groups = []
    for i in range(0, len(proxy_names), max_size):
        group_proxies = proxy_names[i : i + max_size]
        group_num = i // max_size + 1
        group_name = f"{args.target_group} Group {group_num}"
        groups.append({"name": group_name, "type": "select", "proxies": group_proxies})

    logger.info(f"Created {len(groups)} proxy groups with max {max_size} proxies each")
    return groups


def preprocess_profile(profile: str) -> dict:
    """Preprocess profile string and return as YAML dict"""
    in_yaml = load_raw_clash_yaml(profile)
    for proxy in in_yaml["proxies"]:
        if "obfs" in proxy and "obfs-password" not in proxy:
            proxy["obfs-password"] = "none"
    return in_yaml


def apply_failure_filter(
    proxies: list[dict], filtered_hosts: set[str], keep_hosts: set[str]
) -> tuple[list[dict], list[str]]:
    """Drop hosts with too many consecutive failures, except pinned keep-feed hosts."""
    kept: list[dict] = []
    dropped_names: list[str] = []
    for proxy in proxies:
        host = str(proxy["server"])
        if host in filtered_hosts and host not in keep_hosts:
            dropped_names.append(proxy["name"])
            logger.debug(f"Filtering proxy {proxy['name']} ({host}) due to consecutive failures")
            continue
        if host in filtered_hosts:
            logger.info(f"Keeping pinned proxy {proxy['name']} ({host}) despite failure history")
        kept.append(proxy)
    return kept, dropped_names


def has_unsupported_field(proxy: dict) -> bool:
    """Check if proxy has any unsupported fields"""
    for unsupported in args.unsupported_names:
        # Parse the unsupported pattern (e.g., "cipher: chacha20-poly1305")
        if ":" in unsupported:
            key, value = unsupported.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key in proxy and value in str(proxy[key]):
                return True
        else:
            # For patterns without ":", check if it appears in any value
            for val in proxy.values():
                if unsupported in str(val):
                    return True
    if str(proxy.get("type", "")).lower() == "vless":
        enc = proxy.get("encryption")
        if enc is not None and not mihomo_accepts_vless_encryption(str(enc)):
            return True
    return False


def get_proxy_identity_label(proxy: dict) -> str:
    """Return a stable label to disambiguate proxies with conflicting names."""
    for field in ("source", "provider", "proxy-provider", "proxy_provider", "provider-name", "provider_name"):
        value = proxy.get(field)
        if value:
            return str(value)
    return str(proxy["server"])


def build_conflict_name(original_name: str, proxy: dict, used_names: set[str]) -> str:
    """Build a unique name for a proxy whose original name conflicts with another endpoint."""
    label = get_proxy_identity_label(proxy)
    candidate = f"{original_name} [{label}]"
    if candidate not in used_names:
        return candidate

    suffix = 2
    while True:
        candidate = f"{original_name} [{label}] #{suffix}"
        if candidate not in used_names:
            return candidate
        suffix += 1


def handle_redundant_and_conflicts(proxies: list[dict]) -> tuple[list[dict], dict[str, str], set[str]]:
    """
    Remove redundant proxies (same host) and rename conflicting names.

    - For redundant entries (different names, same host): keep only the first one
    - For conflict names (same name, different host): rename duplicates with an endpoint/provider label

    Returns:
        tuple: (filtered_proxies, host_to_name, skipped_names)
    """
    host_to_proxy: dict[str, dict] = {}
    name_to_host: dict[str, str] = {}
    host_to_name: dict[str, str] = {}
    used_names: set[str] = set()
    skipped_names: set[str] = set()

    result = []

    for proxy in proxies:
        original_name = proxy["name"]
        host = str(proxy["server"])

        if host in host_to_proxy:
            if original_name != host_to_proxy[host]["name"]:
                skipped_names.add(original_name)
            logger.debug(
                f"Skipping redundant proxy: {original_name} (duplicate of {host_to_proxy[host]['name']})"
            )
            continue

        if original_name in name_to_host:
            if name_to_host[original_name] != host:
                new_name = build_conflict_name(original_name, proxy, used_names)
                logger.debug(f"Renaming conflicting proxy: {original_name} -> {new_name}")
                proxy = proxy.copy()
                proxy["name"] = new_name
                name_to_host[new_name] = host
                host_to_name[host] = new_name
        else:
            name_to_host[original_name] = host
            host_to_name[host] = original_name

        host_to_proxy[host] = proxy
        used_names.add(proxy["name"])
        result.append(proxy)

    return result, host_to_name, skipped_names


def update_proxy_references(
    data: dict, original_proxies: list[dict], removed_names: set[str], host_to_name: dict[str, str]
) -> None:
    """
    Update all references to proxy names throughout the profile.

    Args:
        data: The profile dictionary
        original_proxies: The original list of proxies before filtering
        removed_names: Set of proxy names that were removed
        host_to_name: Maps server address to final proxy name
    """
    if "proxy-groups" not in data:
        return

    name_occurrences = defaultdict(list)
    for proxy in original_proxies:
        name_occurrences[proxy["name"]].append(str(proxy["server"]))

    for group in data["proxy-groups"]:
        if "proxies" in group:
            updated_proxies = []
            occurrence_counter = defaultdict(int)

            for proxy_name in group["proxies"]:
                if proxy_name in removed_names:
                    logger.debug(
                        f"Removing reference to deleted proxy '{proxy_name}' from group '{group['name']}'"
                    )
                    continue

                occurrences = name_occurrences.get(proxy_name, [])
                if occurrence_counter[proxy_name] < len(occurrences):
                    host = occurrences[occurrence_counter[proxy_name]]
                    occurrence_counter[proxy_name] += 1

                    if host in host_to_name:
                        final_name = host_to_name[host]
                        if final_name != proxy_name:
                            logger.debug(
                                f"Updating reference '{proxy_name}' -> '{final_name}' in group '{group['name']}'"
                            )
                        updated_proxies.append(final_name)
                    else:
                        logger.debug(
                            f"Removing reference to deleted proxy '{proxy_name}' (host not found) from group '{group['name']}'"
                        )
                else:
                    updated_proxies.append(proxy_name)

            group["proxies"] = updated_proxies


_CORE_PROXY_ERROR = re.compile(r"proxy (\d+):\s*(.+)")


def drop_proxies_rejected_by_core(profile_path: str, max_drops: int = 32) -> None:
    """Drop nodes whose fields fatal-exit Mihomo until `mihomo -t` passes."""
    profile_dir = os.path.dirname(os.path.abspath(profile_path)) or "."
    for _ in range(max_drops):
        logger.info("Validating profile with mihomo -t")
        result = subprocess.run(
            ["mihomo", "-t", "-d", profile_dir],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("mihomo -t passed")
            return
        log = result.stdout + result.stderr
        match = _CORE_PROXY_ERROR.search(log)
        if not match:
            raise RuntimeError(f"mihomo rejected profile:\n{log}")
        idx = int(match.group(1))
        reason = match.group(2).strip()
        with open(profile_path, encoding="utf-8") as f:
            profile_dict = load_raw_clash_yaml(f.read())
        if idx < 0 or idx >= len(profile_dict["proxies"]):
            raise RuntimeError(
                f"mihomo reported proxy {idx} but profile has {len(profile_dict['proxies'])} proxies:\n{log}"
            )
        name = profile_dict["proxies"][idx]["name"]
        logger.warning(f"Dropping proxy {idx} {name!r}: {reason}")
        del profile_dict["proxies"][idx]
        for group in profile_dict.get("proxy-groups", []):
            names = group.get("proxies")
            if not names:
                continue
            group["proxies"] = [item for item in names if item != name] or ["DIRECT"]
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(dump_yaml(profile_dict))
    raise RuntimeError(f"mihomo still rejects the profile after {max_drops} proxy drops")


def fix(profile_path: str):
    """
    Fix clash profile by:
    1. Removing proxies with unsupported fields
    2. Removing redundant proxies (same host, different names)
    3. Renaming conflict names (same name, different host)
    4. Updating all references to renamed/removed proxies in proxy-groups
    5. Filtering out proxies that have failed consecutively based on failure database
    """
    logger.info(f"Fixing {profile_path}")
    profile = open(profile_path, "r", encoding="utf-8").read().strip()

    # Parse YAML
    profile_dict = preprocess_profile(profile)
    original_proxies = profile_dict.get("proxies", [])

    # Load failure database and filter out proxies with too many consecutive failures
    failure_db = ProxyFailureDB()
    failure_db.cleanup_expired()  # Clean up expired entries first
    filtered_by_failures = failure_db.get_filtered_proxies()
    keep_hosts = load_keep_hosts(args.profile_remote_url_path)
    if keep_hosts:
        logger.info(f"Pinning {len(keep_hosts)} keep-feed host(s): {sorted(keep_hosts)}")

    if filtered_by_failures:
        logger.info(f"Filtering {len(filtered_by_failures)} proxies due to consecutive failures")

    # Filter proxies based on failure history
    proxies_after_failure_filter, failure_filtered_names = apply_failure_filter(
        original_proxies, filtered_by_failures, keep_hosts
    )

    if failure_filtered_names:
        logger.info(f"Removed {len(failure_filtered_names)} proxies due to failure history")

    # Use filtered list for subsequent processing
    original_proxies = proxies_after_failure_filter

    # Step 1: Filter out unsupported proxies
    unsupported_names = []
    supported_proxies = []
    for proxy in original_proxies:
        if has_unsupported_field(proxy):
            unsupported_names.append(proxy["name"])
        else:
            supported_proxies.append(proxy)

    logger.info(f"Removing {len(unsupported_names)} unsupported proxies: {unsupported_names}")

    # Step 2: Handle redundant and conflict names
    initial_count = len(supported_proxies)
    fixed_proxies, host_to_name, skipped_names = handle_redundant_and_conflicts(supported_proxies)
    redundant_count = initial_count - len(fixed_proxies)

    logger.info(f"Removed {redundant_count} redundant proxies")
    logger.info(f"Final proxy count: {len(fixed_proxies)}")

    # Step 3: Update all references throughout the profile
    removed_names = set(unsupported_names) | set(failure_filtered_names) | skipped_names
    update_proxy_references(profile_dict, original_proxies, removed_names, host_to_name)

    # Update the profile dict
    profile_dict["proxies"] = fixed_proxies

    # Step 4: Create proxy groups (split if too many proxies)
    proxy_names = [proxy["name"] for proxy in fixed_proxies]
    new_proxy_groups = create_proxy_groups(proxy_names, args.max_proxies_per_group)

    # Find and replace the main proxy selection group(s)
    # Add new groups at the beginning
    profile_dict.pop("global-client-fingerprint", None)
    profile_dict["proxy-groups"] += new_proxy_groups
    for group in profile_dict.get("proxy-groups", []):
        if group.get("type") == "load-balance":
            group["strategy"] = args.load_balance_strategy
            group.pop("tolerance", None)
    logger.info(f"Updated proxy groups: {len(new_proxy_groups)} selection group(s)")

    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(dump_yaml(profile_dict))
    drop_proxies_rejected_by_core(profile_path)


if __name__ == "__main__":
    fix(get_newest_profile())
