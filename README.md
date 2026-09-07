# miniclaude

从零构建轻量级编程 Agent：通过六个阶段，逐步实现 ReAct 工具调用、规划与验证、多智能体协作、上下文管理、审批恢复和终端交互。

这是一个循序渐进的学习项目，参考 Claude Code 的使用形态，重点是理解 Agent 的设计与实现，而不是对齐全部功能。本项目并非 Anthropic 官方项目。

## 当前进度

六个学习阶段均已实现。当前版本为 v0.6.0，支持一次性任务、checkpoint 恢复，以及
带持久 Session、意图路由和右侧状态栏的 Textual TUI。

## 学习路线

| 阶段 | 核心内容 | 状态 |
| --- | --- | --- |
| 1. ReAct | 文件读写、编辑、搜索、命令执行与基础工具调用循环 | 已完成 |
| 2. LangGraph | Plan → Execute → Verify，失败重试与验收 | 已完成 |
| 3. MultiAgent | Planner 调度搜索与编码专家，记录任务交接 | 已完成 |
| 4. Context Engineering | 上下文监控与压缩、持久笔记、分层记忆 | 已完成 |
| 5. Harness Engineering | 风险操作审批、检查点与恢复、执行追踪 | 已完成 |
| 6. 终端交互 | Textual TUI、多轮会话与意图路由 | 已完成 |

计划每完成一个阶段，在测试验收后发布对应版本，从 `v0.1.0` 逐步演进到 `v0.6.0`。

## 计划采用的技术

- Python：项目开发语言。
- LangChain / langchain-openai：模型调用与工具绑定。
- LangGraph：从第二阶段开始引入工作流编排。
- Typer / Rich：命令行入口与输出展示。
- Tavily：搜索能力。
- Textual：最终阶段的终端交互界面。

## 快速启动

    uv sync
    uv run miniclaude

普通 miniclaude 每次创建新 Session；miniclaude -c 恢复最近 Session。一次性任务
仍可使用 miniclaude "任务"。阶段六的完整目录、快捷键和安全说明见
[阶段六文档](docs/stage6.md)。

## 项目文档

- [项目篇规划](项目篇规划.md)：六阶段设计、示例 Prompt、演示任务与视频讲解规划。

规划文档中的代码片段、源码路径和演示输出是设计参考，不代表当前已实现或已验证的功能。

## 安全提醒

- 不提交真实 API Key、`.env` 或含敏感内容的运行日志。
- Agent 的命令执行能力应先在专用测试环境使用。
- 工作目录限制不等于操作系统级沙箱，安全边界需要在实现中明确验证。
