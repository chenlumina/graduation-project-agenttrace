"""AgentTrace 核心 CLI。

用法示例：
    python -m agenttrace run-task tasks/fix_leap_year --db traces.db
    python -m agenttrace attempts --db traces.db
    python -m agenttrace export <attempt_id> --db traces.db --out trace.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .experiments import default_db_path, run_experiment
from .llm import OpenAICompatLLM, ScriptedLLM
from .metrics import classify, compute_metrics
from .runner import TaskPackage, TaskRunner
from .store import TraceStore


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="agenttrace", description="AI 编程 Agent 轨迹采集与评测平台（核心 CLI）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run-task", help="运行一次评测任务并采集轨迹")
    p_run.add_argument("task", help="任务目录（含 task.json）")
    p_run.add_argument("--db", default="traces.db", help="轨迹 SQLite 数据库路径")
    p_run.add_argument("--llm", default="scripted", choices=["scripted", "openai"])
    p_run.add_argument("--max-turns", type=int, default=8)
    p_run.add_argument("--mcp", action="store_true", help="通过真实 MCP 协议执行工具（含声明比对与权限审计）")
    p_run.add_argument("--docker", action="store_true", help="在 Docker 沙箱容器中执行 Agent 命令与测试（资源限额+断网+测试防篡改）")
    p_run.add_argument(
        "--allow-local-commands",
        action="store_true",
        help="本地（宿主机）模式下允许 Agent 执行 run_command；默认禁止（宿主执行等价于执行不可信代码）",
    )

    p_exp = sub.add_parser("export", help="导出某次尝试的完整轨迹 JSON")
    p_exp.add_argument("attempt_id")
    p_exp.add_argument("--db", default="traces.db")
    p_exp.add_argument("--out", required=True)
    p_exp.add_argument(
        "--no-redact", action="store_true",
        help="导出时保留原始内容（默认脱敏：遮蔽密钥/令牌/邮箱等敏感信息，R-22）",
    )

    p_purge = sub.add_parser("purge", help="清理历史轨迹（按保留天数，R-22）")
    p_purge.add_argument("--db", default="traces.db")
    p_purge.add_argument(
        "--days", type=int, required=True, help="保留最近 N 天，早于该窗口的尝试将被删除"
    )

    sub.add_parser("attempts", help="列出历史尝试").add_argument("--db", default="traces.db")

    p_srv = sub.add_parser("serve", help="启动 Web 服务（轨迹查看与回放）")
    p_srv.add_argument("--db", default="traces.db")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.add_argument(
        "--tasks", default="tasks",
        help="任务集目录（Web 端提交任务用；目录不存在则只提供回放）",
    )
    p_srv.add_argument(
        "--allow-llm-run", action="store_true",
        help="允许 Web 端用真实模型（.env 配置）提交任务，默认关闭",
    )
    p_srv.add_argument(
        "--allow-docker-run", action="store_true",
        help="允许 Web 端以 Docker 沙箱提交任务，默认关闭",
    )
    p_srv.add_argument(
        "--token",
        default=None,
        help="回放服务访问令牌（默认取环境变量 AGENTTRACE_API_TOKEN）；非回环地址必须提供",
    )

    p_met = sub.add_parser("metrics", help="计算某次尝试的两层指标与失败归因")
    p_met.add_argument("attempt_id")
    p_met.add_argument("--db", default="traces.db")

    p_x = sub.add_parser("experiment", help="批量实验：N 个模型 × 全部任务（独立实验库，防污染）")
    p_x.add_argument("--models", required=True, help="逗号分隔的模型名列表")
    p_x.add_argument("--db", default=None, help="实验库路径（默认按时间戳新建 experiments/exp-<ts>.db）")
    p_x.add_argument("--llm", default="openai", choices=["scripted", "openai"])
    p_x.add_argument("--repeat", type=int, default=1)
    p_x.add_argument(
        "--fault", default=None, choices=["slow_tool", "flaky_tool", "prompt_injection"],
        help="R-05 故障注入：扰动实验（profile 记入每次尝试 meta，报告标注）",
    )
    p_x.add_argument("--max-turns", type=int, default=10)
    p_x.add_argument("--docker", action="store_true")
    p_x.add_argument(
        "--allow-local-commands",
        action="store_true",
        help="本地模式下允许 Agent 执行 run_command（默认禁止）",
    )
    p_x.add_argument("--sleep", type=float, default=1.0, help="每次尝试之间的间隔秒数")
    p_x.add_argument(
        "--resume", action="store_true",
        help="R-20 断点续跑：目标库已存在时沿用 run_id 跳过已完成组合，单任务异常不中断整批",
    )

    args = parser.parse_args(argv)

    if args.cmd == "run-task":
        store = TraceStore(args.db)
        task = TaskPackage.load(args.task)
        if args.llm == "scripted":
            if not task.scripted_actions:
                parser.error("该任务未提供 scripted_actions，无法使用脚本 LLM")
            llm = ScriptedLLM(task.scripted_actions)
        else:
            llm = OpenAICompatLLM()
        if args.mcp and args.docker:
            parser.error("--mcp 与 --docker 暂不支持同时使用（MCP 工具服务器运行在宿主侧）")
        result = TaskRunner(store).run(
            task, llm, max_turns=args.max_turns, use_mcp=args.mcp, use_docker=args.docker,
            allow_local_commands=args.allow_local_commands,
        )

        counts: dict[str, int] = {}
        prompt = completion = 0
        for ev in store.events(result.attempt_id):
            counts[ev.type] = counts.get(ev.type, 0) + 1
            if ev.type == "llm_call":
                usage = ev.data.get("usage", {})
                prompt += usage.get("prompt_tokens", 0)
                completion += usage.get("completion_tokens", 0)

        print(
            json.dumps(
                {
                    "attempt_id": result.attempt_id,
                    "task": task.id,
                    "status": result.status,
                    "passed": result.passed,
                    "events": counts,
                    "tokens": {"prompt": prompt, "completion": completion},
                    "sandbox": "docker" if args.docker else "local",
                    "local_commands_allowed": bool(args.allow_local_commands and not args.docker),
                    "db": str(args.db),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.passed else 1

    if args.cmd == "serve":
        try:
            import uvicorn
        except ImportError:
            print("缺少依赖：请先执行 pip install fastapi uvicorn")
            return 2
        from .server import create_app

        token = args.token or os.environ.get("AGENTTRACE_API_TOKEN") or None
        loopback = args.host in ("127.0.0.1", "localhost", "::1")
        if not loopback and not token:
            print(
                "拒绝启动：绑定非回环地址时必须提供访问令牌"
                "（--token 或环境变量 AGENTTRACE_API_TOKEN），否则任何人都能读取全部轨迹。",
                file=sys.stderr,
            )
            return 2
        if not token:
            print("提示：当前未启用访问令牌，仅适合本机回环调试；对外暴露请用 --token。", file=sys.stderr)
        else:
            print(
                f"回放地址: http://{args.host}:{args.port}/?token={token}"
                "（令牌以查询参数传入，前端会转成 X-AgentTrace-Token 头）",
                file=sys.stderr,
            )
        uvicorn.run(
            create_app(
                args.db, token=token, tasks_dir=args.tasks,
                allow_llm_run=args.allow_llm_run, allow_docker_run=args.allow_docker_run,
            ),
            host=args.host, port=args.port, log_level="warning",
        )
        return 0

    if args.cmd == "metrics":
        store = TraceStore(args.db)
        meta = store.get_attempt(args.attempt_id)
        if meta is None:
            print(f"尝试不存在: {args.attempt_id}")
            return 1
        events = store.events(args.attempt_id)
        print(
            json.dumps(
                {
                    "attempt_id": args.attempt_id,
                    "metrics": compute_metrics(events),
                    "classification": classify(events),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "experiment":
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        if not models:
            parser.error("--models 不能为空")
        task_dirs = sorted(
            d for d in Path("tasks").iterdir()
            if d.is_dir() and (d / "task.json").exists()
        )
        if not task_dirs:
            parser.error("tasks/ 下没有任务")
        if args.db:
            db = Path(args.db)
            if db.exists() and not args.resume:
                parser.error(
                    f"实验库已存在，拒绝写入（防污染）: {db}（确需续跑请加 --resume）"
                )
        else:
            db = default_db_path()
            db.parent.mkdir(parents=True, exist_ok=True)
        if args.llm == "scripted":
            factory = lambda model, task: ScriptedLLM(task.scripted_actions)  # noqa: E731
        else:
            factory = lambda model, task: OpenAICompatLLM(model=model)  # noqa: E731
        summary = run_experiment(
            models, task_dirs, db, llm_factory=factory,
            max_turns=args.max_turns, use_docker=args.docker,
            repeat=args.repeat, sleep_s=args.sleep,
            allow_local_commands=args.allow_local_commands,
            fault=args.fault,  # R-05：扰动实验
            resume=args.resume,  # R-20：断点续跑
        )
        print(summary["summary_md"])
        print(f"\n实验库: {summary['db']}")
        print(f"报告: {summary['summary_md_path']}")
        return 0

    if args.cmd == "export":
        store = TraceStore(args.db)
        doc = store.export(args.attempt_id, redact=not args.no_redact)
        Path(args.out).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        mode = "（未脱敏）" if args.no_redact else "（已脱敏）"
        print(f"已导出 {len(doc['events'])} 个事件{mode} -> {args.out}")
        return 0

    if args.cmd == "purge":
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat(
            timespec="milliseconds"
        )
        store = TraceStore(args.db)
        removed = store.delete_attempts_before(cutoff)
        store.close()
        print(f"已清理 {removed} 个早于 {cutoff} 的尝试（保留最近 {args.days} 天）")
        return 0

    store = TraceStore(args.db)
    for m in store.list_attempts():
        print(f"{m.attempt_id}  {m.task_id}  {m.agent}/{m.model}  {m.status}  {m.started_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
