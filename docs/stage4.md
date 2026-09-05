# 阶段四：Context Engineering 学习指南

阶段四在阶段三 MultiAgent 的基础上增加三层记忆、上下文计数、自动压缩和恢复路由。
Supervisor、SearchAgent、CodeAgent 与独立 Verifier 的职责不变，阶段二和阶段三的图构建器
也继续保留，方便逐阶段对照。

## 1. 默认工作流

    START -> Contextual Supervisor -> Context Monitor -> Verifier -> Final
                  ^                       |                |
                  |                       v                |
                  +--------------- Context Compressor      |
                  |                                        |
                  +----------- verification failed --------+

Supervisor 仍通过工具调用专家。Context Monitor 不执行代码、不访问网络，只计算当前消息的
token 数并选择 verifier、context compressor 或 final。压缩成功后回到 Supervisor，让它
依据恢复摘要继续；验证成功或达到最大尝试次数后进入 Final。

## 2. 三层记忆

1. Rules：固定的工作区、路径、Todo、可信度和持久化规则。
2. Working memory：当前任务、计划、Todo、验收标准、近期研究、交接和验证状态。
3. History store：HISTORY_SUMMARY.md、NOTEPAD.md、当前恢复摘要和最近三次压缩事件。

每次生成 memory snapshot 都会重新投影和深拷贝。长字段、来源和交接数量均有限制；模型
输出、工具输出、历史摘要与代理声明始终标记为待验证证据。

## 3. Token 计数与固定上限

生产上限固定为 400,000 tokens，不提供 CLI 修改参数。Context Monitor 优先调用当前
OpenAI 兼容模型对象的 get_num_tokens_from_messages；若该方法不存在或失败，则使用
本地 max(1, len(text) // 4) 估算。计数过程不会发起模型请求。

测试可以在 state 中注入更小上限和确定性计数器，以稳定触发压缩，但这不会改变生产默认值。

## 4. 压缩与恢复

Context Compressor 使用 function_calling 结构化输出，保留任务目标、已完成工作、未完成
Todo、重要文件、工具发现、来源、下一步和风险。消息替换采用 LangGraph 的
RemoveMessage(REMOVE_ALL_MESSAGES)，随后只保留一条恢复摘要。

压缩摘要同步写入固定路径 HISTORY_SUMMARY.md：

- UTF-8，最大 64 KiB；
- 拒绝符号链接、目录和硬链接；
- 使用同目录临时文件替换；
- 模型不能选择写入路径。

模型调用失败、结构无效或空输出时使用纯本地确定性摘要。首次摘要仍超限时进一步降为最小
恢复摘要。连续三次压缩后仍超限会安全终止，避免无限循环。持久化失败只产生已净化警告，
内存中的有效摘要仍可继续使用。

## 5. 终端事件

终端继续把 Tool Call 和 Tool Result 分开显示，并新增两个独立面板：

- Context Monitor：当前 token、400,000 上限、计数方式和下一路由；
- Context Compressor：压缩前后 token、删除消息数、fallback、历史写入状态和下一路由。

所有模型文本使用 Rich Text 作为普通文本渲染，方括号和花括号不会当作 markup；状态字符
保持 ASCII，兼容 Windows GBK 终端。

## 6. 离线验证

    uv sync --locked
    uv run --locked pytest -q
    uv run --locked ruff check .
    uv run --locked ruff format --check .
    uv run --locked miniclaude --help

普通测试使用脚本化模型和计数器，不读取仓库 .env，不访问 DeepSeek 或 Tavily。

## 7. 真实 DeepSeek 验收

以下命令会调用已配置的 OpenAI 兼容 DeepSeek，并允许执行生成代码；默认不会运行：

    uv run --locked pytest tests/test_stage4_live.py -q -s --run-live-stage4

该测试直接运行 Stage 4 图，用测试计数器强制至少一次压缩，创建标准库多文件项目并运行
unittest。它不使用 Tavily、不安装依赖、不启动服务器或 GUI，同时检查 History、Notepad、
生成文件、最终验证状态和压缩后 token。

## 8. 当前边界

阶段四仍不是操作系统沙箱。启用 --allow-shell 只适用于可信任务。阶段五的人工审批、
checkpoint 和阶段六的会话管理、完整 TUI 均明确留到后续阶段；README 继续等整个项目完成
后统一编写。
