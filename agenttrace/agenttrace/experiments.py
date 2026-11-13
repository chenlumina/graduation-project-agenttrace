"""批量实验：N 个模型 × M 个任务的一条命令入口（M9，开题 P1 的多模型对比）。

防污染约定（可直接写入论文"实验设置"小节）：
- 实验数据库独立：默认按时间戳新建 experiments/exp-<时间戳>.db，
  并拒绝写入任何已存在的数据库文件（FileExistsError）；
- 每次尝试的 meta 记录 run_id / model / task / repeat / sandbox，分析可精确分组；
- 每次尝试使用全新临时工作区与全新对话历史（与单次运行同一保证）；
- 实验入口由 CLI 强制显式 --db 或时间戳新建，绝不触碰 traces.db / demo-traces.db。

统计口径（R-04 起，建议论文沿用）：
- pass@1 ：单次尝试通过率 = 通过次数 / 尝试次数，刻画能力上限；
- pass^k ：同一"模型×任务"组合 repeat 次全部通过的任务比例，刻画稳定性
  （pass@1 相同的两个模型，pass^k 可以拉开差距）；
- 均值与标准差：tokens / 耗时在 n≥2 时报告 mean±std（样本标准差），n=1 时仅报告均值。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from statistics import stdev as _stdev
from typing import Any, Callable

from .llm import OpenAICompatLLM
from .pricing import compute_cost
from .runner import TaskPackage, TaskRunner
from .store import TraceStore


def default_db_path(runs_dir: str | Path = "experiments") -> Path:
    """实验库默认路径：experiments/exp-<时间戳>.db（保证不与既有库冲突）。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(runs_dir) / f"exp-{ts}.db"


def default_llm_factory(model: str, task: TaskPackage):
    """默认工厂：OpenAI 兼容接口，按实验维度覆盖模型名。"""
    return OpenAICompatLLM(model=model)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float | None:
    """样本标准差；n<2 时无意义，返回 None（报告仅显示均值）。"""
    return round(_stdev(xs), 2) if len(xs) >= 2 else None


def _fmt_mean_std(xs: list[float], unit: str = "") -> str:
    m = _mean(xs)
    s = _std(xs)
    if s is None:
        return f"{m:.1f}{unit}"
    return f"{m:.1f}±{s}{unit}"


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
    """任务×模型单元格：通过次数（pass@1）+ 稳定性标记（pass^k）+ token 均值±std。"""
    rs = [r for r in rows if r["model"] == model and r["task"] == task_id]
    if not rs:
        return "—"
    ok = sum(1 for r in rs if r["passed"])
    n = len(rs)
    toks = [float(r["total_tokens"]) for r in rs]
    # pass^k 视角：n 次全部通过（稳定）/ 部分通过（不稳定）/ 全失败
    mark = "✅" if ok == n else ("◐" if ok > 0 else "❌")
    return f"{mark} {ok}/{n} p@1={ok / n:.2f} · {_fmt_mean_std(toks)}tok"


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
        f"- 成本口径（R-17）：tokens × 单价（pricing.py 价目表，AGENTTRACE_PRICE_FILE 可覆盖；无价目模型如实标注未计价）",
        f"- 统计口径（R-04）：pass@1 = 通过次数/尝试次数（能力上限）；"
        f"pass^k = 同组合多次全部通过的任务比例（稳定性）；"
        f"tokens/耗时为 mean±std（n≥2 时样本标准差）",
        "",
        "## 模型汇总",
        "",
        "| 模型 | 尝试数 | 通过 | pass@1 | 任务 pass^k | 总 tokens | 平均 tokens | token std | 平均耗时(s) | 耗时 std | 总成本(¥) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in models:
        a = aggregates[m]
        rate = f"{a['pass_at_1'] * 100:.1f}%" if a["attempts"] else "-"
        passk = (
            f"{a['task_pass_at_k'] * 100:.1f}%"
            if a.get("task_total") else "-"
        )
        tok_std = a["token_std"] if a["token_std"] is not None else "-"
        dur_std = a["duration_std"] if a["duration_std"] is not None else "-"
        avg_dur = a["avg_duration_s"] if a["avg_duration_s"] is not None else "-"
        cost_txt = f"{a['total_cost_cny']:.4f}" + ("" if a["cost_fully_priced"] else "*")
        lines.append(
            f"| {m} | {a['attempts']} | {a['passed']} | {rate} | {passk} | {a['total_tokens']} "
            f"| {a['avg_tokens']} | {tok_std} | {avg_dur} | {dur_std} | {cost_txt} |"
        )
    lines.append("")
    lines.append("注：带 * 号的模型价目表未收录，成本为部分折算。")

    # R-17：通过率—成本联合分析（"效果更好但成本更高"的权衡呈现）
    lines += [
        "",
        "## 通过率—成本分析",
        "",
        "| 模型 | pass@1 | 任务 pass^k | 总成本(¥) | 单次通过成本(¥) | 性价比 |",
        "|---|---|---|---|---|---|",
    ]
    # 帕累托前沿：pass@1 不更低且成本不更高即占优
    for m in models:
        a = aggregates[m]
        cost = a["total_cost_cny"]
        per_pass = cost / a["passed"] if a["passed"] else None
        dominated = any(
            other != m
            and aggregates[other]["pass_at_1"] >= a["pass_at_1"]
            and aggregates[other]["total_cost_cny"] <= cost
            and (
                aggregates[other]["pass_at_1"] > a["pass_at_1"]
                or aggregates[other]["total_cost_cny"] < cost
            )
            for other in models
        )
        tag = "被占优" if dominated else "帕累托最优"
        per_pass_txt = f"{per_pass:.4f}" if per_pass is not None else "-"
        lines.append(
            f"| {m} | {a['pass_at_1'] * 100:.1f}% | {a['task_pass_at_k'] * 100:.1f}% "
            f"| {cost:.4f} | {per_pass_txt} | {tag} |"
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
    fault: "str | Any | None" = None,  # R-05：故障注入 profile（名称或 FaultProfile）
    resume: bool = False,  # R-20：断点续跑（同库跳过已完成组合）
) -> dict[str, Any]:
    """顺序执行 models × tasks × repeat 次评测，落同一个实验库并产出对比报告。

    fault：R-05 扰动实验——传预置 profile 名（slow_tool / flaky_tool / prompt_injection）
    或 FaultProfile 实例；每次尝试的 meta 记录 fault 配置，报告标注扰动模式。

    resume（R-20）：目标库已存在且 resume=True 时续跑——沿用库中既有 run_id，
    跳过已完成的 (model, task, repeat) 组合，历史行回填进报告；单任务异常被捕获
    并记为失败行，不中断整批实验（异常信息写入 rows[].error）。
    """
    from .faults import resolve_fault
    profile = resolve_fault(fault) if isinstance(fault, str) else fault

    db_path = Path(db_path)
    db_exists = db_path.exists()
    if db_exists and not resume:
        raise FileExistsError(f"实验数据库已存在，拒绝覆盖（防污染）: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    started = datetime.now().isoformat(timespec="seconds")
    store = TraceStore(db_path)
    tasks = [TaskPackage.load(d) for d in task_dirs]
    task_ids = [t.id for t in tasks]
    mode = "docker" if use_docker else "local"
    if profile is not None:
        mode += f"+fault:{profile.name}"

    # R-20：续跑时沿用既有 run_id 并从历史轨迹回填已完成组合
    rows: list[dict[str, Any]] = []
    if db_exists:
        history = [m for m in store.list_attempts() if m.meta.get("run_id")]
        run_ids = sorted({str(m.meta["run_id"]) for m in history})
        run_id = run_ids[0] if run_ids else f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        for m in history:
            if str(m.meta.get("run_id")) != run_id:
                continue
            p, c = _tokens(store, m.attempt_id)
            rows.append({
                "model": str(m.meta.get("model", "")),
                "task": str(m.meta.get("task", "")),
                "repeat": int(m.meta.get("repeat", 1)),
                "attempt_id": m.attempt_id,
                "status": m.status,
                "passed": m.status == "passed",
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": p + c,
                "cost_cny": compute_cost(str(m.meta.get("model", "")), p, c),
                "duration_s": _duration_s(store, m.attempt_id),
                "resumed": True,
            })
    else:
        run_id = f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    done_keys = {(r["model"], r["task"], r["repeat"]) for r in rows}
    skipped = 0

    total = len(models) * len(tasks) * repeat
    done = 0
    try:
        for model in models:
            for task in tasks:
                for rep in range(1, repeat + 1):
                    done += 1
                    if (model, task.id, rep) in done_keys:
                        skipped += 1
                        progress(f"[{done}/{total}] 跳过（已完成）model={model} task={task.id} repeat={rep}")
                        continue
                    progress(f"[{done}/{total}] model={model} task={task.id} repeat={rep}")
                    try:
                        llm = llm_factory(model, task)
                        res = TaskRunner(store).run(
                            task, llm, max_turns=max_turns, use_docker=use_docker,
                            allow_local_commands=allow_local_commands,
                            faults=profile,  # R-05：扰动注入
                            meta_extra={
                                "run_id": run_id, "model": model,
                                "task": task.id, "repeat": rep,
                                **({"fault": profile.to_meta()} if profile is not None else {}),
                            },
                        )
                    except Exception as e:  # R-20：单任务异常不中断整批实验
                        progress(f"  ! 任务异常已捕获: {type(e).__name__}: {e}")
                        rows.append({
                            "model": model, "task": task.id, "repeat": rep,
                            "attempt_id": "", "status": "error", "passed": False,
                            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                            "cost_cny": 0.0, "duration_s": None,
                            "error": f"{type(e).__name__}: {e}",
                        })
                        continue
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
                        "cost_cny": compute_cost(model, p, c),  # R-17：无价目模型为 None
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
        toks = [float(r["total_tokens"]) for r in rs]
        durs = [r["duration_s"] for r in rs if r["duration_s"] is not None]
        # R-04：pass^k = 同"模型×任务"组合 repeat 次全部通过的任务比例（稳定性）
        task_ids_of_model = sorted({r["task"] for r in rs})
        stable_tasks = [
            t for t in task_ids_of_model
            if all(r["passed"] for r in rs if r["task"] == t)
        ]
        aggregates[model] = {
            "attempts": len(rs),
            "passed": passed,
            "pass_at_1": (passed / len(rs)) if rs else 0.0,
            "pass_rate": (passed / len(rs)) if rs else 0.0,  # 兼容字段
            "task_total": len(task_ids_of_model),
            "task_pass_at_k": (len(stable_tasks) / len(task_ids_of_model))
            if task_ids_of_model else 0.0,
            "total_tokens": sum(toks),
            "avg_tokens": round(_mean(toks), 1) if toks else 0,
            "token_std": _std(toks),
            "avg_duration_s": round(_mean(durs), 2) if durs else None,
            "duration_std": _std([float(d) for d in durs]) if durs else None,
            # R-17：成本金额（存在无价目尝试时标注未完全计价）
            "total_cost_cny": round(
                sum(r["cost_cny"] for r in rs if r["cost_cny"] is not None), 6
            ),
            "cost_fully_priced": all(r["cost_cny"] is not None for r in rs),
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
        "skipped": skipped,  # R-20：续跑跳过的已完成组合数
        "errored": sum(1 for r in rows if r.get("error")),  # R-20：异常捕获的失败行数
    }
