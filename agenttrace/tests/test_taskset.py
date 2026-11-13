import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.llm import ScriptedLLM
from agenttrace.runner import TaskPackage, TaskRunner
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"


class TestTaskSet(unittest.TestCase):
    """任务集回归：每个任务用其脚本参照解跑一遍，必须全部通过。

    这同时验证任务包三要素的一致性：setup 中的缺陷可被参照解修复、
    隐藏测试判定的确会从失败转绿、scripted_actions 与任务文件路径对齐。
    """

    def test_every_task_passes_with_scripted_reference(self):
        task_dirs = sorted(p for p in TASKS_DIR.iterdir() if p.is_dir())
        self.assertGreaterEqual(len(task_dirs), 10, f"任务数不足: {[p.name for p in task_dirs]}")
        self.assertTrue((TASKS_DIR / "fix_leap_year" / "task.json").exists())

        failures: list[str] = []
        for d in task_dirs:
            task = TaskPackage.load(d)
            if not task.scripted_actions:
                failures.append(f"{task.id}: 缺少 scripted_actions")
                continue
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                store = TraceStore(Path(td) / "traces.db")
                try:
                    res = TaskRunner(store).run(task, ScriptedLLM(task.scripted_actions))
                finally:
                    store.close()
            if not (res.passed and res.status == "passed"):
                failures.append(f"{task.id}: status={res.status} passed={res.passed}")
        self.assertEqual(failures, [], "以下任务未通过参照解回归: " + "; ".join(failures))


if __name__ == "__main__":
    unittest.main()
