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
    subconvert_base_urls: list[str] = field(
        default_factory=lambda: [
            "https://api.v1.mk/sub?target=clash",
            "https://pub-api-1.bianyuan.xyz/sub?target=clash",
        ],
        metadata={
            "help": "subconverter backends, tried in order until a valid clash YAML is returned; "
            "api.v1.mk first (keeps Mihomo types); bianyuan fallback (legacy Clash types only); "
            "a conversion with no vless/hysteria2/tuic is skipped when a later backend still emits them"
        },
    )
    subconvert_config_url: str = field(
        default="https://fastly.jsdelivr.net/gh/starreeze/blogimage@main/subconverter/external.ini"
    )
    subconvert_timeout: int = field(default=180, metadata={"help": "timeout in seconds for subconverter requests"})
    subconvert_user_agent: str = field(default="clash-meta/1.19.30")
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
        default=512, metadata={"help": "minimum speed in KB/s, below which is considered a failure (512 KB/s = 0.5 MiB/s)"}
    )
    load_balance_strategy: str = field(
        default="round-robin", metadata={"help": "strategy for load-balance proxy groups"}
    )
    meta_start_command: str = field(default="mihomo -d profiles")
    global_group: str = field(
        default="GLOBAL", metadata={"help": "built-in group selected while the core is in global mode"}
    )


@dataclass
class TestArgs:
    speed_test_mode: Literal["sdk", "adaptive"] = field(
        default="adaptive",
        metadata={"help": "throughput test implementation: adaptive HTTPS download or speedtest-cli SDK"},
    )
    speed_test_url: str = field(
        default="https://speed.cloudflare.com/__down?bytes={bytes}",
        metadata={
            "help": "adaptive download URL; use {bytes} for the requested size or a fixed file with Range support"
        },
    )
    speed_test_retry: int = field(
        default=1,
        metadata={
            "help": "retry count for sdk speedtest-cli calls; adaptive mode uses --speed_http_trials instead"
        },
    )
    latency_test_urls: list[str] = field(default_factory=lambda: ["https://google.com", "https://github.com"])
    latency_test_times: int = field(default=1)
    latency_timeout: int = field(default=5000)
    latency_call_timeout: int = field(default=300)
    speedtest_call_timeout: int = field(default=300)
    core_restart_timeout: int = field(default=10)
    speed_http_sizes_mb: list[int] = field(
        default_factory=lambda: [1, 4, 8, 16],
        metadata={"help": "adaptive probe sizes in MiB, tried in ascending order"},
    )
    speed_http_min_duration: float = field(
        default=3.0,
        metadata={"help": "stop ramping once body transfer time reaches this many seconds"},
    )
    speed_http_trials: int = field(
        default=2,
        metadata={"help": "number of measurements at the selected adaptive probe size"},
    )
    speed_http_percentile: float = field(
        default=0.25,
        metadata={"help": "successful-goodput percentile used by adaptive mode"},
    )
    speed_http_connect_overhead: float = field(
        default=5.0,
        metadata={"help": "seconds added to the size-based adaptive probe budget for connect/TTFB"},
    )
    speed_http_connect_timeout: float = field(default=10.0)
    speed_http_read_timeout: float = field(
        default=30.0,
        metadata={"help": "per-chunk stall timeout in seconds, capped by the remaining probe budget"},
    )
    speed_http_max_transfer_seconds: float = field(
        default=30.0,
        metadata={"help": "cap on adaptive probe wall time (overhead + size / min_speed_threshold)"},
    )
    speed_http_chunk_size_kb: int = field(default=64)
    speed_http_ramp_fail_factor: float = field(
        default=0.85,
        metadata={"help": "multiply the adaptive score when a larger ramp size fails and a smaller size is reused"},
    )
    speed_outage_min_samples: int = field(
        default=5,
        metadata={"help": "minimum adaptive speed samples before treating a high fail rate as an endpoint outage"},
    )
    speed_outage_fail_ratio: float = field(
        default=0.8,
        metadata={
            "help": "adaptive low-speed fraction that skips failure-DB writes because the shared endpoint looks down"
        },
    )
    group_proxy_start: list[int] = field(
        default_factory=lambda: [3, 0, -2, -1, -1, 2, 2, 2, -1, -1, 3],
        metadata={"help": ">0: start position for proxies; -1: no proxy, copy all; -2: load balance"},
    )
    max_num: int = field(default=10, metadata={"help": "stop throughput tests after this many nodes meet --load_balance_thres"})
    load_balance_thres: float = field(
        default=1.0, metadata={"help": "minimum MiB/s score to count as a valid speed-test success / load-balance member"}
    )
    update_profile: bool = field(
        default=True, metadata={"help": "update profile before running tests in main"}
    )
    test_speed: bool = field(default=True, metadata={"help": "test speed in addition to latency"})
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


def apply_runtime_proxy_env() -> None:
    """Route outbound tests through the local clash mixed port, but keep localhost API direct."""
    os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = config_args.proxy_url
    os.environ["https_proxy"] = os.environ["HTTPS_PROXY"] = config_args.proxy_url
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"


def get_newest_profile() -> str:
    return max(config_args.profiles, key=lambda x: os.path.getmtime(x))
