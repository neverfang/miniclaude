# 阶段一：运行与学习指南

这个版本只实现一个模型驱动的 ReAct 循环和五个工具，不包含规划器、独立验收器、
多 Agent 或 TUI。最终回答表示模型结束了当前循环，不等于系统已独立证明任务完成。

## 1. 安装

要求 Python 3.11 或更高版本，以及 uv。以下命令在仓库根目录执行：

```powershell
uv sync --locked
uv run miniclaude --help
```

依赖安装在 `.venv/`，具体版本记录在 `uv.lock`。无需激活虚拟环境，也不会安装到全局环境。
运行时代码仅依赖 LangChain 的核心工具/消息包和 OpenAI 集成包，没有引入 LangGraph。

## 2. 先运行不需要密钥的测试

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

默认测试不请求真实模型。文件测试和子进程测试使用真实工具，只有模型回复使用预设测试替身。
例如端到端测试让模型依次调用 FileWriteTool 和 BashTool，实际创建 `add.py` 并运行，
然后检查返回给模型的 ToolMessage 中确实包含输出 `5`。

测试运行目录在 `.miniclaude/tmp/`；pytest 和 Ruff 缓存也放在 `.miniclaude/cache/`。
没有创建符号链接权限的 Windows 环境会跳过真实符号链接测试，其他路径边界测试照常运行。

## 3. 配置模型

在本地复制 `.env.example` 为 `.env`，填入以下配置。不要把真实密钥提交到 GitHub。

```dotenv
OPENAI_API_KEY=你的密钥
OPENAI_MODEL=你的模型名称
# 使用兼容服务时设置；留空则使用 OpenAI 官方端点
# OPENAI_BASE_URL=https://你的服务地址/v1
```

模型必须支持 OpenAI 兼容的 Chat Completions 工具调用。仅支持普通文本回复的接口不够。
非本地服务必须使用 HTTPS。模型名称必须明确指定，没有默认付费模型。
环境变量优先于 `.env`；默认只读取启动目录的 `.env`，不向父目录搜索，也不读取生成工作区的配置。

如配置文件在其他位置：

```powershell
uv run miniclaude "你好" --env-file D:/config/miniclaude.env
```

请求超时为 60 秒，最多自动重试 1 次。一次任务可能发起多轮请求，因此实际时间和费用会累加。

## 4. 最小任务

只创建文件，不执行命令：

```powershell
uv run miniclaude "创建 hello.py，运行时打印 Hello, miniclaude!；不要执行命令"
```

默认每次创建一个独立的 `.miniclaude/workspaces/<运行标识>/`，CLI 会显示完整路径。
也可以显式指定工作区；重复使用同一路径保留文件，但阶段一不会保留上一轮消息历史。

```powershell
uv run miniclaude "读取 hello.py，解释它的功能" -w .miniclaude/workspaces/my-demo
```

注意：这条命令要求指定工作区中已存在 hello.py；应替换成前一次输出的实际工作区。

## 5. 加法程序验收

下面的命令会允许模型在宿主机执行本地命令。仅在可信的专用环境中使用：

```powershell
uv run miniclaude "创建 add.py，提供 add(a,b) 函数；使用 unittest 创建 test_add.py，覆盖正数、负数和零；运行 python -m unittest discover -v，修复失败并总结。只用标准库，不安装依赖。" -w .miniclaude/workspaces/add-demo --allow-shell --max-loops 15
```

检查的不只是最后一段回答，还包括：

- 工作区内确实存在 add.py 和 test_add.py。
- 工具结果中测试命令退出码为 0，实际运行了测试而不是 `Ran 0 tests`。
- 人工查看测试是否真的覆盖需求。

## 6. 贪吃蛇演示

先让 Agent 生成可独立测试的逻辑与有限步数的无界面演示，避免 GUI 阻塞工具循环：

```powershell
uv run miniclaude "用标准库创建 snake.py 贪吃蛇游戏，将逻辑与 tkinter 界面分离。支持 --headless --steps 5，运行五步后输出摘要并退出。创建 test_snake.py，用 unittest 测试移动、吃食物增长、碰撞；运行测试和无界面演示，修复失败。不要打开 GUI、启动后台任务或安装依赖。" -w .miniclaude/workspaces/snake-demo --allow-shell --max-loops 20
```

审核生成代码后，可以手动启动 GUI：

```powershell
uv run python .miniclaude/workspaces/snake-demo/snake.py
```

GUI 需要可用的图形桌面和 tkinter。该游戏由模型现场生成，不是框架附带的固定示例。

## 7. 可选：自动运行真实模型验收

以下选项会花费 API 额度，并执行模型生成的代码；默认测试绝不会自动启用它们。
在项目 `.env` 中配置模型后运行：

```powershell
uv run pytest tests/test_live.py -q -s --run-live
uv run pytest tests/test_live.py -q -s --run-live-snake
```

第一个选项只运行加法案例，第二个只运行贪吃蛇案例。缺少配置会显示 SKIPPED，而不是通过。
测试除了检查 Agent 是否调用工具，还会独立重新运行生成的 unittest，拒绝零测试的结果；
加法案例另行检查函数结果，贪吃蛇案例另行运行有限步无界面模式，不启动 GUI。
`-s` 会输出生成文件所在路径，便于检查。模型输出具有不确定性，失败需要查看实际文件与结果。

## 8. 建议阅读代码顺序

1. `src/miniclaude/core/state.py`、`core/paths.py`：工作区边界和本次运行的已读状态。
2. `tools/file_tools.py`：读取、写入和精确编辑；读取快照为何能发现外部改动。
3. `tools/grep_tool.py`、`tools/bash_tool.py`：有边界的搜索和命令执行。
4. `tools/registry.py`：Python 类型签名如何成为模型看到的工具 schema。
5. `providers/openai_provider.py`：模型配置与工具调用接口。
6. `core/agent.py`：逐行理解 ReAct 循环；它没有隐藏的工作流框架。
7. `cli/app.py`：事件如何转换成终端输出和退出码。
8. `tests/test_agent.py`：观察 AIMessage 与 ToolMessage 如何按调用 ID 配对。

核心循环是：

```text
用户任务 → 模型返回工具调用 → 执行工具 → ToolMessage 返回结果 → 再次调用模型
                  └→ 没有工具调用且有回答 → 结束
```

工具错误会以 `{ok: false, error: ...}` 回传，让模型有机会修正。
请求异常、无效工具调用、空回答、循环耗尽则结束任务并报告错误，不能假装完成。
CLI 正常结束返回 0，运行失败返回 1，配置/参数错误返回 2，中断返回 130。

## 9. 工具边界与局限

| 工具 | 关键规则 |
| --- | --- |
| FileReadTool | UTF-8 文本；offset 从 0 开始，limit 为 1–2000；输出行号从 1 开始 |
| FileWriteTool | 新建文件；覆盖已有文件必须先读且内容未发生变化 |
| FileEditTool | 先读后改，old_text 必须非空且恰好匹配一次；保留 CRLF |
| GrepTool | 正则、glob、大小写开关；最多 200 条结果；跳过敏感/环境目录 |
| BashTool | 默认关闭；需 --allow-shell；Windows 为 PowerShell，POSIX 为 sh |

文件体积上限为 1 MiB；一般工具文本输出上限为 12000 字符，搜索单条文本最多 500 字符。
读取返回 `next_offset` 供分页；单行超出上限时标记 `partial_line` 并保留该行 offset，
此时应缩小目标或人工检查，不应把截断内容当作完整文件。
每次修改后需要重新读取，避免连续盲改。快照检查不是抵御恶意并发修改的原子锁。

Shell 默认超时 30 秒，单次可指定不超过 600 秒。超时/中断会尽力终止进程树，
但没有实现 Windows Job Object、容器或对抗恶意进程的隔离。不要启动后台服务或脱离进程组的任务。
文件工具的路径限制、密钥文件过滤和读取校验不约束 Shell；启用 Shell 就授予了本地命令执行能力。
子进程会过滤常见 API 密钥环境变量，但这也不是完整的秘密隔离。

本阶段没有压缩长上下文。超出模型窗口时会报错，后续阶段再解决。
本阶段的测试通过只能证明已覆盖的框架行为，不保证模型总能完成任意任务。

## 参考

- [文件读取与已读状态](https://www.xuanyuancode.com/learn-claude-code/tutorials/ct3)
- [精确文件编辑](https://www.xuanyuancode.com/learn-claude-code/tutorials/ct4)
- [Shell 工具边界](https://www.xuanyuancode.com/learn-claude-code/tutorials/ct2)
- [LangChain 工具](https://docs.langchain.com/oss/python/langchain/tools)
- [ChatOpenAI 集成](https://docs.langchain.com/oss/python/integrations/chat/openai)

只参考设计原则；本项目为独立的教学实现，不是 Claude Code 的复制品。
