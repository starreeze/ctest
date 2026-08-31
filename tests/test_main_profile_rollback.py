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

    def write_profile(self, path, content):
        with open(path, "w", encoding="utf-8") as profile_file:
            profile_file.write(content)

    def read_profile(self, path=None):
        with open(path or self.profile_path, encoding="utf-8") as profile_file:
            return profile_file.read()

    def assert_staging_path(self, path):
        self.assertNotEqual(path, self.profile_path)
        self.assertEqual(os.path.dirname(path), self.tmp.name)

    def test_speed_failure_never_changes_active_profile(self):
        staging_paths = []

        def update(staging_path):
            self.assert_staging_path(staging_path)
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_path), "original profile\n")
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "updated profile\n")
            return {"merged_proxies": 2}

        def fix(staging_path):
            self.assert_staging_path(staging_path)
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_path), "updated profile\n")
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "intermediate profile\n")
            return {"fixed_proxies": 2}

        def fail_speed(profile_path, **_kwargs):
            staging_path = profile_path
            self.assert_staging_path(staging_path)
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_path), "intermediate profile\n")
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "broken final profile\n")
            raise TypeError("latency response mixed strings and integers")

        self.update_module.update.side_effect = update
        self.fix_module.fix.side_effect = fix
        self.speed_module.test_latency_speed.side_effect = fail_speed

        with self.assertRaises(TypeError):
            self.main.main()

        self.assertEqual(self.read_profile(), "original profile\n")
        with open(self.unrelated_path, encoding="utf-8") as unrelated_file:
            self.assertEqual(unrelated_file.read(), "unrelated state\n")
        self.assertEqual(staging_paths, [staging_paths[0]] * 3)
        self.assertFalse(os.path.exists(staging_paths[0]))
        self.lifecycle.stop.assert_called_once_with()
        self.assertIsInstance(FakeRunRecorder.instances[0].finished_with, TypeError)

    def test_success_replaces_active_profile_only_after_all_stages(self):
        staging_paths = []

        def update(staging_path):
            self.assert_staging_path(staging_path)
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_path), "original profile\n")
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "updated profile\n")
            return {"merged_proxies": 2}

        def fix(staging_path):
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_path), "updated profile\n")
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "fixed profile\n")
            return {"fixed_proxies": 2}

        def speed(profile_path, **_kwargs):
            staging_path = profile_path
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_path), "fixed profile\n")
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "final profile\n")
            return {"preserved_proxies": 1}

        self.update_module.update.side_effect = update
        self.fix_module.fix.side_effect = fix
        self.speed_module.test_latency_speed.side_effect = speed

        def stop_meta():
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_paths[0]), "final profile\n")

        self.lifecycle.stop.side_effect = stop_meta

        self.main.main()

        self.assertEqual(self.read_profile(), "final profile\n")
        self.assertEqual(staging_paths, [staging_paths[0]] * 3)
        self.assertFalse(os.path.exists(staging_paths[0]))
        self.lifecycle.start.assert_called_once_with(staging_paths[0])
        self.lifecycle.stop.assert_called_once_with()
        self.assertIsNone(FakeRunRecorder.instances[0].finished_with)

    def test_meta_shutdown_failure_keeps_active_profile_and_removes_staging(self):
        staging_paths = []

        def update(staging_path):
            staging_paths.append(staging_path)
            self.write_profile(staging_path, "updated profile\n")
            return {"merged_proxies": 2}

        def fix(staging_path):
            self.assertEqual(self.read_profile(), "original profile\n")
            self.write_profile(staging_path, "fixed profile\n")
            return {"fixed_proxies": 2}

        def speed(profile_path, **_kwargs):
            self.assertEqual(self.read_profile(), "original profile\n")
            self.write_profile(profile_path, "final profile\n")
            return {"preserved_proxies": 1}

        def fail_stop():
            self.assertEqual(self.read_profile(), "original profile\n")
            self.assertEqual(self.read_profile(staging_paths[0]), "final profile\n")
            raise RuntimeError("mihomo did not stop")

        self.update_module.update.side_effect = update
        self.fix_module.fix.side_effect = fix
        self.speed_module.test_latency_speed.side_effect = speed
        self.lifecycle.stop.side_effect = fail_stop

        with self.assertRaisesRegex(RuntimeError, "mihomo did not stop"):
            self.main.main()

        self.assertEqual(self.read_profile(), "original profile\n")
        self.assertFalse(os.path.exists(staging_paths[0]))
        self.lifecycle.stop.assert_called_once_with()
        self.assertIsInstance(FakeRunRecorder.instances[0].finished_with, RuntimeError)


if __name__ == "__main__":
    unittest.main()
