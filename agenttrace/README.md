# AgentTrace —— 面向 AI 编程 Agent 的执行轨迹采集、可视化与评测平台

毕业设计核心代码库。对应开题报告《面向 AI 编程 Agent 的执行轨迹采集、可视化与评测平台的设计与实现》。

## 当前进度（对照路线图，如实记录）

**已完成（M1：核心链路）**

- 统一轨迹数据模型：7 类事件（llm_call / tool_call / file_diff / command_exec / run_result / error，M4 新增 mcp_audit），parent_id 父子关系，seq 严格递增，写入前校验
- 轨迹存储：SQLite（attempts + events 两表，(attempt_id, seq) 索引），支持整段导出为可回放 JSON 文档（当前 schema_version=2）
- 轨迹采集器：自动生成 id/ts/seq，统一各事件的 data 字段口径
- mini Agent（ReAct 循环）：read_file / write_file / run_tests / run_command / finish 五个工具，所有动作进轨迹
- 评测执行器：任务包装配 → 运行被测 Agent → 隐藏测试权威判定 → 落库
- 任务包格式：仓库快照（setup/）+ 指令 + 隐藏测试（hidden_tests/）+ 脚本参照动作
- CLI 端到端：run-task / attempts / export

**已完成（M2：Web 可视化回放）**

- FastAPI 服务：`serve` 子命令；GET /api/attempts、/api/attempts/{id}（不存在时 404）、静态资源托管
- 回放前端（原生 JS/CSS，零依赖、离线可用，答辩现场不断网也能演示）：尝试列表 → 摘要卡（状态 / token / 耗时 / 事件构成条）→ 时间线播放器（播放/暂停/步进/重置/全部、三档速度）→ 六类事件详情（对话气泡、diff 红绿着色、命令输出、判定徽章）
- API 集成测试：线程内启动 uvicorn 发真实请求验证（5 项）

**已完成（M3：过程层指标与失败归因）**

- 指标计算（metrics.py，规则版 v1，口径已文档化）：无效工具调用率、重复调用率、循环检测（同键动作连续 ≥3 次）、连续错误重试深度、finish 达成、token/耗时汇总
- 规则版失败归因分类器：passed / parse_error / max_turns / tests_failed / incomplete / workspace_escape / unknown_tool，按优先级提取标签；通过但带过程异常（如越界被拒后完成）时 primary=passed 且保留 labels
- 接入三端：API `GET /api/attempts/{id}/metrics`、CLI `python atp.py metrics <attempt_id>`、前端摘要卡（指标行 + 归因徽章）

**已完成（M4：MCP 工具权限审计）**

- 独立 MCP 服务器（mcp_demo_server.py，官方 SDK，stdio 传输）：工作区工具以 MCP 协议暴露，list_tools 即工具声明
- 策略引擎（policy.py）：工具黑名单 / 禁读路径 / 可写白名单 / 参数禁用正则 / 默认动作，由任务包 task.json 的 policy 字段声明
- 审计包装（mcptools.py，AuditedMCPClient）：声明比对 → 策略评估 → 授权留痕，拒绝的调用不会到达 MCP 服务器；未声明工具一律拦截
- 新事件类型 mcp_audit（schema v2）：decision=declared/allow/deny + rule_id + reason，实现"声明→调用→授权→结果"全程留痕
- 全链路接入：run-task --mcp 开启 MCP 通道；前端时间线与摘要卡展示审计事件与拒绝计数；metrics 新增 audit_total/audit_denied
- 关键工程实践：Windows 下 MCP stdio 管道句柄被子进程继承会导致阻塞，工具内子进程须显式 stdin=DEVNULL

**已完成（M5：真实模型接入验证）**

- 本地凭据加载：atp.py 启动时读取 .env（key 不进源码、不进文档，.gitignore 排除；不覆盖已有环境变量）
- 真实链路首跑通过：小米 MiMo 开放平台 mimo-v2.6-flash，4 轮完成 fix_leap_year（读→修→测→finish），共 1851 tokens，隐藏测试判定通过
- 适配经验：推理模型可能以 <function=…><parameter=…> 标记格式回复（实测 mimo-v2.6-pro 未加约束时如此）——提示词强约束 + 提取器四级兑底（JSON→围栏→花括号→标记）；run_command 字符串参数容错

**已完成（M6：Docker 沙箱执行器）**

- DockerWorkspace：与本地 Workspace 同接口，Agent 命令经 docker exec 在常驻容器执行（python:3.13-slim，--memory 512m --cpus 1 --network none）
- 隐藏测试分离装配：Agent 目录与隐藏测试分开，测试以只读方式挂载进容器——Agent 可运行但无法篡改；最终判定在一次性新鲜容器中用原始隐藏测试执行（Agent 目录亦只读）
- CLI：run-task --docker；本地模式行为完全不变；--docker 与 --mcp 暂不组合（MCP 工具服务器在宿主侧）
- 测试：沙箱内端到端 3 项（跑通 / 篡改无效 / 断网验证），无 Docker 引擎的环境自动跳过

**已完成（M7：AI 生成代码质量审计）**

- codeaudit.py：对每步 AI 改动的新内容做静态审计——AST 语法检查 + 风险调用（eval / exec / compile / __import__、os.system / os.popen、pickle.loads、subprocess shell=True）+ 裸 except + 硬编码密钥模式；非 Python 文件如实标注跳过
- 审计结果作为 file_diff 事件的 audit 字段随轨迹留痕（可选字段，向后兼容），本地与 MCP 两条工具通道均接入
- metrics 新增 code_audit_high / code_audit_warn；前端 diff 详情直接渲染审计结论与逐条发现
- 测试：规则 4 项 + 轨迹集成 1 项（恶意改动留痕且计入指标）

**已完成（M9：批量实验入口）**

- experiments.py：N 模型 × M 任务 × repeat 的一条命令编排（顺序执行 + 礼貌限速），自动产出模型汇总表与任务×模型矩阵的 Markdown 报告
- 防污染硬约束：实验库按时间戳新建于 experiments/ 并拒绝写入已存在库；每次尝试 meta 打标 run_id/model/task/repeat
- TaskRunner 支持注入 meta_extra；CLI experiment 子命令（--models/--repeat/--docker/--sleep）
- 首轮真实实验：mimo-v2.6-flash × 11 任务 × Docker 沙箱，两轮迭代（适配器修复前 90.9%→修复后 100%），报告与实验库存于 experiments/

**已完成（M10：回放工作台 UI 落地 · Ink & Ledger）**

- 按 ui规范.md 全量重制前端（Ink & Ledger 设计系统）：暖纸/墨色/陶土橙红台账视觉、思源宋 900 标题 + DM Mono 数字刻度 + 思源黑正文、纸张颗粒、硬投影、hover 红条与加字距微交互
- 时间线升级为执行瀑布图：横轴真实时间、条长 = 实际耗时（LLM 条按 duration 反推、命令条取事件间隔）、墨色形状编码（圆环 = 工具、菱形 = 改动、圆环勾 = 判定命中），播放游标随事件时间戳联动
- 摘要带改为台账式指标条：结果判定章 + Tokens/耗时/轮次/工具 + 失败归因 + 代码审计与审计拒绝计数 + 过程层指标行
- diff 详情改为墨色控制台反白面板，代码审计结论内联在当步改动下；新增尝试元数据页脚（attempt/run/sandbox/时间）
- 验证：真实实验库（exp-20261108-125155）浏览器走查——尝试列表 / 瀑布图 / 事件详情 / 元数据页脚 / 数据接线全部正确；后端 69/69 回归不受影响

**已完成（M8：任务集扩充）**

- 任务集 1 → 11：新增 10 个任务，缺陷类型覆盖差一错误、边界条件、空输入约定、大小写归一化、顺序保持、递归展平、min/max 交换、规则缺失、全负数组等，难度从单行修复到多行重写
- 生成式构造：tasks/make_tasks.py 幂等生成全部任务包（缺陷代码、隐藏测试、脚本参照解同源维护，可审计、可再生成）
- 任务集回归测试（tests/test_taskset.py）：每个任务用其脚本参照解离线跑通才算过，保证缺陷/测试/参照解三要素始终一致
- 每任务 3~5 个隐藏测试用例（含边界），全部仅依赖标准库，与沙箱镜像 python:3.13-slim 兼容

**已完成（M11：评审问题修复 + 本体完善）**

- 数据与可靠性：seq 唯一约束 + 存储层事务内分配（无空洞）、llm_call 增量记录（schema v4，消除平方级存储放大）、WAL + 批量写入
- 观测深度：全事件统一 duration_ms（耗时占比/TopN）、采样参数与版本快照、n-gram 循环检测 + 无进展检测 + 重复率拆分、上下文增长与截断率、成本金额与通过率—成本帕累托分析
- 归因与重试：全链路结构化 error_code（归因 v2 终止性错误优先）、解析失败与空回复统一重试策略
- 任务集：L1/L2 分级（16 任务，5 个 L2 覆盖多文件联动/需读调用方/误导注释），实验 pass@1/pass^k/均值±标准差
- 实验工程：故障注入（慢工具/随机失败/提示注入样本）、断点续跑、单任务异常容错
- 服务与安全：接口分页/轨迹分段/指标预计算、Web 端任务提交（POST /api/run，默认仅 scripted）、轨迹脱敏与保留期清理、字体自托管（零外链，CSP 收紧）

**未做（后续，按开题排期）**

- Web 端多模型对比视图（实验报告已具备数据，视图可选）
- ECharts 统计图增强（当前为原生 CSS 构成条）
- OTel GenAI 语义映射表（文档层）、轻量对照实验（R-06）

## 快速开始

```powershell
# 安装依赖（mcp 必须锁 1.x：2.x 把 FastMCP 更名为 MCPServer，本项目按 1.x API 编写）
pip install -r requirements.txt

# 运行示例任务（脚本 LLM，离线即可跑通）
cd agenttrace
python atp.py run-task tasks/fix_leap_year --db traces.db

# 导出某次尝试的轨迹 JSON（可回放文档）
python atp.py export <attempt_id> --db traces.db --out trace.json

# 启动 Web 回放界面（需先 pip install fastapi uvicorn）
python atp.py serve --db traces.db   # 浏览器打开 http://127.0.0.1:8000（含 Web 端任务提交）
python atp.py serve --db traces.db --allow-llm-run   # 允许 Web 端用真实模型提交任务
python atp.py serve --db traces.db --token <令牌>   # 开启访问鉴权：/?token=<令牌>

# 运行示例任务（MCP 协议 + 权限审计）
python atp.py run-task tasks/fix_leap_year --db traces.db --mcp

# 真实模型运行（先配置 .env，见下文）
python atp.py run-task tasks/fix_leap_year --db real-traces.db --llm openai

# Docker 沙箱执行（需本机 Docker 引擎在运行）
python atp.py run-task tasks/fix_leap_year --db traces.db --docker

# 本地（宿主机）模式下默认禁止 Agent 执行 run_command；确需放行时显式开启
python atp.py run-task tasks/fix_leap_year --db traces.db --allow-local-commands

# 批量实验（多模型 × 重复 3 次 + 故障注入场景 + 断点续跑）
python atp.py experiment --models a,b,c --repeat 3 --docker
python atp.py experiment --models a --repeat 3 --fault slow_tool
python atp.py experiment --models a,b,c --repeat 3 --db experiments/exp-xxx.db --resume

# 轨迹导出（默认脱敏）与保留期清理
python atp.py export <attempt_id> --db traces.db --out trace.json
python atp.py purge --db traces.db --days 30

# 运行全部测试
python -m unittest discover -s tests -v
```

## 配置真实模型

`.env`（本地文件，已入 .gitignore）填三项即可，任何 OpenAI 兼容服务均可：

```
AGENTTRACE_LLM_BASE_URL=https://api.xiaomimimo.com/v1
AGENTTRACE_LLM_MODEL=mimo-v2.6-flash
AGENTTRACE_LLM_API_KEY=sk-xxxxx
```

已验证：小米 MiMo 开放平台按量付费接口；atp.py 启动时自动加载 .env，不覆盖已有环境变量。

> 入口说明：`atp.py` 是统一命令行入口，显式把源码目录加入 `sys.path`，
> 在启用安全路径模式的 Python（如部分内置发行版）下也能用；
> 标准安装的 Python 上 `python -m agenttrace ...` 同样可用。

## 目录结构

```
agenttrace/
├── agenttrace/            # 核心包
│   ├── model.py           # 统一轨迹数据模型（事件类型与校验）
│   ├── store.py           # SQLite 轨迹存储与导出
│   ├── collector.py       # 事件采集器（seq/时间戳/父子关系）
│   ├── faults.py          # 故障注入（慢工具/随机失败/注入样本）
│   ├── pricing.py         # 模型价目表与成本折算
│   ├── redact.py          # 轨迹脱敏（密钥/令牌/邮箱遮蔽）
│   ├── tools.py           # 受限工作区（路径越界防护、unified diff）
│   ├── llm.py             # LLM 接入层（ScriptedLLM / OpenAICompatLLM）
│   ├── agent.py           # mini Agent（ReAct 循环，默认被测对象）
│   ├── runner.py          # 任务包加载与评测执行器
│   ├── policy.py          # 工具权限策略引擎（M4）
│   ├── verdict.py         # 判定执行器：隐藏测试隔离目录（安全模型 v3）
│   ├── envutil.py         # 子进程环境变量白名单（凭据不外泄）
│   ├── mcptools.py        # MCP stdio 客户端桥 + 审计包装（M4）
│   ├── mcp_demo_server.py # 独立 MCP 工具服务器示例（M4）
│   ├── server.py          # FastAPI 服务（轨迹 API + 静态前端）
│   ├── static/            # 回放前端（index.html / app.js / style.css）
│   └── __main__.py        # CLI
├── tasks/fix_leap_year/   # 示例任务（含缺陷的 utils.py + 隐藏测试）
├── requirements.txt       # 运行依赖（mcp 需锁 1.x）
└── tests/                 # 单元测试与端到端测试（含 test_security.py）
```

> 安全扫描报告与未完成项见仓库上一级目录的 `../SECURITY_AUDIT.md`。

## 设计约定

- **判定权威在执行器**：Agent 说做完了不算，隐藏测试全过才算 passed；Agent 异常记为 error，轨迹中保留原因。
- **一切皆事件**：模型调用、工具动作、文件 diff、命令执行、最终判定全部落轨迹，供回放与后续过程指标分析。
- **被测 Agent 可插拔**：MiniAgent 只依赖统一的 llm.chat(messages) 接口；接入开源 Agent 时实现同一接口即可，平台不绑定任何具体框架。

## 安全模型（schema v4）

平台会执行**不可信的模型输出**，因此安全边界不靠"模型听话"，而靠执行器强制：

| 关注点 | 机制 | 代码位置 |
|---|---|---|
| 隐藏测试不可读、不可篡改 | 隐藏测试源码由宿主读入内存、经 **stdin** 传给独立判定子进程（`agenttrace/_verdict_runner.py`），**全程不落盘**；判定期间磁盘上不存在测试文件，被测代码无法读到测试内容 | `verdict.py`、`_verdict_runner.py`、`runner.py` |
| 判定结果不可伪造 | 判定子进程对 `unittest` 命名空间与 `TestCase` 做前后快照 + 审计钩子阻断改写；判定 **fail-closed**：需同时满足「退出码 0 + 输出含 OK + 哨兵 nonce 匹配 + 用例数 > 0 + 未检出篡改」 | `_verdict_runner.py`、`verdict.py` |
| 隐藏测试不可被投毒 | 判定目录剔除 `sitecustomize.py`、标准库同名模块（遮蔽 `unittest` 等）、`__pycache__`/陈旧字节码、**任意层级的符号链接**；被测代码 `os._exit(0)` 制造"退出码 0"也会被判失败 | `verdict.py` |
| 权限策略在所有通道生效 | 本地通道与 MCP 通道共用同一套 `Policy.evaluate`；未声明策略的任务使用内置安全策略（隐藏测试永不可读写） | `policy.py`、`agent.py`、`runner.py` |
| 路径规则不可被绕过 | 路径先做词法归一化（折叠 `..`、拒绝绝对路径/盘符），再匹配白名单与黑名单 | `policy.py` |
| 宿主执行风险 | 本地（宿主机）模式默认禁止 `run_command`，需 `--allow-local-commands` 显式放行；Docker 模式下命令在断网、限额、`--cap-drop ALL` 的容器内执行 | `runner.py`、`sandbox.py` |
| 凭据不外泄 | Agent 命令、MCP 工具服务器、测试进程一律使用环境变量白名单，`AGENTTRACE_LLM_API_KEY` 等不会被子进程继承 | `envutil.py` |
| 提示注入 | 工具返回的文件内容/命令输出以 `<untrusted_data>` 包裹后回灌模型；命中注入特征时在 `tool_call` 事件留 `injection_flags`。**定位为风险留痕与可观测性，不是防御机制**：命中不阻断、规则覆盖有限（R-21 已在论文口径中明确） | `agent.py` |
| 回放服务暴露面 | 轨迹 API 可用 `--token` 鉴权；非回环地址未给令牌时拒绝启动；响应附 CSP 等安全头；`attempt_id` 必须匹配 32 位十六进制 | `server.py`、`__main__.py` |

事件类型新增 `policy_audit`（授权决策，通道无关），`mcp_audit` 只保留 MCP 协议层的声明事件；
`schema_version` 相应升为 **v3**。扫描报告与未完成项见 `../SECURITY_AUDIT.md`。

> 已知边界（如实记录）：① 宿主本地模式仍是真实用户权限执行，仅靠策略与默认拒绝兜底，
> 正式实验应使用 `--docker`；② Windows 下子进程超时只能回收直接子进程（POSIX 已按进程组回收）；
> ③ 前端字体仍外链 Google Fonts，建议后续自托管；④ 判定子进程与被测代码同进程，
> 被测代码若**刻意遍历磁盘**去找任务包里的隐藏测试（任务包是用户自己的文件）仍可能读到——
> 平台已保证自己的隐藏测试副本在判定期间不在磁盘上。
