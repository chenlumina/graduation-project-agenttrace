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
| R-15 | 解析失败直接终止，策略不一致 | 严重 | 待修复 | — | — |
| R-14 | 失败归因依赖中文文本匹配 | 严重 | 待修复 | — | — |
| R-08 | llm_call 全量落库，存储平方级膨胀 | 严重 | 待修复 | — | — |
| R-03 | 任务集天花板效应 | 阻断 | 待修复 | — | — |
| R-04 | 实验统计口径不足 | 严重 | 待修复 | — | — |
| R-05 | 缺少故障注入实验 | 严重 | 待修复 | — | — |

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
