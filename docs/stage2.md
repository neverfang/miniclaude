# 阶段二：Plan → Execute → Verify 学习指南

阶段二在阶段一 ReAct 外增加 LangGraph。阶段一并未被删除：它成为 Actor 节点的执行内核；外层工作流负责先规划、再独立验证，并在失败时携带证据重试。

## 1. 安装与测试

在仓库根目录执行：

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run miniclaude --help
```

普通测试使用脚本化模型，不请求真实 API。LangGraph、文件工具和验证命令执行均使用真实实现。

## 2. 工作流

```text
START → Planner → Actor → Verifier ──通过──→ Final → END
                     ↑          │
                     └──重新规划─┘（未通过且 attempts < max_attempts）
```

- Planner 生成计划摘要、Todo、验收标准和有限的验证命令。
- Actor 复用阶段一 ReAct，获得文件、搜索、Shell 和 Todo 工具。
- Verifier 实际运行验证命令，只获得 FileReadTool 与 GrepTool 检查工作区；Actor 的总结被明确视为未验证信息。只读检查完成后，另一次 function-calling 结构化调用才生成最终判定。
- Final 不调用模型，只根据验证状态生成成功或失败结果。

## 3. 配置兼容模型

继续使用项目根目录 `.env`：

```dotenv
OPENAI_API_KEY=你的密钥
OPENAI_MODEL=你的模型名称
OPENAI_BASE_URL=https://你的兼容服务地址/v1
# DeepSeek 兼容网关建议设置；官方 api.deepseek.com 会自动启用
OPENAI_THINKING=disabled
```

接口必须兼容 OpenAI Chat Completions，并支持工具调用和结构化输出。环境变量优先于 `.env`。不要提交真实密钥。

DeepSeek thinking 模式的工具对话要求回传非标准 `reasoning_content`，通用 ChatOpenAI
消息转换不会保留该字段。因此本阶段显式禁用 thinking：官方 `api.deepseek.com` 自动处理，
第三方 DeepSeek 兼容网关请设置 `OPENAI_THINKING=disabled`。其他值会直接报配置错误，
避免任务运行到第二次工具请求时才收到 HTTP 400。

## 4. 运行阶段二任务

阶段二已经是默认 CLI 路径。生成代码并运行测试通常需要显式授权 Shell：

```powershell
uv run miniclaude "创建一个带 unittest 的字符串计算器，先写测试再实现，运行测试并修复失败" --allow-shell --max-loops 20 --max-attempts 3
```

默认生成目录仍为 `.miniclaude/workspaces/<运行标识>/`。也可以复用明确目录：

```powershell
uv run miniclaude "修复测试失败并重新验证" -w .miniclaude/workspaces/my-demo --allow-shell --max-attempts 3
```

`--max-loops` 控制每次 Actor ReAct 的最大模型轮数；`--max-attempts` 控制完整 Plan-Execute-Verify 最多重复几次。Verifier 每运行一次，attempts 增加一次。

不传 `--allow-shell` 时，Planner 会生成零命令的只读验收方案，适合阅读、解释和检查文件；
一旦计划需要运行测试或演示，就必须显式传入 `--allow-shell`。

## 5. 康威生命游戏验收案例

```powershell
uv run miniclaude "使用 Python 标准库创建 game_of_life.py。实现纯函数 next_generation(cells)，创建 test_game_of_life.py，用 unittest 覆盖人口不足、存活、人口过剩、繁殖、静物 block 和 blinker 振荡器。支持 python game_of_life.py --pattern blinker --steps 4，输出有限的确定性文本演示并退出，不打开 GUI。先写测试，再实现，运行测试和演示并修复失败。不要安装依赖或启动后台进程。" -w .miniclaude/workspaces/life-demo --allow-shell --max-loops 25 --max-attempts 3
```

完成后人工复核：

```powershell
uv run python -m unittest discover -s .miniclaude/workspaces/life-demo -v
uv run python .miniclaude/workspaces/life-demo/game_of_life.py --pattern blinker --steps 4
```

项目还提供显式 live 验收。它会消耗 API 额度并执行模型生成的代码，默认不会运行：

```powershell
uv run pytest tests/test_stage2_live.py -q -s --run-live-stage2
```

## 6. 推荐阅读顺序

1. `graph/state.py`：理解整个图共享哪些状态。
2. `tools/todo_tools.py`：理解计划怎样成为可更新的 Todo。
3. `graph/nodes.py`：依次阅读 Planner、Actor、Verifier、Final。
4. `graph/workflow.py`：观察条件边如何形成有上限的反馈循环。
5. `core/agent.py`：对照阶段一，理解 ReAct 如何被嵌入 Actor 和 Verifier。
6. `cli/app.py`：理解 `updates`、`custom` 流和退出码。
7. `tests/test_workflow.py`：观察成功、重规划、上限终止及只读验证的确定性测试。

## 7. 安全边界与限制

`--allow-shell` 仍然表示允许宿主机本地命令执行，工作区不是容器或安全沙箱。Verifier 不具备文件写工具，但其验证命令仍通过同一个 Shell 执行，因此也必须遵守用户授权。

结构化计划和判定能减少含糊输出，但不能保证任意兼容服务都完整实现工具调用协议。模型失败、无效 JSON、命令失败或循环耗尽都不能算作验证成功。达到最大尝试次数后 CLI 返回 1，并保留工作区供检查。

本阶段没有多 Agent、网络搜索、上下文压缩或长期记忆；这些能力按项目规划在后续阶段实现。

## 参考

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
- [LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)
