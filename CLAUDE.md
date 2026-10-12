# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这是什么

`cgr`(= `chatmgr`)—— 管理 `~/.claude/projects/*/*.jsonl` 聊天记录的命令行工具。纯 Python 标准库、单文件、无依赖、无构建步骤。

## 标准文件路径

**仓库内**

| 路径 | 说明 |
|---|---|
| `chatmgr.py` | 工具本体,可执行,仓库根 |
| `CLAUDE.md` | 本指引(逐条维护,改约定要同步这里) |
| `docs/prd.md` | 需求 / 功能规格 / 已确认的产品决策 |
| `docs/usage.md` | 命令手册(每个子命令的详细用法) |
| `docs/images/` | 截图、界面参考图、流程图 |
| `docs/api/` | API 文档、接口约定、第三方对接说明 |

**仓库外(运行时会访问)**

| 路径 | 说明 |
|---|---|
| `~/.local/bin/cgr`(及 `chatmgr`) | 软链 → 仓库内 `chatmgr.py`,让 `cgr` / `chatmgr` 可从任意目录运行 |
| `~/.claude/projects/<项目>/<会话id>.jsonl` | **数据源(只读)**;子 agent 在 `<会话id>/subagents/` |
| `~/.claude/history.jsonl` | 用户的 prompt 流水(本工具暂未使用) |
| `~/.claude/settings.json` | 环境配置,含 `cleanupPeriodDays`;**不属于本仓库** |
| `~/claude-archive/<项目>/<会话id>.jsonl` | 归档/备份,同级有 `manifest.json`;**仓库外**,不入 git |
| `$CLAUDE_CONFIG_DIR` / `$CHATMGR_ARCHIVE_DIR` | 覆盖上面两个根目录(测试时用,见「安全测试」) |

## 常用命令

```bash
cgr <命令>                          # ~/.local/bin/cgr 已软链到本文件
python3 chatmgr.py <命令>           # 或直接跑
cgr                                 # 不带子命令=list;cgr 3=show 3;cgr 关键词=search
python3 -m py_compile chatmgr.py    # 语法检查(本项目没有测试套件)
cgr <命令> -h                        # 看某子命令的全部参数
```

子命令:`list / search / show / pick / export / stats / rm / purge`,各有常用别名(见下)。

**裸参数分派**(`main()` 里做):空 → `list`;首参以 `-` 开头 → `list`;纯数字 → `show`;其他词 → `search`;已知命令/别名 → 原样。

**验证靠手动跑**,没有自动化测试。改完后至少跑一遍 `list`(确认会话数与 `find ~/.claude/projects -maxdepth 2 -name '*.jsonl' | wc -l` 一致)和目标子命令。

## 安全测试(重要)

工具读的是**真实**的 `~/.claude/projects`。测 `rm` / `purge` 这类破坏性命令时,必须用环境变量把路径指向临时目录,不要碰真实数据:

```bash
export CLAUDE_CONFIG_DIR=/tmp/fake/config    # 其下需有 projects/<项目>/<会话>.jsonl
export CHATMGR_ARCHIVE_DIR=/tmp/fake/archive # 归档目录
```

`CLAUDE_CONFIG_DIR` 决定 `~/.claude` 的位置,`CHATMGR_ARCHIVE_DIR` 决定归档目录。

## 架构

单文件,但分三层,**上层依赖下层,解析层不依赖 CLI**(便于将来抽成模块或接 MCP):

1. **解析层** —— `SessionStore` / `Session` / `load_session` / `iter_records`:发现并解析 jsonl。
2. **归档层** —— `backup_session` / `load_manifest` / `save_manifest`:原样复制 jsonl 到归档目录并维护 manifest。
3. **CLI 层** —— `build_parser` / `cmd_*` / `resolve`。

数据流:`SessionStore.sessions()` 扫 `projects_dir()` 下的 `*/*.jsonl`(**只扫顶层,不含 `<会话>/subagents/`**),每个文件经 `load_session()` 归一化成 `Session`,按最后活动时间倒序。`--archived` 时额外扫 `archive_dir()/*/*.jsonl`。

`iter_events()` / `search_units()` 是**另两条独立的读取路径**,按原始顺序流式产出内容,供 `show` / `search` / `export` 使用(不复用 `load_session` 的聚合结果)。

## 数据模型(需要读代码才看得出的坑)

- **项目路径以记录里的 `cwd` 字段为准,且必须 strip —— 它有尾随空格**。不要从文件夹名(`-Users-...`)解码,破折号有歧义。缺 `cwd` 才回退到文件夹名。
- **标题**优先级:最新的 `custom-title` > `ai-title` > `agent-name` > 首句真人输入。
- **真人输入极少**:user 记录里绝大多数是 `tool_result`。判定逻辑见 `_user_text()` —— content 为字符串、非 `isMeta`、且不以 `<` / `system-reminder` / `Caveat:` 开头。
- **噪音**:`attachment` 记录里大量是 `total_tokens_reminder`;assistant 记录里有 `thinking`。搜索默认排除工具返回和 thinking(`--all` 才纳入)。
- **token** 从 assistant 的 `message.usage` 累加五类:input / output / cache_read / cache_creation / thinking。`list` 显示的 `tok = input+output`,**不含 cache**(cache_read 是重复上下文,计进去会撑爆数字)。
- **jsonl 是 Claude Code 内部格式,无契约**,随版本变化。所有解析一律逐行 `try/except`、未知 `type` 忽略(见 `iter_records`)。不要硬编码"必需"字段。

## 不可违反的设计约定

- **破坏性操作默认只预览**:`rm` / `purge` 不加 `--force` 只打印、不动手。这是安全底线,不要加"默认即执行"的开关。
- **删前必须备份**:`rm` 先 `backup_session()` 成功,再删原件;备份失败立即中止、绝不删。**没有 `--no-backup` 逃生口**。
- **归档 = 原样复制 jsonl**(可还原),不是转 markdown。markdown 属于 `export`,是另一条线。
- **不做成本估算**:用户的 Claude Code 走 cline-pass 订阅代理(`ANTHROPIC_BASE_URL` 指向本地),没有"每 token 多少钱"这回事,算了是假数字。只报 token。

## 参数与输出约定

- `resolve()` 支持三种指认,**所有命令通用**:id 前缀 / 列表序号 / 标题片段。序号的基准是**无筛选**的完整列表(实时计算)。有歧义就报错并列出候选。
- 命令别名集中在模块常量 `SUBCOMMANDS`(`list`→`ls`/`l`,`search`→`s`/`grep`,`show`→`o`/`cat`,`pick`→`f`,`export`→`e`,`stats`→`st`/`stat`,`rm`→`d`/`del`,`purge`→`p`);`KNOWN_COMMANDS` 供裸参数分派判断。加别名改这一处即可。
- 全局短选项在所有子命令保持一致:`-p`(project) `-s`(since) `-u`(until) `-l`(limit) `-j`(json) `-a`(archived)。
- `search` **默认忽略大小写**,`-c` 才区分;命中关键词高亮。
- `list` 的序号列高亮(便于直接拿来 `cgr 3`)。
- `pick` 用 `fzf`(缺失时报错退出),选中后**委托自身子命令**(`show` / `show -r` / `rm`)。
- `show` 在交互终端下读完会问一句"要续聊吗",确认即 `claude --resume`;非 TTY / `--json` / `--pager` 时不问。
- 输出:尊重 `NO_COLOR`,非 TTY 不上色;默认不分页(便于管道给 `less`/`grep`),`--pager` 才走 `less`;长行不硬截断,交给终端换行。

## 工作说明

改这个仓库时的固定动作:

1. **改代码** → `python3 -m py_compile chatmgr.py` 语法检查 → 实跑 `list` 和目标子命令(本项目没有自动化测试,验证靠手动跑)。
2. **测破坏性命令**(`rm` / `purge`)只在临时目录做,见「安全测试」一节,不要碰真实数据。
3. **加文件/文档** 时,更新上面「标准文件路径」表;新文档放 `docs/`。
4. **提交**:一笔一个逻辑改动,提交信息写「为什么」不写「改了什么」。
5. **守住边界**:未经用户明确同意**不要提交**,也不要擅自改 `~/.claude/settings.json` 之类的用户环境文件。
6. **保持纯标准库、单文件**;要引入第三方依赖先问。

## 文档约定

项目文档统一放在 `docs/` 目录下,不要散落在仓库根目录:

- `docs/prd.md` —— 需求、功能规格、已确认的产品决策
- `docs/usage.md` —— 命令手册(每个子命令的详细用法)
- `docs/images/` —— 截图、界面参考图、流程图等图片
- `docs/api/` —— API 文档、接口约定、第三方服务对接说明

(`CLAUDE.md` 例外,按 Claude Code 惯例放在仓库根目录。)

## 无关 / 历史

- 仓库内**不含**用户的聊天数据。归档在 `~/claude-archive`(仓库外)。
- `~/.claude/settings.json` 里的 `cleanupPeriodDays` 已设为 3650(防 Claude Code 自动清理记录),这属于用户环境、**不属于本仓库**。
