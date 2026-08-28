import importlib
import logging
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class FakeRunRecorder:
    instances = []

    def __init__(self, history_path, origin):
        self.run_id = "test-run"
        self.origin = origin
        self.pid = 123
        self.profile = None
        self.stages = {}
        self.finished_with = None
        self.instances.append(self)

    def set_profile(self, profile_path):
        self.profile = profile_path

    def record_stage(self, name, metrics):
        self.stages[name] = metrics

    def finish(self, error=None):
        self.finished_with = error
        return {"elapsed_seconds": 0.1}


class MainProfileRollbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile_path = os.path.join(self.tmp.name, "profile.yaml")
        self.unrelated_path = os.path.join(self.tmp.name, "unrelated.txt")
        with open(self.profile_path, "w", encoding="utf-8") as profile_file:
            profile_file.write("original profile\n")
        with open(self.unrelated_path, "w", encoding="utf-8") as unrelated_file:
            unrelated_file.write("unrelated state\n")

        args_module = types.ModuleType("common.args")
        args_module.config_args = SimpleNamespace(
            mode="meta",
            run_history_path=os.path.join(self.tmp.name, "history.jsonl"),
            run_lock_path=os.path.join(self.tmp.name, "run.lock"),
            run_origin="manual",
        )
        args_module.test_args = SimpleNamespace(update_profile=True)
        args_module.apply_runtime_proxy_env = MagicMock()
        args_module.clear_proxy_env = MagicMock()
        args_module.get_newest_profile = MagicMock(return_value=self.profile_path)
        args_module.logger = logging.getLogger("test_main_profile_rollback")

        self.lifecycle = MagicMock()
        api_module = types.ModuleType("common.api")
        api_module.MetaLifecycle = MagicMock(return_value=self.lifecycle)

        history_module = types.ModuleType("common.run_history")
        history_module.RunRecorder = FakeRunRecorder

        self.update_module = types.ModuleType("function.update")
        self.update_module.update = MagicMock()
        self.fix_module = types.ModuleType("function.fix")
        self.fix_module.fix = MagicMock()
        self.speed_module = types.ModuleType("function.speed")
        self.speed_module.test_latency_speed = MagicMock()

        FakeRunRecorder.instances.clear()
        self.modules_patch = patch.dict(
            sys.modules,
            {
                "common.args": args_module,
                "common.api": api_module,
                "common.run_history": history_module,
                "function.update": self.update_module,
                "function.fix": self.fix_module,
                "function.speed": self.speed_module,
            },
        )
        self.modules_patch.start()
        sys.modules.pop("main", None)
        self.main = importlib.import_module("main")

    def tearDown(self):
        sys.modules.pop("main", None)
        self.modules_patch.stop()
        self.tmp.cleanup()

    def write_profile(self, content):
        with open(self.profile_path, "w", encoding="utf-8") as profile_file:
            profile_file.write(content)

    def rollback_files(self):
        return [
            name
            for name in os.listdir(self.tmp.name)
            if name.startswith(".profile.yaml.rollback-")
        ]

    def test_speed_failure_restores_profile_after_update_and_fix(self):
        self.update_module.update.side_effect = lambda: (
            self.write_profile("updated profile\n") or {"merged_proxies": 2}
        )
        self.fix_module.fix.side_effect = lambda _path: (
            self.write_profile("intermediate profile\n") or {"fixed_proxies": 2}
        )

        def fail_speed(**_kwargs):
            self.write_profile("broken final profile\n")
            raise TypeError("latency response mixed strings and integers")

        self.speed_module.test_latency_speed.side_effect = fail_speed

        with self.assertRaises(TypeError):
            self.main.main()

        with open(self.profile_path, encoding="utf-8") as profile_file:
            self.assertEqual(profile_file.read(), "original profile\n")
        with open(self.unrelated_path, encoding="utf-8") as unrelated_file:
            self.assertEqual(unrelated_file.read(), "unrelated state\n")
        self.assertEqual(self.rollback_files(), [])
        self.lifecycle.stop.assert_called_once_with()
        self.assertIsInstance(FakeRunRecorder.instances[0].finished_with, TypeError)

    def test_success_keeps_final_profile_and_removes_backup(self):
        self.update_module.update.side_effect = lambda: (
            self.write_profile("updated profile\n") or {"merged_proxies": 2}
        )
        self.fix_module.fix.return_value = {"fixed_proxies": 2}
        self.speed_module.test_latency_speed.side_effect = lambda **_kwargs: (
            self.write_profile("final profile\n") or {"preserved_proxies": 1}
        )

        self.main.main()

        with open(self.profile_path, encoding="utf-8") as profile_file:
            self.assertEqual(profile_file.read(), "final profile\n")
        self.assertEqual(self.rollback_files(), [])
        self.assertIsNone(FakeRunRecorder.instances[0].finished_with)


if __name__ == "__main__":
    unittest.main()
