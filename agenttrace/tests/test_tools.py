import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.tools import Workspace


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ws = Workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.ws.read("../secret.txt")
        with self.assertRaises(ValueError):
            self.ws.write("../evil.txt", "x")

    def test_write_new_file_diff(self):
        diff = self.ws.write("f.txt", "a\nb\n")
        self.assertIn("+a", diff)
        self.assertIn("+b", diff)

    def test_write_modify_diff(self):
        self.ws.write("f.txt", "a\nb\n")
        diff = self.ws.write("f.txt", "a\nc\n")
        self.assertIn("-b", diff)
        self.assertIn("+c", diff)

    def test_run_command(self):
        res = self.ws.run([sys.executable, "-c", "print(1)"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("1", res["stdout_tail"])

    def test_run_command_failure(self):
        res = self.ws.run([sys.executable, "-c", "import sys; sys.exit(3)"])
        self.assertEqual(res["exit_code"], 3)

    def test_run_timeout(self):
        res = self.ws.run([sys.executable, "-c", "import time; time.sleep(3)"], timeout=1)
        self.assertEqual(res["exit_code"], -1)


if __name__ == "__main__":
    unittest.main()
