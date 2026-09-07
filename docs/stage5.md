# 阶段五：Execution Harness

阶段五在阶段四的 LangGraph 工作流外增加一层执行 Harness。图仍负责
Supervisor → Context Monitor/Compressor → Verifier 的业务路由；Harness 只负责审批、
checkpoint、恢复和 trace。把这层放在图外，可以在图尚未启动、节点抛错或用户按下
`Ctrl+C` 时仍然保存一致的恢复信息，也避免把运行时安全策略混入 Agent 提示词。

## 启动与授权

`--allow-shell` 和 `--approval-mode` 解决不同问题：前者决定 BashTool 能否运行任何本地
命令，后者决定已允许 shell 后，风险命令是否还要批准。未加 `--allow-shell` 时，审批
模式不会绕过总开关。

```powershell
uv run miniclaude "创建 calculator.py 和 unittest，并执行测试" --allow-shell
```

审批模式：

- `inline`（默认）：安全命令直接执行；风险命令在交互式终端显示完整风险面板并询问，
  默认答案为拒绝。非交互终端一律拒绝。
- `all`：安全命令和风险命令都在交互式终端显示完整审批面板并询问；默认答案为拒绝。
  blocked 命令仍然直接拒绝，不能人工放行。
- `auto`：自动批准风险命令，适合用户已经审核过的隔离任务；它不会放行硬阻断命令。
- `deny`：安全命令可执行，所有风险命令自动拒绝。

分类示例：

- safe：`python -m unittest discover -v`、`git status`；
- risky：`pip install ...`、`uv add ...`、`curl ...`、启动开发服务器；
- blocked：`git reset --hard`、破坏性 `git clean`、广泛递归删除、关机或格式化命令。

blocked 命令不进入子进程，也不能通过 `auto` 放行。审批事件和结果都会进入 trace。
命令中的凭据样式内容会在终端和持久化文件中脱敏。

## Checkpoint 模式与目录

- `light`（默认）：在安全边界更新 `checkpoint.json`、`RECOVERY.md` 和隔离的 Git 快照；
- `strict`：除 light 内容外，再写 `state.json` 和追加式 `events.jsonl`，便于逐步审计；
- `off`：不写 checkpoint，也不能为本次新运行提供恢复点。

工作区内的布局如下：

```text
.miniclaude/
├── checkpoints/
│   ├── checkpoint.json
│   ├── RECOVERY.md
│   ├── state.json       # strict only
│   ├── events.jsonl     # strict only
│   └── snapshot.git/
└── traces/
    └── <trace-id>/
        ├── events.jsonl
        ├── trace.json
        └── timeline.md
```

快照仓库与用户项目的 `.git` 隔离，不提交 `.env`、`.git`、`.miniclaude`、符号链接、
超大文件或其他受保护内容。

## 中断与语义恢复

在运行中按 `Ctrl+C` 会写入 `interrupted` checkpoint，并以退出码 130 结束。普通恢复：

```powershell
uv run miniclaude --resume D:\workspace\miniclaude\.miniclaude\workspaces\<workspace-id>
```

若要在恢复后审批每一条安全或风险 BashTool 命令，使用：

```powershell
uv run miniclaude --resume D:\path\to\workspace --allow-shell --approval-mode all
```

`all` 仍然需要 `--allow-shell`；它显示的是当前终端内的确认面板，而不是图形界面弹窗。
blocked 命令不会显示确认，也不能人工放行。

恢复时可以省略任务；若提供任务，它必须与 checkpoint 中的任务在空白规范化后相同。
系统严格校验格式版本、工作区身份、尝试次数、消息类型和所有允许恢复的字段，然后用
当前命令行策略重建 Runtime。恢复固定从 `contextual_supervisor` 开始，不重放可能只执行
了一半的工具调用；`passed`、`final_answer` 和瞬态上下文路由也会重置。

普通 `--resume` 不覆盖当前文件，因此中断后手工修复的内容会保留。只有明确要求时才还原
checkpoint 快照：

```powershell
uv run miniclaude --resume D:\path\to\workspace --restore-workspace
```

显式还原会先为当前可跟踪文件创建备份提交，再恢复 checkpoint 版本；未跟踪文件保留。
不要把 `--restore-workspace` 当作清理命令。

## Trace 与排错

`--trace-mode on`（默认）为每次运行创建不可变 trace 目录。恢复运行会在新 trace 的
`resumed_from_trace_id` 中指向上一次 trace。`events.jsonl` 保留有序事件；`trace.json`
包含节点访问、工具调用、失败工具、审批、checkpoint、handoff 和上下文压缩计数；
`timeline.md` 提供有界的人工可读摘要。关闭方式：`--trace-mode off`。

```powershell
Get-Content D:\path\to\workspace\.miniclaude\checkpoints\RECOVERY.md
Get-Content D:\path\to\workspace\.miniclaude\traces\<trace-id>\timeline.md
Get-Content D:\path\to\workspace\.miniclaude\traces\<trace-id>\trace.json
```

所有持久化数据都会限制深度、集合数量和字符串长度，并过滤凭据字段、Bearer token、
URL 用户信息和绝对路径。checkpoint 或 trace 写入失败会产生 warning，但不会伪造成功；
最终是否成功仍只由 Verifier 的证据决定。

checkpoint 损坏、版本不兼容、任务或工作区不匹配时，恢复在模型调用前后以安全错误停止，
CLI 返回退出码 2。检查 `RECOVERY.md`、保留当前文件并修复或移走损坏的 checkpoint；不要
手工猜测并重放中断命令。

退出码：0 表示 Verifier 通过；1 表示验证失败或普通运行错误；2 表示参数、模型配置或
恢复 checkpoint 错误；130 表示用户中断。

## 真实 DeepSeek 验收（显式选择）

默认测试不会调用 DeepSeek、Tavily，也不会执行真实风险命令。付费验收必须由用户单独
授权，并确认仓库根目录 `.env` 已配置兼容 OpenAI 格式的 DeepSeek 参数：

```powershell
uv run --locked pytest tests/test_stage5_live.py -q --run-live-stage5
```

该测试使用临时工作区、禁用 Web 搜索、不安装依赖、不下载文件、不启动服务器或 GUI。
它通过测试注入规则把安全的 unittest 命令标记为 risky，以验证 `auto` 审批路径，而不
执行真正的风险操作。真实模型调用会产生费用，只有在明确授权后才运行。
