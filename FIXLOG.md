# AgentTrace 评审问题修复日志

> 依据《AgentTrace_代码评审报告.docx》（2026-11-10）逐项修复。
> 留痕方式：本日志逐项记录 + 每项修复独立 git commit（消息标注 R 编号）+ 评审报告 docx 追加"修复进度归档"章节。
> 修复顺序按报告优先级：P0（R-01/R-03/R-04/R-05）与 P1 代码层交叉推进，先做决策与低风险代码修复，再做结构性改造。

## 修复进度总表

| 编号 | 问题 | 级别 | 状态 | 完成日期 | 验证方式 |
|---|---|---|---|---|---|
| R-01 | 题目口径不统一（分布式链路追踪名不副实） | 阻断 | ✅ 已完成 | 2026-11-11 | 全仓库文本核查 |
| R-07 | events 表 seq 无唯一约束 | 严重 | ✅ 已完成 | 2026-11-11 | test_store 9 项通过 |
| R-10 | file_diff parent_id 层级错位 | 一般 | 待修复 | — | — |
| R-09 | 事件缺少统一耗时字段 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 119 项通过 |
| R-11 | 缺少采样参数与版本记录 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 120 项通过 |
| R-15 | 解析失败直接终止，策略不一致 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 122 项通过 |
| R-14 | 失败归因依赖中文文本匹配 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 126 项通过 |
| R-08 | llm_call 全量落库，存储平方级膨胀 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 126 项通过 |
| R-03 | 任务集天花板效应 | 阻断 | ✅ 已完成 | 2026-11-11 | taskset 参照解回归全过 |
| R-16 | 循环检测口径过窄 | 一般 | ✅ 已完成 | 2026-11-13 | n-gram+无进展+重复率拆分 |
| R-18 | 缺少上下文/截断指标 | 建议 | ✅ 已完成 | 2026-11-13 | read_file 分页 + 指标 |
| R-19 | 接口无分页无缓存 | 一般 | ✅ 已完成 | 2026-11-13 | 分页/分段/指标预计算 |
| R-20 | 实验容错与续跑 | 一般 | ✅ 已完成 | 2026-11-13 | 续跑+单任务容错 |
| R-21 | 注入检测定位表述 | 一般 | ✅ 已完成 | 2026-11-13 | 文档口径修正 |
| R-22 | 轨迹无脱敏/无保留策略 | 一般 | ✅ 已完成 | 2026-11-13 | 脱敏+purge |
| R-23 | 字体外链与离线承诺冲突 | 建议 | ✅ 已完成 | 2026-11-13 | 自托管字体+CSP |
| R-12 | 序号内存分配 | 一般 | ✅ 已完成 | 2026-11-13 | 存储层事务内分配 |
| R-13 | 逐事件单独提交 | 一般 | ✅ 已完成 | 2026-11-13 | WAL+批量写入 |
| P0-gap | Web 端无任务提交入口 | — | ✅ 已完成 | 2026-11-13 | /api/run + 前端提交栏 |
| R-17 | 缺少成本金额指标 | 一般 | ✅ 已完成 | 2026-11-13 | 价目表+通过率—成本帕累托分析 |
| R-04 | 实验统计口径不足 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 127 项通过 |
| R-05 | 缺少故障注入实验 | 严重 | ✅ 已完成 | 2026-11-11 | 全量 133 项通过 |

---

## R-01　题目口径统一（阻断，P0）

- **决策**：采用报告方案 A —— 统一为"面向 AI 编程 Agent 的执行轨迹采集、可视化与评测平台"，不补分布式架构（成本最低，且与系统实际形态一致）。
- **核查结果**（2026-11-11）：
  - 根 `README.md`、`agenttrace/README.md`：已采用"执行轨迹采集、可视化与评测平台"口径；
  - `毕设材料/开题报告初稿.docx`、`毕设材料/毕设选题一页纸.docx`：解包全文检索，无"分布式/链路追踪"残留；
  - 全仓库 `*.md / *.py / *.txt` 检索：无旧口径残留。
- **结论**：仓库、开题、选题三处口径已一致，无需代码改动。论文写作时沿用此口径即可。
- **遗留说明**：R-02（OTel GenAI 语义映射）随本项决策降为 P1 文档层工作，后续以"自定义事件 → OTel GenAI span 映射表"形式补充，不改事件模型。

---

## R-07　events 表 seq 唯一约束（严重，P1）

- **改动**：
  - `store.py`：`idx_events_attempt_seq` 改为 `CREATE UNIQUE INDEX`；新增 `_ensure_unique_seq_index()`，旧库打开时检测普通索引并自动 DROP 重建（历史库存在重复数据时抛出明确 TraceValidationError）。
  - `tests/test_store.py`：新增 3 项回归——数据库层重复 seq 拒写、`append_event` 重复 seq 抛 TraceValidationError 且不污染已有事件、旧库普通索引自动迁移为 UNIQUE。
- **验证**：`test_store` 9 项全过；全量 118 项通过。
- **遗留说明**：报告附加建议的 `events.attempt_id` 外键约束需重建表（SQLite 不支持 ALTER 加外键），列入后续存储迁移项，本次未做。
- **commit**：`fix(R-07)`。

---

## R-08　llm_call 增量式对话存储（严重，P1）

- **改动**：
  - `model.py`：schema_version 3 → 4；新增 `rebuild_messages(events)` 重放重建函数（规则：incremental 追加、full 重置；旧轨迹无标记视为 full，结果为最后快照，兼容 v3）。
  - `collector.llm_call`：新增 `incremental` 参数，事件 data 写入 `messages_mode`（full / incremental）。
  - `agent.py`：维护 `recorded` 基准——首轮落完整对话快照（system + instruction），其后各轮只落自上轮后新增的消息（assistant 回复 + TOOL_RESULT 回灌），第 n 轮事件体积从"约等于前 n 轮内容之和"降为 O(本轮)；空回复重试轮记录空增量，语义精确。
  - 前端 `app.js`：llm_call 详情标注上下文模式（完整快照 / 本轮新增），气泡按增量渲染更贴合逐步回放。
  - 测试：`test_agent_e2e` 断言首轮 full（2 条）+ 后续 incremental（各 2 条）+ rebuild_messages 精确重建（2 + 2×(轮数-1)）；schema 断言更新为 4（test_store / test_server / test_cli）。
- **效果**：长任务下轨迹存储从平方级增长降为线性，SQLite 文件膨胀与导出/接口传输变慢问题解除。
- **验证**：全量 126 项通过。
- **commit**：`fix(R-08)`。

---

## R-03　任务集难度分级（阻断，P0）

- **改动**：
  - `runner.py`：`TaskPackage` 新增 `level` 字段（默认 L1，task.json 可声明 L1/L2/L3）。
  - 新增 5 个 **L2 任务**（多文件联动 / 需阅读调用方 / 误导性注释，指令只描述症状不指明缺陷位置）：
    1. `fix_cart_total`：税率口径需交叉核对 config.py；含误导性注释（"统一按 8%"）；
    2. `fix_area_dispatch`：circle.py 误用周长公式，需读调用方 dispatch.py 确认 area 语义；
    3. `fix_config_merge`：deep_merge 覆盖而非递归合并，正确语义以调用方 main.py 为准；
    4. `fix_cache_lookup`：缓存未命中缺回源逻辑，需理解 cache/db/service 三层结构；
    5. `fix_stack_pop`：stack.py 注释声称 FIFO 但产品语义是 LIFO——诱导 Agent 改错文件。
  - `tools_gen_l2_tasks.py`：任务生成脚本入库（可重复生成，留痕）。
  - 11 个既有任务默认 L1（单点缺陷），形成 L1/L2 两个梯度；L3 待真实模型通过率数据反馈后再补。
- **验证**：`test_taskset` 参照解回归 16 任务全过（任务可解性闭环）；全量 126 项通过。
- **效果预期**：L2 任务要求多文件阅读与调用方语义推断，预期真实模型通过率脱离满分区间（待 M11 后续实验验证）。
- **commit**：`feat(R-03)`。

---

## R-04　实验统计口径（严重，P0）

- **改动**（`experiments.py`）：
  - 模型聚合新增：`pass_at_1`（能力上限）、`task_pass_at_k`（同"模型×任务"组合 repeat 次全部通过的任务比例，稳定性——pass@1 相同的模型可用 pass^k 拉开差距）、`token_std` / `duration_std`（n≥2 时样本标准差，n=1 仅均值）。
  - 任务×模型单元格：`✅/◐/❌ ok/n p@1=… · mean±std tok`（◐ = 部分通过，即不稳定）。
  - 报告头部新增统计口径说明（可直接写入论文实验设置）。
  - 模块 docstring 记录三口径定义（pass@1 / pass^k / mean±std）。
  - 测试：新增 `test_stats_pass_at_k_and_std`——半失败工厂（奇数次尝试失败）验证 pass@1=0.5 且 pass^k=0，标准差非空，报告含口径说明。
- **注意**：统计生效需要真实实验跑 `repeat≥3`（报告路线图阶段一实验矩阵：≥3 模型 × 全任务 × 3 次），建议命令 `python atp.py experiment --models a,b,c --repeat 3 --docker`。
- **验证**：全量 127 项通过。
- **commit**：`feat(R-04)`。

---

## R-05　故障注入实验机制（严重，P0）

- **改动**：
  - 新模块 `faults.py`：`FaultProfile`（slow_tool_ms / flaky_rate / inject_instruction，可序列化进 meta）+ 预置场景（baseline / slow_tool / flaky_tool / prompt_injection）。
  - `agent.py`（本地通道）注入三处：工具执行前 sleep（计入真实耗时）、以 flaky_rate 概率抛 fault（走工具失败路径、Agent 可重试、结构化 tool_error 留痕）、read_file 内容前插提示注入样本（触发 injection_flags 留痕）。
  - `runner.py`：`TaskRunner.run` 增加 `faults` 参数转发 MiniAgent；扰动配置记入 attempt.meta（实验可归因）。
  - `experiments.py`：`run_experiment` 增加 `fault` 参数（profile 名或实例），meta 打标、报告标注 `local+fault:<name>`。
  - CLI：`atp.py experiment --fault slow_tool|flaky_tool|prompt_injection`。
  - 新增 `tests/test_faults.py`（6 项）——基线无信号（对照组）+ 三类扰动各自产生可观测信号：慢工具 → tool_time_ms ≥ 3×延迟且 tool_share > 0.9、TopN 为 tool_call；必失败工具 → invalid_tool_rate=0.75 且 error_code=tool_error 留痕；注入样本 → injection_flags 命中（判定不受影响，验证 R-21"留痕不阻断"定位）。
- **效果**：这组断言即论文"故障注入实验"章节的证据链——功能存在性 → 功能有效性。真实模型实验建议跑 `--fault` 各场景 × repeat≥3，对比各模型扰动下的 pass^k 与过程指标。
- **验证**：全量 133 项通过。
- **commit**：`feat(R-05)`。

---

## R-16　循环/无进展检测与重复率拆分（一般，P2，2026-11-13）

- n-gram 循环检测：周期 1–3 连续 ≥3 周期（AAA/ABAB/ABCABC），输出 loop_patterns（周期、重复数、工具序列），旧"同键连续≥3"为其特例。
- 无进展检测：连续 tool_call（不含 finish）无新 file_diff 的最长长度 max_no_progress_streak；≥4 且未通过 → no_progress_detected。
- 重复率拆分：写后重读同一文件计为合理行为（reread_after_write_count），effective_duplicate_tool_rate 予以剔除。
- commit：`feat(R-16)`。

## R-17　成本金额指标（一般，P2，2026-11-13）

- 新模块 `pricing.py`：每百万 token 输入/输出价目表（CNY），AGENTTRACE_PRICE_FILE JSON 覆盖；未知模型不猜测价格，计入 unpriced_tokens 并标注。
- `metrics.compute_metrics` 新增 cost_cny / cost_fully_priced / unpriced_tokens。
- `experiments`：row/聚合成本字段；报告新增"通过率—成本分析"（单次通过成本 + 帕累托最优标注）；原"单价按价目折算"的口径描述与实现终于一致。
- commit：`feat(R-17)`。

## R-14　结构化 error_code 失败归因（严重，P1）

- **改动**：
  - `tools.py`：新增 `WorkspaceEscapeError`（ValueError 子类，携带 `error_code="workspace_escape"`），越界抛出改用该类型，兼容现有捕获。
  - `agent.py`：未知工具 → `unknown_tool`；工具异常 → 从异常透传 error_code（越界）或 `tool_error`；max_turns → `max_turns`；空回复/解析失败沿用 R-15 的 `empty_response` / `parse_error`。
  - `runner.py`：`agent_exception` / `verdict_failed` / `runner_exception` 三个 error 事件带码。
  - `mcptools.py`：MCP 调用异常 tool_call 带 `tool_error`。
  - `metrics.classify` 重写（v2）：**优先读结构化 error_code**（error 事件 + error 状态 tool_call，按 seq 排序）；旧轨迹回退 v1 文本匹配（过渡兼容）。主因判定规则明确可写进论文：通过→passed；否则取**最早的终止性错误码**（max_turns/parse_error/empty_response/agent_exception/runner_exception）；无终止码但判定未通过或已 finish→tests_failed；仅过程性异常→取最早者；无证据→incomplete。错误文案调整不再导致归因静默失效。
  - 测试：新增 4 项——终止性优先于过程性异常、verdict_failed→tests_failed、旧文本回退、通过但保留过程标签。
- **验证**：全量 126 项通过。
- **commit**：`fix(R-14)`。

---

## R-15　动作解析失败可重试（严重，P1）

- **改动**：
  - `agent.py`：解析失败不再直接 `raise`——与空回复同一策略，最多重试 `MAX_PARSE_RETRIES`（3）次；重试时把模型原回复 + 明确的格式修正指令反馈给模型；超过上限才判 `agent action parse error`。
  - `collector.error`：新增 `code` 参数（error 事件写入结构化 `error_code` 字段，机器可读、与展示文案分离，为 R-14 铺垫）；空回复/解析失败事件分别带 `empty_response` / `parse_error` 码。
  - `metrics.py`：新增 `parse_error_retries` / `empty_response_retries` 过程指标（读结构化 error_code，不做文本匹配）。
  - 测试：新增 `TestParseErrorRetry`——解析失败一次后反馈修正提示、任务最终通过；持续解析失败 3 次后判 error（不再首轮即死）。
- **效果**：输出格式较松散的模型不再被过早判负，模型间通过率可比性恢复。
- **验证**：全量 122 项通过。
- **commit**：`fix(R-15)`。

---

## R-11　采样参数与运行环境记录（严重，P1）

- **改动**：
  - `llm.py`：`LLMResponse` 增加 `params`（采样参数快照，不含密钥）与 `finish_reason` 字段；`OpenAICompatLLM` 构造时接受 temperature / top_p / seed 并记录 base_url / interface，chat 请求按记录参数发送、响应回填 finish_reason。
  - `collector.py`：`llm_call` 增加 `params` / `finish_reason` 可选参数并落 data。
  - `agent.py`：落 llm_call 事件时携带 `resp.params` / `resp.finish_reason`。
  - `runner.py`：新增 `_git_commit()`（短 hash，取不到时 "unknown"）；attempt.meta 统一记录 `code_version`（git commit）、`python` 版本，docker 模式追加 `sandbox_image`（延迟导入，本地模式不背容器依赖）。
  - 测试：`test_metrics.py` 新增 params 快照落库断言（含"不含密钥"断言）；`test_agent_e2e.py` 断言 meta 必含 code_version / python。
- **验证**：全量 120 项通过。
- **commit**：`fix(R-11)`。

---

## R-09　全事件统一耗时字段（严重，P1）

- **改动**：
  - `collector.py`：`tool_call` / `command_exec` 增加 `duration_ms` 参数（可选字段，旧轨迹兼容）。
  - `agent.py`（本地通道）：工具执行统一计时（`tool_t0`），run_tests / run_command 命令单独计时，tool_call 落库携带真实耗时。
  - `mcptools.py`（MCP 通道）：`call()` 全程计时，与本地通道同一口径（denied 未执行，不计耗时）。
  - `runner.py`：最终判定阶段的 command_exec 同样计时。
  - `metrics.py`：新增 `tool_time_ms` / `command_time_ms` / `time_breakdown`（llm/tool 占比，command 为子集不重复计入）/ `slowest_steps`（最慢步骤 TopN，标注类型）。
  - 前端 `static/app.js` + `style.css`：瀑布图 command_exec 优先用真实 `duration_ms`（旧轨迹回退事件间隔估算）；tool_call 有耗时时画条形（新增 `.bar.tool` 样式）。
  - 测试：`test_metrics.py` 新增耗时占比与 TopN 口径测试；`test_agent_e2e.py` 断言工具/命令事件必须带耗时字段。
- **验证**：全量 119 项通过。
- **commit**：`fix(R-09)`。

---

## R-10　file_diff parent_id 层级修正（一般，P1）

- **改动**：
  - `agent.py`（本地通道）：write_file 的 diff/audit 先暂存 `pending_diff`，`tool_call` 落库后再以其 id 为 parent 落 `file_diff`，形成 llm_call → tool_call → file_diff 三层结构。
  - `mcptools.py`（MCP 通道）：同步修正，与原留痕链路注释（tool_call → file_diff）保持一致。
  - `model.py`：更新 parent_id 层级口径文档。
  - `tests/test_agent_e2e.py`：新增 `test_file_diff_nested_under_tool_call` 断言三层归属。
- **影响**：前端回放树由 parent_id 驱动，改动后文件变更自动缩进在对应工具调用下，无需前端改动。
- **验证**：全量 118 项通过（含新增回归）。
- **commit**：`fix(R-10)`。


---

# 第二批修复（2026-11-13，本体完善收尾）

## R-18　上下文增长/截断指标 + read_file 分页

- `agent.py`：read_file 支持 offset/limit（默认 0/2000，上限 8000），事件记录 offset/limit/original_length/truncated，截断时向模型提示续读参数；SYSTEM_PROMPT 说明分页用法。
- `collector` / `agent`：llm_call 记录 context_chars（本轮完整 prompt 字符数）。
- `metrics`：context_chars_per_turn / max_context_chars / context_growth_chars / truncated_tool_calls / truncated_tool_rate。
- 测试：分页三态（默认截断/续读仍截断/读完不截断）+ 上下文指标口径。
- commit：`fix(R-18)`。

## R-19　接口分页与指标预计算

- `store`：list_attempts(limit/offset/task_id)、count_attempts、count_events、update_attempt_meta；export 不下发 _ 前缀派生字段。
- `runner`：判定完成后预计算 _metrics/_classification 落库。
- `server`：/api/attempts 分页（page/page_size/task_id）+ 元数据；/api/attempts/{id} 支持 events_offset/events_limit 分段；/metrics 优先读缓存，缺失回写。
- 前端：列表请求分页参数并显示"最近 N / 共 M"。
- 测试：分页参数、轨迹分段、缓存命中、派生字段不下发。
- commit：`feat(R-19)`。

## R-20　实验容错与断点续跑

- `experiments`：resume=True 时沿用既有 run_id、跳过已完成 (model,task,repeat)、历史行回填报告；单任务异常捕获记为失败行（error 字段），不中断整批；返回 skipped / errored。
- CLI：`experiment --resume`（已存在库需显式开启）。
- 测试：续跑跳过与 run_id 沿用；注入异常任务不影响其余任务。
- commit：`feat(R-20)`。

## R-22 / R-21　轨迹脱敏与注入检测定位

- 新模块 `redact.py`：API Key / Bearer / 键值密钥字段 / PEM 私钥 / 邮箱遮蔽（幂等）。
- `store.export(redact=True)`；CLI export 默认脱敏（--no-redact 关闭）；`AGENTTRACE_REDACT=1` 采集侧钩子；`purge --days N` 保留期清理。
- R-21：README、agent.py 注释明确注入检测是"风险留痕与可观测性，非防御机制"。
- 测试：4 项（模式/导出/采集钩子/保留期清理）。
- commit：`feat(R-22,R-21)`。

## R-23　字体自托管与 CSP 收紧

- 下载 Google Fonts latin 子集 8 个 woff2（122KB）到 static/fonts/，生成本地 fonts.css；中文回退系统字体栈；index.html 移除全部外链；CSP 收紧为 font-src 'self'。
- 测试：页面无 googleapis 外链、fonts.css 可访问、CSP 不含外部字体域。
- commit：`feat(R-23)`。

## R-12 / R-13　序号下沉与批量事务

- `store`：WAL + synchronous=NORMAL；seq<=0 时由存储层在事务内分配（取号与插入同事务，失败回滚不留空洞）；新增 append_events 批量单事务写入。
- `collector`：不再维护内存计数器，emit 交由存储层分配回填。
- 测试：存储层分配 1/2/3、批量连续分配、WAL 生效。
- commit：`feat(R-12,R-13)`。

## Web 端任务提交入口（开题 P0 形式缺口）

- `server`：GET /api/tasks（任务集+分级）、POST /api/run（后台线程执行，独立 sqlite 连接）；scripted 默认开放，openai/docker 需 `--allow-llm-run` / `--allow-docker-run`；task_id 白名单防目录穿越。
- CLI serve：--tasks / --allow-llm-run / --allow-docker-run。
- 前端：header "提交任务"按钮 + 提交栏（任务/LLM/模型/Docker），提交后自动刷新列表。
- 测试：任务列表、提交后台执行并出现在列表、非法输入拒绝（404/403/400）。
- commit：`feat(web-run)`。

## 第二批验证

- 全量测试：152 项（含新增），修复 7 项 test_security 的 TestClient 依赖（httpx2）。
- 修复复核：f-string 嵌套引号兼容、批量 seq 分配 bug、RunRequest 函数内定义导致 422 等 3 个问题在提交前被发现并修正。
