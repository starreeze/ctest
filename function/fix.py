#!/bin/env python
# -*- coding: utf-8 -*-
# @Date    : 2024-06-18 09:33:04
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

from __future__ import annotations

import re
from collections import defaultdict

import yaml

from common.args import config_args as args
from common.args import get_newest_profile, logger
from common.db import ProxyFailureDB
from common.utils import dump_yaml


def create_proxy_groups(proxy_names: list[str], max_size: int) -> list[dict]:
    """
    Create proxy groups with at most max_size proxies each.

    Args:
        proxy_names: List of proxy names
        max_size: Maximum number of proxies per group

    Returns:
        List of proxy group dictionaries for clash config
    """
    if not proxy_names or len(proxy_names) <= max_size:
        # If proxies fit in one group, return a single group
        return [{"name": "🔰 节点选择", "type": "select", "proxies": proxy_names}]

    # Split into multiple groups
    groups = []
    for i in range(0, len(proxy_names), max_size):
        group_proxies = proxy_names[i : i + max_size]
        group_num = i // max_size + 1
        group_name = f"🔰 节点选择 Group {group_num}"
        groups.append({"name": group_name, "type": "select", "proxies": group_proxies})

    logger.info(f"Created {len(groups)} proxy groups with max {max_size} proxies each")
    return groups


def preprocess_profile(profile: str) -> dict:
    """Preprocess profile string and return as YAML dict"""
    profile = profile.replace("!<str>", "!!str")
    profile = quote_ipv6_server_addresses(profile)
    in_yaml = yaml.safe_load(profile)
    for proxy in in_yaml["proxies"]:
        if "obfs" in proxy and "obfs-password" not in proxy:
            proxy["obfs-password"] = "none"
    return in_yaml


def quote_ipv6_server_addresses(yaml_content: str) -> str:
    """Quote IPv6 addresses in server fields"""

    def replacer(match):
        value = match.group(2)
        if ":" in value:
            return f'{match.group(1)} "{value}"'
        else:
            return match.group(0)

    pattern = r"(server:)\s+([0-9a-fA-F:]+)"
    result = re.sub(pattern, replacer, yaml_content)
    return result


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
    return False


def handle_redundant_and_conflicts(proxies: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """
    Remove redundant proxies (same server:port) and rename conflicting names.

    - For redundant entries (different names, same server:port): keep only the first one
    - For conflict names (same name, different server/port): rename duplicates with suffix

    Returns:
        tuple: (filtered_proxies, server_port_to_name) where server_port_to_name maps
               "server:port" to the final proxy name (possibly renamed)
    """
    server_port_to_proxy = {}  # Maps "server:port" -> first proxy dict
    name_to_count = defaultdict(int)  # Track how many times we've seen each name
    name_to_server_port = {}  # Maps name -> "server:port" for first occurrence
    server_port_to_name = {}  # Maps "server:port" -> final name (possibly renamed)

    result = []

    for proxy in proxies:
        original_name = proxy["name"]
        server_port = f"{proxy['server']}:{proxy['port']}"

        # Check if this server:port combination already exists
        if server_port in server_port_to_proxy:
            # This is a redundant entry (different name, same server:port)
            # Skip it - we keep only the first one
            logger.debug(
                f"Skipping redundant proxy: {original_name} (duplicate of {server_port_to_proxy[server_port]['name']})"
            )
            continue

        # Check if this name already exists with different server:port
        if original_name in name_to_server_port:
            if name_to_server_port[original_name] != server_port:
                # This is a conflict - same name, different server/port
                # Rename this proxy
                name_to_count[original_name] += 1
                new_name = f"{original_name} #{name_to_count[original_name]}"
                logger.debug(f"Renaming conflicting proxy: {original_name} -> {new_name}")
                proxy = proxy.copy()  # Don't modify the original
                proxy["name"] = new_name
                # Update tracking for the new name
                name_to_server_port[new_name] = server_port
                server_port_to_name[server_port] = new_name
        else:
            # First time seeing this name
            name_to_count[original_name] = 1
            name_to_server_port[original_name] = server_port
            server_port_to_name[server_port] = original_name

        # Add this proxy to our results
        server_port_to_proxy[server_port] = proxy
        result.append(proxy)

    return result, server_port_to_name


def update_proxy_references(
    data: dict, original_proxies: list[dict], removed_names: set[str], server_port_to_name: dict[str, str]
) -> None:
    """
    Update all references to proxy names throughout the profile.

    Args:
        data: The profile dictionary
        original_proxies: The original list of proxies before filtering
        removed_names: Set of proxy names that were removed
        server_port_to_name: Maps "server:port" to final proxy name
    """
    if "proxy-groups" not in data:
        return

    # Build a mapping from original proxy name to server:port for each occurrence
    # This is needed to handle multiple proxies with the same name
    name_occurrences = defaultdict(list)
    for proxy in original_proxies:
        server_port = f"{proxy['server']}:{proxy['port']}"
        name_occurrences[proxy["name"]].append(server_port)

    for group in data["proxy-groups"]:
        if "proxies" in group:
            updated_proxies = []
            # Track which occurrence of each name we're at
            occurrence_counter = defaultdict(int)

            for proxy_name in group["proxies"]:
                # Skip if this proxy was removed
                if proxy_name in removed_names:
                    logger.debug(
                        f"Removing reference to deleted proxy '{proxy_name}' from group '{group['name']}'"
                    )
                    continue

                # Find which occurrence of this name we're dealing with
                occurrences = name_occurrences.get(proxy_name, [])
                if occurrence_counter[proxy_name] < len(occurrences):
                    server_port = occurrences[occurrence_counter[proxy_name]]
                    occurrence_counter[proxy_name] += 1

                    # Get the final name for this server:port
                    if server_port in server_port_to_name:
                        final_name = server_port_to_name[server_port]
                        if final_name != proxy_name:
                            logger.debug(
                                f"Updating reference '{proxy_name}' -> '{final_name}' in group '{group['name']}'"
                            )
                        updated_proxies.append(final_name)
                    else:
                        # This server:port was removed (redundant or unsupported)
                        logger.debug(
                            f"Removing reference to deleted proxy '{proxy_name}' (server:port not found) from group '{group['name']}'"
                        )
                else:
                    # This shouldn't happen, but keep the name as-is if we can't find it
                    updated_proxies.append(proxy_name)

            group["proxies"] = updated_proxies


def fix(profile_path: str):
    """
    Fix clash profile by:
    1. Removing proxies with unsupported fields
    2. Removing redundant proxies (same server:port, different names)
    3. Renaming conflict names (same name, different server:port)
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

    if filtered_by_failures:
        logger.info(f"Filtering {len(filtered_by_failures)} proxies due to consecutive failures")

    # Filter proxies based on failure history
    failure_filtered_names = []
    proxies_after_failure_filter = []
    for proxy in original_proxies:
        server_port = f"{proxy['server']}:{proxy['port']}"
        if server_port in filtered_by_failures:
            failure_filtered_names.append(proxy["name"])
            logger.debug(f"Filtering proxy {proxy['name']} ({server_port}) due to consecutive failures")
        else:
            proxies_after_failure_filter.append(proxy)

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
    fixed_proxies, server_port_to_name = handle_redundant_and_conflicts(supported_proxies)
    redundant_count = initial_count - len(fixed_proxies)

    # Count renamed proxies
    renamed_count = sum(1 for sp, name in server_port_to_name.items() if "#" in name)

    logger.info(f"Removed {redundant_count} redundant proxies")
    if renamed_count:
        logger.info(f"Renamed {renamed_count} conflicting proxies")
    logger.info(f"Final proxy count: {len(fixed_proxies)}")

    # Step 3: Update all references throughout the profile
    removed_names = set(unsupported_names) | set(failure_filtered_names)
    update_proxy_references(profile_dict, original_proxies, removed_names, server_port_to_name)

    # Update the profile dict
    profile_dict["proxies"] = fixed_proxies

    # Step 4: Create proxy groups (split if too many proxies)
    proxy_names = [proxy["name"] for proxy in fixed_proxies]
    new_proxy_groups = create_proxy_groups(proxy_names, args.max_proxies_per_group)

    # Find and replace the main proxy selection group(s)
    # Add new groups at the beginning
    profile_dict["proxy-groups"] += new_proxy_groups
    logger.info(f"Updated proxy groups: {len(new_proxy_groups)} selection group(s)")

    # Write back to file
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(dump_yaml(profile_dict))


if __name__ == "__main__":
    fix(get_newest_profile())
