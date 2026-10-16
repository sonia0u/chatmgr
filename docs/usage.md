# 聊天记录命令手册

`cgr`(=`chatmgr` / `cla` / `cod` / `opc`)—— 管理本机 agent 聊天记录的命令行工具。纯 Python 标准库,无依赖。管三个数据源:Claude Code、Codex、OpenCode。

> **本文档统一用 `cgr` 举例。** `cla`(只看 Claude)、`cod`(只看 Codex)、`opc`(只看 OpenCode)敲法**完全一样**,只是看到的数据不同。凡是行为有差异的地方,各节里都用 **「Codex 差异」/「OpenCode 差异」** 标出来;两个非 Claude 源表现一样时就合并成 **「Codex / OpenCode 差异」**。

## 安装 / 运行

```bash
cgr <命令>                          # ~/.local/bin/cgr 已软链到仓库内 chatmgr.py;cgr = 全部源,分段列出
cla <命令>                          # 只看 Claude Code
cod <命令>                          # 只看 Codex
opc <命令>                          # 只看 OpenCode
python3 /Users/sonia/01_AI/001_code/tools/ChatManager/chatmgr.py <命令>   # 或直接跑
cgr -h                              # 顶层帮助
cgr <命令> -h                        # 某子命令的全部参数
```

`cgr` / `chatmgr` / `cla` / `cod` / `opc` 是同一个文件的五个软链,五个名字都能用 —— **靠命令名决定默认看哪些源**。想加新 agent,以后照这个再建一张软链即可。

## 四个命令:看全部 / 只看某一个

| 命令 | 看到哪些 | 记录在哪 |
|---|---|---|
| `cgr`(=`chatmgr`) | **全部**,各源**分段**列出 | 下面三处都读 |
| `cla` | 只看 Claude Code | `~/.claude/projects/<项目>/<会话id>.jsonl` |
| `cod` | 只看 Codex | `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl` |
| `opc` | 只看 OpenCode | `~/.local/share/opencode/opencode.db`(SQLite) |

- **四个命令的子命令、参数、指认方式完全一样**。本文档里所有的 `cgr xxx` 都适用于 `cla xxx` / `cod xxx` / `opc xxx`。
- 不想换命令名,也可以用 `-S` 收窄:`cgr list -S codex`、`cgr list -S claude,opencode`(可逗号分隔)。
- **序号只在你当前看到的那个列表里有意义**:多源分段后,Claude 段在前;`cgr 3` 是它列表的第 3 条,`opc 3` 是 OpenCode 列表的第 3 条。
- **多源时列表按源分段**(每段一个来源),段内各自按时间倒序;段序由源的选择顺序决定,`-S codex,claude` 就能反过来。只看单源时不分段。

**各源行为不同的地方:**

| | Claude | Codex | OpenCode |
|---|---|---|---|
| 备份 / 归档 | 我们自己的 `~/claude-archive`(`purge` 清它) | **用 Codex 自带的归档**:`rm` 的"删并备份"= `codex archive`,取回用 `codex unarchive <id>`;`purge` 是空操作 | 它**没有归档概念**:`rm` 的"删并备份"= `opencode session export` 存成 JSON 到 `<归档>/_opencode/`(能用 `opencode session import` 装回去);`purge` 不管它 |
| 续聊 | `claude --resume <id>` | `codex resume <id>` | `opencode --session <id>` |
| 改名 | 写回会话 jsonl 的 `custom-title` | 改 Codex 状态库的 `threads.name`(Codex 里也看得见) | 改 `session_v2.title`(**认不认未验**) |
| `prompts` | 有(读 `~/.claude/history.jsonl`) | 没有对应物,会提示一句 | 没有对应物,会提示一句 |
| `dedupe` | 有用(同 id 可能多份) | 无需处理(已按 `session_id` 归组) | 无需处理(一张表一个会话) |
| token 列 | 一直有 | 旧版不记 token 的显示 `—` | 失败 / 没产出的显示 `—` |

Codex 和 OpenCode 的**删除 / 改名 / 续聊都需要各自那个命令**(`codex` / `opencode`)。找不到时**只读功能照常能用**,写操作会明确提示。

## 怎么读这份文档

- **凡是占位符,都要替换成你自己的值**。两种写法:
  - **语法里的尖括号**:`<命令>`(子命令)、`<ref>`(会话,见「[指认会话(ref)](#指认会话ref)」)、`<词>`(搜索关键词)、`<项目>`(项目名)、`<N>`(数字)。
  - **例子里的裸词**:统一只用 **`词`**(搜索关键词)和 **`项目`**(项目名)—— 比如 `cgr 词` 你实际敲 `cgr 想搜的词`。
- **`-x` / `--xxx` 是选项,照敲即可**,不是占位符。
- 代码块里的命令都是**原样敲**(前面没有 `$` 提示符)。

## 命令一览

四个入口见上一节。**子命令**如下(用 `cgr` 举例 —— 换成 `cla` / `cod` / `opc` 完全同理):

| 完整命令 | 简写 | 作用 | Codex? | OpenCode? |
|---|---|---|---|---|
| `cgr list` | `cgr` | 列出会话 | ✓ | ✓ |
| `cgr search <词>` | `cgr <词>` | 搜索内容 | ✓ | ✓ |
| `cgr show <ref>` | `cgr <序号>` | 阅读一个会话 | ✓ | ✓ |
| `cgr pick [词]` | `cgr f` | fzf 交互选择(可加 `词` 初筛) | ✓ | ✓ |
| `cgr export <ref>` | — | 导出 md / txt / json | ✓ | ✓ |
| `cgr stats` | — | 用量统计 | ✓(旧版不记 token 的显示 `—`) | ✓(失败/没产出的显示 `—`) |
| `cgr rm <ref...>` | — | 删除会话(默认预览) | ✓(走它自带的 `archive` / `delete`) | ✓(走 `session delete`;备份是导出成 JSON) |
| `cgr rename <ref> <新标题>` | — | 给会话改名 | ✓(写它的状态库,Codex 里也看得见) | ✓(写 `session_v2.title`;**认不认未验**) |
| `cgr prompts [词]` | — | **你自己的提问流水**(含 jsonl 已删的会话) | ✗ 只有 Claude 有 | ✗ 只有 Claude 有 |
| `cgr purge` | — | 清除备份(默认预览) | ✗ 归档由它自己管 | ✗ 备份是导出文件,自己清 |
| `cgr dedupe` | — | 合并重复会话(同 id 多份时保留最新,其余移到归档) | ✗ 已按 `session_id` 归组,无需处理 | ✗ 一张表一个会话,不会有重复 |

**裸参数分派**:`cgr`(无参)=列表;`cgr 3`(纯数字)=打开第 3 个;`cgr 9852ae3a`(像 id 前缀)=打开该会话;`cgr 词`(其他词)=搜索「词」。`cla` / `cod` / `opc` 同理,只是序号基准换成它们各自看到的列表。

**`-S` 可以顶替命令名**:`cgr list -S claude` ≡ `cla list`。可逗号分隔或重复:`-S claude,codex`。

## 通用规则(所有命令适用)

### 指认会话(ref)

凡是要"选一个会话"的命令(`show` / `export` / `rm` / `rename` …),那个 `<ref>` 有**三种写法,任选一种**:

| 写法 | 长什么样 | 从哪拿 | 例子 |
|---|---|---|---|
| **列表序号** | 一个数字 | `cgr` 列表最左的 `#` 列 | `cgr 3` |
| **id 前缀** | 一串十六进制 | `show` 头部那行 `会话: 9852ae3a-…` 的开头几位(≥6 位) | `cgr 9852ae3a` |
| **标题片段** | 标题里的一段字 | `cgr` 列表的「标题」列 | `cgr show 词` |

**能不能省掉动词?**(即直接 `cgr <ref>`)

- 数字序号 → **能**:`cgr 3` 等于 `cgr show 3`
- id 前缀 → **能**:`cgr 9852ae3a` 会被认成 id(≥6 位十六进制自动走 show)
- 标题片段 → **不能**:`cgr 词` 被当成**搜索**;要打开得写 `cgr show 词` 

**其它规则**

- 序号以**无筛选的完整列表**为基准(按最后活动时间倒序),实时计算。
- 标题是**大小写不敏感的模糊匹配**。
- **有歧义**(多个匹配)会报错并列出候选,让你改用序号或 id;**找不到**也报错。
- 序号会随会话增删而变化;拿不准时用 **id 前缀**(最稳)。

### 全局参数(每个命令都能加)

| 完整 | 简写 | 说明 |
|---|---|---|
| `--project PROJECT` | `-p PROJECT` | 按项目筛选(子串匹配);**可重复或逗号分隔**,多个取并集 |
| `--since WHEN` | `-s WHEN` | 起始时间:YYYY-MM-DD 或 7d / 2w / 3m / 1y |
| `--until WHEN` | `-u WHEN` | 截止时间,同上 |
| `--limit N` | `-l N` | 最多处理 N 个会话 |
| `--json` | `-j` | 机器可读输出(可接 `jq`) |
| `--archived` | `-a` | 把归档也纳入数据源,输出标注 `[已归档]`(Claude 指 `~/claude-archive`,Codex 指它自己的 `archived_sessions/`,OpenCode 指 `time_archived` 非空的会话) |
| `--source SRC` | `-S SRC` | 选数据源:`claude` / `codex` / `opencode`。**可逗号分隔或重复,顺序就是列表里分段的先后**(`-S codex,claude` 让 Codex 排前面)。默认由命令名决定。**大写 S**,别和 `-s`(since)混 |
| `--color` | — | 强制彩色输出(管道给 `less -R`、或放进 fzf 预览时用) |
| `--no-color` | — | 关闭彩色输出 |

### 输出约定

- 自动上色并**尊重 `NO_COLOR`**;非 TTY(管道)不上色。要强制上色用 `--color`(例如管道给 `less -R`),关闭用 `--no-color`
- `list` 序号列高亮;`search` 命中关键词高亮(仅上色时)
- **每个项目(文件夹)固定一个颜色**,同一项目在 `list` / `pick` / `search` / `show` 里颜色一致,一眼区分不同文件夹
- 默认**不分页**,便于 `| less` / `| grep`;要分页加 `--pager`
- 长行不硬截断,交给终端换行;中文按全角宽度对齐

---

## list — 列出会话

**完整语法**

```bash
cgr list [--project PROJECT] [--since WHEN] [--until WHEN] [--limit N] [--json] [--archived]
```

**说明** 列出所有会话,新的在上。输出列:序号(高亮) · 时间 · 项目 · 标题 · 消息(问+答) · 大小 · tok。`tok = input + output`(**不含 cache**,cache 单独看 `stats`)。

> **Codex / OpenCode 差异**:没记 token 的会话(旧版 Codex 根本不写;OpenCode 那边是失败 / 没产出的),tok 列显示 `—`(而不是 0 —— 那是"没记录",不是"没用量")。
>
> **多源时按源分段**(`cgr`,或 `-S claude,codex,opencode`):每个源一段,段标题写明来源和条数,段内各自按时间倒序 —— **不按时间全局穿插**,免得两边的对话混在一起看不清哪句是哪边的:

```
#   时间              项目          标题              消息    大小     tok
── Claude (21) ────────────────────────────
1   2026-10-15 23:32  ChatManager   聊天记录管理 4…   15+541  5.4MB   772.9k
2   2026-10-15 18:33  ChatManager   聊天记录管理3…   3+99    1.1MB   171.8k
…
── Codex (33) ─────────────────────────────
22  2026-10-15 19:54  抢 iphone      参考脚本工具…     7+30    3.8MB   401.7k
23  2026-10-15 18:15  抢 iphone      02               13+64   9.3MB   2.6M
…
── OpenCode (3) ───────────────────────────
55  2026-10-16 20:56  000_AI-Chats  Greeting         2+3     44.9KB  15.5k
56  2026-10-16 17:38  000_AI-Chats  你好             1+0     818B    —
```

**段的先后由源的选择顺序决定** —— 想让 Codex 排前面就 `cgr list -S codex,claude`。序号是**全局连续的页面顺序**(上例里 Claude 占 1–21、Codex 从 22 起),所以 `cgr 22` 直接能打开。只看单源时不分段,输出和以前一样。

> `-j` 的每条记录都带 `source` 字段(`claude` / `codex` / `opencode`),脚本靠它分辨 —— 不然分段信息在 JSON 里就丢了。

同一会话 id 若落在多个项目文件夹(例如在不同目录 resume 过),**只显示最新的一份**,并在末尾提示重复数量。

交互终端下末尾会提示 `提示:要筛选 / 打开 / 改名 / 删 → cgr f` —— `list` 是"看",要操作就用 `pick`。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr list` | `cgr` | 列出所有会话;不带子命令时默认就是它 |
| `cgr list --project 项目` | `cgr -p 项目` | `-p`:只看某个项目;**动词也可省** |
| `cgr list --since 7d` | `cgr -s 7d` | `-s`:只看某时间之后的(`7d`=近 7 天) |
| `cgr list --limit 5` | `cgr -l 5` | `-l`:最多列几个 |
| `cgr list --json` | `cgr -j` | `-j`:输出 JSON(给脚本 / jq 用) |
| `cgr list --archived` | `cgr -a` | `-a`:连归档备份一起列(标 [已归档]) |
| `cgr list --project 项目 --since 7d` | `cgr -p 项目 -s 7d` | 组合 |
| `cgr list --project 项目 --limit 5 --json` | `cgr -p 项目 -l 5 -j` | 最短形式 |

> `list` 的动词可以省(它本来就是默认,连同"首参以 `-` 开头"也走 list);`show`→`cgr <序号>`、`search`→`cgr <词> [选项]`(**词要写在最前**)同理。其余命令(`export` / `stats` / `rm` / `purge` / `dedupe` / `rename`)没有默认,动词不能省。

**例子**

```bash
cgr                        # = cgr list
cgr -p 项目                # 只看项目名含「项目」的
cgr -s 7d -l 5             # 最近 7 天,前 5 个
cgr -a                     # 连备份一起列(带 [已归档])
cgr -j | jq '.[].title'    # 只取标题(JSON 喂给 jq)
```

---

## search — 搜索对话内容

**完整语法**

```bash
cgr search <query> [--case-sensitive] [--regex]
           [--role {user,assistant}] [--all] [--grep]
           [--context N] [--max-hits N] [全局参数]
```

**说明** **搜索范围**:**标题** + 你的输入 + 对方正文(Claude / Codex / OpenCode)+ 工具调用(名字和参数);工具返回和 thinking 属噪音,`--all` 才纳入。**默认忽略大小写**;**命中关键词高亮 —— 标题和内容片段里的词都会标**。

结果分**两组**:

- **【标题命中】** —— 会话标题里含搜索词的
- **【内容命中】** —— 正文 / 工具调用里含搜索词的,每条标明**属于哪个标题**

每组里每个会话都标了 **项目 · 最后活动时间 · N 问 / M 答**(这个会话有多少条消息),并给出 `→ 打开 cgr <序号> · 续聊 cgr <序号> -r`。

命中行前的标签表示**这个词出现在哪类内容**里:`你`(你的输入)/ `Claude`(正文回复)/ `工具`(工具调用);`--all` 时还会出现 `思考` / `工具返回`。

> **Codex / OpenCode 差异**:回复的标签跟着**那个会话自己的源**走 —— `cod` 结果显示 `Codex`、`opc` 结果显示 `OpenCode`,都不是 `Claude`。(Codex 没有"工具返回"这类独立记录,它那边 `--all` 只多出 `思考`;OpenCode 和 Claude 一样有工具返回。)
>
> **混排时**:`cgr search` 的每个会话摘要行前面会带源名(`Claude · 项目 · …` / `Codex · 项目 · …` / `OpenCode · 项目 · …`),一眼看出是哪边的。

`--grep` 平铺时每行前面也带标题(如 `词 › Claude: …`)。`--json` 输出 `{query, title_matches, content_matches}`。

**搜完想直接打开**:结果里每个会话都带 `→ 打开 cgr <序号> · 续聊 cgr <序号> -r`;或加 `--pick` 直接进 fzf(Enter 打开 / Ctrl-R 续聊,只列命中的会话)。

**命中太多看不全**:默认每组每个会话只展开 5 处。截断处会打印**完整可复制的命令**,如
`… 另有 243 处。全看:cgr search 词 --max-hits 0`(你加过的筛选也会带上)。等价的其它办法:
- `--max-hits 0` —— **不限**,全部展开(仍是分组、带标题)
- `--grep` —— 平铺全部命中,每行带标题,适合 `| less -R` 或 `> 结果.txt`
- 配合 `-p 项目名` 缩小范围

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr search 词` | `cgr 词` | 搜「词」;省掉动词(词放最前) |
| `cgr search --case-sensitive 词` | `cgr 词 -c` | `-c`:区分大小写(默认忽略)—— 想只找 `SSH` 不含 `ssh` 时用它 |
| `cgr search --role user 词` | `cgr 词 --role user` | `--role user`:只搜**你**说的话(`--role assistant` 则只搜对方) |
| `cgr search --all 词` | `cgr 词 --all` | `--all`:连**工具返回**和 **thinking** 一起搜(默认这些算噪音,不搜) |
| `cgr search --grep 词` | `cgr 词 --grep` | `--grep`:平铺输出,每行带标题(适合 `\| less -R`) |
| `cgr search --context 30 词` | `cgr 词 --context 30` | `--context N`:命中处前后各留 N 字(默认 60) |
| `cgr search --max-hits 0 词` | `cgr 词 --max-hits 0` | `--max-hits N`:每个会话最多显示几处;`0` = 不限,全展开 |
| `cgr search --project 项目 词` | `cgr 词 -p 项目` | `-p`:只在某个项目里搜 |
| `cgr search --project 项目 --regex 词` | `cgr 词 --regex -p 项目` | `--regex`:把 `词` 当**正则**匹配 |

> **默认忽略大小写**:不动它就是忽略大小写;要**区分大小写**才加 `-c`。
>
> **省掉 `search` 时,`词` 必须写在最前**(`cgr 词 --grep` ✓)。写成 `cgr --grep 词` / `cgr -c 词` 会被当成 **`list` 的参数**而报错 —— 裸参数规则是"首参以 `-` 开头 → `list`"。

**例子**

```bash
cgr 词                          # = cgr search 词
cgr search 词 -c                # 区分大小写(只匹配大小写一样的)
cgr search 词 --regex           # 把词当正则
cgr search 词 --role user       # 只在"我"说过的话里找
cgr search 词 --all --grep      # 连工具返回一起搜,平铺
cgr search 词 -p 项目 -s 3d     # 限定项目 + 近 3 天
```

结果里每个会话都附 `→ 打开 cgr <序号> · 续聊 cgr <序号> -r`,可直接复制。

---

## prompts — 我的提问流水

**完整语法**

```bash
cgr prompts [词] [-c] [全局参数]
```

**说明** 数据源是 `~/.claude/history.jsonl` —— Claude Code 记的**你自己的提问流水**,每条一行。它和会话 jsonl **互相独立**,所以:

- **jsonl 已经被删的会话,它的提问仍留在这里** —— `prompts` 翻得到 `search` 翻不到的东西
- 但**只有你的提问**,没有 Claude 回复和工具调用(那些要用 `search`)

一句话分工:**`search` 搜「会话内容」,`prompts` 只看「我问过什么」**。两者互补。

> **Codex / OpenCode 差异**:这两个都**没有**这种提问流水,`cod prompts` / `opc prompts` 会直接提示一句、不做事。`cgr prompts` 读的仍然是 Claude 的那条流水。
>
> 想要它们的提问?用 `cod list` / `opc list` 看标题、`cod search` / `opc search` 搜内容 —— 只是没有"连已删会话的提问都能翻到"这个能力。

**输出**:表格 `时间 / 项目 / 提问`,按时间**倒序**(新的在前)。**默认忽略大小写**,`-c` 区分。`-j` 输出 `[{text, time, project, session}]`(含 `session` id,可拿去和 `search` / `show` 对照)。

**常用选项**(其余全局参数见上)

| 写法 | 说明 |
|---|---|
| `cgr prompts 词` | 只列提问里含「词」的 |
| `cgr prompts 词 -c` | `-c`:区分大小写(默认忽略) |
| `cgr prompts -p 项目` | 只在某个项目的提问里翻 |
| `cgr prompts -s 7d` | 只看近 7 天的提问 |
| `cgr prompts -l 20` | 最多 20 条 |

**例子**

```bash
cgr prompts                        # 全部提问,新的在前
cgr prompts 部署                    # 问过哪些和「部署」有关的
cgr prompts -p 项目 -s 30d          # 某项目近 30 天的提问
cgr prompts -j | jq -r '.[].text'   # 只把提问正文抽出来
```

---

## show — 阅读一个会话

**完整语法**

```bash
cgr show <ref> [--full] [--pager] [--resume] [--json] [全局参数]
```

**说明** `<ref>` 的三种写法见上面「[指认会话(ref)](#指认会话ref)」。默认渲染:你的输入 + 对方正文 + 工具调用(压成一行摘要),**隐藏 thinking 和工具返回**。**交互终端下,读完会问一句"要续聊这个会话吗?[y/N]"**,确认即调对应 agent 的续聊命令。

> **Codex 差异**:续聊走 `codex resume <id>`(不是 `claude --resume`);回复的标签是 `[Codex]`;旧版 Codex 不记 token 的会话,头部显示 `token: 未记录`。
>
> **OpenCode 差异**:续聊走 `opencode --session <id>`;回复的标签是 `[OpenCode]`;没记 token 的会话头部显示 `token: 未记录`。
>
> **混排时**:`show` 的每段回复都标着它自己的源(`[Claude]` / `[Codex]` / `[OpenCode]`)。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr show 3` | `cgr 3` | 纯数字分派,省掉动词 |
| `cgr show --full 3` | `cgr show -f 3` | `-f`:连思考 / 工具返回一起显示 |
| `cgr show --resume 3` | `cgr 3 -r` | `-r`:直接续聊(`claude --resume` / `codex resume` / `opencode --session`) |
| `cgr show --pager 3` | `cgr 3 --pager` | `--pager`:用 `less` 分页翻看 |
| `cgr show --json 3` | `cgr 3 -j` | `-j`:输出 JSON(给脚本) |
| `cgr show 词 --full --pager` | `cgr 词 -f --pager` | 按标题 + 组合 |

**例子**

```bash
cgr 3                 # 打开第 3 个
cgr 词                # 用标题片段
cgr 3 -f              # 全显(含思考、工具返回)
cgr 3 -r              # 直接接着聊
cgr 3 --pager         # 长会话翻页
```

---

## pick — fzf 交互选择

**完整语法**

```bash
cgr pick [词] [全局参数]        # 最常用就是 cgr f(不给词);给了词就填进搜索框初筛
```

**说明** 用 `fzf` 把所有会话列成一个**可搜索的清单**,选中后直接打开——省掉"先 `list` 看序号,再 `show 3`"两步。右侧实时预览光标所在会话的内容。给个关键词(`cgr f 词`)会把 fzf 的搜索框**预填**成它,直接就是筛好的。`cgr search <词> --pick` 则只把**搜到的会话**送进来挑。

```
  搜索:  [ 可直接打字过滤 ]                  ┌──────────────────────────┐
> 2026-10-12 19:47  ChatManager   聊天记录管理 1 (19+183) │ === 配置yazi ===          │
  2026-10-12 19:46  markdown yazi md 渲染 1    (19+194) │ 项目: ...                 │
  2026-10-12 17:29  000_AI-Chats  配置yazi      (14+475) │ [你] ...                  │
  2026-10-04 22:45  ob笔记        看下这篇文章放哪 (4+58)  │ [Claude] ...              │
                                                          └──────────────────────────┘
                             ↑ 右边实时预览当前光标所在会话
```

左上角**`搜索 >` 框就是模糊过滤**:打字即筛选(匹配日期/项目/标题/消息数那一列),空白则显示全部;`Esc` 或 `Ctrl-U` 清空。上方 `命中/总数` 是匹配计数。所以敲 `词` 就只剩含它的会话。列表行带颜色(每个项目一色),**右侧预览与直接跑 `cgr show` 完全一致(带颜色)**。

**前提**:它是**交互式**的,必须在真终端里跑;在脚本/管道里调用会直接报错退出(不会卡死等 fzf)。

**典型用法**:`cgr f`,打字筛选 → 回车打开 / Ctrl-R 续聊;**按 `Tab` 勾选多个,再 `Ctrl-D` 删除**(会先预览确认)。

> **混排时**:每行标题前面会带源名(`Claude …` / `Codex …` / `OpenCode …`);续聊 / 删除 / 改名都按**那行自己的源**走,不会张冠李戴。

> **Codex / OpenCode 差异**:若本机找不到对应的 `codex` / `opencode` 命令,Ctrl-R(续聊)与 Ctrl-D(删除)会明确报错、不会静默失败;打开和浏览不受影响。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr pick` | `cgr f` | 子命令别名 |
| `cgr pick 词` | `cgr f 词` | 关键词预填进 fzf 搜索框 |
| `cgr pick --project 项目` | `cgr f -p 项目` | 全局筛选可组合 |
| `cgr pick --archived` | `cgr f -a` | 连备份一起挑 |

**按键**

| 按键 | 动作 | 等价于 |
|---|---|---|
| `Enter` | 打开(多选时依次打开) | `cgr <序号>` |
| `Tab` | **多选**(勾选 / 取消勾选) | — |
| `Ctrl-E` | **改名**(就地输入新标题,改完列表自动刷新) | `cgr rename <序号>` |
| `Ctrl-R` | 续聊(多选时取第一个) | `cgr <序号> -r` |
| `Ctrl-D` | 删除(预览;对勾选的) | `cgr rm <序号...>`(dry-run,不会真删) |

---

## export — 导出会话

**完整语法**

```bash
cgr export <ref> [--format {md,txt,json}] [--out FILE] [--full] [--pager] [全局参数]
```

**说明** 把会话导出成文件或打印。`md` 默认;`json` 给机器;`txt` 纯文本。

> **Codex / OpenCode 差异**:只是正文小标题跟着源走(Claude 是 `## Claude`,Codex / OpenCode 则是 `## Codex` / `## OpenCode`),其余一样。JSON 里的 `messages[].kind` 取值不变(`user` / `assistant` / `tool` / `thinking`)。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr export --format md 3` | `cgr export -f md 3` | `-f`:导出格式 md / txt / json |
| `cgr export --out 对话.md 3` | `cgr export 3 -o 对话.md` | `-o`:写到文件(不给则打到终端) |
| `cgr export --full --format md 3` | `cgr export 3 --full -f md` | `--full`:连思考 / 工具返回一起导出 |
| `cgr export --out 对话.md --format json 3` | `cgr export 3 -f json -o 对话.md` | 最短形式 |

**例子**

```bash
cgr export 3                          # markdown 打到终端
cgr export 3 -o 对话.md                # 写成文件
cgr export 词 -f json -o s.json        # 按标题导出 JSON
```

---

## stats — 用量统计

**完整语法**

```bash
cgr stats [--by {day,project,model,tool}] [全局参数]
```

**说明** 不带 `--by` 给总览:会话数、问答数、时间跨度、各 token 类型合计(**为 0 的类型不列出**)、模型与工具分布。`cache_read` 是每轮重复回读的缓存上下文,累加后远大于 `input`/`output`,**不等于新增用量**。**不计算成本**(本项目走订阅代理,无单 token 价格)。

> **Codex 差异**:① **旧版 Codex CLI(0.149 等)完全不写 token**,这类会话记为"未记录"(列表里显示 `—`),不参与合计;`--by day` 里它们那天记录数照算、tok 是 0。② Codex 的 `input_tokens` **本来含缓存**,我们已经减掉了,所以 `input` 的口径和 Claude 一致(都是"不含缓存回读")。③ `--by tool` 列的是 Codex 的动作类型(`CommandExecution` / `FileChange` / …)。④ **OpenCode 的 input 本来就不含缓存**(拿它自己的 `opencode stats` 对过账,相加正好对上),所以不用像 Codex 那样减;失败 / 没产出的会话不参与合计。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr stats --by day` | `cgr stats -b day` | `--by` → `-b`,按日期 |
| `cgr stats --by project` | `cgr stats -b project` | 按项目 |
| `cgr stats --by model` | `cgr stats -b model` | 按模型 |
| `cgr stats --by tool` | `cgr stats -b tool` | 按工具 |
| `cgr stats --by model --since 7d` | `cgr stats -b model -s 7d` | 组合筛选 |

**例子**

```bash
cgr stats                # 总览
cgr stats -b day            # 按天趋势
cgr stats -b model -s 7d    # 最近 7 天按模型
```

---

## rm — 删除会话(默认预览)

**完整语法**

```bash
cgr rm <ref...> [--force] [全局参数]
```

**说明** **默认只预览**(列出将删的:标题、项目、时间、大小、首句),**不会直接删**。

**删什么**:给会话 refs(如 `cgr rm 3 5 8`),或**不给 refs、用 `-p` 按项目批量删**(可多选:`cgr rm -p 项目1 -p 项目2`,或 `cgr rm -p 项目1,项目2`)。两边都不给会报错,不会误删全部。

- **交互终端**:预览后问一句,三选:
  - `y` = **直接删**(不备份)
  - `n` = **删,并备份一份**到 `~/claude-archive`
  - 其他 = 取消
- **脚本 / 管道**(非 TTY):只预览、不删;要删加 `-f`(跳过交互,**直接删、不备份**)。

选 `n` 时:先备份成功 → 再删原件,备份失败立即中止、绝不删。选 `y` 或 `-f` 则**不备份**,删了不可恢复(会明确提示)。

> **Codex 差异**:Codex 的删除/备份**用它自己的机制** —— `n`(删并备份)= `codex archive`(挪进它自己的归档目录,`cod list -a` 能看到 `[已归档]`,取回用 `codex unarchive <id>`),`y` / `-f`(直删)= `codex delete --force`。**不经过 `~/claude-archive`**,所以 `purge` 管不到它,你也不用管。
>
> 那个 `--force` 是因为 **`codex delete` 自带一道确认**,而我们是以子进程调它的、没有终端,不给 `--force` 它就拒绝执行(报 `cannot confirm session deletion without an interactive terminal`)。**所以"确认"只剩我们自己的那道提示** —— 也正因如此,非 TTY 下我们绝不替你执行,只预览。
>
> 另外:**`codex delete` 自己不清 Codex 侧边栏的会话目录**(那是桌面应用自己的库 `codex-dev.db`),所以光删会话的话,侧边栏会一直挂着一个点不开的空壳。**我们已经替你补了这一刀** —— `rm` 直删时会顺手把那条目录记录也清掉。改完**要重启 Codex 应用**才看得见(侧边栏缓存在内存里)。
>
> **OpenCode 差异**:直删 = `opencode session delete <id>`(它**没有**交互确认参数,和 codex 不同);`n`(删并备份)= 先 `opencode session export` 存成 JSON 到 `<归档>/_opencode/` 再删 —— **这份能用 `opencode session import` 装回去**。注意 `purge` 只扫 Claude 那套 `<归档>/<项目>/*.jsonl`,**管不到 `_opencode/`**,要清自己 rm。
>
> 前提是本机能找到 `codex` 命令(路径上或 ChatGPT.app 包里)。找不到时只读功能照常,`rm` 会明确报错、不会删一半。

**Codex 的归档不是"我们存的一份备份",是 Codex 自己的状态**,所以它和 Claude 那套 `~/claude-archive` 互不相干、各管各的。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr rm --force 3` | `cgr rm -f 3` | `-f`:跳过交互,**直接删**(不备份) |
| `cgr rm 3 5 8` | `cgr rm 3 5 8` | 批量(仍会问 y / n) |
| `cgr rm --force 3 5 8` | `cgr rm 3 5 8 -f` | 批量直接删 |
| `cgr rm --project 项目 --force` | `cgr rm -p 项目 -f` | 按项目批量删 |
| `cgr rm --project 项目1 --project 项目2 --force` | `cgr rm -p 项目1 -p 项目2 -f` | 多项目一起删 |

**例子**

```bash
cgr rm 3                 # 预览(然后按提示选 y / n / 其他)
cgr rm 3 5 8             # 批量预览
cgr rm 3 -f              # 直接删(不备份)
```

---

## purge — 清除备份(默认预览)

**完整语法**

```bash
cgr purge [refs...] [--older-than DAYS] [--force] [全局参数]
```

**说明** 操作对象是 `~/claude-archive` 里的备份,**不是活会话**。**默认只预览**;交互终端下问 `[y/N]`,按 `y` 才清(脚本用 `-f`)。**默认不清"孤儿备份"**(原件已删、只剩备份的)——它们恰恰最该留。`dedupe` 挪进 `_dupes/` 的重复副本也不算备份,**`purge` 不碰**(要清自己 `rm -r ~/claude-archive/_dupes`)。

> **Codex / OpenCode 差异**:这里**只清 Claude 的备份**。Codex 的归档由它自己的 `codex archive` / `delete` / `unarchive` 管,所以 `cod purge` 是空操作(只提示一句);OpenCode 的备份是导出的 JSON(在 `<归档>/_opencode/`),`opc purge` 同样不管,要清自己 rm。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr purge --older-than 30` | `cgr purge -n 30` | `-n N`:只清 N 天前的备份 |
| `cgr purge --force` | `cgr purge -f` | `-f`:真的清(不给则只预览) |
| `cgr purge --older-than 30 --force` | `cgr purge -n 30 -f` | 真删 30 天前的 |
| `cgr purge fe92eade --force` | `cgr purge fe92eade -f` | 只清某个会话的备份 |

**例子**

```bash
cgr purge                 # 预览全部
cgr purge -n 30           # 预览 30 天前的
cgr purge -n 30 -f        # 真删
```

---

## dedupe — 合并重复会话

```bash
cgr dedupe [-f] [全局参数]
```

同一个会话 id 可能落在**多个项目文件夹**(在不同目录 `resume` 过,或项目文件夹被复制),两份文件内容会不一致。`list` 默认只显示最新的一份,但多余副本仍在磁盘上。`dedupe` 把多余副本**移入** `~/claude-archive/_dupes/`(**保留,不删**),让每个会话只留一份。

预览会列出每组:保留哪份、移走哪份。默认只预览,交互下问 `[y/N]`,`-f` 直接执行。

> **Codex / OpenCode 差异**:这两个都**无需去重** —— Codex 在发现阶段已按 `session_id` 把多文件归成一个会话,OpenCode 更是一张表一个会话。`cod dedupe` / `opc dedupe` 会提示一句、不做事。`cgr dedupe` 处理的是 Claude 那部分。

```bash
cgr dedupe                # 预览重复组
cgr dedupe -f             # 执行:多余副本移入归档
```

> 两份内容**各有独占记录**时(例如中途被 compaction 重写过),移走的那份里可能有新版没有的内容——不会丢(在 `_dupes/` 里),但要知道它在哪。要取回直接 `cp` 回对应项目文件夹即可。

---

## rename — 给会话改名

```bash
cgr rename <ref> [新标题] [全局参数]     # 不给标题则交互式提问
```

向会话**写回改名**,`list` / `search` 里就显示新标题。只追加 / 只改一个字段,不动已有内容,可反复改。

> **Codex 差异**:Claude 是往会话 jsonl 追加一条 `custom-title` 记录(与 Claude Code 自身改名同格式);**Codex 是改它状态库 `state_<N>.sqlite` 的 `threads.name`** —— 因为实测 Codex 只认那里,写索引文件它认不到。改完 **Codex 桌面版里也看得见新标题**。
>
> 刻意**不改 `updated_at`**:改个名字不该把会话顶到 Codex 侧边栏最前面。
>
> **OpenCode 差异**:它**没有改名命令**,我们是直接 `UPDATE session_v2.title`。**应用认不认还没验证过**(这台机器上 OpenCode 才刚用起来,没有样本可对照)。

不给新标题时会**提示输入新标题** —— `cgr f` 里按 `Ctrl-E` 走的就是这条。

```bash
cgr rename 3 新标题                    # 直接给新标题
cgr rename 9852ae3a "整理笔记"         # 也可以按 id 前缀
cgr rename 3                           # 不给标题 → 交互式输入
```

---

## 常见任务速查

| 命令 | 场景 |
|---|---|
| `cgr` | 看有哪些会话(**三个源分段列出**) |
| `cla` / `cod` / `opc` | 只想看 Claude / Codex / OpenCode(等于 `cgr -S claude` 等) |
| `cgr f` | 交互浏览,选中即打开 |
| `cgr 词` | 找"上次在哪聊过某词" —— 三个 agent 一次搜遍 |
| `cgr prompts 词` | 在**我问过的提问**里找(连 jsonl 已删的会话都能翻到;**只有 Claude 有**) |
| `cgr 3` | 读第 3 个会话(当前列表的第 3 个) |
| `cgr 3 -r` | 读完接着聊(自动用对 `claude` / `codex` / `opencode`) |
| `cgr export 3 -o 对话.md` | 把第 3 个会话导出成文件 `对话.md` |
| `cgr stats -b project` | 各项目用量 |
| `cgr rename 3 新标题` | 给会话改名(`cgr f` 里按 `Ctrl-E` 也行;Codex 的改完它自己也看得见,OpenCode 的**未验**) |
| `cgr rm 3` | 预览删除(再按 y / n) |
| `cgr rm 3 -f` | 直接删(不备份) |

> 序号**只在你当前看到的那个列表里有意义**。`cgr 3`、`cla 3`、`cod 3`、`opc 3` 很可能是四个不同的会话 —— 拿不准就用 id 前缀。
