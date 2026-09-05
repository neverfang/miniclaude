# 阶段三：MultiAgent 协作学习指南

阶段三把默认执行路径升级为 Supervisor、SearchAgent、CodeAgent 与独立 Verifier 的协作图。阶段一 ReAct 和阶段二工作流仍保留，便于逐层对照学习。

## 1. 安装与离线测试

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run miniclaude --help
```

普通测试使用脚本化模型和搜索客户端，不访问 DeepSeek 或 Tavily。真实联调默认跳过。

## 2. 工作流与权限

```text
START -> Supervisor -> Verifier -> Final -> END
             ^            |
             +------------+  未通过且未达到 max-attempts
```

- Supervisor 只能使用 `TodoWriteTool`、`CallSearchAgentTool`、`CallCodeAgentTool`。每次尝试都必须先发布完整 Todo、验收标准和验证命令。
- SearchAgent 只能使用 `WebSearchTool`，负责查找外部事实和返回来源，不写代码。
- CodeAgent 使用文件、Grep、可选 Shell、Todo 和固定路径 Notepad 工具，不直接访问网络。
- Verifier 独立运行明确的验证命令，并只使用 FileRead、Grep、NotepadRead 检查工作区。代理总结、来源和交接均被视为待验证证据。
- Final 只根据验证状态生成最终结果。

有外部事实需求时，交接顺序为 `planner -> searchAgent -> planner -> codeAgent`；纯编码任务可直接交给 CodeAgent。每次交接都记录 instruction、result 和 ok 状态，并在终端中单独显示。

## 3. DeepSeek 与 Tavily 配置

在仓库根目录创建不提交的 `.env`：

```dotenv
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_THINKING=disabled
TAVILY_API_KEY=你的Tavily密钥
```

环境变量优先于 `.env`。不要把真实密钥写入源码、测试、NOTEPAD 或 Git。兼容端点需要支持 OpenAI Chat Completions、工具调用和结构化输出。

## 4. 启动示例

需要创建代码并运行测试时显式允许 Shell：

```powershell
uv run miniclaude "调研 Python dataclasses 的官方用法，创建带来源链接的示例页面并测试" --allow-shell --max-loops 20 --max-attempts 3
```

指定可复用工作区：

```powershell
uv run miniclaude "继续修复并验证当前实现" -w .miniclaude/workspaces/my-demo --allow-shell
```

不传 `--allow-shell` 时，Supervisor 不允许发布验证命令，适合只读分析任务。工作区不是安全沙箱；启用 Shell 仅用于可信任务。

## 5. Notepad 与事件 UI

CodeAgent 可把跨交接仍需保留的短决定写入工作区 `NOTEPAD.md`。路径固定、UTF-8、最大 64 KiB，模型不能指定任意路径。Verifier 可只读检查该文件。

终端将 Supervisor、Handoff、SearchAgent、CodeAgent、Verifier 分成独立面板；每个角色内部的 Tool Call 与 Tool Result 也分别显示。长输出会截断，原始工作区文件不受影响。

## 6. 真实端到端验收

以下命令会消耗 DeepSeek 与 Tavily 额度，并允许执行生成的验证命令，默认不会运行：

```powershell
uv run pytest tests/test_stage3_live.py -q -s --run-live-stage3
```

测试要求环境中存在 `OPENAI_API_KEY`、`OPENAI_MODEL` 和 `TAVILY_API_KEY`，生成有限的 `research.html`，不启动服务器或 GUI，并检查至少两个 HTTP(S) 来源链接。

## 7. 推荐阅读顺序

1. `graph/state.py`：共享状态、来源和交接记录。
2. `graph/supervisor.py`：三个 Supervisor 工具与调用顺序约束。
3. `agents/search_agent.py`：单一网络能力的研究代理。
4. `agents/code_agent.py`：实现代理及 Todo/Notepad 协作。
5. `graph/stage3_workflow.py`：条件边和重试上限。
6. `graph/nodes.py`：独立验证的证据处理。
7. `core/agent.py` 与 `cli/render.py`：事件归一化和分面板 UI。
8. `tests/test_stage3_workflow.py`：成功、失败重试和上限终止。

## 8. 当前边界

阶段三不提供操作系统级沙箱。WebSearch 返回内容和代理总结都是不可信输入，最终结果必须由实际文件检查、验收标准和命令结果决定。上下文压缩、长期记忆与更复杂的并发调度留到后续阶段。
