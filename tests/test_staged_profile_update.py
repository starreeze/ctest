import os
import tempfile
import unittest
from unittest.mock import patch

from common.utils import staged_profile_update


class StagedProfileUpdateTest(unittest.TestCase):
    def test_success_atomically_replaces_active_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = os.path.join(tmp, "profile.yaml")
            with open(profile_path, "w", encoding="utf-8") as profile_file:
                profile_file.write("original profile\n")

            original_replace = os.replace
            with patch("common.utils.os.replace", wraps=original_replace) as replace:
                with staged_profile_update(profile_path) as staged_path:
                    self.assertNotEqual(staged_path, profile_path)
                    self.assertEqual(os.path.dirname(staged_path), tmp)
                    with open(staged_path, encoding="utf-8") as staged_file:
                        self.assertEqual(staged_file.read(), "original profile\n")
                    with open(staged_path, "w", encoding="utf-8") as staged_file:
                        staged_file.write("completed profile\n")
                    with open(profile_path, encoding="utf-8") as profile_file:
                        self.assertEqual(profile_file.read(), "original profile\n")

            replace.assert_called_once_with(staged_path, profile_path)
            self.assertFalse(os.path.exists(staged_path))
            with open(profile_path, encoding="utf-8") as profile_file:
                self.assertEqual(profile_file.read(), "completed profile\n")

    def test_failure_leaves_active_profile_unchanged_and_removes_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = os.path.join(tmp, "profile.yaml")
            with open(profile_path, "w", encoding="utf-8") as profile_file:
                profile_file.write("original profile\n")

            staged_path = None
            with self.assertRaisesRegex(RuntimeError, "stage failed"):
                with staged_profile_update(profile_path) as staged_path:
                    with open(staged_path, "w", encoding="utf-8") as staged_file:
                        staged_file.write("incomplete profile\n")
                    raise RuntimeError("stage failed")

            self.assertIsNotNone(staged_path)
            self.assertFalse(os.path.exists(staged_path))
            with open(profile_path, encoding="utf-8") as profile_file:
                self.assertEqual(profile_file.read(), "original profile\n")


if __name__ == "__main__":
    unittest.main()
