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

## 命令一览

| 完整命令 | 简写 | 作用 |
|---|---|---|
| `cgr list` | `cgr` · `cgr ls` · `cgr l` | 列出会话 |
| `cgr search <词>` | `cgr <词>` · `cgr s <词>` · `cgr grep <词>` | 搜索内容 |
| `cgr show <ref>` | `cgr <序号>` · `cgr o <ref>` · `cgr cat <ref>` | 阅读一个会话 |
| `cgr pick` | `cgr f` | fzf 交互选择 |
| `cgr export <ref>` | `cgr e <ref>` | 导出 md / txt / json |
| `cgr stats` | `cgr st` · `cgr stat` | 用量统计 |
| `cgr rm <ref...>` | `cgr d <ref...>` · `cgr del <ref...>` | 删除会话(默认预览) |
| `cgr purge` | `cgr p` | 清除备份(默认预览) |

**裸参数分派**:`cgr`(无参)=列表;`cgr 3`(纯数字)=打开第 3 个;`cgr yazi`(词)=搜索 yazi。

## 通用规则(所有命令适用)

### 指认会话(ref)

| 写法 | 例子 | 说明 |
|---|---|---|
| 列表序号 | `3` | 基准是**无筛选**的完整列表(按最后活动时间倒序),实时计算 |
| id 前缀 | `fe92eade` | 会话 uuid 前几位,最精确 |
| 标题片段 | `配置yazi` | 大小写不敏感模糊匹配 |

有歧义时(多个匹配)报错并列出候选;找不到也报错。

### 全局参数(每个命令都能加)

| 完整 | 简写 | 说明 |
|---|---|---|
| `--project PROJECT` | `-p PROJECT` | 按项目路径或项目名筛选(子串匹配) |
| `--since WHEN` | `-s WHEN` | 起始时间:YYYY-MM-DD 或 7d / 2w / 3m / 1y |
| `--until WHEN` | `-u WHEN` | 截止时间,同上 |
| `--limit N` | `-l N` | 最多处理 N 个会话 |
| `--json` | `-j` | 机器可读输出(可接 `jq`) |
| `--archived` | `-a` | 把备份(`~/claude-archive`)也纳入数据源,输出标注 `[已归档]` |

### 输出约定

- 自动上色并**尊重 `NO_COLOR`**;非 TTY(管道)不上色
- `list` 序号列高亮;`search` 命中关键词高亮(仅上色时)
- 默认**不分页**,便于 `| less` / `| grep`;要分页加 `--pager`
- 长行不硬截断,交给终端换行;中文按全角宽度对齐

---

## list — 列出会话

**完整语法**

```bash
cgr list [--project PROJECT] [--since WHEN] [--until WHEN] [--limit N] [--json] [--archived]
```

**说明** 列出所有会话,新的在上。输出列:序号(高亮) · 时间 · 项目 · 标题 · 消息(问+答) · 大小 · tok。`tok = input + output`(**不含 cache**,cache 单独看 `stats`)。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr list` | `cgr` | 省掉动词(不带子命令默认 list) |
| `cgr list` | `cgr ls` / `cgr l` | 子命令别名 |
| `cgr list --project yazi` | `cgr list -p yazi` | `--project` → `-p` |
| `cgr list --since 7d` | `cgr list -s 7d` | `--since` → `-s` |
| `cgr list --limit 5` | `cgr list -l 5` | `--limit` → `-l` |
| `cgr list --json` | `cgr list -j` | `--json` → `-j` |
| `cgr list --archived` | `cgr list -a` | `--archived` → `-a` |
| `cgr list --project yazi --since 7d` | `cgr -p yazi -s 7d` | 动词也可省 |
| `cgr list --project yazi --limit 5 --json` | `cgr -p yazi -l 5 -j` | 最短形式 |

**例子**

```bash
cgr                    # = cgr list
cgr -p yazi            # 只看项目名含 yazi 的
cgr -s 7d -l 5         # 最近 7 天,前 5 个
cgr -a                 # 连备份一起列(带 [已归档])
cgr -j | jq '.[].title'
```

---

## search — 搜索对话内容

**完整语法**

```bash
cgr search <query> [--ignore-case | --case-sensitive] [--regex]
           [--role {user,assistant}] [--all] [--grep]
           [--context N] [--max-hits N] [全局参数]
```

**说明** **默认范围**:你的输入 + Claude 正文 + 工具调用(名字和参数);工具返回和 thinking 属噪音,`--all` 才纳入。**默认忽略大小写**,**默认按会话分组**,命中关键词高亮。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr search yazi` | `cgr yazi` | 裸词分派,省掉动词 |
| `cgr search yazi` | `cgr s yazi` / `cgr grep yazi` | 子命令别名 |
| `cgr search --ignore-case yazi` | `cgr search yazi` | 忽略大小写本就是默认 |
| `cgr search --case-sensitive SSH` | `cgr search -c SSH` | `--case-sensitive` → `-c` |
| `cgr search --role user 密钥` | `cgr search --role user 密钥` | `--role` 无短选项 |
| `cgr search --all timeout` | `cgr search --all timeout` | `--all` 无短选项 |
| `cgr search --grep yazi` | `cgr search --grep yazi` | `--grep` 无短选项(平铺输出) |
| `cgr search --context 30 yazi` | `cgr search --context 30 yazi` | `--context` 无短选项 |
| `cgr search --project markdown yazi` | `cgr search -p markdown yazi` | 全局选项可短写 |
| `cgr search --project markdown --regex 'a.*b'` | `cgr search -p markdown --regex 'a.*b'` | 组合 |

**例子**

```bash
cgr yazi                        # = cgr search yazi
cgr search -c SSH               # 只匹配大写
cgr search '报错.*timeout' --regex
cgr search 密钥 --role user      # 只在"我"说过的话里找
cgr search timeout --all --grep  # 连工具返回一起搜,平铺
cgr search yazi -p markdown -s 3d
```

结果里每个会话都附 `→ claude --resume <id>`,可直接复制续聊。

---

## show — 阅读一个会话

**完整语法**

```bash
cgr show <ref> [--full] [--pager] [--resume] [--json] [全局参数]
```

**说明** 默认渲染:你的输入 + Claude 正文 + 工具调用(压成一行摘要),**隐藏 thinking 和工具返回**。**交互终端下,读完会问一句"要续聊这个会话吗?[y/N]"**,确认即 `claude --resume`。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr show 3` | `cgr 3` | 纯数字分派,省掉动词 |
| `cgr show 3` | `cgr o 3` / `cgr cat 3` | 子命令别名 |
| `cgr show --full 3` | `cgr show -f 3` | `--full` → `-f`,含思考/工具返回 |
| `cgr show --resume 3` | `cgr 3 -r` | `--resume` → `-r`,直接续聊 |
| `cgr show --pager 3` | `cgr 3 --pager` | `--pager` 无短选项 |
| `cgr show --json 3` | `cgr 3 -j` | 结构化输出 |
| `cgr show 配置yazi --full --pager` | `cgr 配置yazi -f --pager` | 按标题 + 组合 |

**例子**

```bash
cgr 3                 # 打开第 3 个
cgr 配置yazi          # 用标题
cgr 3 -f              # 全显(含思考、工具返回)
cgr 3 -r              # 直接接着聊
cgr 3 --pager         # 长会话翻页
```

---

## pick — fzf 交互选择

**完整语法**

```bash
cgr pick [全局参数]
```

**说明** 用 `fzf` 列出所有会话(日期 · 项目 · 标题 · 消息数),**右侧实时预览会话内容**。在 fzf 里直接打字即可过滤(默认匹配标题和项目)。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr pick` | `cgr f` | 子命令别名 |
| `cgr pick --project yazi` | `cgr f -p yazi` | 全局筛选可组合 |
| `cgr pick --archived` | `cgr f -a` | 连备份一起挑 |

**按键**

| 按键 | 动作 | 等价于 |
|---|---|---|
| `Enter` | 打开 | `cgr <序号>` |
| `Ctrl-R` | 续聊 | `cgr <序号> -r` |
| `Ctrl-D` | 预览删除 | `cgr rm <序号>`(dry-run,不会真删) |

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
| `cgr export 3` | `cgr e 3` | 子命令别名 |
| `cgr export --format md 3` | `cgr export -f md 3` | `--format` → `-f` |
| `cgr export --format json 3` | `cgr e -f json 3` | 组合 |
| `cgr export --out 对话.md 3` | `cgr export 3 -o 对话.md` | `--out` → `-o` |
| `cgr export --full --format md 3` | `cgr export 3 --full -f md` | `--full` 无短选项 |
| `cgr export --out 对话.md --format json 3` | `cgr e 3 -f json -o 对话.md` | 最短形式 |

**例子**

```bash
cgr export 3                          # markdown 打屏
cgr e 3 -o 对话.md                     # 写文件
cgr e 配置yazi -f json -o s.json
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
| `cgr stats` | `cgr st` / `cgr stat` | 子命令别名 |
| `cgr stats --by day` | `cgr stats -b day` | `--by` → `-b`,按日期 |
| `cgr stats --by project` | `cgr st -b project` | 按项目 |
| `cgr stats --by model` | `cgr st -b model` | 按模型 |
| `cgr stats --by tool` | `cgr st -b tool` | 按工具 |
| `cgr stats --by model --since 7d` | `cgr st -b model -s 7d` | 组合筛选 |

**例子**

```bash
cgr stats                # 总览
cgr st -b day            # 按天趋势
cgr st -b model -s 7d    # 最近 7 天按模型
```

---

## rm — 删除会话(默认预览)

**完整语法**

```bash
cgr rm <ref...> [--force] [全局参数]
```

**说明** **默认只预览**(列出将删的:标题、项目、时间、大小、首句),**不加 `--force` 不会真删**。加 `--force` 才执行,且顺序严格:**先备份到 `~/claude-archive` 成功 → 再删原件**;备份失败立即中止、绝不删。**没有 `--no-backup` 逃生口**。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr rm 3` | `cgr d 3` / `cgr del 3` | 子命令别名 |
| `cgr rm --force 3` | `cgr rm -f 3` | `--force` → `-f`,真删 |
| `cgr rm 3 5 8` | `cgr d 3 5 8` | 批量 |
| `cgr rm --force 3 5 8` | `cgr d 3 5 8 -f` | 批量真删 |

**例子**

```bash
cgr d 3                  # 预览
cgr rm 3 5 8             # 批量预览
cgr rm 3 -f              # 确认后真删(自动备份)
```

---

## purge — 清除备份(默认预览)

**完整语法**

```bash
cgr purge [refs...] [--older-than DAYS] [--force] [全局参数]
```

**说明** 操作对象是 `~/claude-archive` 里的备份,**不是活会话**。**默认只预览**。**默认不清"孤儿备份"**(原件已删、只剩备份的)——它们恰恰最该留。

**完整 → 简写**

| 完整写法 | 简写 | 说明 |
|---|---|---|
| `cgr purge` | `cgr p` | 子命令别名,预览全部备份 |
| `cgr purge --older-than 30` | `cgr purge -n 30` | `--older-than` → `-n` |
| `cgr purge --force` | `cgr purge -f` | 真删 |
| `cgr purge --older-than 30 --force` | `cgr p -n 30 -f` | 真删 30 天前的 |
| `cgr purge fe92eade --force` | `cgr p fe92eade -f` | 只清某个会话的备份 |

**例子**

```bash
cgr purge                 # 预览全部
cgr p -n 30               # 预览 30 天前的
cgr p -n 30 -f            # 真删
```

---

## 常见任务速查

| 完整写法 | 简写 | 场景 |
|---|---|---|
| `cgr list` | `cgr` | 看有哪些会话 |
| `cgr pick` | `cgr f` | 交互浏览,选中即打开 |
| `cgr search yazi` | `cgr yazi` | 找"上次在哪聊过 yazi" |
| `cgr show 3` | `cgr 3` | 读第 3 个会话 |
| `cgr show --resume 3` | `cgr 3 -r` | 读完接着聊 |
| `cgr export 3 --out 对话.md` | `cgr e 3 -o 对话.md` | 导出存档 |
| `cgr stats --by project` | `cgr st -b project` | 各项目用量 |
| `cgr rm 3` | `cgr d 3` | 预览删除 |
| `cgr rm --force 3` | `cgr d 3 -f` | 确认删除(自动备份) |
