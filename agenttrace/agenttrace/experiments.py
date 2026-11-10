"""批量实验：N 个模型 × M 个任务的一条命令入口（M9，开题 P1 的多模型对比）。

防污染约定（可直接写入论文"实验设置"小节）：
- 实验数据库独立：默认按时间戳新建 experiments/exp-<时间戳>.db，
  并拒绝写入任何已存在的数据库文件（FileExistsError）；
- 每次尝试的 meta 记录 run_id / model / task / repeat / sandbox，分析可精确分组；
- 每次尝试使用全新临时工作区与全新对话历史（与单次运行同一保证）；
- 实验入口由 CLI 强制显式 --db 或时间戳新建，绝不触碰 traces.db / demo-traces.db。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .llm import OpenAICompatLLM
from .runner import TaskPackage, TaskRunner
from .store import TraceStore


def default_db_path(runs_dir: str | Path = "experiments") -> Path:
    """实验库默认路径：experiments/exp-<时间戳>.db（保证不与既有库冲突）。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(runs_dir) / f"exp-{ts}.db"


def default_llm_factory(model: str, task: TaskPackage):
    """默认工厂：OpenAI 兼容接口，按实验维度覆盖模型名。"""
    return OpenAICompatLLM(model=model)


def _tokens(store: TraceStore, attempt_id: str) -> tuple[int, int]:
    prompt = completion = 0
    for ev in store.events(attempt_id):
        if ev.type == "llm_call":
            u = ev.data.get("usage", {})
            prompt += u.get("prompt_tokens", 0)
            completion += u.get("completion_tokens", 0)
    return prompt, completion


def _duration_s(store: TraceStore, attempt_id: str) -> float | None:
    m = store.get_attempt(attempt_id)
    if m is None or not m.started_at or not m.finished_at:
        return None
    try:
        t0 = datetime.fromisoformat(m.started_at)
        t1 = datetime.fromisoformat(m.finished_at)
        return round((t1 - t0).total_seconds(), 3)
    except ValueError:
        return None


def _cell(rows: list[dict[str, Any]], model: str, task_id: str) -> str:
    rs = [r for r in rows if r["model"] == model and r["task"] == task_id]
    if not rs:
        return "—"
    ok = sum(1 for r in rs if r["passed"])
    tok = sum(r["total_tokens"] for r in rs)
    mark = "✅" if ok == len(rs) else "❌"
    return f"{mark} {ok}/{len(rs)} · {tok}tok"


def build_summary_md(
    run_id: str,
    started: str,
    models: list[str],
    task_ids: list[str],
    rows: list[dict[str, Any]],
    aggregates: dict[str, dict[str, Any]],
    mode: str,
) -> str:
    lines: list[str] = [
        f"# 实验报告 {run_id}",
        "",
        f"- 开始时间：{started}",
        f"- 模型：{', '.join(models)}",
        f"- 任务数：{len(task_ids)}",
        f"- 执行模式：{mode}",
        f"- 成本口径：tokens（单价按各平台价目折算）",
        "",
        "## 模型汇总",
        "",
        "| 模型 | 尝试数 | 通过 | 通过率 | 总 tokens | 平均 tokens | 平均耗时(s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in models:
        a = aggregates[m]
        rate = f"{a['pass_rate'] * 100:.1f}%" if a["attempts"] else "-"
        avg_dur = a["avg_duration_s"] if a["avg_duration_s"] is not None else "-"
        lines.append(
            f"| {m} | {a['attempts']} | {a['passed']} | {rate} | {a['total_tokens']} "
            f"| {a['avg_tokens']} | {avg_dur} |"
        )
    lines += ["", "## 任务 × 模型", "", "| 任务 | " + " | ".join(models) + " |",
              "|---|" * 1 + "---|" * len(models)]
    for t in task_ids:
        cells = " | ".join(_cell(rows, m, t) for m in models)
        lines.append(f"| {t} | {cells} |")
    lines.append("")
    return "\n".join(lines)


def run_experiment(
    models: list[str],
    task_dirs: list[Path],
    db_path: Path,
    llm_factory: Callable[[str, TaskPackage], Any] = default_llm_factory,
    max_turns: int = 10,
    use_docker: bool = False,
    repeat: int = 1,
    sleep_s: float = 0.0,
    progress: Callable[[str], None] = print,
    allow_local_commands: bool = False,
) -> dict[str, Any]:
    """顺序执行 models × tasks × repeat 次评测，落同一个实验库并产出对比报告。"""
    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(f"实验数据库已存在，拒绝覆盖（防污染）: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    started = datetime.now().isoformat(timespec="seconds")
    store = TraceStore(db_path)
    tasks = [TaskPackage.load(d) for d in task_dirs]
    task_ids = [t.id for t in tasks]
    mode = "docker" if use_docker else "local"

    rows: list[dict[str, Any]] = []
    total = len(models) * len(tasks) * repeat
    done = 0
    try:
        for model in models:
            for task in tasks:
                for rep in range(1, repeat + 1):
                    done += 1
                    progress(f"[{done}/{total}] model={model} task={task.id} repeat={rep}")
                    llm = llm_factory(model, task)
                    res = TaskRunner(store).run(
                        task, llm, max_turns=max_turns, use_docker=use_docker,
                        allow_local_commands=allow_local_commands,
                        meta_extra={
                            "run_id": run_id, "model": model,
                            "task": task.id, "repeat": rep,
                        },
                    )
                    p, c = _tokens(store, res.attempt_id)
                    rows.append({
                        "model": model,
                        "task": task.id,
                        "repeat": rep,
                        "attempt_id": res.attempt_id,
                        "status": res.status,
                        "passed": res.passed,
                        "prompt_tokens": p,
                        "completion_tokens": c,
                        "total_tokens": p + c,
                        "duration_s": _duration_s(store, res.attempt_id),
                    })
                    if sleep_s > 0 and done < total:
                        time.sleep(sleep_s)
    finally:
        store.close()

    aggregates: dict[str, dict[str, Any]] = {}
    for model in models:
        rs = [r for r in rows if r["model"] == model]
        passed = sum(1 for r in rs if r["passed"])
        toks = [r["total_tokens"] for r in rs]
        durs = [r["duration_s"] for r in rs if r["duration_s"] is not None]
        aggregates[model] = {
            "attempts": len(rs),
            "passed": passed,
            "pass_rate": (passed / len(rs)) if rs else 0.0,
            "total_tokens": sum(toks),
            "avg_tokens": round(sum(toks) / len(toks), 1) if toks else 0,
            "avg_duration_s": round(sum(durs) / len(durs), 2) if durs else None,
        }

    summary_md_path = db_path.with_name(db_path.stem + ".summary.md")
    md = build_summary_md(run_id, started, models, task_ids, rows, aggregates, mode)
    summary_md_path.write_text(md, encoding="utf-8")

    return {
        "run_id": run_id,
        "started": started,
        "db": str(db_path),
        "summary_md_path": str(summary_md_path),
        "summary_md": md,
        "rows": rows,
        "aggregates": aggregates,
        "total_failed": sum(1 for r in rows if not r["passed"]),
    }
