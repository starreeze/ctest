# -*- coding: utf-8 -*-
import re
import time
from collections import defaultdict
from math import isfinite

import yaml

from common.api import get_latency, get_speed
from common.args import apply_runtime_proxy_env, config_args, get_newest_profile, logger, test_args
from common.db import ProxyFailureDB
from common.feeds import load_keep_hosts
from common.utils import dump_yaml, staged_profile_update


BUILTIN_POLICIES = {
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "PASS",
    "GLOBAL",
    "COMPATIBLE",
}
MEASUREMENT_PREFIX = re.compile(
    r"^\d{4,} - (?:N/A|\d+(?:\.\d+)?) - "
    r"(?:(?:N/A|\d+(?:\.\d+)?%) - )?"
)


def should_persist_speed_failures(tested_count: int, failure_count: int) -> bool:
    """Suppress speed-derived host failures during a shared measurement outage."""
    if not test_args.test_speed or failure_count == 0:
        return False
    if test_args.speed_test_mode != "adaptive":
        return True
    if test_args.speed_outage_min_samples <= 0:
        raise ValueError("speed_outage_min_samples must be positive")
    if not 0 <= test_args.speed_outage_fail_ratio <= 1:
        raise ValueError("speed_outage_fail_ratio must be between 0 and 1")
    failure_ratio = failure_count / tested_count if tested_count else 0
    if (
        tested_count >= test_args.speed_outage_min_samples
        and failure_ratio >= test_args.speed_outage_fail_ratio
    ):
        logger.warning(
            f"Not persisting {failure_count}/{tested_count} speed-failed host(s): "
            f"failure ratio {failure_ratio:.0%} suggests a shared measurement outage"
        )
        return False
    return True


def original_name(name: str) -> str:
    return MEASUREMENT_PREFIX.sub("", name, count=1)


def measured_name(
    name: str, speed: float | None, stability: float | None, latency: int
) -> str:
    speed_label = "N/A" if speed is None else f"{speed:.2f}"
    stability_label = "N/A" if stability is None else f"{stability:.0%}"
    return f"{latency:04d} - {speed_label} - {stability_label} - {original_name(name)}"


def score_from_name(name: str) -> tuple[bool, float, bool, float, int]:
    latency, speed, stability = name.split(" - ", 3)[:3]
    numeric_speed = None if speed == "N/A" else float(speed)
    numeric_stability = None if stability == "N/A" else float(stability.removesuffix("%"))
    return (
        numeric_speed is None,
        -(numeric_speed or 0.0),
        numeric_stability is None,
        -(numeric_stability or 0.0),
        int(latency),
    )


def convert_to_str(config: dict) -> dict:
    for proxy in config.get("proxies", []):
        proxy["name"] = str(proxy["name"])
    for group in config.get("proxy-groups", []):
        if "proxies" in group:
            group["proxies"] = [str(name) for name in group["proxies"]]
    return config


def generated_test_group(name: str) -> bool:
    return bool(re.fullmatch(rf"{re.escape(config_args.target_group)} Group [1-9]\d*", name))


def prefer_load_balance_default(config: dict) -> bool:
    """Make the target selector choose its existing load-balance group by default."""
    groups = config.get("proxy-groups", [])
    target_group = next(
        (group for group in groups if str(group.get("name", "")) == config_args.target_group),
        None,
    )
    if target_group is None:
        raise ValueError(f"Target group {config_args.target_group!r} is missing")

    load_balance_names = {
        str(group["name"])
        for group in groups
        if group.get("type") == "load-balance"
    }
    refs = [str(ref) for ref in target_group.get("proxies", [])]
    load_balance_refs = [ref for ref in refs if ref in load_balance_names]
    if not load_balance_refs:
        raise ValueError(
            f"Target group {config_args.target_group!r} does not reference a load-balance group"
        )
    target_group["proxies"] = load_balance_refs + [
        ref for ref in refs if ref not in load_balance_names
    ]
    return True


def configure_final_listener(config: dict) -> None:
    """Expose one mixed HTTP/SOCKS listener in the generated profile."""
    config["port"] = 0
    config["socks-port"] = 0
    config["mixed-port"] = config_args.mixed_port


def rebuild_proxy_groups(
    config: dict,
    old_proxy_names: set[str],
    final_names: list[str],
    final_speeds: dict[str, float | None],
) -> None:
    """Remove test groups and replace node references without relying on group positions."""
    groups = [
        group
        for group in config.get("proxy-groups", [])
        if not generated_test_group(str(group.get("name", "")))
    ]
    group_names = {str(group["name"]) for group in groups}
    if len(group_names) != len(groups):
        raise ValueError("Duplicate proxy-group names after removing test groups")
    if len(final_names) != len(set(final_names)):
        raise ValueError("Duplicate final proxy names")
    if set(final_speeds) != set(final_names):
        raise ValueError("Final speed metadata does not match final proxy names")

    if not isfinite(test_args.speed_load_balance_min_mibps) or test_args.speed_load_balance_min_mibps < 0:
        raise ValueError("speed_load_balance_min_mibps must be finite and non-negative")
    if any(speed is not None and (not isfinite(speed) or speed < 0) for speed in final_speeds.values()):
        raise ValueError("Final speed metadata must be finite and non-negative")
    fast_names = [
        name
        for name in final_names
        if (speed := final_speeds[name]) is not None
        and speed >= test_args.speed_load_balance_min_mibps
    ]
    for group in groups:
        refs = [str(ref) for ref in group.get("proxies", [])]
        node_refs = [ref for ref in refs if ref in old_proxy_names and ref not in group_names]
        if not node_refs:
            continue
        static_refs = [ref for ref in refs if ref not in old_proxy_names or ref in group_names]
        if group.get("type") == "load-balance":
            replacements = fast_names
            group["strategy"] = config_args.load_balance_strategy
            group.pop("tolerance", None)
        else:
            replacements = final_names
        group["proxies"] = list(dict.fromkeys(static_refs + replacements)) or ["DIRECT"]

    known = set(final_names) | group_names | BUILTIN_POLICIES
    dangling = [
        (str(group["name"]), str(ref))
        for group in groups
        for ref in group.get("proxies", [])
        if str(ref) not in known
    ]
    if dangling:
        preview = ", ".join(f"{group}->{ref}" for group, ref in dangling[:5])
        raise ValueError(f"Proxy groups contain {len(dangling)} dangling reference(s): {preview}")
    config["proxy-groups"] = groups


def choose_keep_fallbacks(
    proxies: list[dict],
    all_valid: dict[str, int],
    selected: dict[str, tuple[float | None, float | None, int]],
    keep_hosts: set[str],
) -> None:
    """Pinned hosts always retain one endpoint, preferring a latency-valid candidate."""
    by_host: defaultdict[str, list[dict]] = defaultdict(list)
    for proxy in proxies:
        by_host[str(proxy["server"])].append(proxy)
    selected_hosts = {
        str(proxy["server"])
        for proxy in proxies
        if str(proxy["name"]) in selected
    }
    for host in keep_hosts - selected_hosts:
        candidates = by_host.get(host, [])
        if not candidates:
            continue
        candidates.sort(key=lambda proxy: all_valid.get(str(proxy["name"]), test_args.latency_timeout))
        name = str(candidates[0]["name"])
        selected[name] = (None, None, all_valid.get(name, test_args.latency_timeout))


def test_latency_speed(
    failure_cooldown_anchor: float | None = None,
    profile_path: str | None = None,
) -> dict:
    if failure_cooldown_anchor is None:
        failure_cooldown_anchor = time.time()
    profile_path = profile_path or get_newest_profile()
    with open(profile_path, "r", encoding="utf-8") as profile_file:
        config = convert_to_str(yaml.safe_load(profile_file))
    proxies = config.get("proxies", [])
    old_proxy_names = {str(proxy["name"]) for proxy in proxies}
    name_to_proxy = {str(proxy["name"]): proxy for proxy in proxies}
    name_to_host = {name: str(proxy["server"]) for name, proxy in name_to_proxy.items()}

    selection_groups = [
        group
        for group in config.get("proxy-groups", [])
        if generated_test_group(str(group.get("name", "")))
    ]
    if not selection_groups:
        raise ValueError(f"No generated '{config_args.target_group} Group N' groups found")
    logger.info(f"Testing latency for {len(proxies)} host:port candidates in {len(selection_groups)} groups")

    all_valid: dict[str, int] = {}
    for index, group in enumerate(selection_groups, 1):
        proxy_group = group.get("proxies", [])
        valid = get_latency(proxy_group, str(group["name"]))
        all_valid.update(valid)
        logger.info(f"Latency group {index}/{len(selection_groups)}: {len(valid)}/{len(proxy_group)} passed")
    logger.info(f"Latency total: {len(all_valid)}/{len(proxies)} candidates passed")

    valid = sorted(all_valid.items(), key=lambda item: item[1])
    selected, cooldown_avoided_hosts = get_speed(valid, name_to_host)
    keep_hosts = load_keep_hosts(config_args.profile_remote_url_path)
    choose_keep_fallbacks(proxies, all_valid, selected, keep_hosts)

    all_hosts = {str(proxy["server"]) for proxy in proxies} - keep_hosts
    latency_hosts = {name_to_host[name] for name in all_valid} - keep_hosts
    retained_hosts = {name_to_host[name] for name in selected} - keep_hosts
    success_hosts = cooldown_avoided_hosts - keep_hosts
    latency_failed_hosts = all_hosts - latency_hosts
    speed_not_retained_hosts = latency_hosts - retained_hosts if test_args.test_speed else set()
    speed_failed_hosts = latency_hosts - success_hosts if test_args.test_speed else set()
    na_hosts = {
        name_to_host[name]
        for name, (speed, _stability, _latency) in selected.items()
        if speed is None and name_to_host[name] not in keep_hosts
    }

    failure_db = ProxyFailureDB()
    failure_db.record_successes_batch(sorted(success_hosts))
    failures_to_record = set(latency_failed_hosts)
    if should_persist_speed_failures(len(latency_hosts), len(speed_failed_hosts)):
        failures_to_record |= speed_failed_hosts
    failure_db.record_failures_batch(
        sorted(failures_to_record),
        now=failure_cooldown_anchor,
    )
    logger.info(
        f"Host outcomes: {len(retained_hosts)} retained ({len(na_hosts)} speed N/A), "
        f"{len(success_hosts)} avoided cooldown, {len(latency_failed_hosts)} failed latency, "
        f"{len(speed_not_retained_hosts)} below retain threshold, "
        f"{len(speed_failed_hosts)} entered cooldown, {len(keep_hosts)} pinned"
    )

    final_proxies: list[dict] = []
    final_speeds: dict[str, float | None] = {}
    for old_name, (speed, stability, latency) in selected.items():
        proxy = name_to_proxy[old_name]
        proxy["name"] = measured_name(old_name, speed, stability, latency)
        final_speeds[str(proxy["name"])] = speed
        final_proxies.append(proxy)
    final_proxies.sort(key=lambda proxy: score_from_name(str(proxy["name"])))
    config["proxies"] = final_proxies
    final_names = [str(proxy["name"]) for proxy in final_proxies]
    rebuild_proxy_groups(config, old_proxy_names, final_names, final_speeds)
    prefer_load_balance_default(config)
    configure_final_listener(config)

    with open(profile_path, "w", encoding="utf-8") as profile_file:
        profile_file.write(dump_yaml(config))
    return {
        "input_candidates": len(proxies),
        "latency_passed": len(all_valid),
        "hosts_passed": len(retained_hosts),
        "hosts_avoided_cooldown": len(success_hosts),
        "hosts_failed_latency": len(latency_failed_hosts),
        "hosts_failed_speed": len(speed_failed_hosts),
        "hosts_speed_na": len(na_hosts),
        "hosts_not_retained_speed": len(speed_not_retained_hosts),
        "pinned_hosts": len(keep_hosts),
        "preserved_proxies": len(final_proxies),
    }


if __name__ == "__main__":
    from common.run_history import run_single_stage

    apply_runtime_proxy_env()
    path = get_newest_profile()
    with staged_profile_update(path) as staged_path:
        run_single_stage(
            "speed",
            lambda: test_latency_speed(profile_path=staged_path),
            config_args.run_history_path,
            config_args.run_origin,
            path,
            logger,
        )
