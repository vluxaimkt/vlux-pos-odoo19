from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_release


class TestOdooHomeResolution(unittest.TestCase):
    def make_git_checkout(self) -> Path:
        temp_dir = Path(tempfile.mkdtemp())
        subprocess.check_call(["git", "init"], cwd=temp_dir, stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "config", "user.email", "test@example.test"], cwd=temp_dir)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=temp_dir)
        (temp_dir / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "README.md"], cwd=temp_dir)
        subprocess.check_call(["git", "commit", "-m", "fixture"], cwd=temp_dir, stdout=subprocess.DEVNULL)
        return temp_dir

    def test_explicit_odoo_home_valid(self):
        checkout = self.make_git_checkout()
        commit = build_release.git_sha(checkout)
        with patch.object(build_release, "ODOO_COMMIT", commit):
            self.assertEqual(build_release.validate_odoo_baseline(checkout), checkout.resolve())

    def test_env_odoo_home_valid(self):
        checkout = self.make_git_checkout()
        commit = build_release.git_sha(checkout)
        with patch.dict(os.environ, {"ODOO_HOME": str(checkout)}), patch.object(
            build_release, "ODOO_COMMIT", commit
        ):
            self.assertEqual(build_release.validate_odoo_baseline(), checkout.resolve())

    def test_missing_path_fails(self):
        with self.assertRaises(SystemExit):
            build_release.resolve_odoo_home(Path(tempfile.gettempdir()) / "vlux-missing-odoo")

    def test_non_git_directory_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                build_release.resolve_odoo_home(directory)

    def test_wrong_commit_fails(self):
        checkout = self.make_git_checkout()
        with patch.object(build_release, "ODOO_COMMIT", "0" * 40):
            with self.assertRaises(SystemExit):
                build_release.validate_odoo_baseline(checkout)


if __name__ == "__main__":
    unittest.main()
