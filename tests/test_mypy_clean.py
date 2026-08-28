"""
mypy type-check test (iter #13) — CI smoke test for type safety.

确保 mypy 在当前代码上 0 错误。如果以后有人引入 type 不一致的代码, 会 fail。
运行耗时约 30s, 所以是单独 test, 不会被 default pytest collection 强制跑。
"""
import os
import subprocess
import sys
import unittest


class TestMypyClean(unittest.TestCase):
    """CI: mypy must report 0 errors on agents/ optimization/ web/backend/"""

    @unittest.skipIf(
        os.environ.get("SKIP_MYPY_TEST") == "1",
        "SKIP_MYPY_TEST=1 set (e.g. CI without mypy installed)"
    )
    def test_mypy_zero_errors(self):
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        env = os.environ.copy()
        env["MYPYPATH"] = repo_root

        result = subprocess.run(
            [
                sys.executable, "-m", "mypy",
                "agents/", "optimization/", "web/backend/",
                "--ignore-missing-imports",
                "--no-strict-optional",
                "--explicit-package-bases",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        # 排除 notes/annotations; 只看 errors
        output = result.stdout + result.stderr
        error_lines = [
            line for line in output.split("\n")
            if " error:" in line and "annotation-unchecked" not in line
        ]
        if error_lines:
            self.fail(
                f"mypy found {len(error_lines)} error(s):\n"
                + "\n".join(error_lines[:20])
            )


if __name__ == "__main__":
    unittest.main()
