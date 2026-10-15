# 产品需求文档(PRD)

> 本文件存放:项目需求、功能规格、已确认的产品决策。

## 背景 / 目标

Claude Code 把每次对话完整存成 `~/.claude/projects/<项目>/<会话id>.jsonl`,Codex 存成 `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl`。这些记录**留着但用不起来**——搜不到、读不了、统计不了。此前只有一个 `list-sessions.sh` 能列出会话(已被 `chatmgr list` 取代并删除)。

**目标**:一个命令行工具,把聊天记录"用起来"——搜索、阅读、统计、安全删除。**多个 agent 的记录用同一套命令管**,不换工具。

**为什么自造**:GitHub 同类工具很多(ccpeek、claude-code-history 等),但没有一个是 Python CLI 且贴合本项目的数据结构(中文 `custom-title` 标题、自定义模型名、按项目分目录)。

## 目标用户与场景

单用户(本机 macOS)、个人历史记录。典型场景:

- "我上次在哪聊过 X" → 跨所有项目搜内容
- "那次对话到底怎么解决的" → 读某个会话的完整内容
- "我用了多少量、都用了哪些模型" → 统计
- "这条记录没用了" → 看完内容再删

## 功能规格

### 子命令

下表对**两个数据源都适用**(命令名换成 `cod` 即管 Codex;个别只在单边有意义的地方见 `docs/usage.md` 的「两个数据源」)。

| 命令 | 作用 |
|---|---|
| `list` | 列出会话:序号 / 时间 / 项目 / 标题 / 消息数 / 大小 / token。不带子命令时默认走它 |
| `search <query>` | 跨会话搜索内容,默认按会话分组 |
| `prompts [query]` | 列出/搜索你自己的提问流水(`~/.claude/history.jsonl`),含 jsonl 已删的会话 |
| `show <ref>` | 阅读一个会话,可 `--resume` 直接续聊 |
| `pick` | fzf 交互选择:Enter 打开 / Ctrl-R 续聊 / Ctrl-D 预览删除 |
| `export <ref>` | 导出 md / txt / json |
| `stats` | 用量统计,`--by day\|project\|model\|tool` |
| `rm <ref...>` | 删除会话(默认预览;交互 y=直删 / n=删并备份) |
| `purge` | 清除备份(默认 dry-run) |
| `dedupe` | 合并重复会话:同 id 多份时保留最新,其余移到归档(默认 dry-run) |
| `rename <ref> <新标题>` | 给会话改名(向 jsonl 追加 custom-title 记录) |

### 全局参数(所有子命令一致)

`-p/--project`、`-s/--since`、`-u/--until`、`-l/--limit`、`-j/--json`、`-a/--archived`、`-S/--source`。
时间支持 `YYYY-MM-DD` 与 `7d`/`2w`/`3m` 相对写法。

### 指认会话

三种写法全支持,统一入口:会话 id 前缀(`fe92eade`)、列表序号(`3`)、标题片段(`配置yazi`)。有歧义时报错并列出候选。

## 已确认的产品决策

- **核心是"用起来",不是"防丢"。** 防自动清理只需在 `~/.claude/settings.json` 设 `cleanupPeriodDays`(已设 3650,约 10 年),不需要为它写工具。工具的价值在搜索 / 阅读 / 统计。
- **删除 = "看到内容再删"。** `rm` 默认只预览;交互下 `y` = 直接删(不备份,会提示不可恢复)、`n` = 删并备份一份;非交互用 `-f`(直接删、不备份)。走备份时先备份成功再删原件,失败即中止。
- **归档 = 原样复制 jsonl**(忠实、可还原),不是转 markdown。存到 `~/claude-archive/<项目>/<会话id>.jsonl`,配一个 `manifest.json`(日期存 manifest、不进路径)。
- **`purge` 独立清备份**,默认 dry-run,支持 `--older-than`;默认不清"孤儿备份"(原件已删只剩备份的,恰恰最该留);也不碰 `_dupes/` 里的重复副本。
- **不做成本估算。** 用户走 cline-pass 订阅代理(`ANTHROPIC_BASE_URL` 指向本地),没有"每 token 多少钱",算了是假数字。只报 token。
- **搜索默认范围**:user 输入 + assistant 正文 + 工具调用(名字/参数);工具返回和 thinking 属噪音,`--all` 才纳入。
- **搜索默认忽略大小写**,`-c` 才区分;`--grep` 平铺输出(默认按会话分组)。
- **`prompts` 读提问流水,与 `search` 分工**:数据源是 `~/.claude/history.jsonl`(每条 prompt 一行),独立于会话 jsonl —— 所以**jsonl 已删的会话,其提问仍可搜到**(实测有 34 段已删会话仅存于流水)。search 搜「会话内容」(含回复 / 工具),prompts 只看「我问过什么」;因流水无回复,故只做列表 / 搜索,不做 `show`。
- **即时扫描,不建索引。** 当前规模(31 会话 / 约 17MB)全量解析毫秒级;解析层解耦,将来上千会话时只换这一层即可上索引。
- **`show` 默认隐藏 thinking 和工具返回**,`--full` 全显;自动上色并尊重 `NO_COLOR`;默认不分页(便于管道),`--pager` 才走 less;长行不硬截断。
- **备份默认不参与搜索**,`--archived` 时才纳入并标注 `[已归档]`。
- **`--resume` 闭环**:找到旧对话后能直接 `claude --resume` 接着聊。
- **v1 范围**:`list / search / show / pick / export / stats / rm / purge`,后续加入 `dedupe` / `rename` / `prompts`,共用同一解析层。
- **命令行要好敲(合用顺手)**:命令名 `cgr`;裸参数分派(`cgr 3` = 打开、`cgr 关键词` = 搜索、不带参数 = 列表);别名只留 `pick`→`f`(其余命令敲全名);`pick` 用 fzf 一步挑;`list` 序号高亮、`search` 命中高亮;`show` 读完可一键续聊。

- **多数据源 = 一个工具多个马甲**(2026-10-15 实现):同一个 `chatmgr.py`,靠"自己被叫什么名字"决定默认源 —— `cgr` / `chatmgr` 管 Claude,`cod` 管 Codex;`-S/--source` 可覆盖。**两个命令的子命令、参数、指认方式完全一致**,只是数据源不同。不换工具、不学新语法。
- **Codex 的删除 / 备份 / 续聊委托给 Codex 自己**,不另造轮子:它的 CLI(藏在 ChatGPT.app 包里)自带 `archive` / `delete` / `resume`。所以 `cod rm` 的"删并备份"= `codex archive`(挪进它自己的归档,`cod list -a` 看得到,`codex unarchive` 可取回),`~/claude-archive` 那一套**完全不参与 Codex**。
- **Codex 侧不写启发式噪音过滤器**:它的 `item_completed` 记录本身就是干净的语义骨架(提问 / 回复 / 执行的命令 / 改了哪些文件),注入的脚手架不在里面。相比靠文本前缀猜 `response_item`,这是数量级上的差别(6106 条里真提问只有 342 条)。
- **续聊两边都支持,但命令不同**:`claude --resume <id>` / `codex resume <id>`,由数据源自己提供。

## 非目标(Non-goals)

- 不做成本 / 计费估算(见上)。
- 不做 Web UI / TUI(第一版只做 CLI)。
- 不做批量标签 / 重命名(schema 无契约,写回风险高,暂不做)。

## 待定 / v2

- **MCP**:让 Claude 自己能查询本机历史。解析层已解耦,v2 只需薄薄一层调用。v1 不做。
- 索引层(SQLite FTS5),仅当会话量增长到即时扫描变慢时再上。
- **跨源入口**:让 `cgr` 从"Claude 专用"变成"全部源"(再加一个 `cla` 管单个 Claude)。会改掉最常用命令的观感,所以单独一轮做、单独验。**多数据源本身已实现**(见「已确认的产品决策」)。
- **OpenCode 等更多源**:会话正文存哪**完全未知**,得实际用过再勘查。接入清单见 `docs/sources.md`。
- **Codex 的 `prompts`**:索引里有已删会话的标题,但没有完整提问流水,形态和 Claude 的 `history.jsonl` 不同,要单独设计。
