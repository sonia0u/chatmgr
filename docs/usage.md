# cgr 命令手册

`cgr`(=`chatmgr`)—— 管理 `~/.claude/projects/*/*.jsonl` 聊天记录的命令行工具。纯 Python 标准库,无依赖。

## 安装 / 运行

```bash
cgr <命令>                          # ~/.local/bin/cgr 已软链到仓库内 chatmgr.py
python3 /Users/sonia/01_AI/001_code/tools/ChatManager/chatmgr.py <命令>   # 或直接跑
cgr -h                              # 顶层帮助
cgr <命令> -h                        # 某子命令的全部参数
```

`cgr` 和 `chatmgr` 是同一个文件,两个名字都能用。

## 怎么读这份文档

- **凡是占位符,都要替换成你自己的值**。两种写法:
  - **语法里的尖括号**:`<命令>`(子命令)、`<ref>`(会话,见「[指认会话(ref)](#指认会话ref)」)、`<词>`(搜索关键词)、`<项目>`(项目名)、`<N>`(数字)。
  - **例子里的裸词**:统一只用 **`词`**(搜索关键词)和 **`项目`**(项目名)—— 比如 `cgr 词` 你实际敲 `cgr 想搜的词`。
- **`-x` / `--xxx` 是选项,照敲即可**,不是占位符。
- 代码块里的命令都是**原样敲**(前面没有 `$` 提示符)。

## 命令一览

| 完整命令 | 简写 | 作用 |
|---|---|---|
| `cgr list` | `cgr` | 列出会话 |
| `cgr search <词>` | `cgr <词>` | 搜索内容 |
| `cgr show <ref>` | `cgr <序号>` | 阅读一个会话 |
| `cgr pick [词]` | `cgr f` | fzf 交互选择(可加 `词` 初筛) |
| `cgr export <ref>` | — | 导出 md / txt / json |
| `cgr stats` | — | 用量统计 |
| `cgr rm <ref...>` | — | 删除会话(默认预览) |
| `cgr purge` | — | 清除备份(默认预览) |
| `cgr dedupe` | — | 合并重复会话(同 id 多份时保留最新,其余移到归档) |
| `cgr rename <ref> <新标题>` | — | 给会话改名 |

**裸参数分派**:`cgr`(无参)=列表;`cgr 3`(纯数字)=打开第 3 个;`cgr 9852ae3a`(像 id 前缀)=打开该会话;`cgr 词`(其他词)=搜索「词」。

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
| `--archived` | `-a` | 把备份(`~/claude-archive`)也纳入数据源,输出标注 `[已归档]` |
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
cgr search <query> [--ignore-case | --case-sensitive] [--regex]
           [--role {user,assistant}] [--all] [--grep]
           [--context N] [--max-hits N] [全局参数]
```

**说明** **搜索范围**:**标题** + 你的输入 + Claude 正文 + 工具调用(名字和参数);工具返回和 thinking 属噪音,`--all` 才纳入。**默认忽略大小写**;**命中关键词高亮 —— 标题和内容片段里的词都会标**。

结果分**两组**:

- **【标题命中】** —— 会话标题里含搜索词的
- **【内容命中】** —— 正文 / 工具调用里含搜索词的,每条标明**属于哪个标题**

每组里每个会话都标了 **项目 · 最后活动时间 · N 问 / M 答**(这个会话有多少条消息),并给出 `→ 打开 cgr <序号> · 续聊 cgr <序号> -r`。

命中行前的标签表示**这个词出现在哪类内容**里:`你`(你的输入)/ `Claude`(正文回复)/ `工具`(工具调用);`--all` 时还会出现 `思考` / `工具返回`。

`--grep` 平铺时每行前面也带标题(如 `词 › assistant: …`)。`--json` 输出 `{query, title_matches, content_matches}`。

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
| `cgr search --role user 词` | `cgr 词 --role user` | `--role user`:只搜**你**说的话(`--role assistant` 则只搜 Claude) |
| `cgr search --all 词` | `cgr 词 --all` | `--all`:连**工具返回**和 **thinking** 一起搜(默认这些算噪音,不搜) |
| `cgr search --grep 词` | `cgr 词 --grep` | `--grep`:平铺输出,每行带标题(适合 `\| less -R`) |
| `cgr search --context 30 词` | `cgr 词 --context 30` | `--context N`:命中处前后各留 N 字(默认 60) |
| `cgr search --max-hits 0 词` | `cgr 词 --max-hits 0` | `--max-hits N`:每个会话最多显示几处;`0` = 不限,全展开 |
| `cgr search --project 项目 词` | `cgr 词 -p 项目` | `-p`:只在某个项目里搜 |
| `cgr search --project 项目 --regex 词` | `cgr 词 --regex -p 项目` | `--regex`:把 `词` 当**正则**匹配 |

> **默认忽略大小写**:`--ignore-case`(`-i`)本来就是默认,加不加一个样;要**区分大小写**才加 `-c`。
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

结果里每个会话都附 `→ claude --resume <id>`,可直接复制续聊。

---

## show — 阅读一个会话

**完整语法**

```bash
cgr show <ref> [--full] [--pager] [--resume] [--json] [全局参数]
```

**说明** `<ref>` 的三种写法见上面「[指认会话(ref)](#指认会话ref)」。默认渲染:你的输入 + Claude 正文 + 工具调用(压成一行摘要),**隐藏 thinking 和工具返回**。**交互终端下,读完会问一句"要续聊这个会话吗?[y/N]"**,确认即 `claude --resume`。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr show 3` | `cgr 3` | 纯数字分派,省掉动词 |
| `cgr show --full 3` | `cgr show -f 3` | `-f`:连思考 / 工具返回一起显示 |
| `cgr show --resume 3` | `cgr 3 -r` | `-r`:直接 `claude --resume` 接着聊 |
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

**说明** 不带 `--by` 给总览:会话数、问答数、时间跨度、五类 token 合计(input / output / cache_read / cache_creation / thinking)、模型与工具分布。**不计算成本**(本项目走订阅代理,无单 token 价格)。

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

**说明** 操作对象是 `~/claude-archive` 里的备份,**不是活会话**。**默认只预览**;交互终端下问 `[y/N]`,按 `y` 才清(脚本用 `-f`)。**默认不清"孤儿备份"**(原件已删、只剩备份的)——它们恰恰最该留。

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

向会话的 jsonl **追加一条 `custom-title` 记录**(与 Claude Code 自身改名同格式),`list` / `search` 里就显示新标题。只追加一行、不改动已有内容,可反复改。

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
| `cgr` | 看有哪些会话 |
| `cgr f` | 交互浏览,选中即打开 |
| `cgr 词` | 找"上次在哪聊过某词" |
| `cgr 3` | 读第 3 个会话 |
| `cgr 3 -r` | 读完接着聊 |
| `cgr export 3 -o 对话.md` | 把第 3 个会话导出成文件 `对话.md` |
| `cgr stats -b project` | 各项目用量 |
| `cgr rename 3 新标题` | 给会话改名(`cgr f` 里按 `Ctrl-E` 也行) |
| `cgr rm 3` | 预览删除(再按 y / n) |
| `cgr rm 3 -f` | 直接删(不备份) |
