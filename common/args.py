import logging
from logging.handlers import RotatingFileHandler
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
    controller_timeout: float = field(default=10.0)
    proxy_url: str = field(default="http://127.0.0.1:7890")
    mixed_port: int = field(
        default=7890,
        metadata={"help": "mixed HTTP/SOCKS listener port written to the final profile"},
    )
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
    subconvert_attempts: int = field(
        default=3,
        metadata={"help": "total attempts per subconverter backend before trying the next backend"},
    )
    subconvert_user_agent: str = field(default="clash-meta/1.19.30")
    target_group: str = field(default="🔰 节点选择")
    max_proxies_per_group: int = field(
        default=100, metadata={"help": "maximum proxies per test group for splitting large groups"}
    )
    failure_db_path: str = field(
        default="", metadata={"help": "path to failure tracking database (default: local app data)"}
    )
    run_history_path: str = field(
        default="", metadata={"help": "JSONL run summary path (default: local app data)"}
    )
    run_origin: Literal["auto", "cron", "manual"] = field(
        default="auto",
        metadata={"help": "invocation origin; auto recognizes cron from Linux process ancestry"},
    )
    failure_cooldown_days: list[int] = field(
        default_factory=lambda: [0, 1, 3, 7],
        metadata={
            "help": "host cooldown day tiers after consecutive failed runs; positive tiers use the configured head start"
        },
    )
    failure_cooldown_head_start_hours: int = field(
        default=1,
        metadata={"help": "hours subtracted from each positive host cooldown tier"},
    )
    load_balance_strategy: str = field(
        default="round-robin", metadata={"help": "strategy for load-balance proxy groups"}
    )
    meta_start_command: str = field(default="mihomo -d profiles")
    run_lock_path: str = field(default="/tmp/clash-profile-update.lock")
    log_level: str = field(default="INFO")
    log_file: str = field(default="")
    log_max_bytes: int = field(default=10 * 1024 * 1024)
    log_backup_count: int = field(default=5)
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
        metadata={
            "help": "adaptive probe wall-time cap; also the full budget when speed_http_deadline_rate_kibps is 0"
        },
    )
    speed_http_deadline_rate_kibps: int = field(
        default=512,
        metadata={
            "help": "KiB/s rate used only to tighten adaptive probe deadlines; 0 uses the full wall-time cap"
        },
    )
    speed_http_chunk_size_kb: int = field(default=64)
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
    speed_retain_min_mibps: float = field(
        default=0.0,
        metadata={"help": "inclusive numeric MiB/s threshold for retaining a latency-valid proxy"},
    )
    speed_avoid_cooldown_min_mibps: float = field(
        default=0.0,
        metadata={"help": "inclusive numeric MiB/s threshold that prevents a host cooldown"},
    )
    speed_load_balance_min_mibps: float = field(
        default=1.0,
        metadata={"help": "inclusive numeric MiB/s threshold for load-balance membership"},
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
app_data_dir = os.path.join(local_appdata, "clash-proxy-tester")
if not config_args.failure_db_path:
    os.makedirs(app_data_dir, exist_ok=True)
    config_args.failure_db_path = os.path.join(app_data_dir, "proxy_failures.db")
if not config_args.run_history_path:
    os.makedirs(app_data_dir, exist_ok=True)
    config_args.run_history_path = os.path.join(app_data_dir, "run_history.jsonl")
if not config_args.profiles:
    config_args.profiles = [
        os.path.join(config_args.profile_dir, f)
        for f in os.listdir(config_args.profile_dir)
        if (f.endswith(".yaml") or f.endswith(".yml"))
        and os.path.getsize(os.path.join(config_args.profile_dir, f))
        > config_args.profile_size_filter_kb * 1024
    ]

log_level = getattr(logging, config_args.log_level.upper(), None)
if not isinstance(log_level, int):
    raise ValueError(f"Invalid log level: {config_args.log_level}")
handlers: list[logging.Handler] = [RichHandler()]
if config_args.log_file:
    file_handler = RotatingFileHandler(
        config_args.log_file,
        maxBytes=config_args.log_max_bytes,
        backupCount=config_args.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )
    handlers.append(file_handler)
logging.basicConfig(level=log_level, format="%(message)s", datefmt="[%X]", handlers=handlers)
logger = logging.getLogger("rich")


PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
)


def clear_proxy_env() -> None:
    """Remove inherited proxy routing before direct feed or controller requests."""
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)


def apply_runtime_proxy_env() -> None:
    """Route outbound tests through the local clash mixed port, but keep localhost API direct."""
    clear_proxy_env()
    os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = config_args.proxy_url
    os.environ["https_proxy"] = os.environ["HTTPS_PROXY"] = config_args.proxy_url
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"


def get_newest_profile() -> str:
    return max(config_args.profiles, key=lambda x: os.path.getmtime(x))
