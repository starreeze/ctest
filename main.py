# -*- coding: utf-8 -*-
# @Date    : 2025-04-01 10:35:05
# @Author  : Shangyu.Xing (starreeze@foxmail.com)

from common.api import restart_core
from common.args import get_newest_profile, test_args
from function.fix import fix
from function.speed import test_latency_speed
from function.update import update


def main():
    if test_args.update_profile:
        update()
        fix(get_newest_profile())
        # restart_core()
        input("Please reactivate profile manually and press ENTER to run latency test ...")
    test_latency_speed()


if __name__ == "__main__":
    main()
