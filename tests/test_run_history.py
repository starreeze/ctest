import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import common.run_history as run_history
from common.run_history import REDACTED, RunRecorder, redact_argv, resolve_run_origin


class RunHistoryTests(unittest.TestCase):
    def test_success_record_contains_identity_timing_and_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            run = RunRecorder(path, "manual", argv=["main.py", "--mode", "meta"], pid=123)
            run.set_profile(os.path.join(tmp, "profile.yaml"))
            run.record_stage("fix", {"input_proxies": 20, "preserved_proxies": 8})
            summary = run.finish()

            with open(path, encoding="utf-8") as history_file:
                stored = json.loads(history_file.read())
            self.assertEqual(stored, summary)
            self.assertEqual(stored["origin"], "manual")
            self.assertEqual(stored["pid"], 123)
            self.assertEqual(stored["argv"], ["main.py", "--mode", "meta"])
            self.assertIn("commit", stored["revision"])
            self.assertIsInstance(stored["revision"]["dirty"], bool)
            self.assertEqual(stored["status"], "success")
            self.assertGreaterEqual(stored["elapsed_seconds"], 0)
            self.assertEqual(stored["stages"]["fix"]["preserved_proxies"], 8)
            self.assertNotIn("error", stored)

    def test_failure_record_preserves_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            run = RunRecorder(path, "cron", argv=["main.py"])
            run.record_stage("update", {"merged_proxies": 100})
            run.finish(ValueError("bad profile"))

            with open(path, encoding="utf-8") as history_file:
                stored = json.loads(history_file.read())
            self.assertEqual(stored["status"], "failed")
            self.assertEqual(stored["error"], {"type": "ValueError", "message": "bad profile"})
            self.assertEqual(stored["stages"]["update"], {"merged_proxies": 100})

    def test_a_run_cannot_be_recorded_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = RunRecorder(os.path.join(tmp, "history.jsonl"), "manual")
            run.finish()
            with self.assertRaisesRegex(RuntimeError, "already recorded"):
                run.finish()

    def test_auto_origin_recognizes_cron_ancestor(self):
        with patch("common.run_history._ancestor_process_names", return_value=["bash", "cron"]):
            self.assertEqual(resolve_run_origin("auto"), "cron")
        with patch("common.run_history._ancestor_process_names", return_value=["bash", "systemd"]):
            self.assertEqual(resolve_run_origin("auto"), "manual")

    def test_sensitive_argv_values_are_redacted(self):
        self.assertEqual(
            redact_argv(
                [
                    "main.py",
                    "--controller_password",
                    "separate-secret",
                    "--controller_password=inline-secret",
                    "--mode",
                    "meta",
                ]
            ),
            [
                "main.py",
                "--controller_password",
                REDACTED,
                f"--controller_password={REDACTED}",
                "--mode",
                "meta",
            ],
        )

    def test_recorder_never_persists_controller_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            run = RunRecorder(
                path,
                "manual",
                argv=["main.py", "--controller_password=top-secret"],
            )
            run.finish()

            with open(path, encoding="utf-8") as history_file:
                stored_text = history_file.read()
            self.assertNotIn("top-secret", stored_text)
            self.assertEqual(json.loads(stored_text)["argv"][1], f"--controller_password={REDACTED}")

    def test_windows_lock_branch_locks_and_unlocks_sidecar_byte(self):
        lock_api = SimpleNamespace(LK_LOCK=1, LK_UNLCK=2, locking=Mock())
        with tempfile.TemporaryFile("w+b") as lock_file:
            with (
                patch("common.run_history.os.name", "nt"),
                patch("common.run_history.msvcrt", lock_api, create=True),
                run_history._exclusive_file_lock(lock_file),
            ):
                pass
        self.assertEqual(
            [call.args[1:] for call in lock_api.locking.call_args_list],
            [(lock_api.LK_LOCK, 1), (lock_api.LK_UNLCK, 1)],
        )


if __name__ == "__main__":
    unittest.main()
