# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:35:05
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

from common.args import get_newest_profile, config_args, test_args
from common.api import MetaLifecycle
from function.fix import fix
from function.speed import test_latency_speed
from function.update import update


def main():
    meta = MetaLifecycle()
    if test_args.update_profile:
        update()
        fix(get_newest_profile())
        if config_args.mode == "meta":
            meta.start()
        else:
            input("Please reactivate profile manually and press ENTER to run latency test ...")
    test_latency_speed()
    meta.stop()


if __name__ == "__main__":
    main()
