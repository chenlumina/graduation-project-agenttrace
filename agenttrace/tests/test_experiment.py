import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.experiments import default_db_path, run_experiment
from agenttrace.llm import ScriptedLLM
from agenttrace.runner import TaskPackage
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]


def scripted_factory(model, task):
    """离线工厂：忽略模型名，每个任务用其脚本参照解。"""
    return ScriptedLLM(task.scripted_actions)


class TestExperiment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        task_dirs = sorted(
            d for d in (ROOT / "tasks").iterdir()
            if d.is_dir() and (d / "task.json").exists()
        )
        self.task_roots = task_dirs[:3]

    def tearDown(self):
        self.tmp.cleanup()

    def test_orchestration_tagging_and_report(self):
        db = Path(self.tmp.name) / "experiments" / "exp-test.db"
        summary = run_experiment(
            ["modelA", "modelB"], self.task_roots, db,
            llm_factory=scripted_factory, sleep_s=0,
        )
        rows = summary["rows"]
        self.assertEqual(len(rows), 6)  # 2 模型 × 3 任务
        self.assertTrue(all(r["passed"] for r in rows))

        # 每次尝试都打标：meta 记录 run_id / model / repeat
        store = TraceStore(db)
        first = store.get_attempt(rows[0]["attempt_id"])
        self.assertEqual(first.meta["model"], rows[0]["model"])
        self.assertEqual(first.meta["run_id"], summary["run_id"])
        self.assertEqual(first.meta["repeat"], 1)
        store.close()

        # 报告生成
        md = Path(summary["summary_md_path"]).read_text(encoding="utf-8")
        self.assertIn("实验报告", md)
        self.assertIn("modelA", md)
        self.assertIn("任务 × 模型", md)

        # 聚合：两模型同任务同参照解 → 结果应一致（编排无串扰的证据）
        self.assertEqual(summary["aggregates"]["modelA"]["attempts"], 3)
        self.assertEqual(summary["aggregates"]["modelA"]["passed"], 3)
        self.assertAlmostEqual(summary["aggregates"]["modelA"]["pass_rate"], 1.0)
        self.assertEqual(
            summary["aggregates"]["modelB"]["total_tokens"],
            summary["aggregates"]["modelA"]["total_tokens"],
        )

    def test_refuses_existing_db(self):
        db = Path(self.tmp.name) / "existing.db"
        db.write_bytes(b"x")
        with self.assertRaises(FileExistsError):
            run_experiment(["m"], self.task_roots, db,
                           llm_factory=scripted_factory, sleep_s=0)

    def test_default_db_path_is_isolated(self):
        # 显式目录：落在提供的目录下，文件名带时间戳，且不会预先存在
        db = default_db_path(Path(self.tmp.name) / "experiments")
        self.assertEqual(db.parent.name, "experiments")
        self.assertTrue(db.name.startswith("exp-"))
        self.assertFalse(db.exists())
        # 默认目录名即 experiments（与日常调试库 traces.db 天然隔离）
        self.assertEqual(default_db_path().parent.name, "experiments")


if __name__ == "__main__":
    unittest.main()
