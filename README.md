# AgentTrace — 面向 AI 编程 Agent 的执行轨迹采集、可视化与评测平台

本科毕业设计（软件工程）。平台把「AI 编程 Agent 的一次运行」变成**可回放、可评测、可审计**的数据：

- **执行瀑布图**：横轴真实时间、条长 = 实际耗时——模型思考、工具执行、测试判定一眼可读
- **两层评测**：结果层（隐藏测试权威判定，fail-closed）+ 过程层（无效调用率、循环检测、重试深度）
- **安全模型 v4**：隐藏测试内存加载、判定防篡改（unittest 快照 + 审计钩子）、策略下沉双通道、Docker 沙箱加固
- **MCP 审计**：真实 MCP 协议（stdio）+ 策略引擎，声明 → 调用 → 授权全程留痕

## 快速开始

详见 [agenttrace/README.md](agenttrace/README.md)。

```powershell
cd agenttrace
python atp.py run-task tasks/fix_leap_year --db traces.db --docker   # 沙箱执行
python atp.py serve --db traces.db                                   # 回放工作台 http://127.0.0.1:8000
python atp.py experiment --models a,b --docker                       # 批量对比实验
```

## 仓库结构

| 路径 | 说明 |
|---|---|
| `agenttrace/` | 平台核心：轨迹模型 / 存储 / 策略 / 沙箱 / MCP / 实验管线 / 回放前端 |
| `SECURITY_AUDIT.md` | 安全审计与修复报告（安全模型 v4，含对抗性验证） |
| `ui规范.md` | 回放界面设计系统规范（Ink & Ledger） |
| `原型/` | 回放界面静态视觉稿 |

## 测试

```powershell
cd agenttrace
python -m unittest discover -s tests
```

## 说明

- 实验数据（`experiments/`）与本地凭据（`.env`）不入库；`.env` 模板字段见 `agenttrace/README.md`
- 本地（宿主机）模式默认禁用 `run_command`，正式评测统一使用 `--docker` 沙箱模式
