# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这是什么

聊天记录管理 CLI,纯 Python 标准库、单文件、无依赖、无构建步骤。

| 命令 | 管哪些源 | 数据 |
|---|---|---|
| `cgr`(=`chatmgr`) | **全部**,混排 | Claude + Codex |
| `cla` | 只看 Claude Code | `~/.claude/projects/*/*.jsonl` |
| `cod` | 只看 Codex | `~/.codex/sessions/**/*.jsonl` |

三个名字是**同一个文件的不同软链** —— 靠"自己被叫什么名字"决定默认管哪些源(`SOURCES_BY_PROG`),`-S/--source` 可覆盖(可重复或逗号分隔,如 `-S claude,codex`)。子命令、筛选、指认方式完全一致;序号的基准是**当前命令看到的那个列表**。
以后加新 agent:补一个 `Source` 实现 + 在 `SOURCES_BY_PROG` 加一行 + 建个软链。

## 标准文件路径

**仓库内**

| 路径 | 说明 |
|---|---|
| `chatmgr.py` | 工具本体,可执行,仓库根 |
| `CLAUDE.md` | 本指引(逐条维护,改约定要同步这里) |
| `docs/prd.md` | 需求 / 功能规格 / 已确认的产品决策 |
| `docs/usage.md` | 命令手册(每个子命令的详细用法) |
| `docs/sources.md` | 多数据源:接一个新 agent 要回答的几个问题、代码里要动的几处、以及各源的实测记录 |
| `tests/test_chatmgr.py` | 最小测试(标准库 unittest;数据全在临时目录,不碰真实记录) |

**仓库外(运行时会访问)**

| 路径 | 说明 |
|---|---|
| `~/.local/bin/cgr`(及 `chatmgr`、`cla`、`cod`) | 软链 → 仓库内 `chatmgr.py`,让命令可从任意目录运行;调用名决定默认管哪些源 |
| `~/.claude/projects/<项目>/<会话id>.jsonl` | **Claude 数据源(只读)**;子 agent 在 `<会话id>/subagents/` |
| `~/.claude/history.jsonl` | 用户的 prompt 流水(每条 prompt 一行);**`prompts` 子命令读取**,数据源与会话 jsonl 独立 |
| `~/.claude/settings.json` | 环境配置,含 `cleanupPeriodDays`;**不属于本仓库** |
| `~/claude-archive/<项目>/<会话id>.jsonl` | Claude 的归档/备份,同级有 `manifest.json`;**仓库外**,不入 git |
| `~/claude-archive/_dupes/<会话id>.<来源文件夹>.jsonl` | `dedupe` 移入的重复副本(保留不删) |
| `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl` | **Codex 数据源(只读)**;一个会话可能横跨多个文件 |
| `~/.codex/archived_sessions/*.jsonl` | Codex 自己的归档(`cod list -a` 才纳入) |
| `~/.codex/state_<N>.sqlite` | Codex 的**状态库**(`threads` / `thread_dynamic_tools` / …)。**标题的真身是 `threads.name`** —— `cod rename` 写这里,`codex archive <名字>` 也认这里。取最大版本号的那个文件 |
| `~/.codex/session_index.jsonl` | 标题索引的**镜像**(`id → thread_name`),覆盖比状态库全,**但 Codex 不认它**。我们只在读标题时当兜底、改名时顺带补一条 |
| `codex`(在 `$PATH` 上,如 `~/.local/bin/codex`) | Codex 独立版 CLI;`cod` 的删/改名/续聊要用它。找不到时回退到 app 包里自带的那份 `/Applications/ChatGPT.app/…/codex-cli/bin/codex` |
| `~/.codex/thread_history_1.sqlite` | Codex 的 `thread_items` / `thread_turns` —— **建在 rollout jsonl 之上的增量索引**(存的是 byte offset),不是把 jsonl 搬走。**我们不用它**,但它说明格式在演进 |
| `$CLAUDE_CONFIG_DIR` / `$CHATMGR_ARCHIVE_DIR` / `$CHATMGR_CODEX_DIR` / `$CHATMGR_CODEX_BIN` / `$CODEX_HOME` | 覆盖上面几个根目录与可执行文件。`$CODEX_HOME` 是 **Codex 自己的**变量,我们必须认(否则用户挪走数据后,我们读一处、删另一处);`$CHATMGR_*` 是我们测试用的,优先级更高 |

## 常用命令

```bash
cgr <命令>                          # ~/.local/bin/cgr 已软链到本文件;cgr = 全部源混排
cla <命令>                          # 只看 Claude Code
cod <命令>                          # 只看 Codex
python3 chatmgr.py <命令>           # 或直接跑(argv[0] 是 chatmgr.py,也当"全部")
cgr                                 # 不带子命令=list;cgr 3=show 3;cgr 关键词=search
cgr list -S codex                   # 也可以不换命令名,用 -S 临时收窄(可逗号分隔)
python3 -m py_compile chatmgr.py    # 语法检查
python3 tests/test_chatmgr.py        # 跑测试(标准库 unittest)
cgr <命令> -h                        # 看某子命令的全部参数
```

子命令:`list / search / prompts / show / pick / export / stats / rm / purge / dedupe / rename`(`pick` 有别名 `f`,其余一律敲全名)。

**裸参数分派**(`main()` 里做):空 → `list`;首参以 `-` 开头 → `list`;纯数字 → `show`(当序号);形如会话 id 前缀(≥6 位十六进制,可带 `-`)→ `show`;其他词 → `search`;已知命令/别名 → 原样。**推论**:`search` 的动词只有在"词在前"时才能省(`cgr yazi --grep`),写成 `cgr --grep yazi` 会走 `list` 而报错。

**验证 = 跑测试 + 手动跑关键命令。** `python3 tests/test_chatmgr.py`(标准库 unittest,桩数据全在临时目录)覆盖解析 / `resolve` / `rename` 写回 / `prompts` 过滤 / 以及破坏性命令"非 TTY 不动文件"的安全底线;但它不覆盖观感与交互,**改完仍要实跑一遍 `list` 和目标子命令**。注意:会话数 = jsonl 文件数 **减去重复 id**(同一会话可能在多个项目文件夹各有一份,见下;`list` 会提示"重复会话")。

## 安全测试(重要)

工具读的是**真实**的 `~/.claude/projects` 和 `~/.codex`。测破坏性命令时必须用环境变量把路径指向临时目录,不要碰真实数据:

```bash
export CLAUDE_CONFIG_DIR=/tmp/fake/config    # 其下需有 projects/<项目>/<会话>.jsonl
export CHATMGR_ARCHIVE_DIR=/tmp/fake/archive # Claude 的归档目录
export CHATMGR_CODEX_DIR=/tmp/fake/codex     # 其下需有 sessions/<年>/<月>/<日>/*.jsonl
export CHATMGR_CODEX_BIN=/tmp/fake/codex-stub.sh  # 桩脚本;置空 = 当作"没有 codex"
```

**Codex 这边更要紧**:`cod rename` 是**真的改你的 `~/.codex/state_<N>.sqlite`**(那是个正在被应用使用的活数据库),`cod rm` 是**真的调 `codex delete` / `archive`**。测这两个必须同时设 `CHATMGR_CODEX_DIR`(改到临时目录,夹具里得有 `state_5.sqlite` 和 `threads(id, name)` 表)和 `CHATMGR_CODEX_BIN`(指向桩脚本),**一个都不能漏**。`tests/` 里的 `CodexTest` 就是这套。

**Codex 侧改动的复验手法**(不用翻 UI):`codex archive <名字>` 会按名字找会话 —— 名字认不认,一跑便知。改完名字用它探一下,再 `unarchive` 还原,就完成了闭环。归档/取回本身也是完全可逆的。

`codex_bin()` 的取值顺序:`$CHATMGR_CODEX_BIN` → `$PATH` 上的 `codex` → app 包内路径。
`codex_dir()` 的顺序:`$CHATMGR_CODEX_DIR` → `$CODEX_HOME`(Codex 自己的变量)→ `~/.codex`。

`CLAUDE_CONFIG_DIR` 决定 `~/.claude` 的位置,`CHATMGR_ARCHIVE_DIR` 决定归档目录,`CHATMGR_CODEX_DIR` 决定 `~/.codex` 的位置。

**`cgr` 默认是"全部源",所以任何测试都必须同时把 Codex 目录也指到临时目录** —— 否则 `list` 会去读你真实的 `~/.codex`。`tests/` 里 `BaseTest.setUp` 已经这么做(而且把 `CHATMGR_CODEX_BIN` 置空,免得测试真去调 codex)。

## 架构

单文件,但分层,**上层依赖下层,解析层不依赖 CLI**(便于将来抽成模块或接 MCP):

1. **数据源层** —— `Source` 基类 + `ClaudeSource` / `CodexSource`(注册在 `SOURCES`)。每个源负责:发现文件并归一化成 `Session`、按时间产出 `(kind, text)` 事件流、`daily_usage()`(供 `stats --by day`)、以及写操作(`resume_argv` / `delete` / `set_title`)。**命令层只认 `Session` 和这两个元组流,所以加一个源 = 补一个实现,基本不用动命令。**
2. **解析层** —— Claude:`load_session` / `_claude_events` / `_claude_units`。Codex:`_discover_codex` / `_codex_build` / `_codex_items` / `_codex_merged_records`。
3. **归档层** —— Claude 走 `backup_session` / `load_manifest` / `save_manifest`(原样复制 jsonl + manifest);**Codex 不经过这层**,它调自己自带的 `codex archive` / `delete`。
4. **CLI 层** —— `build_parser` / `cmd_*` / `resolve` / `source_of`。

数据流:`SessionStore.sessions()` 把**每个选中源**的 `discover()` 结果并起来 —— **按源分段**(段序 = 选中源顺序),段内各自按最后活动时间倒序,再按 `(source, sid)` 去重。`cgr` 就是多源分段,`cla`/`cod` 只有一段。Claude 侧扫 `projects_dir()/*/*.jsonl`(**只扫顶层,不含 `<会话>/subagents/`**);Codex 侧扫 `~/.codex/sessions/**/*.jsonl`,**按 `session_id` 把多文件归成一个会话**。

`iter_events()` / `search_units()` 是**另两条独立的读取路径**,按原始顺序流式产出内容,供 `show` / `search` / `export` 使用(不复用 `load_session` 的聚合结果);它们按 `s.source` 分派到对应源。

## 数据模型(需要读代码才看得出的坑)

- **项目路径以记录里的 `cwd` 字段为准,且必须 strip —— 它有尾随空格**。不要从文件夹名(`-Users-...`)解码,破折号有歧义。缺 `cwd` 才回退到文件夹名。
- **标题**优先级:最新的 `custom-title` > `ai-title` > `agent-name` > 首句真人输入。
- **真人输入极少**:user 记录里绝大多数是 `tool_result`。判定逻辑见 `_user_text()` —— content 为字符串、非 `isMeta`、且不以 `<` / `system-reminder` / `Caveat:` 开头。
- **噪音**:`attachment` 记录里大量是 `total_tokens_reminder`;assistant 记录里有 `thinking`。搜索默认排除工具返回和 thinking(`--all` 才纳入)。
- **token** 从 assistant 的 `message.usage` 累加五类:input / output / cache_read / cache_creation / thinking。`list` 显示的 `tok = input+output`,**不含 cache**(cache_read 是重复上下文,计进去会撑爆数字)。`show` 头部和 `stats` 总览**只列非 0 的类型**(代理不报 cache_creation / thinking 时它们恒为 0,列出来像坏了);`stats` 里当 cache_read 大于 input+output 时再附一句说明它是重复回读。
- **标题 / 项目里可能夹带控制字符,渲染前必须剔掉**。实测真有一条 Codex 标题以 `\x08`(退格)开头:`\b` 被终端当指令,会把光标退一格、**吃掉前面那个空格**(`Codex 这是…` → `Codex这是…`),整列跟着歪;`\x1b` 更糟 —— 那是转义序列的开头,等于让聊天数据往终端注入转义。所以 `flat()` 会剔掉非空白控制字符(`\t\n\r` 等照旧压成空格),`dwidth()` 也不再把它们算作占列。
- **jsonl 是 Claude Code 内部格式,无契约**,随版本变化。所有解析一律逐行 `try/except`、未知 `type` 忽略(见 `iter_records`)。不要硬编码"必需"字段。
- **同一会话 id 可能落在多个项目文件夹**(在不同 cwd 下 resume 过,或项目文件夹被复制),两份内容会不一致。`SessionStore._discover()` 按 `(source, id)` 去重:保留最后活动最新、且优先活会话的那一份;被丢弃的份数记在 `store.duplicates`,由 `list` 提示。

### Codex 的坑(都实测过,别凭直觉改)

- **`thread_source` 是过滤子线程的唯一钥匙**:`session_meta.payload.thread_source` 只有 `user`(真人主线程)和 `guardian_review`(Codex 自派的"审批复核"子线程,内容全是机器文本)。**只收 `user`** —— 子线程不用去算 `parent_thread_id` 那棵树。
- **`event_msg` / `item_completed` 是干净的语义骨架**,里面**不含** `app-context` / `AGENTS.md` / `environment_context` 那些注入脚手架(它们不是 item)。所以 **Codex 侧没有任何启发式噪音过滤器**。反例:若去读 `response_item`,role=user 的记录里真提问只占 1/18(6106 条里 342 条),靠文本前缀猜必错。
- **一个会话可能横跨多个文件**(续写/分叉),靠 `session_id` 粘合(`session_meta.payload.session_id`;子线程文件带的也是父会话的 id)。各文件的 `item.id` 不重叠,按 `timestamp` 归并即可。**`ordinal` 每个文件各自从 0 开始,不能跨文件排序。**
- **token 口径和 Claude 是反的**:Codex 的 `input_tokens` **含**缓存(恒定 `cached ⊆ input`、`reasoning ⊆ output`、`total == input+output`)。所以 `input = input_tokens − cached_input_tokens`,否则 `tok` 会虚高。旧版 CLI(0.149 等)**完全不写 token** —— 那种会话 `has_usage=False`,`list` 显示 `—` 而不是 0。
- **标题的真身是 `~/.codex/state_<N>.sqlite` 的 `threads.name`,不是 `session_index.jsonl`。** 实测铁证:拿写进 jsonl 的名字去 `codex archive <名字>` → `No active session found matching`;拿状态库里的名字 → 找到并归档。而 `threads.name` 有 11 行是空的、jsonl 覆盖更全,所以**读的时候两边都读、状态库优先**(实测 43 个会话零冲突,改读法当天标题一个没变)。
  jsonl 仍是**追加**式的(同一 id 可能多行,取最后一行),里面也含 guardian_review 子线程的标题 —— 过滤得靠文件里的 `thread_source`。
- **`codex doctor` 自己也报了 `duplicate thread inventory entries`** —— 就是 jsonl 里那几个重复 id,印证了"取最后一行"。
- **`cwd` 可能是 Windows 路径**(`E:\...`)。`Session.short_project` 走 `basename_any()`,同时认 `/` 和 `\` —— 直接用 `Path().name` 在 macOS 上会返回整串。
- **文件里可能有被截断的坏行**(流式写入的残行)。沿用逐行 try/except。

## 不可违反的设计约定

- **破坏性操作默认只预览**:`rm` / `purge` / `dedupe` 不加 `--force` 只打印预览;**交互终端下再问一句**(`rm` 用 `_ask_delete()` 三选:y=直删、n=删并备份、其他=取消;`purge` / `dedupe` 用 `_confirm()`);**非 TTY 一律只预览、绝不执行**。这是安全底线,不要加"默认即执行"的开关,脚本里显式用 `--force`。
- **删除:备份可选,直删要明示**:`rm` 交互下 `y` = 直接删(不备份)、`n` = 删并备份一份;脚本用 `-f` = 直接删(不备份)。走备份时先 `backup_session()` 成功再删原件,备份失败立即中止、绝不删;直删会明确提示"删了不可恢复"。
- **归档 = 原样复制 jsonl**(可还原),不是转 markdown。markdown 属于 `export`,是另一条线。
- **Codex 的删 / 备份 / 续聊委托给 Codex 自己**,不另造轮子:`cod rm` 的 `n`(备份)= `codex archive`(挪进它自己的 `archived_sessions/`,`cod list -a` 看得到,`codex unarchive` 可取回),`y`/`-f`(直删)= **`codex delete --force`**,`show -r` = `codex resume <id>`。所以 `~/claude-archive` 和 manifest **完全不参与 Codex**。`cod purge` 因此是空操作(只说明一句)。
  **那个 `--force` 是必须的**:`codex delete` 自带交互确认,而我们是以子进程调它、没有 TTY,不加就被拒(`cannot confirm session deletion without an interactive terminal`)。**因此"确认"这一步只剩我们自己的提示 —— 这恰恰是"非 TTY 只预览、绝不执行"这条底线比以前更要紧的原因。** `archive` 不需要 `--force`(安全操作,自己不拦)。
- **改名两边机制不同,但都"写回原处"**:Claude 是往会话 jsonl 追加 `custom-title`;Codex 是 `UPDATE state_<N>.sqlite` 的 `threads.name`(**不是** `session_index.jsonl` —— 写那里 Codex 认不到,实测过;jsonl 只顺带补一条镜像)。**刻意不动 `updated_at` / `recency_at`**:那会把会话顶到 Codex 侧边栏最前面,改个名字不该改变它的时间位置。
- **`purge` / `list -a` 不碰 `_dupes/`**:`dedupe` 挪走的重复副本是"刻意保留"的兜底,不算备份。归档扫描统一走 `archive_files()`(排除 `<归档>/_dupes/`),否则 `purge` 会把刻意留的副本一并清掉。要清副本自己 `rm -r <归档>/_dupes`。
- **不做成本估算**:用户的 Claude Code 走 cline-pass 订阅代理(`ANTHROPIC_BASE_URL` 指向本地),没有"每 token 多少钱"这回事,算了是假数字。只报 token。

## 参数与输出约定

- `resolve()` 支持三种指认,**所有命令通用**:id 前缀 / 列表序号 / 标题片段。序号的基准是**无筛选**的完整列表(实时计算)。有歧义就报错并列出候选。
- 子命令别名只保留 `pick`→`f`(常用),其余一律敲全名 —— 集中在模块常量 `SUBCOMMANDS`(其余命令的别名列表为空),`KNOWN_COMMANDS` 供裸参数分派判断。要增删别名改这一处即可。注意别名删掉后,`cgr ls` 这类词会被当成**搜索**(非命令词都走 search)。
- 全局短选项在所有子命令保持一致:`-p`(project,可重复或逗号分隔,多个取并集) `-s`(since) `-u`(until) `-l`(limit) `-j`(json) `-a`(archived) `-S`(source,可重复或逗号分隔,取值 `claude` / `codex`;用**大写 S** 避开 `-s`)。默认管哪些源由**调用名**决定(`SOURCES_BY_PROG`:`cgr`/`chatmgr`/`chatmgr.py` → 全部,`cla` → claude,`cod` → codex),认不出的名字也回落到"全部"。
- **多源时 `list` 按源分段**,段标题写明来源与条数(段序 = 选中源的顺序,所以 `-S codex,claude` 能把 Codex 放前面),段内各自按时间倒序。**不按时间全局穿插** —— 分段显示 + 全局时间序会让序号跳号(1,3,5… 然后 2,4,6…),看着像坏了。`pick` 的行首、`search` 的 meta 前缀仍逐行标来源(fzf 里没法分段)。这些都只在**当前列出的会话真的横跨多个源**时出现(按内容判断,不按选了哪些源),单源输出与以前逐字节一致。
- `rm` 的 `refs` 可为空:此时按 `-p` 的筛选批量删;`refs` 与 `-p` 都不给则报错 —— 绝不误删全部。
- `search` **默认忽略大小写**,`-c` 才区分;命中关键词高亮。搜索**同时搜标题**(标题不在 `search_units()` 里,得单独对 `s.title` 匹配);结果分**【标题命中】/【内容命中】**两组,内容命中标明所属标题。每个会话默认只展开 `--max-hits`(默认 5)处,`--max-hits 0` 或 `--grep` = 不限。`--grep` 每行前缀标题;`--pick` 把命中的会话送进 `run_picker()`;`--json` 为 `{query, title_matches, content_matches}`;命中来源标签经 `KIND_LABEL` 映射成中文(`user`→你、`assistant`→Claude、`tool`→工具、`thinking`→思考、`tool_result`→工具返回),`--json` 里仍是原始 kind。
- `list` 的序号列高亮(便于直接拿来 `cgr 3`)。
- `pick` 与 `search --pick` 都走 `run_picker(store, sessions, ...)`(启动 fzf 并**委托自身子命令** `show` / `show -r` / `rm`)。`--multi`(`Tab` 多选):`Ctrl-D` 把全部勾选的交给 `rm` 批量预览删,`Enter` 多选时依次 `show`;`Ctrl-E` 绑 `rename`(交互式问标题)再 `reload`(调内部子命令 `_picklines`,复用 `session_lines()`;它不在 `<命令>` 帮助里,由 `main()` 提前拦截)—— 只有能 reload 的路径(`pick`)才给 Ctrl-E。`pick` 可带初筛词 `cgr f yazi`(传 fzf `--query`);`search --pick` 只把命中的会话送进 picker。列表行上色(`--ansi`),预览强制加 `--color` —— 否则 fzf 预览不是 TTY,颜色会被自动关掉,和直接跑 `show` 不一致。
- `show` 在交互终端下读完会问一句"要续聊吗",确认即 `claude --resume`;非 TTY / `--json` / `--pager` 时不问。
- 项目(文件夹)配色:`project_colors()` 按项目名排序分配 `PROJECT_COLORS` 调色板(`38;5;N` 256 色),出现在 `list` / `pick` / `search` / `show` 的项目列;以**完整会话列表**为基准,所以各命令、各筛选下同一项目颜色一致。改配色改这个常量即可。
- 交互输入用 `input()`;模块顶部 `import readline`(try/except)让方向键/行编辑/历史可用 —— Python 脚本默认不加载 readline,不加的话方向键会变成 `^[[D` 之类的转义串。
- `print_table()` 的**列内容宽度固定**(`list` 里项目 22 / 标题 34),但塞不下时会把**列间距从 2 收到 1**(传 `max_width`,最多省 6~7 列),**不缩列内容**。整行超过终端会折行,把"消息"/`tok` 的尾巴(如 `k`)甩到下一行,极易误读成"最右边多出一列数字"——所以宁可收间距也要避免折行。别改成缩标题宽度(试过,标题太短,用户不接受)。
- 输出:尊重 `NO_COLOR`,非 TTY 不上色;`--color` / `--no-color` 强制覆盖(模块级 `_COLOR_MODE`,由 `main()` 设置);默认不分页(便于管道给 `less`/`grep`),`--pager` 才走 `less`;长行不硬截断,交给终端换行。

## 工作说明

改这个仓库时的固定动作:

1. **改代码** → `python3 -m py_compile chatmgr.py` 语法检查 → `python3 tests/test_chatmgr.py` 跑测试 → 实跑 `list` 和目标子命令(测试只管逻辑,观感/交互还得手验)。
2. **测破坏性命令**(`rm` / `purge`)只在临时目录做,见「安全测试」一节,不要碰真实数据;`tests/` 里的安全用例用的就是同一套环境变量。
3. **加文件/文档** 时,更新上面「标准文件路径」表;新文档放 `docs/`。
4. **提交**:一笔一个逻辑改动,提交信息写「为什么」不写「改了什么」。
5. **守住边界**:未经用户明确同意**不要提交**,也不要擅自改 `~/.claude/settings.json` 之类的用户环境文件。
6. **保持纯标准库、单文件**;要引入第三方依赖先问。

## 文档约定

项目文档统一放在 `docs/` 目录下,不要散落在仓库根目录:

- `docs/prd.md` —— 需求、功能规格、已确认的产品决策
- `docs/usage.md` —— 命令手册(每个子命令的详细用法)
- `docs/sources.md` —— 多数据源(Claude 之外)的接入清单:接一个新 agent 要回答的 8 个问题、代码里要动的几处、以及已勘查的源记录

(本项目没有 API 集成、也没有图片,故不设 `docs/api/` 与 `docs/images/`;将来真需要再建。`CLAUDE.md` 例外,按 Claude Code 惯例放在仓库根目录。)

## 无关 / 历史

- 仓库内**不含**用户的聊天数据。归档在 `~/claude-archive`(仓库外)。
- `~/.claude/settings.json` 里的 `cleanupPeriodDays` 已设为 3650(防 Claude Code 自动清理记录),这属于用户环境、**不属于本仓库**。
