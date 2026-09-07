# 阶段六：Textual TUI、Session 与意图路由

阶段六为 Miniclaude 增加本地终端交互界面。它复用阶段五的工作流、审批、
checkpoint 和 trace，不建立第二套 Agent 运行时。飞书 Bot API 不在本阶段范围内。

## 启动方式

在项目目录中启动一个全新的、相互隔离的 Session：

    uv run miniclaude

普通启动每次都会创建新 Session。恢复最近一次 Session 及其工作区：

    uv run miniclaude -c
    uv run miniclaude --continue

按 ID 打开指定 Session：

    uv run miniclaude --session abc123def456

单次任务和阶段五 checkpoint 恢复仍保持原有入口：

    uv run miniclaude "创建并测试一个 Python 模块"
    uv run miniclaude --resume D:/path/to/checkpoint-workspace

--continue、--session、位置任务和 --resume 不能混用。交互 Session 的工作区固定
在启动目录下，不接受 --workspace；如需换项目，请先切换目录。

## Session 数据

每个 Session 使用独立目录：

    .miniclaude/sessions/
    ├── index.json
    └── <session-id>/
        ├── session.json
        ├── SESSION_SUMMARY.md
        └── workspace/

index.json 只保存 Session ID 和更新时间。session.json 使用版本化格式和原子替换，
保存有界的最近对话、route、checkpoint 与 trace 引用。模型上下文最多 7000 字符，只
列出最多 30 个安全文件名，绝不读取文件正文；.env、.git、.miniclaude、密钥文件
和链接路径不会进入上下文。

## 意图路由

每轮先由配置的 OpenAI 兼容 DeepSeek 模型返回结构化 chat 或 workflow 决策。
置信度必须为有限的 0 到 1，且至少为 0.55。低置信度、无效结构、未知 route 或服务商
异常都会安全回退到 workflow。

chat 路径不绑定任何工具，不能声称读过文件、运行命令、搜索网络或修改工作区。
编码、文件、测试、搜索以及“继续”“修一下”等上下文续接请求进入现有工作流。

## 界面与快捷键

主区域分为 Plan、Event Stream 和 Conversation。Tool Call、Tool Result、handoff、
checkpoint、trace 与 failure 分别显示为独立卡片；Conversation 只显示用户输入和最终
回复。右侧 Session 栏显示状态、turn、ID、route、工作区、checkpoint、trace、工具调用
及失败数、审批、上下文 token 和 todo 进度。

- Ctrl+C：有任务时取消并拒绝待审批；空闲时退出。
- Ctrl+L：只清空当前视觉内容，不删除 Session 历史。
- Ctrl+N：空闲时创建一个新 Session。
- Ctrl+O：在事件区显示完整工作区路径。
- Ctrl+S：折叠或展开 Plan。

窄终端会把 Session 栏移到底部并隐藏 Plan，以优先保留输入和事件内容。

## Shell 审批

开启 Shell 并要求每条命令审批：

    uv run miniclaude --allow-shell --approval-mode all

只有 Y 或 Approve 按钮批准当前精确命令。N、Esc、Enter、弹窗关闭、超时、
取消任务和退出 TUI 全部拒绝。阶段五的 blocked 命令仍不可人工放行。

## 测试

默认离线测试不调用 DeepSeek、Tavily 或真实风险命令：

    uv run pytest -q

显式付费验收会使用 .env 中配置的 DeepSeek，运行一轮无工具聊天和一轮禁用 Shell、
禁用 Tavily 的工作流：

    uv run pytest tests/test_stage6_live.py -q --run-live-stage6
