import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Literal, cast

from iterwrap import HfArgumentParser
from rich.logging import RichHandler


@dataclass
class Config:
    mode: Literal["meta", "verge"] = field(default="verge")
    unsupported_names: list[str] = field(
        default_factory=lambda: ["cipher: chacha20-poly1305", "obfs: none", "cipher: ss"]
    )
    profile_dir: str = field(default="io.github.clash-verge-rev.clash-verge-rev/profiles")
    profile_size_filter_kb: int = field(default=10)
    profiles: list[str] = field(default_factory=list)
    profile_remote_url_path: str = field(default="urls.txt")
    controller_url: str = field(default="http://127.0.0.1:9090")
    controller_password: str = field(default="-10123")
    proxy_url: str = field(default="http://127.0.0.1:7890")
    discard: bool = field(
        default=True, metadata={"help": "discard the proxies that are not valid in latency test"}
    )
    subconvert_base_url: str = field(default="https://api.dler.io/sub?target=clash")
    subconvert_config_url: str = field(
        default="https://raw.githubusercontent.com/starreeze/blogimage/main/subconverter/external.ini"
    )
    target_group: str = field(default="🔰 节点选择")
    max_proxies_per_group: int = field(
        default=100, metadata={"help": "maximum proxies per test group for splitting large groups"}
    )
    failure_db_path: str = field(
        default="", metadata={"help": "path to failure tracking database (default: local app data)"}
    )
    consecutive_failure_threshold: int = field(
        default=3, metadata={"help": "number of consecutive failures needed to filter out a proxy"}
    )
    failure_filter_duration_days: int = field(
        default=30, metadata={"help": "days to filter out failed proxies"}
    )
    failure_dedup_hours: int = field(
        default=24, metadata={"help": "hours within which multiple failures count as one"}
    )
    min_speed_threshold_kbps: int = field(
        default=512, metadata={"help": "minimum speed in KB/s, below which is considered a failure"}
    )
    meta_start_command: str = field(default="mihomo -d profiles")


@dataclass
class TestArgs:
    speed_test_url: str = field(default="http://speedtest.tele2.net/100MB.zip")
    speed_test_retry: int = field(default=1)
    latency_test_urls: list[str] = field(default_factory=lambda: ["https://google.com", "https://github.com"])
    latency_test_times: int = field(default=1)
    latency_timeout: int = field(default=5000)
    latency_call_timeout: int = field(default=300)
    speedtest_call_timeout: int = field(default=300)
    core_restart_timeout: int = field(default=10)
    speed_duration: int = field(default=15)
    speed_window_size: int = field(default=5)
    group_proxy_start: list[int] = field(
        default_factory=lambda: [3, 0, -2, -1, -1, 2, 2, 2, -1, -1, 3],
        metadata={"help": ">0: start position for proxies; -1: no proxy, copy all; -2: load balance"},
    )
    max_num: int = field(default=3, metadata={"help": "the max valid proxies to return in speed test"})
    load_balance_thres: float = field(
        default=2.0, metadata={"help": "the min MB/s to be valid for load balancing"}
    )
    update_profile: bool = field(
        default=True, metadata={"help": "update profile before running tests in main"}
    )
    test_speed: bool = field(default=False, metadata={"help": "test speed in addition to latency"})
    test_latency_retry: int = field(default=5, metadata={"help": "retry times for latency test"})


config_args, test_args = HfArgumentParser([Config, TestArgs]).parse_args_into_dataclasses()  # type: ignore
config_args = cast(Config, config_args)
test_args = cast(TestArgs, test_args)

if sys.platform == "win32":
    base_appdata = os.getenv("APPDATA")
    local_appdata = os.getenv("LOCALAPPDATA")
    if base_appdata is None or local_appdata is None:
        raise ValueError("APPDATA or LOCALAPPDATA is not set")
elif sys.platform == "darwin":  # macOS
    base_appdata = os.path.expanduser("~/.local/share")
    local_appdata = os.path.expanduser("~/Library/Application Support")
else:  # Linux and others
    base_appdata = os.path.expanduser("~/.local/share")
    local_appdata = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))

config_args.profile_dir = os.path.join(base_appdata, config_args.profile_dir)

# Set failure database path to local app data if not specified
if not config_args.failure_db_path:
    app_data_dir = os.path.join(local_appdata, "clash-proxy-tester")
    os.makedirs(app_data_dir, exist_ok=True)
    config_args.failure_db_path = os.path.join(app_data_dir, "proxy_failures.db")
if not config_args.profiles:
    config_args.profiles = [
        os.path.join(config_args.profile_dir, f)
        for f in os.listdir(config_args.profile_dir)
        if f.endswith(".yaml")
        or f.endswith(".yml")
        and os.path.getsize(os.path.join(config_args.profile_dir, f))
        > config_args.profile_size_filter_kb * 1024
    ]

logging.basicConfig(level="INFO", format="%(message)s", datefmt="[%X]", handlers=[RichHandler()])
logger = logging.getLogger("rich")
proxies = {"http": config_args.proxy_url, "https": config_args.proxy_url}
for name in proxies:
    os.environ[name.lower()] = os.environ[name.upper()] = proxies[name]


def get_newest_profile() -> str:
    return max(config_args.profiles, key=lambda x: os.path.getmtime(x))
