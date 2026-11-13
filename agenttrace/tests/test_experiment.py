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
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def test_stats_pass_at_k_and_std(self):
        """R-04：pass@1 / pass^k / mean±std 统计口径。"""
        from agenttrace.llm import LLMResponse

        class HalfFailFactory:
            """第奇数次尝试整体失败、偶数次通过：pass@1=0.5、pass^k=0。"""

            def __init__(self):
                self.calls = 0

            def __call__(self, model, task):
                self.calls += 1
                scripted = ScriptedLLM(task.scripted_actions)
                fail_this_attempt = self.calls % 2 == 1

                class _LLM:
                    model = "half-fail"

                    def chat(self, messages):
                        if fail_this_attempt:
                            return LLMResponse("不是JSON，只是文字。", "half-fail", 5, 5)
                        return scripted.chat(messages)

                return _LLM()

        db = Path(self.tmp.name) / "exp-stats.db"
        summary = run_experiment(
            ["half-fail"], self.task_roots[:2], db,
            llm_factory=HalfFailFactory(), repeat=2, sleep_s=0,
        )
        a = summary["aggregates"]["half-fail"]
        # 2 任务 × 2 次：奇数次失败 → pass@1 = 0.5
        self.assertEqual(a["attempts"], 4)
        self.assertAlmostEqual(a["pass_at_1"], 0.5)
        # 每个任务都有一次失败 → pass^k = 0（稳定性与能力区分开）
        self.assertEqual(a["task_pass_at_k"], 0.0)
        # n=4 ≥ 2 → 标准差有值
        self.assertIsNotNone(a["token_std"])
        md = summary["summary_md"]
        self.assertIn("pass@1", md)
        self.assertIn("pass^k", md)
        self.assertIn("mean±std", md)
        # R-17：成本分析段落与未计价标注
        self.assertIn("通过率—成本分析", md)
        self.assertIn("帕累托", md)
        self.assertFalse(summary["aggregates"]["half-fail"]["cost_fully_priced"])

    def test_resume_skips_completed(self):
        """R-20：同库续跑跳过已完成组合，历史行回填、run_id 沿用。"""
        db = Path(self.tmp.name) / "exp-resume.db"
        first = run_experiment(["mA"], self.task_roots[:2], db,
                               llm_factory=scripted_factory, sleep_s=0)
        self.assertEqual(first["skipped"], 0)
        self.assertEqual(len(first["rows"]), 2)

        second = run_experiment(["mA"], self.task_roots[:2], db,
                                llm_factory=scripted_factory, sleep_s=0, resume=True)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(len(second["rows"]), 2)  # 历史行回填，不重复
        self.assertEqual(second["run_id"], first["run_id"])  # 沿用 run_id
        self.assertTrue(all(r.get("resumed") for r in second["rows"]))
        self.assertEqual(second["aggregates"]["mA"]["passed"], 2)

    def test_single_task_error_does_not_abort(self):
        """R-20：单任务抛异常被捕获记为失败行，其余任务继续执行。"""
        def exploding_factory(model, task):
            if task.id == self.task_roots[0].name:
                raise RuntimeError("boom: injected setup failure")
            return ScriptedLLM(task.scripted_actions)

        db = Path(self.tmp.name) / "exp-fault-tolerance.db"
        summary = run_experiment(["mX"], self.task_roots[:2], db,
                                 llm_factory=exploding_factory, sleep_s=0)
        self.assertEqual(len(summary["rows"]), 2)  # 两行都在
        self.assertEqual(summary["errored"], 1)
        self.assertEqual(sum(1 for r in summary["rows"] if r["passed"]), 1)
        err_row = [r for r in summary["rows"] if r.get("error")][0]
        self.assertIn("boom", err_row["error"])

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
