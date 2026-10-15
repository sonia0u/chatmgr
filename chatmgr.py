#!/usr/bin/env python3
"""chatmgr —— 聊天记录管理 CLI

把本机 agent 的聊天记录"用起来":搜索 / 阅读 / 统计 / 安全删除。

数据源(命令名决定默认用哪个;`-S/--source` 可覆盖):
  claude  ~/.claude/projects/<项目>/<会话id>.jsonl        cgr / chatmgr
  codex   ~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl  cod

分三层:
  解析层  Source / SessionStore / Session —— 无 CLI 依赖,可复用(将来可接 MCP)
  归档层  Claude 用自己的 backup/purge + manifest;
          Codex 用它自带的 codex archive / delete(所以不另存一份)
  CLI 层  argparse 子命令 + resolve()

仅用标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import heapq
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    import readline  # noqa: F401  让 input() 支持方向键/行编辑/历史(脚本里默认不加载)
except ImportError:
    pass

# ---------------------------------------------------------------- 常量 / 路径


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def projects_dir() -> Path:
    return claude_dir() / "projects"


def history_file() -> Path:
    """用户的提问流水(~/.claude/history.jsonl):每条 prompt 一行,独立于会话 jsonl。

    所以 jsonl 已被删的会话,它的提问仍在这条流水里 —— 这是 prompts 子命令的数据源。
    """
    return claude_dir() / "history.jsonl"


def archive_dir() -> Path:
    return Path(os.environ.get("CHATMGR_ARCHIVE_DIR") or (Path.home() / "claude-archive"))


def dupes_dir() -> Path:
    return archive_dir() / "_dupes"


def archive_files() -> list[Path]:
    """归档里的**备份**文件,不含 dedupe 挪进来的重复副本(<归档>/_dupes/)。

    副本是"刻意保留、不删"的兜底,不该被当成备份:否则 purge 会把它一并清掉。
    要清副本得自己动手 rm -r <归档>/_dupes。
    """
    ad = archive_dir()
    if not ad.is_dir():
        return []
    dupes = dupes_dir()
    return [p for p in sorted(ad.glob("*/*.jsonl")) if p.parent != dupes]


def codex_dir() -> Path:
    """Codex 的数据根目录。

    必须认 `$CODEX_HOME`(Codex 自己的变量):用户若把数据挪到别处,而我们只认
    `~/.codex`,就会出现"读的是一处、`cod rm` 删的是另一处"。测试用的
    `$CHATMGR_CODEX_DIR` 优先级最高,免得真去碰用户的数据。
    """
    return Path(os.environ.get("CHATMGR_CODEX_DIR")
                or os.environ.get("CODEX_HOME")
                or (Path.home() / ".codex"))


def codex_index_file() -> Path:
    """Codex 的标题索引(id → thread_name)。

    注意:它**不是** Codex 认的那个 —— 实测拿这里写的名字去 `codex archive <名字>`
    会报 "No active session found",而拿 sqlite 里的旧名字就能找到。真身是
    `state_*.sqlite` 的 `threads.name`,这个文件只是一份镜像(覆盖更全)。
    """
    return codex_dir() / "session_index.jsonl"


def codex_state_db() -> Path | None:
    """Codex 的状态库 `state_<N>.sqlite`。版本号会涨(state_5 → state_6),所以取最大的那个。"""
    best: tuple[int, Path] | None = None
    for p in codex_dir().glob("state_*.sqlite"):
        m = re.fullmatch(r"state_(\d+)\.sqlite", p.name)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), p)
    return best[1] if best else None


def codex_bin() -> str | None:
    """Codex CLI 的位置。找不到返回 None(读相关功能照常,只有删/改名/续聊不可用)。

    顺序:$CHATMGR_CODEX_BIN(测试)→ $PATH 上的 codex(用户自己装的独立版)→
    app 包里自带的那份(装了 ChatGPT.app 但这台机器没有独立 CLI 时的兜底)。
    $CHATMGR_CODEX_BIN 置空 = 明确当作"没有 codex"。
    """
    env = os.environ.get("CHATMGR_CODEX_BIN")
    if env is not None:
        return env or None
    found = shutil.which("codex")
    if found:
        return found
    bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
    return str(bundled) if bundled.is_file() else None


TOKEN_KEYS = ("input", "output", "cache_read", "cache_creation", "thinking")


def empty_tokens() -> dict:
    return {k: 0 for k in TOKEN_KEYS}


# 子命令别名:只留真正会用的(pick→f);其余一律敲全名,帮助更清爽
SUBCOMMANDS = {
    "list": [],
    "search": [],
    "prompts": [],
    "show": [],
    "pick": ["f"],
    "export": [],
    "stats": [],
    "rm": [],
    "purge": [],
    "dedupe": [],
    "rename": [],
}
KNOWN_COMMANDS = set(SUBCOMMANDS) | {a for al in SUBCOMMANDS.values() for a in al}

# 搜索结果里"命中来源"的显示名(和 show 里的措辞一致,别用 user/assistant 这种程序叫法)
KIND_LABEL = {
    "user": "你",
    "assistant": "Claude",
    "tool": "工具",
    "thinking": "思考",
    "tool_result": "工具返回",
}


# ---------------------------------------------------------------- 小工具


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def prog_name() -> str:
    """当前是以什么命令名被调用的(cgr / chatmgr / cod ...)。提示语里用它,免得串味。"""
    return os.path.basename(sys.argv[0]) or "cgr"


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def dwidth(s: str) -> int:
    """显示宽度:CJK 全角算 2。先剥掉 ANSI 颜色码,否则对齐会错。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in ANSI_RE.sub("", s))


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dwidth(s))


def truncate(s: str, width: int) -> str:
    if dwidth(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


def flat(s) -> str:
    """把任意空白(含换行、制表符)压成单个空格。

    标题/项目里若混入换行,会撑破表格行、也会把 fzf 的一条记录拆成两条。
    """
    return " ".join(str(s).split())


def basename_any(p) -> str:
    """取路径最后一段。同时认 / 和 \\ —— 记录里的 cwd 可能是 Windows 路径,
    而 Path("E:\\a\\b").name 在 macOS 上会把整串当文件名返回。
    """
    parts = [x for x in re.split(r"[\\/]+", str(p)) if x]
    return parts[-1] if parts else str(p)


def human_size(n: int) -> str:
    for unit, div in (("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n}B"


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1000:
        return f"{n / 1e3:.1f}k"
    return str(n)


_COLOR_MODE: bool | None = None  # None=自动(看是否 TTY);True/False=强制开/关


def color_enabled() -> bool:
    if _COLOR_MODE is not None:
        return _COLOR_MODE
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def paint(s: str, code: str, on: bool) -> str:
    return f"\033[{code}m{s}\033[0m" if on else s


# 项目(文件夹)配色:按项目名排序分配,保证同一批会话里各项目颜色互不相同
PROJECT_COLORS = [
    "38;5;39", "38;5;208", "38;5;35", "38;5;170", "38;5;214", "38;5;44",
    "38;5;203", "38;5;141", "38;5;78", "38;5;220", "38;5;117", "38;5;180",
    "38;5;213", "38;5;84", "38;5;45", "38;5;209",
]


def project_colors(sessions) -> dict:
    """{项目名: 颜色码}。以**完整会话列表**为基准,所以各命令、各筛选下同一项目颜色一致。"""
    uniq = sorted({s.short_project for s in sessions})
    return {p: PROJECT_COLORS[i % len(PROJECT_COLORS)] for i, p in enumerate(uniq)}


def emit(text: str, use_pager: bool):
    if use_pager and sys.stdout.isatty():
        try:
            p = subprocess.Popen(["less", "-R"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return
        except FileNotFoundError:
            pass
    sys.stdout.write(text)


def _confirm(prompt: str, cancel_msg: str = "已取消,什么都没动。") -> bool:
    """只在交互终端返回 True;非 TTY 一律 False(保持 dry-run 的安全默认)。

    取消时自己打印提示,所以非交互场景(脚本/管道)不会冒出"已取消"这种误导性输出。
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        if input(prompt).strip().lower() in ("y", "yes"):
            return True
    except (EOFError, KeyboardInterrupt):
        print()
    print(paint(cancel_msg, "2", color_enabled()))
    return False


def _ask_delete(on: bool) -> str:
    """删除时的三选:y=直接删(不备份)、n=删并备份、其他=取消。非 TTY 返回 cancel。"""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return "cancel"
    print(paint("怎么删?y = 直接删(不备份) / n = 删并备份一份 / 其他 = 取消", "1;33", on))
    try:
        ans = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "cancel"
    if ans in ("y", "yes"):
        return "direct"
    if ans in ("n", "no"):
        return "backup"
    return "cancel"


def parse_ts(v):
    """时间戳 → 本地 naive datetime。支持 ISO 与毫秒 epoch。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        secs = v / 1000 if v > 1e11 else v
        return dt.datetime.fromtimestamp(secs)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = dt.datetime.fromisoformat(s)
        except ValueError:
            try:
                return dt.datetime.fromtimestamp(float(s) / 1000)
            except ValueError:
                return None
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    return None


def parse_when(v, end_of_day: bool = False):
    """解析 --since / --until:支持 YYYY-MM-DD 与 7d / 2w / 3m / 1y。"""
    if not v:
        return None
    v = v.strip()
    m = re.fullmatch(r"(\d+)([hdwmy])", v)
    if m:
        n = int(m.group(1))
        days = {"h": 1 / 24, "d": 1, "w": 7, "m": 30, "y": 365}[m.group(2)] * n
        return dt.datetime.now() - dt.timedelta(days=days)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y/%m/%d"):
        try:
            d = dt.datetime.strptime(v, fmt)
        except ValueError:
            continue
        if end_of_day and fmt == "%Y-%m-%d":
            d += dt.timedelta(days=1) - dt.timedelta(seconds=1)
        return d
    die(f"看不懂的时间格式:{v}(用 YYYY-MM-DD 或 7d / 2w / 3m)")


def fmt_time(d) -> str:
    return d.strftime("%Y-%m-%d %H:%M") if d else "?"


# ---------------------------------------------------------------- 解析层


@dataclass
class Session:
    sid: str
    path: Path
    project: str = ""
    title: str = ""
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    size: int = 0
    prompts: int = 0
    replies: int = 0
    tokens: dict = field(default_factory=empty_tokens)
    models: Counter = field(default_factory=Counter)
    tools: Counter = field(default_factory=Counter)
    first_prompt: str = ""
    archived: bool = False
    title_explicit: bool = False  # 标题是否来自 custom-title/ai-title/agent-name
    source: str = "claude"        # 数据源 key(见 SOURCES)
    paths: tuple[Path, ...] = ()  # 该会话的全部文件;空 = 只有 path 一个
    has_usage: bool = True        # 该会话到底有没有用量数据(旧版 Codex 不写 token)

    @property
    def short_project(self) -> str:
        return basename_any(self.project) or "?"

    @property
    def tok_io(self) -> int:
        return self.tokens["input"] + self.tokens["output"]


def session_files(s: Session) -> tuple[Path, ...]:
    """该会话对应的文件。Codex 一个会话可能横跨多个文件(续写/分叉)。"""
    return s.paths or (s.path,)


def iter_records(path: Path):
    """逐行流式解析,坏行跳过,不整文件载入。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def _content_text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"
        )
    return ""


def _user_text(rec) -> str | None:
    """真人输入。跳过 meta / 工具返回 / 系统注入。"""
    if rec.get("isMeta"):
        return None
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        t = " ".join(
            x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"
        )
    else:
        return None
    t = t.strip()
    if not t:
        return None
    if t.startswith("<") or "system-reminder" in t or t.startswith("Caveat:"):
        return None
    return t


def _tool_summary(x: dict) -> str:
    name = x.get("name") or "?"
    inp = x.get("input")
    if not isinstance(inp, dict):
        return f"{name}(…)"
    for k in ("command", "file_path", "path", "pattern", "query", "url", "prompt"):
        if k in inp:
            v = " ".join(str(inp[k]).split())
            return f"{name}({k}={truncate(v, 80)})"
    return f"{name}({truncate(json.dumps(inp, ensure_ascii=False), 80)})"


def _add_usage(acc: dict, u: dict):
    acc["input"] += u.get("input_tokens") or 0
    acc["output"] += u.get("output_tokens") or 0
    acc["cache_read"] += u.get("cache_read_input_tokens") or 0
    acc["cache_creation"] += u.get("cache_creation_input_tokens") or 0
    otd = u.get("output_tokens_details") or {}
    acc["thinking"] += otd.get("thinking_tokens") or 0


def load_session(path: Path, archived: bool = False) -> Session:
    s = Session(sid=path.stem, path=path, archived=archived)
    try:
        s.size = path.stat().st_size
    except OSError:
        pass
    custom = ai = agent = None
    for rec in iter_records(path):
        t = rec.get("type")
        if not s.project:
            cwd = rec.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                s.project = flat(cwd)
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            if s.start is None or ts < s.start:
                s.start = ts
            if s.end is None or ts > s.end:
                s.end = ts
        if t == "custom-title" and rec.get("customTitle"):
            custom = rec["customTitle"].strip()
        elif t == "ai-title" and rec.get("aiTitle"):
            ai = rec["aiTitle"].strip()
        elif t == "agent-name" and rec.get("agentName"):
            agent = rec["agentName"].strip()
        elif t == "user":
            ut = _user_text(rec)
            if ut:
                s.prompts += 1
                if not s.first_prompt:
                    s.first_prompt = ut
        elif t == "assistant":
            m = rec.get("message") or {}
            model = m.get("model")
            if model and model != "<synthetic>":
                s.models[model] += 1
            _add_usage(s.tokens, m.get("usage") or {})
            c = m.get("content")
            has_reply = False
            if isinstance(c, list):
                for x in c:
                    if not isinstance(x, dict):
                        continue
                    xt = x.get("type")
                    if xt == "tool_use":
                        has_reply = True
                        if x.get("name"):
                            s.tools[x["name"]] += 1
                    elif xt == "text" and (x.get("text") or "").strip():
                        has_reply = True
            if has_reply:
                s.replies += 1
    s.title_explicit = bool(custom or ai or agent)
    s.title = flat(custom or ai or agent or (truncate(s.first_prompt, 60) if s.first_prompt else "(无标题)"))
    if not s.project:
        s.project = path.parent.name
    return s


class SessionStore:
    """发现并缓存会话。include_archived 时把归档也纳入数据源。

    可以装**多个源**(`cgr` 就是全部混排):发现阶段把各源的会话并到一起,按最后
    活动时间倒序,再按 `(源, id)` 去重 —— 不同源的 id 空间互不相干,撞车了不能当重复。
    """

    def __init__(self, include_archived: bool = False, dedupe: bool = True,
                 sources: list[Source] | None = None):
        self.sources = list(sources) if sources else [SOURCES["claude"]]
        self.include_archived = include_archived
        self.dedupe = dedupe
        self._cache: list[Session] | None = None
        self.duplicates = 0

    def has(self, key: str) -> bool:
        return any(s.key == key for s in self.sources)

    def sessions(self) -> list[Session]:
        if self._cache is None:
            self._cache = self._discover()
        return self._cache

    def _discover(self) -> list[Session]:
        out: list[Session] = []
        for src in self.sources:
            out.extend(src.discover(self.include_archived))
        out.sort(key=lambda s: (s.end or s.start or dt.datetime.min, not s.archived), reverse=True)
        if not self.dedupe:
            return out
        # 同一个会话 id 可能落在多个项目文件夹里(在不同目录 resume 过),文件内容会不一致。
        # 只保留最新、且优先活会话的那一份,否则列表会出现两行相互矛盾的同一会话。
        # 键带上源:不同源的 id 空间互不相干,撞车了不能当重复吞掉。
        best_title: dict[tuple[str, str], str] = {}
        for s in out:
            if s.title_explicit:
                best_title.setdefault((s.source, s.sid), s.title)
        seen: set[tuple[str, str]] = set()
        uniq: list[Session] = []
        for s in out:
            key = (s.source, s.sid)
            if key in seen:
                self.duplicates += 1
                continue
            seen.add(key)
            if not s.title_explicit and key in best_title:
                s.title = best_title[key]  # 借用同一会话另一份里的自定义标题,免得标题忽然变样
            uniq.append(s)
        return uniq

    def index_map(self) -> dict[str, int]:
        return {str(s.path): i for i, s in enumerate(self.sessions(), 1)}


# ---------------------------------------------------------------- 筛选 / 指认


def _project_filters(args) -> list[str]:
    """把 -p 的多个值(可重复、可逗号分隔)摊平成小写项目名列表。"""
    raw = getattr(args, "project", None) or []
    out: list[str] = []
    for item in raw:
        out += [p.strip().lower() for p in str(item).split(",") if p.strip()]
    return out


def apply_filters(sessions: list[Session], args) -> list[Session]:
    out = sessions
    projs = _project_filters(args)
    if projs:
        out = [s for s in out if any(p in s.project.lower() or p in s.short_project.lower() for p in projs)]
    since = parse_when(getattr(args, "since", None))
    until = parse_when(getattr(args, "until", None), end_of_day=True)
    if since:
        out = [s for s in out if (s.end or s.start) and (s.end or s.start) >= since]
    if until:
        out = [s for s in out if (s.start or s.end) and (s.start or s.end) <= until]
    return out


def _candidates(sessions: list[Session], idx: dict[str, int]) -> str:
    lines = []
    for s in sessions[:15]:
        lines.append(f"  [{idx.get(str(s.path), '?')}] {s.title}  ({s.sid[:8]})")
    if len(sessions) > 15:
        lines.append(f"  … 还有 {len(sessions) - 15} 个")
    return "\n".join(lines)


def resolve(ref: str, store: SessionStore) -> Session:
    sessions = store.sessions()
    idx = store.index_map()
    ref = ref.strip()
    if ref.isdigit():
        i = int(ref)
        if 1 <= i <= len(sessions):
            return sessions[i - 1]
        die(f"序号 {i} 超出范围(1-{len(sessions)})")
    by_id = [s for s in sessions if s.sid.startswith(ref)]
    if len(by_id) == 1:
        return by_id[0]
    if len(by_id) > 1:
        die(f"id 前缀 {ref!r} 不唯一:\n" + _candidates(by_id, idx))
    by_title = [s for s in sessions if ref.lower() in s.title.lower()]
    if len(by_title) == 1:
        return by_title[0]
    if len(by_title) > 1:
        die(f"标题 {ref!r} 匹配多个:\n" + _candidates(by_title, idx))
    die(f"找不到会话:{ref}")


# ---------------------------------------------------------------- 事件流(供 show / search / export)
#
# 每个源各有一套 *事件 / 可搜索单元* 的实现,产出统一的 (kind, text);
# 对外的 iter_events / search_units 按 session.source 分派(见"数据源"一节)。
# kind 取值:user / assistant / tool / thinking / tool_result


def _claude_events(path: Path, full: bool = False):
    """Claude:按原始顺序产出 (kind, text)。"""
    for rec in iter_records(path):
        t = rec.get("type")
        m = rec.get("message") or {}
        if t == "user":
            ut = _user_text(rec)
            if ut:
                yield ("user", ut)
            elif full:
                c = m.get("content")
                if isinstance(c, list):
                    for x in c:
                        if isinstance(x, dict) and x.get("type") == "tool_result":
                            yield ("tool_result", _content_text(x.get("content")))
        elif t == "assistant":
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for x in c:
                if not isinstance(x, dict):
                    continue
                xt = x.get("type")
                if xt == "text" and (x.get("text") or "").strip():
                    yield ("assistant", x["text"])
                elif xt == "tool_use":
                    yield ("tool", _tool_summary(x))
                elif full and xt == "thinking":
                    yield ("thinking", x.get("thinking") or "")


def _claude_units(path: Path, include_all: bool = False, role: str | None = None):
    """Claude:产出可搜索单元 (kind, text)。默认 user 输入 + assistant 正文 + 工具调用。"""
    for rec in iter_records(path):
        t = rec.get("type")
        m = rec.get("message") or {}
        if t == "user":
            if role and role != "user":
                continue
            ut = _user_text(rec)
            if ut:
                yield ("user", ut)
            if include_all:
                c = m.get("content")
                if isinstance(c, list):
                    for x in c:
                        if isinstance(x, dict) and x.get("type") == "tool_result":
                            yield ("tool_result", _content_text(x.get("content")))
        elif t == "assistant":
            if role and role != "assistant":
                continue
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for x in c:
                if not isinstance(x, dict):
                    continue
                xt = x.get("type")
                if xt == "text" and (x.get("text") or "").strip():
                    yield ("assistant", x["text"])
                elif xt == "tool_use":
                    yield ("tool", f"{x.get('name')} {json.dumps(x.get('input') or {}, ensure_ascii=False)}")
                elif include_all and xt in ("thinking", "redacted_thinking"):
                    yield ("thinking", x.get("thinking") or "")


def load_prompts() -> list[dict]:
    """读提问流水,归一化成 {text, ts, project, session},坏行跳过。

    注意 project 字段和记录里的 cwd 一样带尾随空格,必须 strip。
    """
    out: list[dict] = []
    for rec in iter_records(history_file()):
        text = rec.get("display")
        if not isinstance(text, str) or not text.strip():
            continue
        out.append({
            "text": text.strip(),
            "ts": parse_ts(rec.get("timestamp")),
            "project": (rec.get("project") or "").strip(),
            "session": rec.get("sessionId") or "",
        })
    return out


# ---------------------------------------------------------------- Codex 解析层
#
# Codex 的记录格式和 Claude 完全不同,而且**一个会话可能横跨多个文件**。这里的设计
# 由三条实测结论支撑(~/.codex 68 个文件 / 43 个会话 / 166MB,全量解析约 0.5s):
#
#   1. session_meta.payload.thread_source 只有两个取值:"user"(真人主线程)和
#      "guardian_review"(Codex 自派的"审批复核"子线程,内容全是机器文本)。
#      —— 一条规则就能把子线程排干净,不用去算 parent_thread_id 那棵树。
#
#   2. event_msg / item_completed 是一份**干净的语义骨架**:UserMessage / AgentMessage /
#      CommandExecution / FileChange / ... 里面**不含** app-context、AGENTS.md、
#      environment_context 这些注入脚手架(它们不是 item)。
#      —— 所以这里完全不需要写启发式噪音过滤器。对比:若去读 response_item,
#      role=user 的记录里真提问只占 1/18(6106 条中 342 条),靠文本前缀猜必错。
#
#   3. ordinal 是**每个文件各自从 0 开始**的,跨文件不能拿来排序 —— 只能按 timestamp。

CODEX_INJECTED = (
    "# Files mentioned by the user:",
    "<send_user_message_question_reply>",
)

#: 归到 `tool` 这个 kind 的 item 类型
CODEX_TOOL_ITEMS = frozenset(
    {"CommandExecution", "FileChange", "McpToolCall", "WebSearch", "Extension", "ImageView"}
)


def _codex_msg_text(item) -> str:
    """取 item 的正文。逐块丢掉已知的注入前言,其余照收。

    这里**不**学 Claude 那套"以 < 开头一律丢":Codex 的真人提问可能就是 markdown。
    """
    parts = []
    for c in item.get("content") or []:
        if not isinstance(c, dict):
            continue
        t = (c.get("text") or "").strip()
        if not t or t.startswith(CODEX_INJECTED):
            continue
        parts.append(t)
    return "\n".join(parts)


def _codex_tool_summary(item) -> str:
    it = item.get("type")
    if it == "CommandExecution":
        cmd = item.get("command")
        if isinstance(cmd, list):
            return truncate(" ".join(str(x) for x in cmd), 100)
        return truncate(str(cmd or "?"), 100)
    if it == "FileChange":
        paths = list((item.get("changes") or {}).keys())
        head = ", ".join(basename_any(p) for p in paths[:3])
        more = f" 等 {len(paths)} 个文件" if len(paths) > 3 else ""
        return f"改动 {head}{more}".strip()
    if it == "McpToolCall":
        return f"{item.get('server', '?')}.{item.get('tool', '?')}"
    if it == "WebSearch":
        return f"搜索 {item.get('query', '')}".strip()
    if it == "Extension":
        return f"{item.get('kind', '')} {item.get('query', '')}".strip()
    if it == "ImageView":
        return f"看图 {basename_any(item.get('path') or '?')}"
    return it or "?"


def _codex_item_event(item, full: bool):
    """item → (kind, text);不产出事件时返回 None。"""
    it = item.get("type")
    if it == "UserMessage":
        t = _codex_msg_text(item)
        return ("user", t) if t else None
    if it == "AgentMessage":
        t = _codex_msg_text(item)
        return ("assistant", t) if t else None
    if it in CODEX_TOOL_ITEMS:
        return ("tool", _codex_tool_summary(item))
    if it == "Reasoning":
        if not full:
            return None
        summary = item.get("summary_text")
        t = " ".join(str(x) for x in summary) if isinstance(summary, list) else ""
        return ("thinking", t.strip()) if t.strip() else None
    # ContextCompaction 等一律忽略:压缩记录会把前面整段历史重述一遍,读出来是重复
    return None


def _codex_add_usage(acc: dict, u: dict):
    """Codex 的 input_tokens **含**缓存,Claude 的不含 —— 照抄字段会让 tok 虚高。

    实测 3171 条记录恒定满足:cached ⊆ input、reasoning ⊆ output、total == input+output。
    """
    cached = u.get("cached_input_tokens") or 0
    acc["input"] += max(0, (u.get("input_tokens") or 0) - cached)
    acc["output"] += u.get("output_tokens") or 0
    acc["cache_read"] += cached
    acc["cache_creation"] += u.get("cache_write_input_tokens") or 0
    acc["thinking"] += u.get("reasoning_output_tokens") or 0


def _codex_meta(path: Path) -> dict | None:
    """只读文件开头找 session_meta(实测永远是第 0 条,不必整文件解析)。"""
    for i, rec in enumerate(iter_records(path)):
        if rec.get("type") == "session_meta":
            p = rec.get("payload") or {}
            return {
                "sid": p.get("session_id") or p.get("id") or path.stem,
                "cwd": p.get("cwd") or "",
                "thread_source": p.get("thread_source") or "",
            }
        if i > 20:
            break
    return None


def _codex_merged_records(files: tuple[Path, ...]):
    """把同一会话的多个文件按 timestamp 做 k 路归并(不全量载入内存)。"""
    iters = [iter(iter_records(p)) for p in files]
    heap: list = []
    seq = 0

    def advance(fi: int):
        nonlocal seq
        for rec in iters[fi]:
            ts = parse_ts(rec.get("timestamp"))
            if ts:
                seq += 1
                heapq.heappush(heap, (ts, seq, fi, rec))
                return

    for i in range(len(iters)):
        advance(i)
    while heap:
        _, _, fi, rec = heapq.heappop(heap)
        yield rec
        advance(fi)


def _codex_items(s: Session):
    """按时间顺序产出该会话所有 item_completed 里的 item。"""
    for rec in _codex_merged_records(session_files(s)):
        if rec.get("type") != "event_msg":
            continue
        p = rec.get("payload") or {}
        if p.get("type") != "item_completed":
            continue
        item = p.get("item")
        if isinstance(item, dict):
            yield item


def _codex_events(s: Session, full: bool = False):
    for item in _codex_items(s):
        ev = _codex_item_event(item, full)
        if ev:
            yield ev


def _codex_units(s: Session, include_all: bool = False, role: str | None = None):
    """与 Claude 对齐:默认只出 user 提问 + assistant 正文 + 工具调用;
    thinking 属噪音,--all 才纳入。(Codex 没有"工具返回"这个独立 item。)
    """
    for item in _codex_items(s):
        it = item.get("type")
        if role == "user" and it != "UserMessage":
            continue
        if role == "assistant" and it != "AgentMessage":
            continue
        ev = _codex_item_event(item, full=include_all)
        if not ev:
            continue
        if ev[0] == "thinking" and not include_all:
            continue
        yield ev


def _codex_titles() -> dict[str, str]:
    """id → 标题。两个来源,**状态库优先**:

    1. `session_index.jsonl` —— 追加式(同一 id 可能多行,取**最后一行**),覆盖更全
       (实测有 10 个会话在状态库里 name 是空的)。它也含 guardian_review 子线程的标题,
       过滤得靠会话文件里的 thread_source,不能靠这里。
    2. `state_*.sqlite` 的 `threads.name` —— **Codex 真正认的那个**(拿它去
       `codex archive <名字>` 能找到会话,拿 jsonl 里的就找不到)。所以它覆盖前者。

    两边都读是为了稳:状态库读不到(没有 / 被锁 / 将来换了 schema)时还有 jsonl 兜底,
    标题是显示用的,不该因为它让整个命令失败。实测今天 43 个会话两边零冲突。
    """
    out: dict[str, str] = {}
    for rec in iter_records(codex_index_file()):
        sid, name = rec.get("id"), rec.get("thread_name")
        if isinstance(sid, str) and isinstance(name, str) and name.strip():
            out[sid] = flat(name)
    db = codex_state_db()
    if db is not None:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for sid, name in con.execute("select id, name from threads"):
                if isinstance(sid, str) and isinstance(name, str) and name.strip():
                    out[sid] = flat(name)
            con.close()
        except sqlite3.Error:
            pass  # 读不到就只用 jsonl 的
    return out


def _file_start(p: Path):
    for rec in iter_records(p):
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            return ts
    return dt.datetime.min


def _codex_build(sid: str, files: tuple[Path, ...], archived: bool, cwd: str, titles: dict) -> Session:
    s = Session(sid=sid, path=files[0], archived=archived, source="codex", paths=files)
    s.project = flat(cwd)
    s.size = sum(p.stat().st_size for p in files if p.exists())
    s.has_usage = False  # 只有真见到 token_usage_record 才为 True
    for rec in _codex_merged_records(files):
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            if s.start is None or ts < s.start:
                s.start = ts
            if s.end is None or ts > s.end:
                s.end = ts
        t = rec.get("type")
        p = rec.get("payload") or {}
        if t == "token_usage_record":
            _codex_add_usage(s.tokens, p.get("usage") or {})
            s.has_usage = True
        elif t == "turn_context":
            if p.get("model"):
                s.models[p["model"]] += 1
        elif t == "event_msg" and p.get("type") == "item_completed":
            item = p.get("item") or {}
            it = item.get("type")
            if it == "UserMessage":
                txt = _codex_msg_text(item)
                if txt:
                    s.prompts += 1
                    if not s.first_prompt:
                        s.first_prompt = txt
            elif it == "AgentMessage":
                if _codex_msg_text(item):
                    s.replies += 1
            elif it in CODEX_TOOL_ITEMS:
                s.tools[it] += 1
    name = titles.get(sid)
    s.title_explicit = bool(name)
    s.title = flat(name or (truncate(s.first_prompt, 60) if s.first_prompt else "(无标题)"))
    return s


def _discover_codex(include_archived: bool) -> list[Session]:
    d = codex_dir()
    cands: list[tuple[Path, bool]] = []
    live = d / "sessions"
    if live.is_dir():
        cands += [(p, False) for p in sorted(live.rglob("*.jsonl"))]
    arch = d / "archived_sessions"
    if include_archived and arch.is_dir():
        cands += [(p, True) for p in sorted(arch.glob("*.jsonl"))]

    groups: dict[str, list[Path]] = defaultdict(list)
    is_arch: dict[str, bool] = {}
    cwd_of: dict[str, str] = {}
    for path, archived in cands:
        m = _codex_meta(path)
        if not m or m["thread_source"] != "user":  # 排掉 guardian_review 子线程
            continue
        sid = m["sid"]
        groups[sid].append(path)
        is_arch[sid] = is_arch.get(sid, True) and archived  # 只要有一份没归档就算活会话
        if m["cwd"] and sid not in cwd_of:
            cwd_of[sid] = m["cwd"]

    titles = _codex_titles()
    out = []
    for sid, files in groups.items():
        files.sort(key=_file_start)  # path 取最早那份;事件读时再按 timestamp 归并
        out.append(_codex_build(sid, tuple(files), is_arch[sid], cwd_of.get(sid, ""), titles))
    return out


# ---------------------------------------------------------------- 数据源
#
# 每个源负责:发现文件 → 归一化成 Session;按时间产出 (kind, text);以及写操作。
# 命令层只认 Session 和这两个元组流,所以加一个源 = 补一个实现,不用动命令。


def _now_iso_z() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Source:
    key = ""
    label = ""

    def root(self) -> Path:
        """数据根目录(用来判断"这个源有没有用过")。"""
        raise NotImplementedError

    def missing_hint(self) -> str:
        raise NotImplementedError

    def discover(self, include_archived: bool) -> list[Session]:
        raise NotImplementedError

    def events(self, s: Session, full: bool = False):
        raise NotImplementedError

    def units(self, s: Session, include_all: bool = False, role: str | None = None):
        raise NotImplementedError

    def daily_usage(self, s: Session):
        """产出 (日期, tok_in, tok_out) —— 每条带时间戳的记录一条,供 stats --by day。
        非用量记录 tok 记 0,但仍占一条(这样"记录数"这一列和以前一致)。"""
        raise NotImplementedError

    def resume_argv(self, s: Session) -> list[str] | None:
        """续聊要执行的命令;None = 当前环境装不了这个能力(调用方应提示而不是崩)。"""
        raise NotImplementedError

    def delete(self, s: Session, backup: bool) -> str:
        """执行删除(backup=True 时先备份)。返回一句结果描述;失败**抛异常**,调用方据此中止。"""
        raise NotImplementedError

    def set_title(self, s: Session, title: str):
        raise NotImplementedError


class ClaudeSource(Source):
    """Claude Code:一个会话 = 一个 jsonl;归档是我们自己的 ~/claude-archive。"""

    key = "claude"
    label = "Claude"

    def root(self) -> Path:
        return projects_dir()

    def missing_hint(self) -> str:
        return f"找不到 {projects_dir()}(Claude Code 还没用过?)"

    def discover(self, include_archived: bool) -> list[Session]:
        out: list[Session] = []
        pd = projects_dir()
        if pd.is_dir():
            for p in sorted(pd.glob("*/*.jsonl")):
                out.append(load_session(p, archived=False))
        if include_archived:
            for p in archive_files():
                out.append(load_session(p, archived=True))
        return out

    def events(self, s: Session, full: bool = False):
        return _claude_events(s.path, full=full)

    def units(self, s: Session, include_all: bool = False, role: str | None = None):
        return _claude_units(s.path, include_all=include_all, role=role)

    def daily_usage(self, s: Session):
        for rec in iter_records(s.path):
            ts = parse_ts(rec.get("timestamp"))
            if not ts:
                continue
            if rec.get("type") == "assistant":
                u = (rec.get("message") or {}).get("usage") or {}
                yield ts.date(), (u.get("input_tokens") or 0), (u.get("output_tokens") or 0)
            else:
                yield ts.date(), 0, 0

    def resume_argv(self, s: Session) -> list[str] | None:
        return ["claude", "--resume", s.sid]

    def delete(self, s: Session, backup: bool) -> str:
        dest = None
        if backup:
            try:
                dest = backup_session(s)  # 先备份成功再删;失败即抛,绝不会删未备份的
            except Exception as e:
                raise RuntimeError(f"备份失败,已中止(不会删未备份的):{e}") from e
        try:
            s.path.unlink()
        except OSError as e:
            raise RuntimeError(f"删除失败:{s.path}  {e}" + (f"(备份已在 {dest})" if dest else "")) from e
        return f"(备份 → {dest})" if dest else "(未备份)"

    def set_title(self, s: Session, title: str):
        """向 jsonl 追加一条 custom-title(与 Claude Code 自身改名同格式)。"""
        rec = {"type": "custom-title", "customTitle": title, "sessionId": s.sid}
        with open(s.path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":  # 保证追加不会挤到上一行
                    f.write(b"\n")
            f.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))


class CodexSource(Source):
    """Codex:一个会话可能横跨多个文件;归档/删除用它自带的 codex archive / delete,
    所以 ~/claude-archive 那一套完全不参与。"""

    key = "codex"
    label = "Codex"

    def root(self) -> Path:
        return codex_dir() / "sessions"

    def missing_hint(self) -> str:
        return f"找不到 {self.root()}(Codex 还没用过?)"

    def discover(self, include_archived: bool) -> list[Session]:
        return _discover_codex(include_archived)

    def events(self, s: Session, full: bool = False):
        return _codex_events(s, full=full)

    def units(self, s: Session, include_all: bool = False, role: str | None = None):
        return _codex_units(s, include_all=include_all, role=role)

    def daily_usage(self, s: Session):
        for rec in _codex_merged_records(session_files(s)):
            ts = parse_ts(rec.get("timestamp"))
            if not ts:
                continue
            if rec.get("type") == "token_usage_record":
                u = (rec.get("payload") or {}).get("usage") or {}
                cached = u.get("cached_input_tokens") or 0
                yield ts.date(), max(0, (u.get("input_tokens") or 0) - cached), (u.get("output_tokens") or 0)
            else:
                yield ts.date(), 0, 0

    def resume_argv(self, s: Session) -> list[str] | None:
        b = codex_bin()
        return [b, "resume", s.sid] if b else None

    def delete(self, s: Session, backup: bool) -> str:
        """backup=True → codex archive(挪进它自己的归档,可 unarchive 取回);
        backup=False → codex delete(永久删)。"""
        cmd = "archive" if backup else "delete"
        r = subprocess.run([self._bin(), cmd, s.sid], capture_output=True, text=True)
        if r.returncode != 0:
            lines = [ln for ln in (r.stderr or r.stdout or "").strip().splitlines() if ln.strip()]
            raise RuntimeError(lines[-1] if lines else f"codex {cmd} 退出码 {r.returncode}")
        return "(已归档,codex unarchive 可取回)" if backup else "(codex 永久删除,不可恢复)"

    def set_title(self, s: Session, title: str):
        """改 Codex 的标题。

        真身是 `state_*.sqlite` 的 `threads.name` —— 只往 `session_index.jsonl` 追加是
        **没用**的:实测拿那个名字去 `codex archive <名字>` 报 "No active session found",
        拿状态库里的名字就能找到。所以先改状态库(它失败就算失败),再补一条 jsonl 镜像
        (Codex 自己改名也是两处都写;镜像失败不影响改名本身)。

        刻意**不动** `updated_at` / `recency_at`:那会把会话顶到 Codex 侧边栏最前面 ——
        改个名字不该改变它的时间位置。
        """
        db = codex_state_db()
        if db is None:
            raise RuntimeError(f"找不到 Codex 的状态库({codex_dir()}/state_*.sqlite)")
        try:
            con = sqlite3.connect(db, timeout=5)  # 应用在跑,可能要等锁
            try:
                with con:
                    cur = con.execute("update threads set name=? where id=?", (title, s.sid))
                    n = cur.rowcount
            finally:
                con.close()
        except sqlite3.Error as e:
            raise RuntimeError(f"写 Codex 状态库失败:{e}(是不是 Codex 正开着?稍后再试)") from e
        if not n:
            raise RuntimeError(f"状态库里没有这个会话:{s.sid}")

        rec = {"id": s.sid, "thread_name": title, "updated_at": _now_iso_z()}
        p = codex_index_file()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass  # 镜像写不进去不影响改名(真身在状态库)

    def _bin(self) -> str:
        b = codex_bin()
        if not b:
            die("找不到 codex 命令 —— 删 / 改名 / 续聊要用它(只读记录不需要)")
        return b


SOURCES: dict[str, Source] = {s.key: s for s in (ClaudeSource(), CodexSource())}
ALL_SOURCES = list(SOURCES)

#: 靠"自己被叫什么名字"决定默认管**哪些**源(busybox 那招)。
#:   cgr = 全部(这才是工具最初的场景:"我上次在哪聊过 X",不该搜两遍)
#:   cla / cod = 只看那一个
#: 以后加新 agent:往这里加一行 + 建个软链即可。
#: chatmgr.py 是 run_picker 自己调自己时的 argv[0],也归"全部";
#: picker 会显式把 -S 传下去,所以子进程不会飘。
SOURCES_BY_PROG = {
    "cgr": ALL_SOURCES,
    "chatmgr": ALL_SOURCES,
    "chatmgr.py": ALL_SOURCES,
    "cla": ["claude"],
    "cod": ["codex"],
}


def default_source_keys() -> list[str]:
    return list(SOURCES_BY_PROG.get(os.path.basename(sys.argv[0]), ALL_SOURCES))


def _split_sources(vals) -> list[str]:
    out: list[str] = []
    for v in vals or []:
        out += [x.strip() for x in str(v).split(",") if x.strip()]
    return out


def selected_source_keys(args) -> list[str]:
    """本次要管哪些源。-S 覆盖命令名的默认,可重复或逗号分隔。去重且保持顺序。"""
    keys = _split_sources(getattr(args, "source", None)) or default_source_keys()
    bad = [k for k in keys if k not in SOURCES]
    if bad:
        die(f"不认识的数据源:{', '.join(bad)}(可用:" + " / ".join(ALL_SOURCES) + ")")
    seen: set[str] = set()
    uniq: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def selected_sources(args) -> list[Source]:
    return [SOURCES[k] for k in selected_source_keys(args)]


def iter_events(s: Session, full: bool = False):
    return SOURCES[s.source].events(s, full=full)


def search_units(s: Session, include_all: bool = False, role: str | None = None):
    return SOURCES[s.source].units(s, include_all=include_all, role=role)


def _src(s: Session) -> Source:
    return SOURCES[s.source]


#: 来源在列表里的颜色(和 show 里的 [Claude] / [Codex] 标签呼应)
SOURCE_COLORS = {"claude": "1;34", "codex": "1;35"}


def source_tag(s: Session, on: bool) -> str:
    """多源混排时给每行标出来源 —— 否则看不出这行是哪个 agent 的。"""
    return paint(_src(s).label, SOURCE_COLORS.get(s.source, "1;36"), on)


def mixed(sessions) -> bool:
    """列出来的会话真的横跨多个源,才值得加"源"那一列。

    按**内容**而不是按选中的源判断:只装了 Claude 的人跑 `cgr list` 不该平白多一列
    "Claude"。单源时输出和以前逐字节一致。
    """
    return len({s.source for s in sessions}) > 1


# ---------------------------------------------------------------- 归档层


def _manifest_path() -> Path:
    return archive_dir() / "manifest.json"


def load_manifest() -> list[dict]:
    p = _manifest_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_manifest(entries: list[dict]):
    d = archive_dir()
    d.mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def backup_session(s: Session) -> Path:
    """复制到 <归档>/<项目>/<id>.jsonl 并更新 manifest。失败抛异常(调用方据此中止)。"""
    dest_dir = archive_dir() / _safe_name(s.short_project)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{s.sid}.jsonl"
    shutil.copy2(s.path, dest)
    entries = [e for e in load_manifest() if e.get("id") != s.sid or e.get("source") != str(s.path)]
    entries.append(
        {
            "id": s.sid,
            "title": s.title,
            "project": s.project,
            "source": str(s.path),
            "backed_up_at": dt.datetime.now().isoformat(timespec="seconds"),
            "size": dest.stat().st_size,
        }
    )
    save_manifest(entries)
    return dest


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name) or "unknown"


# ---------------------------------------------------------------- 渲染


def render_session(s: Session, full: bool, on: bool, proj_color: str = "36") -> str:
    src = _src(s)
    out = []
    head = f"=== {s.title} ==="
    if s.archived:
        head += "  [已归档]"
    out.append(paint(head, "1;36", on))
    span = f"{fmt_time(s.start)} ~ {fmt_time(s.end)}"
    out.append(f"项目: {paint(s.project, proj_color, on)}")
    out.append(f"时间: {span}    会话: {s.sid}")
    toks = s.tokens
    if not s.has_usage:
        tok_line = "未记录(这版没写 token)"  # 旧版 Codex 不记用量;显示 0 会像坏了
    else:
        # 只列非 0 的类型:某些代理不报 cache_creation / thinking,恒为 0 会像"坏了"
        tok_parts = [f"in {human_tokens(toks['input'])}", f"out {human_tokens(toks['output'])}"]
        if toks["cache_read"]:
            tok_parts.append(f"cache_r {human_tokens(toks['cache_read'])}")
        if toks["cache_creation"]:
            tok_parts.append(f"cache_w {human_tokens(toks['cache_creation'])}")
        if toks["thinking"]:
            tok_parts.append(f"thinking {human_tokens(toks['thinking'])}")
        tok_line = " / ".join(tok_parts)
    out.append(f"消息: {s.prompts} 问 / {s.replies} 答    token: {tok_line}")
    if s.models:
        out.append("模型: " + ", ".join(f"{m}({c})" for m, c in s.models.most_common()))
    out.append("")
    for kind, text in iter_events(s, full=full):
        if kind == "user":
            out.append(paint("[你]", "1;32", on))
            out.append(text.rstrip())
        elif kind == "assistant":
            out.append(paint(f"[{src.label}]", "1;34", on))
            out.append(text.rstrip())
        elif kind == "tool":
            out.append(paint(f"  · {text}", "2", on))
        elif kind == "tool_result":
            out.append(paint("  · 工具返回:", "2", on))
            out.append(paint(_indent(text.rstrip()), "2", on))
        elif kind == "thinking":
            out.append(paint("  · 思考:", "2", on))
            out.append(paint(_indent(text.rstrip()), "2", on))
        out.append("")
    argv = src.resume_argv(s)
    if argv:
        # 显示用短名(codex 可能藏在 app 包里,完整路径印出来太长);执行仍用 argv
        out.append(paint(f"→ 继续此对话: {src.key} {' '.join(argv[1:])}", "2", on))
    return "\n".join(out)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + ln for ln in text.splitlines())


def export_markdown(s: Session, full: bool) -> str:
    out = [f"# {s.title}", ""]
    out.append(f"- 会话: `{s.sid}`")
    out.append(f"- 项目: `{s.project}`")
    out.append(f"- 时间: {fmt_time(s.start)} ~ {fmt_time(s.end)}")
    out.append(f"- 消息: {s.prompts} 问 / {s.replies} 答")
    if s.models:
        out.append("- 模型: " + ", ".join(m for m, _ in s.models.most_common()))
    out.append("")
    out.append("---")
    out.append("")
    for kind, text in iter_events(s, full=full):
        if kind == "user":
            out += ["## 你", "", text.rstrip(), ""]
        elif kind == "assistant":
            out += [f"## {_src(s).label}", "", text.rstrip(), ""]
        elif kind == "tool":
            out += [f"> `{text}`", ""]
        elif kind == "tool_result":
            out += ["> **工具返回**", "", "```", text.rstrip(), "```", ""]
        elif kind == "thinking":
            out += ["<details><summary>思考</summary>", "", text.rstrip(), "", "</details>", ""]
    return "\n".join(out)


def export_plain(s: Session, full: bool) -> str:
    out = [f"=== {s.title} ===", f"项目: {s.project}", f"时间: {fmt_time(s.start)} ~ {fmt_time(s.end)}", ""]
    for kind, text in iter_events(s, full=full):
        label = {"user": "[你]", "assistant": f"[{_src(s).label}]", "tool": "  · ", "tool_result": "  · 工具返回:", "thinking": "  · 思考:"}[kind]
        out.append(label)
        out.append(text.rstrip())
        out.append("")
    return "\n".join(out)


def export_json(s: Session, full: bool) -> str:
    msgs = [{"kind": k, "text": t} for k, t in iter_events(s, full=full)]
    return json.dumps(
        {
            "id": s.sid,
            "title": s.title,
            "project": s.project,
            "start": s.start.isoformat() if s.start else None,
            "end": s.end.isoformat() if s.end else None,
            "tokens": s.tokens,
            "models": dict(s.models),
            "tools": dict(s.tools),
            "messages": msgs,
        },
        ensure_ascii=False,
        indent=2,
    )


def sess_to_dict(s: Session) -> dict:
    return {
        "id": s.sid,
        "title": s.title,
        "project": s.project,
        "archived": s.archived,
        "start": s.start.isoformat() if s.start else None,
        "end": s.end.isoformat() if s.end else None,
        "size": s.size,
        "prompts": s.prompts,
        "replies": s.replies,
        "tokens": s.tokens,
        "models": dict(s.models),
        "tools": dict(s.tools),
        "path": str(s.path),
    }


# ---------------------------------------------------------------- 表格


def print_table(rows: list[list[str]], max_width: int | None = None):
    widths = [max(dwidth(r[i]) for r in rows) for i in range(len(rows[0]))]
    gaps = [2] * (len(widths) - 1)
    if max_width is not None:
        # 塞不下就先把列间距由 2 收到 1(最多省 6~7 列),优先保证不折行 —— 不缩列内容,标题长度不受影响
        while sum(widths) + sum(gaps) > max_width and any(g == 2 for g in gaps):
            gaps[gaps.index(2)] = 1
    for r in rows:
        out = []
        for i, c in enumerate(r):
            out.append(pad(c, widths[i]))
            if i < len(gaps):
                out.append(" " * gaps[i])
        print("".join(out).rstrip())


# ---------------------------------------------------------------- 子命令


def cmd_list(args, store: SessionStore):
    sessions = apply_filters(store.sessions(), args)
    if args.limit:
        sessions = sessions[: args.limit]
    if args.json:
        print(json.dumps([sess_to_dict(s) for s in sessions], ensure_ascii=False, indent=2))
        return
    if not sessions:
        print("没有匹配的会话")
        return
    idx = store.index_map()
    on = color_enabled()
    pcolors = project_colors(store.sessions())
    multi = mixed(sessions)  # 混排时才加"源"列;单源输出保持和以前逐字节一致
    rows = [["#", "时间"] + (["源"] if multi else []) + ["项目", "标题", "消息", "大小", "tok"]]
    for s in sessions:
        title = s.title + ("  [已归档]" if s.archived else "")
        row = [
            paint(str(idx.get(str(s.path), "?")), "1;33", on),  # 序号高亮,便于复制去 show/rm
            fmt_time(s.end or s.start),
        ]
        if multi:
            row.append(source_tag(s, on))
        row += [
            paint(truncate(s.short_project, 22), pcolors.get(s.short_project, "36"), on),
            truncate(title, 34),
            f"{s.prompts}+{s.replies}",
            human_size(s.size),
            human_tokens(s.tok_io) if s.has_usage else "—",
        ]
        rows.append(row)
    print_table(rows, shutil.get_terminal_size((120, 24)).columns)
    print(f"\n{len(sessions)} 个会话   (tok = input+output)")
    if store.duplicates:
        print(paint(
            f"⚠ 有 {store.duplicates} 份重复会话(同一 id 落在多个项目文件夹,内容不一致)"
            f",已只保留最新的一份", "2", on))
    if sys.stdout.isatty():  # 只在人看的终端里提示;管道/脚本不打扰
        print(paint(f"提示:要筛选 / 打开 / 改名 / 删 → {prog_name()} f", "2", on))


def cmd_prompts(args, store: SessionStore):
    """列出 / 搜索你自己的提问流水(~/.claude/history.jsonl)。

    和 search 的分工:search 搜「会话内容」(含 Claude 回复、工具调用);prompts 只看
    「我问过什么」—— 数据源独立,所以 jsonl 已被删的会话,它的提问仍在这里能翻到。
    """
    if not store.has("claude"):
        print(f"{' / '.join(s.label for s in store.sources)} 没有「提问流水」这种东西 —— "
              f"标题看它的标题索引,完整提问用 {prog_name()} list / {prog_name()} search。")
        return
    if not history_file().is_file():
        die(f"找不到 {history_file()}(还没有提问流水)")
    prompts = load_prompts()
    projs = _project_filters(args)
    if projs:
        prompts = [p for p in prompts
                   if any(q in p["project"].lower() or q in Path(p["project"]).name.lower()
                          for q in projs)]
    since = parse_when(getattr(args, "since", None))
    until = parse_when(getattr(args, "until", None), end_of_day=True)
    if since:
        prompts = [p for p in prompts if p["ts"] and p["ts"] >= since]
    if until:
        prompts = [p for p in prompts if p["ts"] and p["ts"] <= until]
    pat = None
    if args.query:
        pat = re.compile(re.escape(args.query), re.IGNORECASE if args.ignore_case else 0)
        prompts = [p for p in prompts if pat.search(p["text"])]
    prompts.sort(key=lambda p: p["ts"] or dt.datetime.min, reverse=True)  # 新的在前
    if args.limit:
        prompts = prompts[: args.limit]
    if args.json:
        print(json.dumps(
            [{"text": p["text"], "time": p["ts"].isoformat() if p["ts"] else None,
              "project": p["project"], "session": p["session"]} for p in prompts],
            ensure_ascii=False, indent=2))
        return
    if not prompts:
        print("没有匹配的提问")
        return
    on = color_enabled()
    pcolors = project_colors(store.sessions())  # 复用会话配色,各命令下同一项目颜色一致
    rows = [["时间", "项目", "提问"]]
    for p in prompts:
        short = Path(p["project"]).name or p["project"] or "?"
        rows.append([
            fmt_time(p["ts"]),
            paint(truncate(short, 22), pcolors.get(short, "36"), on),
            _highlight(" ".join(p["text"].split()), pat, on),
        ])
    print_table(rows, shutil.get_terminal_size((120, 24)).columns)
    print(f"\n{len(prompts)} 条提问")


def kind_label(kind: str, src: Source) -> str:
    """命中来源的显示名。assistant 用**那个会话自己的**源名 —— 在 cod 里把 Codex 的
    回复标成 "Claude" 是错的;cgr 混排时更是每行都得看它属于哪个源。"""
    return src.label if kind == "assistant" else KIND_LABEL.get(kind, kind)


def cmd_search(args, store: SessionStore):
    sessions = apply_filters(store.sessions(), args)
    if args.limit:
        sessions = sessions[: args.limit]
    flags = re.IGNORECASE if args.ignore_case else 0
    if args.regex:
        try:
            pat = re.compile(args.query, flags)
        except re.error as e:
            die(f"正则错误:{e}")
    else:
        pat = re.compile(re.escape(args.query), flags)

    ctx = args.context
    on = color_enabled()
    pcolors = project_colors(store.sessions())
    idx = store.index_map()
    cap = 10**9 if (args.grep or args.max_hits == 0) else args.max_hits  # 0 = 不限

    # 拼一条"看全部"的完整命令(含当前查询和筛选),提示里直接给可复制的整条
    q = [prog_name(), "search"]
    if args.regex:
        q.append("--regex")
    if args.role:
        q += ["--role", args.role]
    if args.all:
        q.append("--all")
    if not args.ignore_case:
        q.append("-c")
    if args.context != 60:
        q += ["--context", str(args.context)]
    for p in (args.project or []):
        q += ["-p", shlex.quote(p)]
    if args.since:
        q += ["-s", shlex.quote(args.since)]
    if args.until:
        q += ["-u", shlex.quote(args.until)]
    q.append(shlex.quote(args.query))
    all_cmd = " ".join([*q, "--max-hits", "0"])

    title_hits = [s for s in sessions if pat.search(s.title)]  # 标题里命中
    content_hits = []  # (session, 真实命中数, 用于展示的片段)
    for s in sessions:
        count = 0
        snippets = []
        for kind, text in search_units(s, include_all=args.all, role=args.role):
            for m in pat.finditer(text):
                count += 1
                if len(snippets) < cap:
                    snippets.append((kind, _snippet(text, m, ctx, pat, on)))
        if count:
            content_hits.append((s, count, snippets))

    if args.pick:  # 搜完直接进 fzf 挑一个打开 / 续聊
        seen: set[str] = set()
        matched: list[Session] = []
        for s in (*title_hits, *(s for s, _, _ in content_hits)):
            if s.sid not in seen:
                seen.add(s.sid)
                matched.append(s)
        if not matched:
            print("没有命中")
            return
        run_picker(store, matched, archived=args.archived)
        return

    if args.json:
        print(json.dumps(
            {
                "query": args.query,
                "title_matches": [sess_to_dict(s) for s in title_hits],
                "content_matches": [
                    {"session": sess_to_dict(s), "count": c,
                     "hits": [{"kind": k, "text": t} for k, t in sn]}
                    for s, c, sn in content_hits
                ],
            },
            ensure_ascii=False, indent=2,
        ))
        return
    if not title_hits and not content_hits:
        print("没有命中")
        return

    def sidx(s: Session) -> str:
        return idx.get(str(s.path), "?")

    mixed_hits = mixed([s for s in title_hits] + [s for s, _, _ in content_hits])

    def meta(s: Session) -> str:
        tag = " [已归档]" if s.archived else ""
        prefix = f"{_src(s).label} · " if mixed_hits else ""
        return paint(
            f"{prefix}{s.short_project} · {fmt_time(s.end or s.start)} · {s.prompts} 问 / {s.replies} 答{tag}",
            pcolors.get(s.short_project, "36"), on)

    if args.grep:  # 平铺:每行带标题,一眼看出是哪次对话里的
        for s, _, sn in content_hits:
            for kind, text in sn:
                print(f"{_highlight(s.title, pat, on)} › {kind_label(kind, _src(s))}: {text}")
        if title_hits:
            print(paint(f"— 另有 {len(title_hits)} 个会话是【标题】命中 —", "2", on))
        total = sum(c for _, c, _ in content_hits)
        print(f"{total} 处内容命中,分布在 {len(content_hits)} 个会话")
        return

    if title_hits:
        print(paint(f"【标题命中】{len(title_hits)} 个会话", "1;36", on))
        for s in title_hits:
            print(f"  [{sidx(s)}] {_highlight(s.title, pat, on)}")
            print(f"      {meta(s)}")
            print(paint(f"      → 打开 {prog_name()} {sidx(s)} · 续聊 {prog_name()} {sidx(s)} -r", "2", on))
        print()
    if content_hits:
        total = sum(c for _, c, _ in content_hits)
        print(paint(f"【内容命中】{len(content_hits)} 个会话,{total} 处", "1;36", on))
        for s, c, sn in content_hits:
            print(f"  [{sidx(s)}] {_highlight(s.title, pat, on)}  ({c} 处)")  # 标明是哪次对话里的内容
            print(f"      {meta(s)}")
            for kind, text in sn:
                print(f"      {paint(kind_label(kind, _src(s)), '2', on)}: {text}")
            if c > len(sn):
                print(paint(f"      … 另有 {c - len(sn)} 处。全看:{all_cmd}", "2", on))
            print(paint(f"      → 打开 {prog_name()} {sidx(s)} · 续聊 {prog_name()} {sidx(s)} -r", "2", on))
            print()


def _snippet(text: str, m, ctx: int, pat=None, on: bool = False) -> str:
    a = max(0, m.start() - ctx)
    b = min(len(text), m.end() + ctx)
    pre = "…" if a > 0 else ""
    post = "…" if b < len(text) else ""
    out = pre + " ".join(text[a:b].split()) + post
    if on and pat is not None:
        mm = pat.search(out)
        if mm and mm.end() > mm.start():
            out = out[: mm.start()] + paint(out[mm.start(): mm.end()], "1;31", True) + out[mm.end():]
    return out


def _highlight(text: str, pat, on: bool) -> str:
    """把 text 里所有匹配 pat 的地方上色(用于标题里的命中词)。"""
    if not on or pat is None or not pat.pattern:
        return text
    out, last = [], 0
    for m in pat.finditer(text):
        if m.end() == m.start():
            continue
        out += [text[last:m.start()], paint(text[m.start():m.end()], "1;31", on)]
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _resume_session(s: Session):
    src = _src(s)
    argv = src.resume_argv(s)
    if not argv:
        die(f"{src.label} 的续聊在这台机器上不可用 —— 装不了对应的命令行工具")
    cwd = s.project if os.path.isdir(s.project) else None
    print(f"→ {src.key} {' '.join(argv[1:])}  (cwd: {cwd or '未找到,用当前目录'})")
    try:
        subprocess.run(argv, cwd=cwd)
    except FileNotFoundError:
        die(f"找不到 {argv[0]} 命令")


def cmd_show(args, store: SessionStore):
    s = resolve(args.ref, store)
    if args.resume:
        _resume_session(s)
        return
    if args.json:
        print(export_json(s, args.full))
        return
    on = color_enabled()
    pcolors = project_colors(store.sessions())
    emit(render_session(s, args.full, on, pcolors.get(s.short_project, "36")) + "\n", args.pager)
    # 读完顺手问一句要不要续聊(仅交互终端)
    if not args.pager and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            ans = input("\n要续聊这个会话吗?[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if ans in ("y", "yes"):
            _resume_session(s)


def pick_sessions(store: SessionStore, args) -> list[Session]:
    """pick 列出的会话(套用全局筛选 + limit)。"""
    sessions = apply_filters(store.sessions(), args)
    if getattr(args, "limit", None):
        sessions = sessions[: args.limit]
    return sessions


def session_lines(store: SessionStore, sessions: list[Session]) -> list[str]:
    """pick / _picklines 共用:生成 fzf 的列表行(第 1 列显示,末列藏序号)。"""
    idx = store.index_map()
    on = color_enabled()
    pcolors = project_colors(store.sessions())
    multi = mixed(sessions)
    lines = []
    for s in sessions:
        tag = " [已归档]" if s.archived else ""
        head = (f"{_src(s).label} " if multi else "") + s.title + tag
        display = "  ".join(
            [
                pad(truncate(head, 32), 32),  # 标题放最前,最能认出是哪次对话
                paint(pad(truncate(s.short_project, 18), 18), pcolors.get(s.short_project, "36"), on),
                paint(f"{s.prompts}+{s.replies}", "2", on),
                paint(fmt_time(s.end or s.start), "2", on),  # 时间往后放
            ]
        )
        lines.append(display + "\t" + str(idx.get(str(s.path), "?")))
    return lines


def cmd_picklines(args, store: SessionStore):
    """内部用:给 fzf 的 reload 重新生成列表行。"""
    print("\n".join(session_lines(store, pick_sessions(store, args))))


def _filter_args(args) -> list[str]:
    """把当前的筛选选项拼回命令行,供 pick 的 reload 复用。"""
    out: list[str] = []
    for k in getattr(args, "source", None) or []:  # 带上源,否则 reload 会跳回默认源
        out += ["-S", k]
    for p in getattr(args, "project", None) or []:
        out += ["-p", p]
    if getattr(args, "since", None):
        out += ["-s", args.since]
    if getattr(args, "until", None):
        out += ["-u", args.until]
    if getattr(args, "limit", None):
        out += ["-l", str(args.limit)]
    if getattr(args, "archived", False):
        out.append("-a")
    return out


def run_picker(store: SessionStore, sessions: list[Session], prefill: str = "",
               reload_cmd: str | None = None, archived: bool = False):
    """fzf 交互:Enter 打开 / Tab 多选 / Ctrl-R 续聊 / Ctrl-E 改名 / Ctrl-D 删除。"""
    if not sys.stdin.isatty():
        die("需要在交互终端下运行(fzf 需要 tty);非交互场景请用 list / search")
    lines = session_lines(store, sessions)
    if not lines:
        print("没有会话")
        return
    fzf = shutil.which("fzf")
    if not fzf:
        die("没装 fzf —— brew install fzf")
    exe, script = sys.executable, os.path.abspath(__file__)
    # 委托给自己起的新进程:必须带上源,否则它按 argv[0](chatmgr.py)回到默认源,
    # 序号会张冠李戴 —— 打开/删除的可能是另一个源的会话。
    sflag = [x for src in store.sources for x in ("-S", src.key)]
    extra = [*sflag] + (["-a"] if archived else [])  # -a 也要带,否则序号对不上
    sq = " ".join(shlex.quote(x) for x in sflag)
    selfq = f"{shlex.quote(exe)} {shlex.quote(script)}"
    preview = f"{selfq} show {{2}} {sq}"
    if color_enabled():
        preview += " --color"  # 预览在 fzf 里不是 TTY,得强制上色,否则和你直接跑 cgr 不一样
    argv = [
        fzf,
        "--ansi",
        "--multi",  # Tab / Shift-Tab 多选
        "--layout=reverse",  # 列表从顶部往下排,别贴底留大片空白
        "--prompt=搜索 > ",  # 左上角那个框是模糊过滤用的,标清楚
        "--delimiter=\t",
        "--with-nth=1",
        "--nth=1",
        "--preview", preview,
        "--preview-window=right:60%:wrap",
        "--expect=ctrl-r,ctrl-d",
        "--height=~90%",  # 自适应:条目少时窗口自动缩,不留空白
    ]
    if reload_cmd:  # 能重生成列表才给 Ctrl-E 改名(改名后 reload 刷新)
        argv += ["--bind", f"ctrl-e:execute({selfq} rename {{2}} {sq})+reload({reload_cmd})",
                 "--header=Enter 打开 · Tab 多选 · Ctrl-R 续聊 · Ctrl-E 改名 · Ctrl-D 删除(预览)"]
    else:
        argv += ["--header=Enter 打开 · Tab 多选 · Ctrl-R 续聊 · Ctrl-D 删除(预览)"]
    if prefill:
        argv += ["--query", prefill]

    sub = subprocess.run(argv, input="\n".join(lines), text=True, capture_output=True)
    if sub.returncode != 0 or not sub.stdout.strip():
        return
    rows = sub.stdout.splitlines()
    key = rows[0] if rows else ""
    refs = [r.rsplit("\t", 1)[-1].strip() for r in rows[1:] if r.strip()]
    if not refs:
        return
    if key == "ctrl-d":  # 选中的(可多个)全部进入删除预览
        subprocess.run([exe, script, "rm", *refs, *extra])
    elif key == "ctrl-r":  # 续聊只对第一个
        subprocess.run([exe, script, "show", refs[0], "-r", *extra])
    elif len(refs) == 1:
        subprocess.run([exe, script, "show", refs[0], *extra])
    else:  # 多个:依次打开
        for r in refs:
            subprocess.run([exe, script, "show", r, *extra])


def cmd_pick(args, store: SessionStore):
    """fzf 交互选择(可给初筛词):cgr f [关键词]"""
    selfq = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))}"
    reload_cmd = " ".join([selfq, "_picklines", *[shlex.quote(x) for x in _filter_args(args)]])
    run_picker(store, pick_sessions(store, args), prefill=args.query or "",
               reload_cmd=reload_cmd, archived=args.archived)


def cmd_export(args, store: SessionStore):
    s = resolve(args.ref, store)
    if args.format == "md":
        text = export_markdown(s, args.full)
    elif args.format == "json":
        text = export_json(s, args.full)
    else:
        text = export_plain(s, args.full)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}  ({human_size(len(text.encode('utf-8')))})")
    else:
        emit(text + "\n", args.pager)


def cmd_stats(args, store: SessionStore):
    sessions = apply_filters(store.sessions(), args)
    if not sessions:
        print("没有匹配的会话")
        return
    total_tokens = empty_tokens()
    models: Counter = Counter()
    tools: Counter = Counter()
    for s in sessions:
        for k in TOKEN_KEYS:
            total_tokens[k] += s.tokens[k]
        models.update(s.models)
        tools.update(s.tools)

    if args.json:
        print(json.dumps(
            {
                "sessions": len(sessions),
                "prompts": sum(s.prompts for s in sessions),
                "replies": sum(s.replies for s in sessions),
                "tokens": total_tokens,
                "models": dict(models),
                "tools": dict(tools),
            },
            ensure_ascii=False, indent=2,
        ))
        return

    if args.by == "model":
        rows = [["模型", "次数"]] + [[m, str(c)] for m, c in models.most_common()]
        print_table(rows)
        return
    if args.by == "tool":
        rows = [["工具", "次数"]] + [[t, str(c)] for t, c in tools.most_common()]
        print_table(rows)
        return
    if args.by == "project":
        agg = defaultdict(lambda: {"n": 0, "tok": 0})
        for s in sessions:
            a = agg[s.short_project]
            a["n"] += 1
            a["tok"] += s.tok_io
        rows = [["项目", "会话", "tok(in+out)"]]
        for name, a in sorted(agg.items(), key=lambda kv: -kv[1]["tok"]):
            rows.append([name, str(a["n"]), human_tokens(a["tok"])])
        print_table(rows)
        return
    if args.by == "day":
        days = defaultdict(lambda: {"n": 0, "tok": 0})
        for s in sessions:
            for d, tin, tout in _src(s).daily_usage(s):
                day = d.isoformat()
                days[day]["n"] += 1
                days[day]["tok"] += tin + tout
        rows = [["日期", "记录数", "tok(in+out)"]]
        for d in sorted(days):
            rows.append([d, str(days[d]["n"]), human_tokens(days[d]["tok"])])
        print_table(rows)
        return

    # 总览
    on = color_enabled()
    span_start = min((s.start for s in sessions if s.start), default=None)
    span_end = max((s.end for s in sessions if s.end), default=None)
    print(f"会话: {len(sessions)}    消息: {sum(s.prompts for s in sessions)} 问 / "
          f"{sum(s.replies for s in sessions)} 答")
    print(f"时间跨度: {fmt_time(span_start)} ~ {fmt_time(span_end)}")
    print()
    rows = [["token 类型", "合计"]]
    for k in TOKEN_KEYS:
        if total_tokens[k]:  # 为 0 的类型不占行(代理不报 cache_creation / thinking 时免得像坏了)
            rows.append([k, human_tokens(total_tokens[k])])
    rows.append(["input+output", human_tokens(total_tokens["input"] + total_tokens["output"])])
    print_table(rows)
    zero = [k for k in TOKEN_KEYS if not total_tokens[k]]
    if zero:
        print(paint("(为 0 未列出:" + " / ".join(zero) + ")", "2", on))
    if total_tokens["cache_read"] > total_tokens["input"] + total_tokens["output"]:
        print(paint("cache_read 是每轮重复回读的缓存上下文,累加后远大于 input/output,不等于新增用量。", "2", on))
    print()
    print("模型: " + (", ".join(f"{m}({c})" for m, c in models.most_common()) or "—"))
    print("工具: " + (", ".join(f"{t}({c})" for t, c in tools.most_common(10)) or "—"))
    print("\n细看: stats --by day|project|model|tool")


def cmd_rm(args, store: SessionStore):
    if args.refs:
        targets = [resolve(r, store) for r in args.refs]
    else:
        if not _project_filters(args):
            die("没指定要删什么:给会话(id 前缀/序号/标题),或用 -p 指定要清空的项目")
        targets = apply_filters(store.sessions(), args)
    if not targets:
        die("没有匹配的会话")
    on = color_enabled()
    print(paint(f"将删除 {len(targets)} 个会话:", "1;33", on))
    for s in targets:
        print(f"  [{s.sid[:8]}] {s.title}")
        print(f"      {s.project}    {fmt_time(s.end or s.start)}    {human_size(s.size)}")
        if s.first_prompt:
            print(paint(f"      首句: {truncate(flat(s.first_prompt), 70)}", "2", on))
        if s.archived:
            print(paint("      ⚠ 这是备份文件本身,不是活会话", "1;31", on))

    backup = not args.force  # -f = 直接删(不备份);交互下再由 y / n 决定
    if not args.force:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(paint("\n非交互终端,未删除。要删加 -f。", "1;33", on))
            return
        choice = _ask_delete(on)
        if choice == "cancel":
            print(paint("已取消,什么都没删。", "2", on))
            return
        backup = choice == "backup"

    done = []
    for s in targets:
        try:
            note = _src(s).delete(s, backup)
        except Exception as e:
            print(paint(f"\n已中止:{s.sid}\n  {e}", "1;31", on))
            if done:
                print("已完成:" + ", ".join(done))
            sys.exit(1)
        done.append(s.sid)
        print(f"  已删 {s.sid[:8]}  {note}")

    print(f"\n完成:{len(done)} 个会话已删除。")
    if backup:
        if any(s.source == "codex" for s in targets):
            print(paint("已归档进 Codex 自己的归档目录。要取回:codex unarchive <id>", "2", on))
        else:
            print(paint(f"备份留在 {archive_dir()}(当保险)。要一次清掉全部备份:", "2", on))
            print(paint("  cgr purge -f", "2", on))
    else:
        print(paint("⚠ 这次没备份,删了不可恢复。", "1;33", on))


def cmd_purge(args, store: SessionStore):
    if not store.has("claude"):
        print("Codex 的归档由它自己管(codex archive / delete),这里没有本工具存的备份可清。")
        print("要取回已归档的会话:codex unarchive <id>")
        return
    ad = archive_dir()
    if not ad.is_dir():
        print(f"归档目录不存在:{ad}")
        return
    entries = load_manifest()
    by_id = {e.get("id"): e for e in entries}

    targets: list[Path] = []
    if args.refs:
        for r in args.refs:
            matches = [p for p in archive_files() if p.stem.startswith(r)]
            if not matches:
                die(f"归档里找不到:{r}")
            targets += matches
    else:
        targets = archive_files()

    if args.older_than is not None:
        cutoff = dt.datetime.now() - dt.timedelta(days=args.older_than)
        keep = []
        for p in targets:
            e = by_id.get(p.stem)
            when = None
            if e and e.get("backed_up_at"):
                when = parse_ts(e["backed_up_at"])
            if when is None:
                when = dt.datetime.fromtimestamp(p.stat().st_mtime)
            if when < cutoff:
                keep.append(p)
        targets = keep

    if not targets:
        print("没有匹配的备份")
        return

    on = color_enabled()
    print(paint(f"将清除 {len(targets)} 个备份文件:", "1;33", on))
    total = 0
    for p in targets:
        sz = p.stat().st_size
        total += sz
        e = by_id.get(p.stem)
        title = flat(e.get("title", "")) if e else ""
        if not title:  # 没 manifest 记录(如 dedupe 移来的)——现读文件取标题
            title = load_session(p, archived=True).title
        print(f"  {p.stem[:8]}  {human_size(sz):>8}  {title}")
    print(f"合计 {human_size(total)}")
    if not args.force:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(paint("\n非交互终端,未删除。要删加 -f。", "1;33", on))
            return
        if not _confirm(paint("\n要现在清除这些备份吗?[y/N] ", "1;33", on), "已取消,什么都没删。"):
            return

    removed = set()
    for p in targets:
        try:
            p.unlink()
            removed.add(p.stem)
        except OSError as e:
            print(paint(f"删除失败:{p}  {e}", "1;31", on))
    # 清理 manifest 中已不存在的条目
    keep_entries = [
        e for e in entries
        if any(pp.stem == e.get("id") for pp in archive_files())
    ]
    save_manifest(keep_entries)
    # 清掉空目录
    for d in ad.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    print(f"\n已清除 {len(removed)} 个备份。")


def cmd_dedupe(args, store: SessionStore):
    """同一会话 id 有多个副本时,保留最新一份,其余移入归档(不删除)。"""
    if not store.has("claude"):
        # 不是"不支持",是没这回事:Codex 一个会话横跨多个文件时,发现阶段已按
        # session_id 归成一个会话,不存在重复副本。
        print("Codex 无需去重:发现时已按 session_id 归组,同一会话不会出现两份。")
        return
    alls = SessionStore(include_archived=args.archived, dedupe=False,
                        sources=[SOURCES["claude"]]).sessions()
    groups: dict[str, list[Session]] = defaultdict(list)
    for s in alls:
        groups[s.sid].append(s)
    dupes = {sid: g for sid, g in groups.items() if len(g) > 1}
    if not dupes:
        print("没有重复会话(每个 id 只有一份)")
        return

    on = color_enabled()
    extra: list[Session] = []
    print(paint(f"发现 {len(dupes)} 组重复会话(同 id 落在多个项目文件夹):", "1;33", on))
    for sid, g in sorted(dupes.items()):
        g.sort(key=lambda s: (s.end or s.start or dt.datetime.min, not s.archived), reverse=True)
        keep = g[0]
        if not keep.title_explicit:
            for s in g[1:]:
                if s.title_explicit:
                    keep.title = s.title  # 借用另一份的自定义标题来显示
                    break
        print(f"\n  [{sid[:8]}]  {keep.title}")
        print(paint(f"    保留  {fmt_time(keep.end or keep.start)}  {keep.path.parent.name}", "2", on))
        for s in g[1:]:
            extra.append(s)
            print(paint(f"    移走  {fmt_time(s.end or s.start)}  {s.path.parent.name}  ({human_size(s.size)})", "2", on))

    dest_dir = dupes_dir()
    print(paint(f"\n{len(extra)} 份多余副本会移到 {dest_dir}(保留,不删)", "1;33", on))
    if not args.force:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(paint("\n非交互终端,未执行。要执行加 -f。", "1;33", on))
            return
        if not _confirm(paint("\n要现在执行吗?[y/N] ", "1;33", on)):
            return

    dest_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for s in extra:
        dest = dest_dir / f"{s.sid}.{_safe_name(s.path.parent.name)}.jsonl"
        try:
            shutil.copy2(s.path, dest)
            s.path.unlink()
            moved += 1
            print(f"  已移走 {s.sid[:8]}  ({s.path.parent.name}) → {dest}")
        except Exception as e:
            print(paint(f"移走失败:{s.path}  {e}", "1;31", on))
    print(f"\n完成:{moved} 份副本已移入归档(原样保留,需要可取回)。")


def cmd_rename(args, store: SessionStore):
    """给会话改名。写法由源决定:Claude 是往 jsonl 追加 custom-title 记录;
    Codex 是往它的标题索引追加一行。"""
    s = resolve(args.ref, store)
    if s.archived:
        die("这是归档副本,不能直接改名;请对活会话改名")
    if args.title:
        title = flat(" ".join(args.title))
    else:  # 没给标题 → 交互式问(供 pick 里 Ctrl-E 调用)
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            die(f"没给新标题;非交互下请写成:{prog_name()} rename <ref> <新标题>")
        try:
            title = flat(input(f"新标题(回车确定 · 留空取消 · 当前:{s.title})= ").strip())
        except (EOFError, KeyboardInterrupt):
            print()
            title = ""
        if not title:
            print("已取消,标题未改。")
            return
    try:
        _src(s).set_title(s, title)
    except Exception as e:
        die(f"改名失败:{e}")
    print(f"已改名:[{s.sid[:8]}] → {title}")


# ---------------------------------------------------------------- main


def _common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--project", action="append", metavar="P",
                        help="按项目筛选,可重复或逗号分隔(多个取并集)")
    common.add_argument("-s", "--since", metavar="WHEN", help="起始时间:YYYY-MM-DD 或 7d/2w/3m")
    common.add_argument("-u", "--until", metavar="WHEN", help="截止时间:YYYY-MM-DD 或 7d/2w/3m")
    common.add_argument("-l", "--limit", type=int, metavar="N", help="最多处理 N 个会话")
    common.add_argument("-j", "--json", action="store_true", help="机器可读输出")
    common.add_argument("-a", "--archived", action="store_true", help="把归档也纳入数据源")
    common.add_argument("-S", "--source", action="append", metavar="SRC",
                        help="选数据源,可重复或逗号分隔(" + " / ".join(ALL_SOURCES) + ");默认看命令名")
    common.add_argument("--color", dest="color", action="store_true", default=None, help="强制彩色输出")
    common.add_argument("--no-color", dest="color", action="store_false", help="关闭彩色输出")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    prog = prog_name()
    p = argparse.ArgumentParser(
        prog=prog,
        description=f"聊天记录管理。不带子命令=列表;{prog} 3=打开第3个;{prog} 关键词=搜索。",
    )
    sub = p.add_subparsers(dest="cmd", metavar="<命令/数字/关键词>")

    sp = sub.add_parser("list", aliases=SUBCOMMANDS["list"], parents=[common], help="列出会话")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", aliases=SUBCOMMANDS["search"], parents=[common], help="搜索对话内容")
    sp.add_argument("query")
    sp.add_argument("-c", "--case-sensitive", dest="ignore_case", action="store_false",
                    default=True, help="区分大小写(默认忽略大小写)")
    sp.add_argument("--regex", action="store_true")
    sp.add_argument("--role", choices=["user", "assistant"])
    sp.add_argument("--all", action="store_true", help="连工具返回和 thinking 一起搜")
    sp.add_argument("--grep", action="store_true", help="平铺输出,不按会话分组")
    sp.add_argument("--context", type=int, default=60, metavar="N", help="命中前后字数(默认 60)")
    sp.add_argument("--max-hits", type=int, default=5, metavar="N",
                    help="每个会话最多显示几处(默认 5;0 = 不限)")
    sp.add_argument("--pick", action="store_true", help="搜完直接进 fzf 挑一个打开 / 续聊")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("prompts", aliases=SUBCOMMANDS["prompts"], parents=[common],
                        help="列出/搜索你自己的提问流水(含 jsonl 已删的会话)")
    sp.add_argument("query", nargs="?", help="按关键词过滤,默认忽略大小写")
    sp.add_argument("-c", "--case-sensitive", dest="ignore_case", action="store_false",
                    default=True, help="区分大小写(默认忽略大小写)")
    sp.set_defaults(func=cmd_prompts)

    sp = sub.add_parser("show", aliases=SUBCOMMANDS["show"], parents=[common], help="阅读一个会话")
    sp.add_argument("ref", help="id 前缀 / 列表序号 / 标题")
    sp.add_argument("-f", "--full", action="store_true", help="连 thinking 和工具返回一起显示")
    sp.add_argument("--pager", action="store_true", help="用 less 分页")
    sp.add_argument("-r", "--resume", action="store_true", help="直接续聊此会话(调对应 agent 的命令)")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("pick", aliases=SUBCOMMANDS["pick"], parents=[common],
                        help="fzf 交互选择(可带初筛词):Enter 打开 / Ctrl-R 续聊 / Ctrl-E 改名 / Ctrl-D 删除")
    sp.add_argument("query", nargs="?", help="可选的初筛词,直接填进 fzf 搜索框")
    sp.set_defaults(func=cmd_pick)

    sp = sub.add_parser("export", aliases=SUBCOMMANDS["export"], parents=[common], help="导出会话")
    sp.add_argument("ref")
    sp.add_argument("-f", "--format", choices=["md", "txt", "json"], default="md")
    sp.add_argument("-o", "--out", metavar="FILE", help="输出文件(不给则打印到终端)")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--pager", action="store_true")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("stats", aliases=SUBCOMMANDS["stats"], parents=[common], help="统计用量")
    sp.add_argument("-b", "--by", choices=["day", "project", "model", "tool"], help="按维度汇总")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("rm", aliases=SUBCOMMANDS["rm"], parents=[common],
                        help="删除会话(默认预览;交互下 y=直删 / n=删并备份)")
    sp.add_argument("refs", nargs="*", help="会话(id 前缀/序号/标题);也可不给,改用 -p 按项目选")
    sp.add_argument("-f", "--force", action="store_true", help="跳过交互直接删(不备份);不给则只预览")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("purge", aliases=SUBCOMMANDS["purge"], parents=[common], help="清除备份(默认 dry-run)")
    sp.add_argument("refs", nargs="*", help="指定会话 id 前缀(不给则全部)")
    sp.add_argument("-n", "--older-than", type=int, metavar="DAYS", help="只清 N 天前的备份")
    sp.add_argument("-f", "--force", action="store_true", help="真的删(不给则只预览)")
    sp.set_defaults(func=cmd_purge)

    sp = sub.add_parser("dedupe", aliases=SUBCOMMANDS["dedupe"], parents=[common],
                        help="合并重复会话:同 id 多份时保留最新,其余移到归档(默认 dry-run)")
    sp.add_argument("-f", "--force", action="store_true", help="真的移(不给则只预览)")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser("rename", aliases=SUBCOMMANDS["rename"], parents=[common],
                        help="给会话改名(写入 custom-title)")
    sp.add_argument("ref", help="id 前缀 / 列表序号 / 标题")
    sp.add_argument("title", nargs="*", help="新标题;不给则交互式问(pick 里 Ctrl-E 用的就是这个)")
    sp.set_defaults(func=cmd_rename)

    return p


def _make_store(args) -> SessionStore:
    return SessionStore(include_archived=getattr(args, "archived", False),
                        sources=selected_sources(args))


def main(argv=None):
    global _COLOR_MODE
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "_picklines":  # 内部用:pick 的 reload,不进 <命令> 帮助
        args = _common_parser().parse_args(raw[1:])
        _COLOR_MODE = getattr(args, "color", None)
        srcs = selected_sources(args)
        if not any(s.root().is_dir() for s in srcs):
            die(" / ".join(s.missing_hint() for s in srcs))
        cmd_picklines(args, _make_store(args))
        return
    # 分派:空→list;选项开头→list;纯数字→show;其他词→search;已知命令/别名→原样
    if not raw:
        raw = ["list"]
    elif raw[0] in ("-h", "--help"):
        pass
    elif raw[0].startswith("-"):
        raw = ["list"] + raw
    elif raw[0] not in KNOWN_COMMANDS:
        if raw[0].isdigit():
            raw = ["show"] + raw                                    # 纯数字 → 序号,打开
        elif re.fullmatch(r"[0-9a-fA-F]{6,}(-[0-9a-fA-F]+)*", raw[0]):
            raw = ["show"] + raw                                    # 像会话 id 前缀 → 打开
        else:
            raw = ["search"] + raw                                  # 其他词 → 搜索

    args = build_parser().parse_args(raw)
    _COLOR_MODE = getattr(args, "color", None)  # --color / --no-color 覆盖自动判断
    srcs = selected_sources(args)
    # 只要有一个选中的源有数据就继续(缺的那个自然是空的);一个都没有才报错。
    # purge 对 Codex 是空操作,不要求数据目录存在。
    if args.func is not cmd_purge and not any(s.root().is_dir() for s in srcs):
        die(" / ".join(s.missing_hint() for s in srcs))
    store = _make_store(args)
    try:
        args.func(args, store)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
