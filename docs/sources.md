# 多数据源

> 本文件存放:**`cgr` 怎么支持 Claude Code 之外的 agent CLI 的聊天记录**。
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

同一个 `chatmgr.py`,不同软链 → 不同默认源(busybox 那招,`SOURCE_BY_PROG`):

```
cgr list            # Claude(也接受 chatmgr)
cod list            # Codex
cgr list -S codex   # 不换命令名,用 -S 临时换源(大写 S,避开 -s/since)
```

**必须处理的坑:`run_picker` 是"自己调自己"**(`sys.executable` + `chatmgr.py` 绝对路径)。那条路径下 `argv[0]` 是 `chatmgr.py` 而不是 `cgr`,所以:

1. `chatmgr` / `chatmgr.py` 必须归到 claude 一族
2. 自调用时**显式把 `-S` 传下去**(预览、`show`、`rm`、`rename`、`_picklines` 的 reload 全都要带),否则 picker 里打开/删除会跳源、张冠李戴

## 三、接入一个新源必须回答的问题

| # | 问题 | Claude Code | Codex |
|---|---|---|---|
| 1 | 数据在哪 | `~/.claude/projects/<项目>/<id>.jsonl` | `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl` |
| 2 | 一次对话怎么认出是"一个"会话 | 一个文件 = 一个会话 | **一个 `session_id`,可能横跨多个文件**(靠 `session_meta.payload.session_id` 粘合) |
| 3 | 哪些是**我的提问** | `type=user` 且过 `_user_text()` | `item_completed` 里 `item.type=="UserMessage"` 的 item |
| 4 | 回复 / 工具调用 / 噪音 | `assistant` 的 `text`/`tool_use`/`thinking` | `AgentMessage` / `CommandExecution`…/ `Reasoning` |
| 5 | **标题**从哪来 | 会话内追加的 `custom-title` 记录 | **另一个文件** `session_index.jsonl` 的 `thread_name`(追加式,**取最后一行**) |
| 6 | **token** 从哪来 | `message.usage.*`(input **不含** cache) | 独立的 `token_usage_record` 行(input_tokens **含** cache,要减掉) |
| 7 | **项目**(cwd)从哪来 | 记录的 `cwd`(带尾随空格,要 strip) | `session_meta.payload.cwd`(可能是 Windows 路径) |
| 8 | **时间戳** | 记录里的 `timestamp` | 记录顶层的 `timestamp`(全部记录都有) |
| 9 | 该排除什么 | `subagents/` 是**另一个目录**,天然不扫 | 靠 `thread_source == "user"` 过滤掉 `guardian_review` 子线程 |
| 10 | 归档 / 删除 / 续聊 | 我们自己的 `~/claude-archive`;`claude --resume` | **它自带**:`codex archive` / `delete` / `resume` |

## 四、多源之后要重新定义的产品语义

- **序号的基准**:`cgr 3` 指当前**源内**无筛选列表的第 3 个。一个 store 只装一个源(命令名或 `-S` 决定),所以不存在跨源合并编号的问题 —— **跨源入口是下一轮的事**(见 `prd.md`)。
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
5. `~/.codex/session_index.jsonl` 是**追加式**:实测 68 行只有 63 个 id(5 个 id 多行 → Codex 自己改标题就是再追加一行);`updated_at` 非单调;mtime 与会话文件同步;还留着 **20 条"文件已删但索引还在"** 的条目 → 它**不是**从会话文件重新生成的,是独立维护的。**读标题必须取最后一行。** 里面也含 guardian_review 子线程的标题。
6. **Codex CLI**:先看 `$PATH` 上的 `codex`(2026-10-15 装的是独立版 `0.162.1`,在 `~/.local/bin/codex` → `~/.codex/packages/standalone/current/bin/codex`);没有才回退到 app 包里自带的那份 `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`(`codex-package.json` 里的官方 entrypoint,一个 shim)。子命令:`resume` / `archive` / `unarchive` / `delete` / `fork` / `agents` / `doctor` / `migrate-rollouts` …
   - **`codex resume <SESSION_ID>` 支持 UUID** —— 与 `claude --resume` 对称,所以"续聊"是能做的
   - **`codex archive` / `delete` 存在** —— 所以 Codex 的删除/备份用它自己的,我们不再造 `~/claude-archive` 那一套
   - **`$CODEX_HOME` 是它的数据根目录变量** —— 我们的 `codex_dir()` 必须认(否则用户挪走数据后,`cod list` 读一处、`cod rm` 删另一处)
7. **有坏行**:至少一个文件含被截断的 JSON(流式写入的残行)。逐行 try/except 是必需的。
8. **格式确实在演进,但 jsonl 仍是源头**(2026-10-15 复查)。`codex migrate-rollouts` 说的"paginated thread history"落地成了 `~/.codex/thread_history_1.sqlite`(实测 47MB:`thread_turns` 462 行、`thread_items` 9055 行)。但它的关键字段是 **`thread_history_projection_state.next_rollout_byte_offset`** —— 它记的是"已经投影到 rollout jsonl 的第几个字节",`thread_items` 里存的 `rollout_ordinal` / `item_json` 也是指回 jsonl 的位置。
   ⇒ **它是建在 jsonl 之上的增量索引,不是把 jsonl 搬走**;rollout 文件仍在持续追加(复查当天还新建了 19 个)。所以按 jsonl 解析的做法依然成立。
   将来若真出现"jsonl 不再写、只有 sqlite"的情况,再考虑加一个 sqlite 读取源(`item_type` / `created_at_ms` 都已经是规范化好的字段,读起来比解析 jsonl 还简单)。
9. **`codex doctor` 自己也报了两条 thread 问题**:`rollout files are missing from the state DB`(增量索引没吃完全部文件)和 `duplicate thread inventory entries found`(**就是 `session_index.jsonl` 里那 5 个重复 id**)。两条都印证了我们的读法:"取最后一行"是对的,索引与投影不是权威全集。

### OpenCode(未实现,**会话正文存哪仍未知**)

- 提问流水:`~/.local/state/opencode/prompt-history.jsonl`,每行 `{"text": …, "files": [], "agents": [], "skills": [], "pasted": [], "mode": "normal"}`
- 配置:`~/.config/opencode/`(只有 `cli.json` / `service.json` / `skills`,**没有会话数据**)
- 二进制:`/opt/homebrew/bin/opencode`(Homebrew 的 `opencode-v2`)
- **没找到会话正文存哪**:`~/.local/share/opencode` 不存在。已知线索:上游 `sst/opencode` 历来把一次会话拆成一堆小 JSON 文件存(`storage/session/` + 每条消息、每个 part 各一个文件),**不是** jsonl、也不在单个文件里。若真是这样,第三节第 2 条(怎么认出"一个"会话)的答案会变成"要跨很多文件拼起来"。
- **结论:等实际用 OpenCode 跑出会话之后再勘查**,别凭猜测写解析器。

## 六、未决 / 待验

- **`cod rename` 写回索引后,Codex 自己认不认?** 已按它的原生机制(追加一行)实现,但**这个假设只有真写一次、再打开 Codex 桌面版看标题变没变,才能确证**。若 Codex 不认(比如它按 `updated_at` 取最新、并列时取先写的),退路是改成"我们自己的覆盖层"(`~/.local/share/cgr/codex-titles.json`,绝不碰 Codex 数据,代价是只有 `cod` 看得到新标题)。
- **`codex archive` / `delete` 需不需要 app-server daemon?** 帮助里有 `--remote` 和 "shared local app-server daemon" 的措辞,未实测。若需要,写操作应改成提示用户手动处理。
- **跨源入口**(`cgr` 变成"全部")是下一轮的事,见 `prd.md`。
- **Codex 的 `prompts`**:索引里有已删会话的标题,但没有完整提问流水,形态和 Claude 的 `history.jsonl` 不同,要单独设计。
