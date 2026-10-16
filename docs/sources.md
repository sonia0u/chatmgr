# 多数据源

> 本文件存放:**`cgr` 怎么支持 Claude Code 之外的 AI Agent 的聊天记录**。
> 现状:已实现 `claude` 与 `codex` 两个源;下面第三节是"再接一个新源要回答什么"的清单。
> 产品范围与决策记在 `prd.md`。

## 一、先说清楚:对 CLI 解耦 ≠ 对数据源解耦

工具分四层,命令层只认 `Session` 和 `(kind, text)` 事件流,所以**加一个源 = 补一个 `Source` 实现**:

```python
class Source:
    key / label
    def root()                  # 数据根目录(判断"这个源有没有用过")
    def missing_hint()          # 找不到时的提示语
    def discover(include_archived) -> list[Session]
    def events(session, full)   -> Iterator[(kind, text)]
    def units(session, include_all, role) -> Iterator[(kind, text)]
    def daily_usage(session)    -> Iterator[(date, tok_in, tok_out)]
    def resume_argv(session)    -> list[str] | None   # None = 本机做不了
    def delete(session, backup) -> str                # 失败抛异常
    def set_title(session, title)
```

命令层不动,`cmd_*` 里对源一无所知。**但"读懂新格式"这件事谁也替不了** —— 下一节就是为此准备的。

## 二、命令形态

同一个 `chatmgr.py`,不同软链 → 默认管哪些源不同(busybox 那招,`SOURCES_BY_PROG`):

```
cgr list              # 全部,按源分段(Claude / Codex / OpenCode;也接受 chatmgr)
cla list              # 只看 Claude
cod list              # 只看 Codex
opc list              # 只看 OpenCode
cgr list -S claude    # 不换命令名,用 -S 收窄(可逗号分隔 / 重复)
```

**必须处理的坑:`run_picker` 是"自己调自己"**(`sys.executable` + `chatmgr.py` 绝对路径)。那条路径下 `argv[0]` 是 `chatmgr.py` 而不是 `cgr`,所以:

1. `chatmgr` / `chatmgr.py` 明确列进 `SOURCES_BY_PROG`(归到"全部"一族)
2. 自调用时**把当时选中的源逐个用 `-S` 传下去**(预览、`show`、`rm`、`rename`、`_picklines` 的 reload 全都要带),否则 picker 里打开/删除会跳源、张冠李戴

## 三、接入一个新源必须回答的问题

| # | 问题 | Claude Code | Codex | OpenCode |
|---|---|---|---|---|
| 1 | 数据在哪 | `~/.claude/projects/<项目>/<id>.jsonl` | `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl` | `<data>/opencode.db`(SQLite,一个库装所有会话) |
| 2 | 一次对话怎么认出是"一个"会话 | 一个文件 = 一个会话 | **一个 `session_id`,可能横跨多个文件**(靠 `session_meta.payload.session_id` 粘合) | `session_v2` 一行 |
| 3 | 哪些是**我的提问** | `type=user` 且过 `_user_text()` | `item_completed` 里 `item.type=="UserMessage"` 的 item | `session_message.type='user'` 的 `data.text` |
| 4 | 回复 / 工具调用 / 噪音 | `assistant` 的 `text`/`tool_use`/`thinking` | `AgentMessage` / `CommandExecution`…/ `Reasoning` | `content[].type` = `text` / `tool` / `reasoning` |
| 5 | **标题**从哪来 | 会话内追加的 `custom-title` 记录 | `state_<N>.sqlite` 的 `threads.name`(**真身**);`session_index.jsonl` 是覆盖更全的镜像,当兜底 | `session_v2.title`(**可能为空** → 回退首句) |
| 6 | **token** 从哪来 | `message.usage.*`(input **不含** cache) | 独立的 `token_usage_record` 行(input_tokens **含** cache,要减掉) | `session_message.data.tokens`(input **不含** cache,直接用) |
| 7 | **项目**(cwd)从哪来 | 记录的 `cwd`(带尾随空格,要 strip) | `session_meta.payload.cwd`(可能是 Windows 路径) | `session_v2.directory` |
| 8 | **时间戳** | 记录里的 `timestamp` | 记录顶层的 `timestamp`(全部记录都有) | `time_created` / `time_updated`(毫秒 epoch) |
| 9 | 该排除什么 | `subagents/` 是**另一个目录**,天然不扫 | 靠 `thread_source == "user"` 过滤掉 `guardian_review` 子线程 | **暂不过滤**(`parent_id` 含义未验,没有证据就别藏会话) |
| 10 | 归档 / 删除 / 续聊 / 改名 | 我们自己的 `~/claude-archive`;`claude --resume`;追加 `custom-title` | **它自带**:`codex archive` / `delete` / `resume`;改名要写它的状态库 | CLI:`session delete` / `export`;`opencode --session`;改名写 `session_v2.title` |

## 四、多源之后要重新定义的产品语义

- **序号的基准**:`cgr 3` 指**当前命令看到的那个列表**的第 3 个 —— `cgr` 多源分段后的页面顺序,`cla`/`cod` 是单源列表。`SessionStore` 可以装多个源:各源的 `discover()` 结果**按源分段接起来**(段序 = 选中源顺序,所以 `-S codex,claude` 能反过来),段内各自按时间倒序,最后按 `(source, sid)` 去重。命令层对源一无所知,靠每个 `Session` 自己带的 `source` 分派。
- **多源时 `list` 分段、其余按行标来源**:`list` 按源分段并印段标题(`pick` 的行首、`search` 的 meta 前缀没法分段,所以逐行标源名)。都只在**列出来的会话真的横跨多源**时出现(按内容判断)。单源输出因此与从前逐字节一致。
- **`show --resume`**:按源查表,`resume_argv()` 返回 `None` 就提示而不崩(Codex 命令可能找不到)。
- **`rm` / 归档**:Claude 走 `backup_session` + manifest;Codex 委托 `codex archive`/`delete`。**两套并存,互不干扰**;`cod purge` 是空操作。
- **`rename`**:Claude 追加 `custom-title`;Codex 追加 `thread_name` 到索引。
- **`prompts`**:只有 Claude 有提问流水(`~/.claude/history.jsonl`)。`cod prompts` 给一句明确说明。
- **`list` 的"大小"列**:Codex 单会话常见几 MB ~ 30MB(Claude 约 1MB),这一列的意义不同,但仍有参考价值。

## 五、各源实测记录

> 测量时间:2026-10-15,本机。

### Claude Code

- 位置:`~/.claude/projects/<项目>/<会话id>.jsonl`;子 agent 在 `<会话id>/subagents/`(**不扫**)
- 提问流水:`~/.claude/history.jsonl`(`prompts` 子命令)
- 规模:32 个文件 / 34MB

### Codex(已实现)

- 位置:`~/.codex/sessions/<年>/<月>/<日>/rollout-<时间戳>-<uuid>.jsonl`;归档在 `~/.codex/archived_sessions/`(平铺)
- 规模:68 个文件 → **43 个会话**(不带 `-a` 显示 33 个,另有 10 个只在归档里);166MB;**全量逐行解析约 0.5 秒,不需要索引**
- 版本:`0.162.0-alpha.17.2`;**从 0.149 到 0.162 跨了好几个版本,格式在变**
- 记录是两层 `type` + `payload`,典型分布(一次 1243 行的会话):
  `response_item 566 / event_msg 535 / token_usage_record 130 / turn_context 9 / world_state 2 / session_meta 1`

**关键设计依据**(都实测过):

1. `session_meta.payload.thread_source` 只有两个取值 —— `user`(51 个文件)和 `guardian_review`(17 个,全带 `parent_thread_id`)。后者是 Codex 自派的"审批复核"子线程,里面的 UserMessage 是机器文本(`"The following is the Codex agent history added since your last approval assessment…"`)。**一条规则过滤器,不用算那棵树。**
2. `event_msg` 的 `item_completed` 是**干净的语义骨架**,`item.type` 取值:`UserMessage` / `AgentMessage` / `CommandExecution` / `FileChange` / `McpToolCall` / `WebSearch` / `Extension` / `ImageView` / `Reasoning` / `ContextCompaction`。**里面不含任何注入脚手架**(那些不是 item),所以**完全不需要启发式噪音过滤**。
   反例:走 `response_item` 的话,`role=user` 有 6106 条,真提问只有 342 条,靠文本前缀猜有 4390 个假阳性。
3. 多文件会话里 **UserMessage 的 `item.id` 互不重复**(15 个多文件会话零重叠)→ 按 `timestamp` 归并即可,不用去重。`ordinal` 是**每文件各自从 0 开始**,不能跨文件排序。
4. token 字段恒定满足 `cached_input_tokens ⊆ input_tokens`、`reasoning_output_tokens ⊆ output_tokens`、`total_tokens == input+output`(实测 3171 条)。**Codex 的 `input_tokens` 含缓存**,而 Claude 的不含 → 所以 `input = input_tokens − cached_input_tokens`。有实测会话 input 到 18 万,其中 18.3 万是缓存回读。
5. `~/.codex/session_index.jsonl` 是**追加**式的:实测 68 行只有 63 个 id(5 个 id 多行);`updated_at` 非单调;mtime 与会话文件同步;还留着 **20 条"文件已删但索引还在"** 的条目 → 它**不是**从会话文件重新生成的。**读标题时同一 id 取最后一行。** 里面也含 guardian_review 子线程的标题。
   **但它不是标题的真身** —— 见下面第 9 条。
6. **Codex CLI**:先看 `$PATH` 上的 `codex`(2026-10-15 装的是独立版 `0.162.1`,在 `~/.local/bin/codex` → `~/.codex/packages/standalone/current/bin/codex`);没有才回退到 app 包里自带的那份 `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`(`codex-package.json` 里的官方 entrypoint,一个 shim)。子命令:`resume` / `archive` / `unarchive` / `delete` / `fork` / `agents` / `doctor` / `migrate-rollouts` …
   - **`codex resume <SESSION_ID>` 支持 UUID** —— 与 `claude --resume` 对称,所以"续聊"是能做的
   - **`codex archive` / `delete` 存在** —— 所以 Codex 的删除/备份用它自己的,我们不再造 `~/claude-archive` 那一套
   - **`$CODEX_HOME` 是它的数据根目录变量** —— 我们的 `codex_dir()` 必须认(否则用户挪走数据后,`cod list` 读一处、`cod rm` 删另一处)
7. **有坏行**:至少一个文件含被截断的 JSON(流式写入的残行)。逐行 try/except 是必需的。
8. **格式确实在演进,但 jsonl 仍是源头**(2026-10-15 复查)。`codex migrate-rollouts` 说的"paginated thread history"落地成了 `~/.codex/thread_history_1.sqlite`(实测 47MB:`thread_turns` 462 行、`thread_items` 9055 行)。但它的关键字段是 **`thread_history_projection_state.next_rollout_byte_offset`** —— 它记的是"已经投影到 rollout jsonl 的第几个字节",`thread_items` 里存的 `rollout_ordinal` / `item_json` 也是指回 jsonl 的位置。
   ⇒ **它是建在 jsonl 之上的增量索引,不是把 jsonl 搬走**;rollout 文件仍在持续追加(复查当天还新建了 19 个)。所以按 jsonl 解析的做法依然成立。
   将来若真出现"jsonl 不再写、只有 sqlite"的情况,再考虑加一个 sqlite 读取源(`item_type` / `created_at_ms` 都已经是规范化好的字段,读起来比解析 jsonl 还简单)。
9. **标题的真身是状态库 `state_<N>.sqlite` 的 `threads.name`,不是 `session_index.jsonl`**(2026-10-15 实测确证)。那张表 41 列,和标题有关的是 `title`(长原文)和 `name`(短名,即界面显示的那个);`archived` / `archived_at` 也在这。
   铁证是一对探针 —— **`codex archive <名字>` 会按名字找会话**:
   - 拿写进 `session_index.jsonl` 的名字 → `Error: No active session found matching '…'`
   - 拿状态库里的名字 → `Archived session …` ✓

   ⇒ 所以 `cod rename` **必须写状态库**;只写 jsonl 是"碰了它的文件却没达到目的"。
   但 `threads.name` 有 11 行是空的、jsonl 覆盖更全,所以**读的时候两边都读、状态库优先**(实测 43 个会话零冲突,换读法当天标题一个没变)。
   写状态库时**只改 `name`**:动 `updated_at` / `recency_at` 会把会话顶到 Codex 侧边栏最前面,改个名字不该改变它的时间位置。
10. **`codex doctor` 也报了两条 thread 问题**:`rollout files are missing from the state DB`(增量索引没吃完全部文件)和 `duplicate thread inventory entries found`(**就是 `session_index.jsonl` 里那 5 个重复 id**)。两条都印证了我们的读法:"取最后一行"是对的,索引与投影不是权威全集。
11. **归档 / 删除 / 取回都实测通过,不需要 daemon**(2026-10-15 在一个一次性会话上走了完整往返):`codex archive` → 文件从 `sessions/<年>/<月>/<日>/` 挪进 `archived_sessions/`、状态库 `archived=1`、`cod list` 消失而 `cod list -a` 标 `[已归档]`;`codex unarchive` 完整还原。**`cod rm` 的路子是对的。**
    ⚠️ 但它走的是 Codex 自己的状态库,**手工造的、没登记进库的会话会被拒**(`failed to archive session`)—— 所以 `cod rm` 的成败依赖 Codex 自己的状态是否正常,不是我们能完全掌控的。失败时我们会明确报错并中止,不会删一半。
12. **`codex delete` 会留下"空壳",得自己补一刀**(2026-10-16 实测)。它清掉了 rollout 文件、`state_*.sqlite.threads`、`session_index.jsonl`,但**不清桌面应用自己的 `~/.codex/sqlite/codex-dev.db`** —— 那张 `local_thread_catalog` 正是**侧边栏**列的东西(`display_title` / `cwd` / `source_kind`)。结果:会话删了,侧边栏还挂着一个点不开的空壳。
    ⇒ `cod rm` 的直删现在会调 `_purge_desktop_catalog()` 按会话 id 清那几处挂行;**归档不清**(归档不是删除)。实测拿得到那个库的写锁(应用在跑也不影响)。
    判断"空壳"要用**我们的发现逻辑**(能不能列出来),不能只看文件在不在:一个会话可能只剩 `guardian_review` 的子线程文件,文件在、会话却已经不算存在。
    ⚠️ **不论改状态库还是桌面库,侧边栏都有内存缓存** —— 要重启 Codex 应用才看得见。

### OpenCode(已实现,2026-10-16)

- 二进制 `/opt/homebrew/bin/opencode`,实测 **v2.0.28 ~ 2.0.30**。**v2 是服务架构**:后台跑一个本地 HTTP 服务(`127.0.0.1:<port>` + 密码,见 `~/.local/state/opencode/service.json`),CLI/TUI 只是客户端。
- **数据是一个 SQLite**,不是文件树:`opencode debug paths` 自己报 `data = ~/.local/share/opencode`,`db = <data>/opencode.db`(认 `$XDG_DATA_HOME`)。~~v1 那套"一堆小 JSON 文件"已经不适用。~~
- 关键表(**一张表一个会话、一张表一条消息**,本机实测 schema):

  | 表 | 字段 |
  |---|---|
  | `session_v2` | `id` / `project_id` / `parent_id` / `fork_session_id` / `directory` / `title` / `model` / `agent` / `time_created`·`time_updated` / `time_archived` / `tokens_input`·`tokens_output`·`tokens_reasoning`·`tokens_cache_read`·`tokens_cache_write` |
  | `session_message` | `id` / `session_id` / **`type`** / **`seq`** / `time_created` / `data`(JSON) |
  | `project` | `worktree`(项目目录)/ `vcs` / `name` / `time_active` |

- **消息类型**(实测):`user` / `assistant` / `idle` / `model-switched` —— **后两种跳过**。

  | type | `data` |
  |---|---|
  | `user` | `{time:{created}, text, files, agents}` |
  | `assistant` | `{time:{created,streamed,completed}, agent, model, content[], finish, tokens:{input,output,reasoning,cache:{read,write}}}` |
  | `idle` | `{time, outcome}`(succeeded / failed) |
  | `model-switched` | `{time, model, previous}` |

- **`assistant.content[]` 的 part**(实测):
  `{"type":"text","text":…}` / `{"type":"reasoning","text":…}` /
  `{"type":"tool","name":…,"state":{"input":{…},"content":[{"type":"text","text":…}]}}`
  —— tool 的 `state.content` 就是"工具返回",和 Claude 的 `tool_use` / `tool_result` 一一对应。

- **token 口径:`input` 不含缓存**(和 Claude 一样,不是 Codex 那种)。**这是对账对的,不是猜的** —— 拿它自己的 `opencode stats` 比:它报 **29k**,正好 = `input 14263 + output 1275 + reasoning 454 + cache_read 13056`(29048);若 `input` 含缓存,那个数会明显更大。早先一次 5.8k 也对上过,两次独立验证。
- **`title` 可能是空的**(实测:失败的那次会话没有标题),用首句提问兜底。**失败 / 没产出的会话一堆列都是 0** —— `has_usage=False`,`list` 显示 `—`,别当成"0 用量"。
- **库里没有"单会话字节数"**,`size` 取该会话所有 `session_message.data` 的字节数之和。
- **`parent_id` / `fork_session_id` 含义未验**(实测全是 NULL),所以**不做过滤** —— 宁可能多列出来,也别把真会话藏了。Codex 那种"排除子线程"的规则在这里没有证据,不照搬。
- **写操作走它的 CLI**:`session delete`(无交互确认参数)/ `session export`(JSON;`--sanitize` 可脱敏)/ `session list` / `import` / `debug paths` / `api`。**没有改名命令**,所以 `opc rename` 直接 `UPDATE session_v2.title` —— **应用认不认没验过**(没有样本可对照),认不动就退回"自己的覆盖层"。

## 六、未决 / 待验

- **续聊没实跑过**:`codex resume <id>` 命令确认存在、按 id 调用不会歧义,但它一跑就会接管终端开 TUI,没在真实会话上试过。风险低(纯按 id),但如实记一笔。
- **写状态库有被覆盖的可能**:Codex 应用正开着,它可能在内存里缓存着会话元数据、下次更新时把我们改的 `name` 覆盖回去。实测改名后立刻用 `codex archive <新名字>` 是认的,但**长期会不会被冲掉没有验证**。真被冲掉的话,退路是改成"我们自己的覆盖层"(`~/.local/share/cgr/codex-titles.json`,绝不碰 Codex 数据,代价是只有 `cod` 看得到新名字)。
- **`state_<N>.sqlite` 的版本号会涨**(现在是 `state_5`)。代码按"取最大的 N"来找,不受影响;但 schema 若是大变,得重新勘查。
- **OpenCode**:**已实现**(`opc`)。存储、消息结构、token 口径都实测清楚了,见第五节。
- **OpenCode 的 `prompts`**:它有 `~/.local/state/opencode/prompt-history.jsonl`,但我们的 `prompts` 还只读 Claude 那套,没接。
- **`opc rename` 应用认不认**:没验过(它没有改名命令,我们是直接 `UPDATE session_v2.title`)。
- **Codex 的 `prompts`**:索引里有已删会话的标题,但没有完整提问流水,形态和 Claude 的 `history.jsonl` 不同,要单独设计。
