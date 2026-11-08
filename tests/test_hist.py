"""Tests for hist/hist on a throwaway repo. Run: python3 -m unittest discover -s tests"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HIST = Path(__file__).resolve().parent.parent / "hist" / "hist"


def sh(*a, cwd, env=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=env, timeout=60)


class Hist(unittest.TestCase):
    def test_finds_old_commit_and_current_file(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@example.invalid", HIST_TRANSCRIPT_DIRS=d + "/none",
                       HIST_REPOS_FILE=d + "/none.txt")
            sh("git", "init", "-q", cwd=d)
            Path(d, "a.txt").write_text("rate limiter with backoff\n")
            sh("git", "add", ".", cwd=d)
            sh("git", "commit", "-q", "-m", "add rate limiter", cwd=d, env=env)
            r = sh(str(HIST), "rate limiter", cwd=d, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("add rate limiter", r.stdout)
            self.assertIn("a.txt", r.stdout)

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HIST_TRANSCRIPT_DIRS=d + "/none", HIST_REPOS_FILE=d + "/none.txt")
            sh("git", "init", "-q", cwd=d)
            r = sh(str(HIST), "zzz-nothing", cwd=d, env=env)
            self.assertIn("(no matches)", r.stdout)


if __name__ == "__main__":
    unittest.main()
