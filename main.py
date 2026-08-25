# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:35:05
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

import contextlib
import fcntl

from common.args import (
    apply_runtime_proxy_env,
    clear_proxy_env,
    config_args,
    get_newest_profile,
    logger,
    test_args,
)
from common.api import MetaLifecycle
from function.fix import fix
from function.speed import test_latency_speed
from function.update import update


@contextlib.contextmanager
def single_run_lock():
    with open(config_args.run_lock_path, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another profile update holds {config_args.run_lock_path}") from exc
        yield


def main():
    with single_run_lock():
        clear_proxy_env()
        meta = MetaLifecycle()
        try:
            if test_args.update_profile:
                update()
                profile_path = get_newest_profile()
                fix(profile_path)
                if config_args.mode == "meta":
                    meta.start(profile_path)
                else:
                    input("Please reactivate profile manually and press ENTER to run latency test ...")
            apply_runtime_proxy_env()
            test_latency_speed()
        finally:
            meta.stop()
            clear_proxy_env()
            logger.info("Profile update run finished")


if __name__ == "__main__":
    main()
