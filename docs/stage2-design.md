# 阶段二设计：Plan → Execute → Verify

## 目标

在阶段一 ReAct 执行器外增加 LangGraph 工作流，使一次任务先形成计划，再执行代码修改，最后由独立验证节点检查结果；失败时根据证据重新规划，直到通过或达到最大尝试次数。

## 边界

- 保留阶段一的 `stream_agent_events`，Actor 继续复用它和现有五个工具。
- 本阶段不实现多 Agent、联网搜索、上下文压缩或持久化，这些属于后续阶段。
- README 和根目录的 `项目篇规划.md` 不改动。
- OpenAI 兼容模型配置保持不变，包括用户当前使用的 DeepSeek 兼容接口。

## 架构

```text
START → planner → actor → verifier ──通过──→ final → END
                    ↑          │
                    └──重新规划─┘（未通过且仍有尝试次数）
```

### Planner

Planner 使用结构化输出生成 `plan_summary`、Todo 列表、验收标准和验证命令。重试时会看到上一轮验证原因和检查结果，并据此修订计划。Planner 不获得文件写入工具。

### Actor

Actor 将用户任务、当前计划、验收标准和上一轮失败证据交给阶段一 ReAct 执行器。它拥有阶段一全部工作区工具，并通过 Todo 工具更新内存中的任务状态。其职责是实现，而不是宣布任务已经被独立验证。

### Verifier

Verifier 先由程序执行 Planner 给出的零到十条验证命令，再让模型使用只读工具检查工作区，
最后通过单独的 function-calling 结构化调用作出判定。验证阶段不提供文件修改能力。
每条验收标准必须恰好对应一个同名检查；一次 Verifier 运行计为一次 attempt。

### Final

Final 不再次调用模型。它根据 `passed`、attempts、检查详情和原因生成稳定的最终结果，避免模型把未验证的结果包装成成功。

## 状态

`MiniclaudeGraphState` 使用 TypedDict，包含：任务、运行时、消息、计划、Todo、验收标准、验证命令、验证结果、验证结论、失败原因、尝试次数、最大尝试次数、Actor 摘要和最终回答。

Todo 状态限制为 `pending`、`in_progress`、`completed`、`blocked`。验证结果记录命令、是否成功、退出码、stdout 和 stderr，并沿用阶段一的输出上限。

## 事件与 CLI

CLI 默认运行阶段二图，并新增 `--max-attempts`（默认 3）。图通过 LangGraph 的 `updates` 与 `custom` 流输出 Planner、Actor、Verifier 和 Final 事件；现有工具调用信息继续显示，但不会泄露密钥或未裁剪的大段内容。

## 错误处理

- Planner 或 Verifier 的结构化输出无效：产生安全错误并结束，不回显供应商异常中的敏感数据。
- Actor 达到 ReAct 循环上限：作为失败证据进入 Verifier，而不是直接判定成功。
- 验证命令失败：保留裁剪后的 stdout/stderr，进入下一轮 Planner。
- 达到最大尝试次数：Final 明确报告未通过以及最近证据，进程返回失败。

## 测试策略

- 单元测试覆盖图状态、Todo 转换和更新、验证命令执行与路由。
- 用脚本化模型完成一次成功流程和一次“失败后重新规划并修复”的流程。
- CLI 测试覆盖 `--max-attempts`、阶段事件显示和退出码。
- 保留全部阶段一测试作为回归测试。
- 阶段二现场案例为康威生命游戏：要求先生成测试、实现规则、运行测试，并执行一个有限步数的无界面演示。

## 规格对齐补完

阶段二完成前还需把已经可运行的实现与根规划中的分层约定对齐，同时保留现有更严格的验证安全边界：

- `MiniclaudeGraphState` 使用 `TypedDict(total=False)`；继续使用新品牌名称，不恢复旧名称。
- ReAct 执行器可选收集本轮实际 `SystemMessage/HumanMessage/AIMessage/ToolMessage`，Actor 与 Verifier 将这些消息作为增量写回共享 `messages`，由 `add_messages` reducer 合并。
- Planner 仍以 function-calling 结构化输出生成完整计划，但发布 Todo 时必须经过真正的 `TodoWriteTool` 和统一 `execute_tool` 错误边界，不直接调用 Tracker 内部方法。
- `core/agent.py` 新增 `stream_workflow_events()`，负责创建 RuntimeState、初始图状态、构建工作流，并同时解析 `updates` 与 `custom` 流。CLI 不再直接构建图。
- `updates` 规范化为节点完成事件，承载 Planner 的计划/Todo/验收标准/命令、Actor 的 Todo/摘要、Verifier 的 checks/命令结果/结论，以及 Final 的最终回答；`custom` 只保留内部模型和工具进度，避免重复显示阶段摘要。
- `FINAL_PROMPT` 是确定性格式模板，由 Final 节点格式化使用，不增加模型请求。
- 新增真实工作流到 CLI 的离线集成测试、消息累积测试、Planner TodoWrite 边界测试和 updates 消费测试。

真实 DeepSeek 康威生命游戏 live 验收不属于本轮自动执行范围，保留为阶段二最后一项人工授权验证。
