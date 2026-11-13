import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestCLI(unittest.TestCase):
    def test_run_task_and_export(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "traces.db"
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
            proc = subprocess.run(
                [sys.executable, str(ROOT / "atp.py"), "run-task", "tasks/fix_leap_year", "--db", str(db)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=120,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            out = json.loads(proc.stdout)
            self.assertTrue(out["passed"])
            self.assertEqual(out["status"], "passed")
            self.assertEqual(out["events"]["llm_call"], 4)

            exp_path = Path(tmp) / "trace.json"
            proc2 = subprocess.run(
                [
                    sys.executable, str(ROOT / "atp.py"), "export",
                    out["attempt_id"], "--db", str(db), "--out", str(exp_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=60,
            )
            self.assertEqual(proc2.returncode, 0, msg=proc2.stdout + proc2.stderr)
            doc = json.loads(exp_path.read_text(encoding="utf-8"))
            self.assertEqual(doc["schema_version"], 4)
            self.assertGreaterEqual(len(doc["events"]), 10)

    def test_metrics_command(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "traces.db"
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
            atp = str(ROOT / "atp.py")
            proc = subprocess.run(
                [sys.executable, atp, "run-task", "tasks/fix_leap_year", "--db", str(db)],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            attempt_id = json.loads(proc.stdout)["attempt_id"]
            proc2 = subprocess.run(
                [sys.executable, atp, "metrics", attempt_id, "--db", str(db)],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=60,
            )
            self.assertEqual(proc2.returncode, 0, msg=proc2.stdout + proc2.stderr)
            out = json.loads(proc2.stdout)
            self.assertIn("metrics", out)
            self.assertIn("classification", out)
            self.assertEqual(out["classification"]["primary"], "passed")


if __name__ == "__main__":
    unittest.main()
