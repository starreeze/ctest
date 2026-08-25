# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:35:05
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

import contextlib
import fcntl
import time

from common.args import (
    apply_runtime_proxy_env,
    clear_proxy_env,
    config_args,
    get_newest_profile,
    logger,
    test_args,
)
from common.api import MetaLifecycle
from common.run_history import RunRecorder
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
    run_started_at = time.time()
    run = RunRecorder(config_args.run_history_path, config_args.run_origin)
    logger.info(
        f"Profile update run {run.run_id} started "
        f"(origin={run.origin}, pid={run.pid})"
    )
    try:
        with single_run_lock():
            clear_proxy_env()
            meta = MetaLifecycle()
            try:
                profile_path = get_newest_profile()
                run.set_profile(profile_path)
                if test_args.update_profile:
                    run.record_stage("update", update())
                    run.record_stage("fix", fix(profile_path))
                    if config_args.mode == "meta":
                        meta.start(profile_path)
                    else:
                        input("Please reactivate profile manually and press ENTER to run latency test ...")
                apply_runtime_proxy_env()
                run.record_stage(
                    "speed",
                    test_latency_speed(failure_cooldown_anchor=run_started_at),
                )
            finally:
                meta.stop()
                clear_proxy_env()
    except BaseException as exc:
        summary = run.finish(exc)
        logger.error(
            f"Profile update run {run.run_id} failed after "
            f"{summary['elapsed_seconds']:.3f}s: {type(exc).__name__}: {exc}"
        )
        raise
    summary = run.finish()
    logger.info(
        f"Profile update run {run.run_id} succeeded after {summary['elapsed_seconds']:.3f}s"
    )


if __name__ == "__main__":
    main()
