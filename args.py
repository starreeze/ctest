import logging, os
from rich.logging import RichHandler
from typing import cast
from dataclasses import dataclass, field
from transformers.hf_argparser import HfArgumentParser


@dataclass
class Config:
    unsupported_names: list[str] = field(default_factory=lambda: ["cipher: chacha20-poly1305"])
    profile_path: str = field(
        default="C:/Users/starreeze/AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev/profiles/RU8K3eg7uUeQ.yaml"
    )
    controller_url: str = field(default="http://127.0.0.1:9090")
    proxy_url: str = field(default="http://127.0.0.1:7890")


@dataclass
class TestArgs:
    speed_test_url: str = field(default="http://speedtest.tele2.net/100MB.zip")
    speed_test_retry: int = field(default=2)
    latency_test_url: str = field(default="http://www.google.com")
    latency_test_times: int = field(default=3)
    latency_timeout: int = field(default=5000)
    latency_call_timeout: int = field(default=60)
    speedtest_call_timeout: int = field(default=120)
    speed_duration: int = field(default=15)
    speed_window_size: int = field(default=5)
    group_proxy_start: list[int] = field(
        default_factory=lambda: [3, 0, -2, -1, -1, 2, 2, 2, -1, -1, 3],
        metadata={"help": ">0: start position for proxies; -1: no proxy, copy all; -2: load balance"},
    )
    max_num: int = field(default=3, metadata={"help": "the max valid proxies to return in speed test"})
    load_balance_thres: float = field(default=1.5, metadata={"help": "the min MB/s to be valid for load balancing"})
    latency_only: bool = field(default=False, metadata={"help": "only test latency"})


config_args, test_args = HfArgumentParser([Config, TestArgs]).parse_args_into_dataclasses()  # type: ignore
config_args = cast(Config, config_args)
test_args = cast(TestArgs, test_args)

logging.basicConfig(level="INFO", format="%(message)s", datefmt="[%X]", handlers=[RichHandler()])
logger = logging.getLogger("rich")
proxies = {"http": config_args.proxy_url, "https": config_args.proxy_url}
for name in proxies:
    os.environ[name.lower()] = os.environ[name.upper()] = proxies[name]
