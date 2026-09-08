# Miniclaude

从零构建一个轻量级编程 Agent：通过六个阶段，逐步实现 ReAct 工具调用、
Plan → Execute → Verify、多智能体协作、上下文工程、执行安全机制和持久化终端会话。

Miniclaude 是一个面向学习与实验的 Python 项目。它参考 Claude Code 的交互形态，
重点在于理解 Coding Agent 的内部结构与工程取舍，而不是复刻全部产品能力。
本项目并非 Anthropic 官方项目。

当前版本：`v0.6.0`。六个学习阶段均已实现，并通过离线测试覆盖主要流程。

## 核心能力

- 使用兼容 OpenAI Chat Completions 格式的模型，支持 DeepSeek。
- ReAct 循环以及文件读取、写入、精确编辑和文本搜索工具。
- 可选的本地 Shell 执行、风险分类、人工审批与默认拒绝策略。
- 基于 LangGraph 的规划、执行、验证和失败重试流程。
- Planner、Search Agent、Code Agent 和 Supervisor 多智能体协作。
- 短期上下文、持久笔记、分层记忆、token 监控与上下文压缩。
- Checkpoint、工作区快照、恢复、trace 和结构化运行事件。
- Textual TUI、多轮 Session、意图路由、状态侧栏和斜杠命令。
- `Ctrl+C` / `Esc` 协作式取消，可停止模型后续调度、工具调用和 Shell 进程树。
- 项目本地 Skill 的安全发现、加载、激活与关闭。

## 六阶段学习路线

| 阶段 | 主题 | 主要成果 | 状态 |
| --- | --- | --- | --- |
| 1 | ReAct | 基础 Agent 循环、文件工具、搜索、Shell 与测试案例 | 已完成 |
| 2 | LangGraph | Plan → Execute → Verify、失败重试和验收条件 | 已完成 |
| 3 | MultiAgent | Planner 调度搜索与编码专家、handoff 和共享记事本 | 已完成 |
| 4 | Context Engineering | token 预算、上下文压缩、三层记忆和持久笔记 | 已完成 |
| 5 | Harness Engineering | 风险审批、checkpoint、恢复、快照和 trace | 已完成 |
| 6 | Terminal UX | Textual TUI、持久 Session、意图路由和运行状态 | 已完成 |

每一阶段都保留独立的学习文档和显式真实模型验收入口。默认测试不访问模型、
Tavily 或真实风险命令。

## 运行架构

交互 Session 中，每一轮输入先结合有限的 Session 上下文进行意图判断：

```text
用户输入 -> Session 上下文 -> 意图路由 -> chat：无工具对话
                                  \-> workflow：Plan -> Execute -> Verify
```

`chat` 路径用于普通问答，不绑定工具；文件操作、编码、测试、搜索和继续执行类请求
进入 `workflow`。工作流复用前五个阶段的工具、专家 Agent、上下文管理、审批、
checkpoint 和 trace，不维护第二套运行时。

核心目录：

```text
src/miniclaude/
├── agents/       # Code Agent 与 Search Agent
├── cli/          # Typer CLI、Rich 输出和 Textual TUI
├── commands/     # 本地斜杠命令注册表
├── core/         # Agent 循环、Session、审批、取消、checkpoint 与 trace
├── graph/        # LangGraph 状态、节点、Supervisor 和阶段工作流
├── prompts/      # 各阶段角色与行为约束
├── providers/    # OpenAI 兼容模型配置
├── skills/       # 项目本地 Skill 的发现与加载
└── tools/        # 文件、搜索、Shell、Todo、Notepad 与 Web Search 工具
```

## 环境要求

- Python `3.11` 或更高版本。
- [uv](https://docs.astral.sh/uv/)。
- 支持工具调用的 OpenAI 兼容模型。
- 可选：Tavily API Key，用于阶段三的联网研究能力。

## 安装

克隆仓库并安装依赖：

```powershell
git clone https://github.com/neverfang/miniclaude.git
cd miniclaude
uv sync
```

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

macOS 或 Linux 可使用：

```bash
cp .env.example .env
```

## 配置 DeepSeek 与 Tavily

Miniclaude 使用 `langchain-openai` 的 Chat Completions 接口，因此可以连接兼容
OpenAI 格式并支持工具调用的 DeepSeek 服务。编辑项目根目录下的 `.env`：

```dotenv
OPENAI_API_KEY=your_deepseek_api_key
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_THINKING=disabled

# 只有需要联网研究时才配置
TAVILY_API_KEY=your_tavily_api_key
```

说明：

- `OPENAI_API_KEY` 和 `OPENAI_MODEL` 必填。
- `OPENAI_BASE_URL` 可指向其他兼容 OpenAI 格式的服务，但非本机地址必须使用 HTTPS。
- 当前只支持 `OPENAI_THINKING=disabled`；尚未实现 `reasoning_content` 的往返传递。
- 官方 `api.deepseek.com` 地址会自动关闭 thinking，避免工具调用轮次缺少非标准字段。
- `TAVILY_API_KEY` 可选；未配置时，不应要求 Agent 完成联网搜索任务。
- `.env` 已被 Git 忽略，不要把真实密钥写入 README、提交记录或运行日志。

也可以通过 `--env-file` 显式指定其他配置文件：

```powershell
uv run miniclaude --env-file D:/secure/miniclaude.env
```

## 启动方式

### 新建交互 Session

```powershell
uv run miniclaude
```

不带参数启动时，每次都会创建一个新的 Session 和独立工作区。

### 继续最近的 Session

```powershell
uv run miniclaude -c
uv run miniclaude --continue
```

两条命令等价，会恢复最近一次 Session 的对话元数据和工作区。

### 打开指定 Session

```powershell
uv run miniclaude --session abc123def456
```

Session ID 必须是 12 位小写十六进制字符。`--continue`、`--session`、位置任务和
`--resume` 不能混用。

### 执行一次性任务

```powershell
uv run miniclaude "创建一个 Python 计算器，并为它编写和运行测试"
```

默认生成目录为 `.miniclaude/workspaces/<随机 ID>/`。也可以显式指定工作区：

```powershell
uv run miniclaude "实现康威生命游戏" --workspace D:/workspace/game-of-life
```

### 恢复 Checkpoint

```powershell
uv run miniclaude --resume D:/path/to/checkpoint-workspace
```

如需先把 checkpoint 快照恢复到工作区，再继续运行：

```powershell
uv run miniclaude --resume D:/path/to/checkpoint-workspace --restore-workspace
```

Session 恢复和 checkpoint 恢复是两个不同概念：`-c` 恢复多轮交互 Session，
`--resume` 恢复阶段五工作流的 checkpoint。

### 允许 Shell

Shell 默认关闭。对于可信任务，可在启动时显式允许：

```powershell
uv run miniclaude --allow-shell --approval-mode all
```

这不是操作系统沙箱。命令会在工作区中作为真实本地进程执行；虽然敏感环境变量会
从子进程环境中过滤，但仍应只在可控目录和可信任务中使用。

完整 CLI 参数：

```powershell
uv run miniclaude --help
```

## TUI 界面

主区域包含 Plan、Event Stream 和 Conversation：

- Plan 展示 Planner 或 Supervisor 生成的任务计划。
- Event Stream 将 Tool Call、Tool Result、handoff、checkpoint、trace 和失败信息
  分成独立卡片。
- Conversation 只显示用户输入和模型最终回复。
- 右侧 Session 栏显示状态、轮次、Session ID、route、工作区、checkpoint、trace、
  工具调用、失败数、审批数、token 和 todo 进度。

### 快捷键

| 快捷键 | 行为 |
| --- | --- |
| `Ctrl+C` | 任务运行时取消当前轮次；空闲时退出程序 |
| `Esc` | 取消当前轮次；空闲时不执行操作 |
| `Ctrl+L` | 清空当前可见卡片，不删除 Session 历史 |
| `Ctrl+N` | 空闲时创建一个新 Session |
| `Ctrl+O` | 在事件区域显示当前工作区 |
| `Ctrl+S` | 折叠或展开 Plan 面板 |

取消操作会拒绝待处理审批，并向模型调度、Agent 工具和运行中的 Shell 进程树传播。
会话会记录该轮为 `cancelled`，界面最终回到 `idle`，旧轮次的迟到事件不会写入新轮次。

## 斜杠命令

在 TUI 输入框输入 `/` 可以查看命令建议；输入 `/help` 查看完整列表。

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示本地命令列表 |
| `/status` | 显示 Session 和当前运行策略 |
| `/new` | 创建新的 Session |
| `/clear` | 清空可见卡片 |
| `/workspace` | 显示 Session 工作区 |
| `/plan` | 折叠或展开 Plan 面板 |
| `/approve [mode]` | 查看或切换 Shell 与审批策略 |
| `/skills` | 列出项目中发现的 Skills |
| `/skill <name>` | 激活指定 Skill |
| `/skill off` | 关闭当前 Skill |
| `/mcp` | 显示 MCP 状态和当前实现边界 |
| `/tools` | 列出当前可用工具 |
| `/exit` | 退出 Miniclaude |

`/approvals` 和 `/permissions` 是 `/approve` 的别名，`/quit` 是 `/exit` 的别名。
`/new`、`/approve` 和 `/exit` 只能在当前轮次空闲时执行。

## Shell 审批策略

启动参数 `--approval-mode` 和 TUI 命令 `/approve` 支持四种模式：

| 模式 | 安全命令 | 风险命令 | TUI 运行时切换效果 |
| --- | --- | --- | --- |
| `inline` | 直接执行 | 每次询问 `Y/N` | `/approve inline` 启用 Shell |
| `all` | 每次询问 `Y/N` | 每次询问 `Y/N` | `/approve all` 启用 Shell |
| `auto` | 直接执行 | 自动批准 | `/approve auto` 启用 Shell |
| `deny` | 启动参数下可执行安全命令 | 自动拒绝 | `/approve deny` 直接关闭 Shell |

示例：

```text
/approve all
```

在 `inline` 或 `all` 模式的审批窗口中，只有 `Y` 或 Approve 按钮会批准当前精确命令。
`N`、Enter 和关闭窗口都会拒绝；`Esc` 会拒绝审批并取消整个当前轮次。

无论使用何种模式，被风险分类器标记为 `blocked` 的命令始终拒绝，不能人工放行。
`auto` 只适合完全可信的本地任务。

## Session、工作区与运行产物

交互 Session 保存在启动目录下：

```text
.miniclaude/sessions/
├── index.json
└── <session-id>/
    ├── session.json
    ├── SESSION_SUMMARY.md
    └── workspace/
```

- `session.json` 保存有界的最近对话、route、checkpoint、trace 和轮次状态。
- `SESSION_SUMMARY.md` 保存供人阅读的 Session 摘要。
- `workspace/` 是该 Session 的独立文件工作区。
- Session 上下文只读取受限元数据和安全文件名，不会自动把整个工作区正文发送给模型。

一次性任务默认使用 `.miniclaude/workspaces/`。测试缓存、Session、checkpoint、trace
和生成文件均应留在 `.miniclaude/` 或明确指定的工作区中，避免污染项目根目录。

## 项目本地 Skills

Miniclaude 会从下面的目录发现项目 Skill：

```text
.miniclaude/skills/<skill-name>/SKILL.md
```

Skill 名称只允许小写字母、数字、下划线和连字符；`SKILL.md` 必须是非空 UTF-8 文本，
大小不超过 16 KiB，且不能通过符号链接逃出项目目录。

```text
/skills
/skill review
/skill off
```

激活后的 Skill 会在后续 Session 轮次中注入受限上下文。当前版本支持发现、加载和激活
已有项目 Skill，但没有 `/skill install` 命令，也不会自动从 GitHub 下载 Skill。

## MCP 当前状态

`/mcp` 命令已经预留，用于显示 MCP server 状态；当前版本尚未实现 MCP 配置加载、
连接、工具发现或调用。因此 README 不把 MCP 描述为可用集成，这部分属于后续阶段。

## 测试

运行全部默认离线测试：

```powershell
uv run pytest -q
```

本次 README 更新前的完整回归结果为：

```text
415 passed, 8 skipped
```

跳过项包括真实 DeepSeek/Tavily 调用、生成代码执行和依赖操作系统权限的测试。
它们不会在默认测试中产生费用或执行未授权风险操作。

显式运行阶段六真实 DeepSeek 验收：

```powershell
uv run pytest tests/test_stage6_live.py -q --run-live-stage6
```

其他阶段的真实验收命令请查看对应阶段文档。运行前必须检查 `.env`、任务内容、
Shell 权限和预期费用。

代码质量检查：

```powershell
uv run ruff check .
```

## 安全边界

- 文件工具把普通操作限制在当前工作区，并拒绝敏感目录、链接逃逸和过大的输入输出。
- 工作目录限制不等于操作系统级沙箱；启用 Shell 后，命令仍具有当前用户权限。
- Shell 默认关闭，必须通过 `--allow-shell` 或 TUI 中的 `/approve` 显式开启。
- 高风险命令需要审批或按策略拒绝，`blocked` 命令始终拒绝。
- 审批只针对当前精确命令，不代表后续命令获得永久授权。
- 真实模型和 Tavily 验收均为显式选择，不属于默认测试。
- 不要提交 `.env`、API Key、Session 运行记录或包含敏感内容的 trace。

## 项目文档

- [项目篇规划](项目篇规划.md)：六阶段目标、演示任务与学习路线。
- [阶段一：ReAct](docs/stage1.md)
- [阶段二：Plan → Execute → Verify](docs/stage2.md)
- [阶段三：MultiAgent](docs/stage3.md)
- [阶段四：Context Engineering](docs/stage4.md)
- [阶段五：Execution Harness](docs/stage5.md)
- [阶段六：Textual TUI 与 Session](docs/stage6.md)

各阶段的 `design` 和 `plan` 文档保存在 `docs/`，详细的功能设计与实施计划保存在
`docs/superpowers/`。规划文档中的示例代码和目标输出属于设计参考；是否已实现应以
当前源码、CLI 帮助和测试结果为准。

## 当前边界与后续方向

- MCP 目前只有命令入口，尚未连接真实 server。
- Skill 目前是项目本地加载，不包含远程发现和安装流程。
- DeepSeek thinking/reasoning 内容的非标准往返尚未实现。
- Shell 依靠风险分类、审批和工作区约束降低风险，不提供容器或操作系统沙箱。
- Miniclaude 是学习型实现，不承诺与 Claude Code 的命令、协议或行为完全兼容。
