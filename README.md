# chatmgr

管理本机 AI 编程 agent 聊天记录的命令行工具。**纯 Python 标准库、单文件、无依赖、无构建步骤。**

几个 agent 把每次对话都存了下来,但这些记录**留着却用不起来**——搜不到、读不了、统计不了。
这个工具把它们"用起来":跨源搜索、阅读、统计、安全删除。

## 支持的数据源

| 命令 | 管哪些源 | 记录在哪 |
|---|---|---|
| `cgr`(= `chatmgr`) | **全部**,按源分段列出 | 下面三处都读 |
| `cla` | 只看 Claude Code | `~/.claude/projects/<项目>/<会话id>.jsonl` |
| `cod` | 只看 Codex | `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl` |
| `opc` | 只看 OpenCode | `~/.local/share/opencode/opencode.db`(SQLite) |

这几个名字是**同一个文件的软链** —— 靠"自己被叫什么名字"决定默认看哪些源,`-S/--source` 可覆盖。
子命令、参数、指认方式完全一致。

## 安装

```bash
git clone <本仓库> && cd ChatManager
ln -sfn "$PWD/chatmgr.py" ~/.local/bin/cgr
ln -sfn "$PWD/chatmgr.py" ~/.local/bin/cla   # 可选:只看 Claude
ln -sfn "$PWD/chatmgr.py" ~/.local/bin/cod   # 可选:只看 Codex
ln -sfn "$PWD/chatmgr.py" ~/.local/bin/opc   # 可选:只看 OpenCode
```

## 快速开始

```bash
cgr                # 列出所有会话(几个源分段列出)
cgr 3              # 打开第 3 个
cgr 关键词         # 跨所有 agent 搜内容
cgr f              # fzf 交互浏览:Enter 打开 / Ctrl-R 续聊 / Ctrl-E 改名 / Ctrl-D 删除
cgr stats -b day   # 按天看用量
cgr -h             # 全部子命令
```

## 文档

- **[docs/usage.md](docs/usage.md)** —— 命令手册(每个子命令的详细用法)
- **[docs/sources.md](docs/sources.md)** —— 各数据源的实测记录:记录格式、踩过的坑、以及"接入一个新 agent 要回答的问题"清单
- **[docs/prd.md](docs/prd.md)** —— 需求与已确认的产品决策
- **[CLAUDE.md](CLAUDE.md)** —— 给 Claude Code 看的开发指引

## 设计要点

- **只读为主,破坏性操作默认只预览。** `rm` / `purge` / `dedupe` 不给 `--force` 只打印预览;
  交互终端下再问一句;**非 TTY 一律只预览、绝不执行**。
- **每个源一个 `Source` 实现**,命令层只认归一化后的 `Session` 和 `(kind, text)` 事件流 ——
  加一个新 agent 基本不用动命令层。
- **解析一律逐行容错**。这些 jsonl / 库都是各家内部格式、没有契约、随版本变,
  所以坏行跳过、未知类型忽略,不硬编码"必需"字段。
- **不做成本估算** —— 只报 token。同一份记录在不同订阅方式下"多少钱"的算法不同,算了是假数字。
