# 阶段一：ReAct 基础 Agent 设计

状态：对话中的范围已获确认；此文件将范围固化为实施和验收依据。

## 目标与边界

实现 Python 命令行编程 Agent，使用 LangChain 工具绑定和手写 ReAct 循环。
本阶段不实现 LangGraph、Planner、Verifier、多 Agent、持久记忆、审批工作流或 TUI。
README 暂不扩写，现有《项目篇规划.md》保持原位。

## 模块与数据流

- `src/miniclaude/core/state.py`：工作区、读取快照、工具资源上限。
- `src/miniclaude/core/paths.py`：解析工作区相对路径，拒绝越界和受保护路径。
- `src/miniclaude/core/agent.py`：模型与工具执行循环，产出结构化事件。
- `src/miniclaude/tools/`：文件读取、写入、精确编辑、文本搜索、Shell 执行和注册。
- `src/miniclaude/providers/openai_provider.py`：从环境变量或指定 .env 创建模型。
- `src/miniclaude/cli/app.py`：Typer 入口、Rich 事件显示、退出码。

消息顺序为 SystemMessage、HumanMessage、AIMessage(tool_calls)、对应 ToolMessage，
重复直到模型返回非空的最终回答。工具调用按顺序执行；每个调用结果使用原始调用 ID 回传。
模型通过参数注入，因此测试可替换模型但使用真实文件工具和子进程。

## 五个工具

1. FileReadTool：UTF-8 文本，offset 从 0 开始，limit 默认 2000 行，返回行号和截断提示。
2. FileWriteTool：创建父目录和新文件；覆盖已有文件前必须先读，且读取后内容不能已变更。
3. FileEditTool：必须先读，旧文本必须恰好匹配一次；失败不能改动文件。
4. GrepTool：正则、文件 glob、大小写和结果数限制；默认跳过 Git、环境、密钥和运行目录。
5. BashTool：默认关闭，用户必须显式提供 --allow-shell；在工作区执行，非交互，限制时间和输出。

工具参数使用类型校验，失败返回结构化错误供模型修正。文件操作拒绝绝对路径、父目录逃逸、
符号链接逃逸、.git 和 .env 等敏感路径。读取有文件体积上限，写入有内容体积上限。
编辑前快照检查降低意外覆盖风险，但不声称对抗并发恶意文件系统修改。

## 命令执行边界

Windows 使用 PowerShell，POSIX 使用 /bin/sh；工具名称沿用规划中的 BashTool。
工作目录不是沙箱：启用 Shell 后，命令可能访问宿主机和网络，必须在可信的专用环境使用。
不通过脆弱的正则黑名单宣称安全。停止超时进程时尽可能清理其进程树；不支持后台任务。
子进程移除常见模型 API Key 和跟踪配置，并使用当前 Python 解释器对应的 PATH。
这不是完整的秘密隔离或操作系统权限隔离。

## 配置与退出状态

配置项：OPENAI_API_KEY、OPENAI_MODEL、可选 OPENAI_BASE_URL。
只读取明确指定的 .env 或启动目录的 .env，不从 Agent 生成的工作区向上搜索配置。
模型名无隐式付费默认值；请求有超时和有限重试。
CLI 支持 task、--workspace/-w、--max-loops、--allow-shell 和 --env-file。
无工具调用的正常回答意味着循环结束，不代表独立验收通过；本阶段没有 Verifier。
配置失败、模型异常、无效响应、循环耗尽、用户中断均明确报告并返回非零退出码。

## 目录与测试

- 代码只放 src，测试只放 tests，学习与设计文档集中 docs。
- 默认工作区位于 .miniclaude/workspaces/ 下，每次运行单独创建；不提交 Git。
- 单元测试使用 pytest 临时目录，默认不联网、不调用真实模型。
- 依赖使用 pyproject.toml，锁定结果写入 uv.lock；虚拟环境放 .venv。
- 不保存重复源码副本或一次性调试脚本。

验收覆盖：五工具的成功与失败、路径边界、读取后变更、唯一编辑、截断、搜索过滤、
Shell 默认拒绝/显式启用/超时/非零退出；ReAct 消息配对、错误反馈、循环终止与耗尽；
CLI 配置错误、帮助与退出码；模拟模型调用真实工具完成创建并执行程序。

真实模型验收单独启用，未配置或未显式选择时跳过。加法案例要求生成代码与测试并运行；
贪吃蛇案例要求模型生成逻辑测试、无界面 smoke 模式及可手动启动的 GUI。
真实验收执行模型生成的代码，仅在用户明确启用时运行；不把离线通过写成真实模型通过。

## 参考

- https://www.xuanyuancode.com/learn-claude-code/tutorials/ct3 ：先读再改、读取快照。
- https://www.xuanyuancode.com/learn-claude-code/tutorials/ct4 ：精确编辑和变更校验。
- https://www.xuanyuancode.com/learn-claude-code/tutorials/ct2 ：专用工具优先，命令执行边界。
- https://docs.langchain.com/oss/python/langchain/tools ：工具 schema 和返回值。
- https://docs.langchain.com/oss/python/integrations/chat/openai ：ChatOpenAI 集成。

参考设计原则，不复制第三方实现。
