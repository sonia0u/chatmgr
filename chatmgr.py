#!/usr/bin/env python3
"""chatmgr —— Claude Code 聊天记录管理 CLI

把 ~/.claude/projects 下的 jsonl 记录"用起来":搜索 / 阅读 / 统计 / 安全删除。

分三层:
  解析层  SessionStore / Session   —— 无 CLI 依赖,可复用(将来可接 MCP)
  归档层  backup / purge + manifest
  CLI 层  argparse 子命令 + resolve()

仅用标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
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


def archive_dir() -> Path:
    return Path(os.environ.get("CHATMGR_ARCHIVE_DIR") or (Path.home() / "claude-archive"))


TOKEN_KEYS = ("input", "output", "cache_read", "cache_creation", "thinking")


def empty_tokens() -> dict:
    return {k: 0 for k in TOKEN_KEYS}


# 子命令别名(第一项用于补全展示,别名让人少敲字)
SUBCOMMANDS = {
    "list": ["ls", "l"],
    "search": ["s", "grep"],
    "show": ["o", "cat"],
    "pick": ["f"],
    "export": ["e"],
    "stats": ["st", "stat"],
    "rm": ["d", "del"],
    "purge": ["p"],
    "dedupe": ["dd"],
    "rename": [],
}
KNOWN_COMMANDS = set(SUBCOMMANDS) | {a for al in SUBCOMMANDS.values() for a in al}


# ---------------------------------------------------------------- 小工具


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    sys.exit(code)


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

    @property
    def short_project(self) -> str:
        return Path(self.project).name or self.project or "?"

    @property
    def tok_io(self) -> int:
        return self.tokens["input"] + self.tokens["output"]


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
    """发现并缓存会话。include_archived 时把备份也纳入数据源。"""

    def __init__(self, include_archived: bool = False, dedupe: bool = True):
        self.include_archived = include_archived
        self.dedupe = dedupe
        self._cache: list[Session] | None = None
        self.duplicates = 0

    def sessions(self) -> list[Session]:
        if self._cache is None:
            self._cache = self._discover()
        return self._cache

    def _discover(self) -> list[Session]:
        out: list[Session] = []
        pd = projects_dir()
        if pd.is_dir():
            for p in sorted(pd.glob("*/*.jsonl")):
                out.append(load_session(p, archived=False))
        if self.include_archived:
            ad = archive_dir()
            if ad.is_dir():
                for p in sorted(ad.glob("*/*.jsonl")):
                    out.append(load_session(p, archived=True))
        out.sort(key=lambda s: (s.end or s.start or dt.datetime.min, not s.archived), reverse=True)
        if not self.dedupe:
            return out
        # 同一个会话 id 可能落在多个项目文件夹里(在不同目录 resume 过),文件内容会不一致。
        # 只保留最新、且优先活会话的那一份,否则列表会出现两行相互矛盾的同一会话。
        best_title: dict[str, str] = {}
        for s in out:
            if s.title_explicit:
                best_title.setdefault(s.sid, s.title)
        seen: set[str] = set()
        uniq: list[Session] = []
        for s in out:
            if s.sid in seen:
                self.duplicates += 1
                continue
            seen.add(s.sid)
            if not s.title_explicit and s.sid in best_title:
                s.title = best_title[s.sid]  # 借用同一会话另一份里的自定义标题,免得标题忽然变样
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


def iter_events(path: Path, full: bool = False):
    """按原始顺序产出 (kind, text)。kind: user/assistant/tool/thinking/tool_result"""
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


def search_units(path: Path, include_all: bool = False, role: str | None = None):
    """产出可搜索单元 (kind, text)。默认:user 输入 + assistant 正文 + 工具调用。"""
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
    out = []
    head = f"=== {s.title} ==="
    if s.archived:
        head += "  [已归档]"
    out.append(paint(head, "1;36", on))
    span = f"{fmt_time(s.start)} ~ {fmt_time(s.end)}"
    out.append(f"项目: {paint(s.project, proj_color, on)}")
    out.append(f"时间: {span}    会话: {s.sid}")
    toks = s.tokens
    out.append(
        f"消息: {s.prompts} 问 / {s.replies} 答    "
        f"token: in {human_tokens(toks['input'])} / out {human_tokens(toks['output'])} / "
        f"cache_r {human_tokens(toks['cache_read'])} / cache_w {human_tokens(toks['cache_creation'])} / "
        f"thinking {human_tokens(toks['thinking'])}"
    )
    if s.models:
        out.append("模型: " + ", ".join(f"{m}({c})" for m, c in s.models.most_common()))
    out.append("")
    for kind, text in iter_events(s.path, full=full):
        if kind == "user":
            out.append(paint("[你]", "1;32", on))
            out.append(text.rstrip())
        elif kind == "assistant":
            out.append(paint("[Claude]", "1;34", on))
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
    out.append(paint(f"→ 继续此对话: claude --resume {s.sid}", "2", on))
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
    for kind, text in iter_events(s.path, full=full):
        if kind == "user":
            out += ["## 你", "", text.rstrip(), ""]
        elif kind == "assistant":
            out += ["## Claude", "", text.rstrip(), ""]
        elif kind == "tool":
            out += [f"> `{text}`", ""]
        elif kind == "tool_result":
            out += ["> **工具返回**", "", "```", text.rstrip(), "```", ""]
        elif kind == "thinking":
            out += ["<details><summary>思考</summary>", "", text.rstrip(), "", "</details>", ""]
    return "\n".join(out)


def export_plain(s: Session, full: bool) -> str:
    out = [f"=== {s.title} ===", f"项目: {s.project}", f"时间: {fmt_time(s.start)} ~ {fmt_time(s.end)}", ""]
    for kind, text in iter_events(s.path, full=full):
        label = {"user": "[你]", "assistant": "[Claude]", "tool": "  · ", "tool_result": "  · 工具返回:", "thinking": "  · 思考:"}[kind]
        out.append(label)
        out.append(text.rstrip())
        out.append("")
    return "\n".join(out)


def export_json(s: Session, full: bool) -> str:
    msgs = [{"kind": k, "text": t} for k, t in iter_events(s.path, full=full)]
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
    rows = [["#", "时间", "项目", "标题", "消息", "大小", "tok"]]
    for s in sessions:
        title = s.title + ("  [已归档]" if s.archived else "")
        rows.append(
            [
                paint(str(idx.get(str(s.path), "?")), "1;33", on),  # 序号高亮,便于复制去 show/rm
                fmt_time(s.end or s.start),
                paint(truncate(s.short_project, 22), pcolors.get(s.short_project, "36"), on),
                truncate(title, 34),
                f"{s.prompts}+{s.replies}",
                human_size(s.size),
                human_tokens(s.tok_io),
            ]
        )
    print_table(rows, shutil.get_terminal_size((120, 24)).columns)
    print(f"\n{len(sessions)} 个会话   (tok = input+output)")
    if store.duplicates:
        print(paint(
            f"⚠ 有 {store.duplicates} 份重复会话(同一 id 落在多个项目文件夹,内容不一致)"
            f",已只保留最新的一份", "2", on))
    if sys.stdout.isatty():  # 只在人看的终端里提示;管道/脚本不打扰
        print(paint("提示:要筛选 / 打开 / 改名 / 删 → cgr f", "2", on))


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
    cap = 10**9 if args.grep else args.max_hits
    hits = []  # (session, 真实命中数, 用于展示的片段)
    for s in sessions:
        count = 0
        snippets = []
        for kind, text in search_units(s.path, include_all=args.all, role=args.role):
            for m in pat.finditer(text):
                count += 1
                if len(snippets) < cap:
                    snippets.append((kind, _snippet(text, m, ctx, pat, on)))
        if count:
            hits.append((s, count, snippets))

    if args.json:
        data = [
            {
                "session": sess_to_dict(s),
                "count": c,
                "hits": [{"kind": k, "text": t} for k, t in sn],
            }
            for s, c, sn in hits
        ]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if not hits:
        print("没有命中")
        return

    total = sum(c for _, c, _ in hits)
    if args.grep:
        for s, _, sn in hits:
            for kind, text in sn:
                print(f"{s.sid[:8]}:{kind}: {text}")
    else:
        idx = store.index_map()
        for s, c, sn in hits:
            tag = "  [已归档]" if s.archived else ""
            print(paint(f"[{idx.get(str(s.path), '?')}] {s.title}{tag}  ({c} 处命中)", "1;36", on))
            print(f"    {fmt_time(s.end or s.start)}  {paint(s.project, pcolors.get(s.short_project, '36'), on)}")
            for kind, text in sn:
                print(f"    {paint(kind, '2', on)}: {text}")
            if c > len(sn):
                print(paint(f"    … 另有 {c - len(sn)} 处", "2", on))
            print(paint(f"    → claude --resume {s.sid}", "2", on))
            print()
    print(f"{total} 处命中,分布在 {len(hits)} 个会话")


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


def _resume_session(s: Session):
    cwd = s.project if os.path.isdir(s.project) else None
    print(f"→ claude --resume {s.sid}  (cwd: {cwd or '未找到,用当前目录'})")
    try:
        subprocess.run(["claude", "--resume", s.sid], cwd=cwd)
    except FileNotFoundError:
        die("找不到 claude 命令")


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


def pick_lines(store: SessionStore, args) -> list[str]:
    """pick / _picklines 共用:生成 fzf 的列表行(第 1 列显示,末列藏序号)。"""
    sessions = apply_filters(store.sessions(), args)
    if getattr(args, "limit", None):
        sessions = sessions[: args.limit]
    idx = store.index_map()
    on = color_enabled()
    pcolors = project_colors(store.sessions())
    lines = []
    for s in sessions:
        tag = " [已归档]" if s.archived else ""
        display = "  ".join(
            [
                pad(truncate(s.title + tag, 32), 32),  # 标题放最前,最能认出是哪次对话
                paint(pad(truncate(s.short_project, 18), 18), pcolors.get(s.short_project, "36"), on),
                paint(f"{s.prompts}+{s.replies}", "2", on),
                paint(fmt_time(s.end or s.start), "2", on),  # 时间往后放
            ]
        )
        lines.append(display + "\t" + str(idx.get(str(s.path), "?")))
    return lines


def cmd_picklines(args, store: SessionStore):
    """内部用:给 fzf 的 reload 重新生成列表行。"""
    print("\n".join(pick_lines(store, args)))


def _filter_args(args) -> list[str]:
    """把当前的筛选选项拼回命令行,供 pick 的 reload 复用。"""
    out: list[str] = []
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


def cmd_pick(args, store: SessionStore):
    """fzf 交互选择:Enter 打开 / Tab 多选 / Ctrl-R 续聊 / Ctrl-E 改名 / Ctrl-D 删除。"""
    if not sys.stdin.isatty():
        die("cgr pick 需要在交互终端下运行(fzf 需要 tty);非交互场景请用 list / search")
    lines = pick_lines(store, args)
    if not lines:
        print("没有会话")
        return
    fzf = shutil.which("fzf")
    if not fzf:
        die("没装 fzf —— brew install fzf")
    exe, script = sys.executable, os.path.abspath(__file__)
    extra = ["-a"] if args.archived else []
    selfq = f"{shlex.quote(exe)} {shlex.quote(script)}"
    preview = f"{selfq} show {{2}}"
    if color_enabled():
        preview += " --color"  # 预览在 fzf 里不是 TTY,得强制上色,否则和你直接跑 cgr 不一样
    reload_cmd = " ".join([selfq, "_picklines", *[shlex.quote(x) for x in _filter_args(args)]])
    bind = f"ctrl-e:execute({selfq} rename {{2}})+reload({reload_cmd})"  # 改名后刷新列表
    sub = subprocess.run(
        [
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
            "--bind", bind,
            "--header=Enter 打开 · Tab 多选 · Ctrl-R 续聊 · Ctrl-E 改名 · Ctrl-D 删除(预览)",
            "--height=~90%",  # 自适应:条目少时窗口自动缩,不留空白
        ],
        input="\n".join(lines),
        text=True,
        capture_output=True,
    )
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
            for rec in iter_records(s.path):
                ts = parse_ts(rec.get("timestamp"))
                if not ts:
                    continue
                d = ts.date().isoformat()
                days[d]["n"] += 1
                if rec.get("type") == "assistant":
                    u = (rec.get("message") or {}).get("usage") or {}
                    days[d]["tok"] += (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        rows = [["日期", "记录数", "tok(in+out)"]]
        for d in sorted(days):
            rows.append([d, str(days[d]["n"]), human_tokens(days[d]["tok"])])
        print_table(rows)
        return

    # 总览
    span_start = min((s.start for s in sessions if s.start), default=None)
    span_end = max((s.end for s in sessions if s.end), default=None)
    print(f"会话: {len(sessions)}    消息: {sum(s.prompts for s in sessions)} 问 / "
          f"{sum(s.replies for s in sessions)} 答")
    print(f"时间跨度: {fmt_time(span_start)} ~ {fmt_time(span_end)}")
    print()
    rows = [["token 类型", "合计"]]
    for k in TOKEN_KEYS:
        rows.append([k, human_tokens(total_tokens[k])])
    rows.append(["input+output", human_tokens(total_tokens["input"] + total_tokens["output"])])
    print_table(rows)
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

    backup = not getattr(args, "no_backup", False)  # 默认删前备份;--no-backup 或交互按 y 关掉
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
        dest = None
        if backup:
            try:
                dest = backup_session(s)
            except Exception as e:
                print(paint(f"\n备份失败,已中止(不会删未备份的):{s.sid}\n  {e}", "1;31", on))
                if done:
                    print("已完成:" + ", ".join(done))
                sys.exit(1)
        try:
            s.path.unlink()
        except OSError as e:
            print(paint(f"\n删除失败:{s.path}\n  {e}", "1;31", on))
            if dest:
                print(f"备份已在:{dest}")
            sys.exit(1)
        done.append(s.sid)
        print(f"  已删 {s.sid[:8]}" + (f"  (备份 → {dest})" if dest else "  (未备份)"))

    print(f"\n完成:{len(done)} 个会话已删除。")
    if backup:
        print(paint(f"备份留在 {archive_dir()}(当保险)。要一次清掉全部备份:", "2", on))
        print(paint("  cgr purge -f", "2", on))
    else:
        print(paint("⚠ 这次没备份,删了不可恢复。", "1;33", on))


def cmd_purge(args, store: SessionStore):
    ad = archive_dir()
    if not ad.is_dir():
        print(f"归档目录不存在:{ad}")
        return
    entries = load_manifest()
    by_id = {e.get("id"): e for e in entries}

    targets: list[Path] = []
    if args.refs:
        for r in args.refs:
            matches = [p for p in ad.glob("*/*.jsonl") if p.stem.startswith(r)]
            if not matches:
                die(f"归档里找不到:{r}")
            targets += matches
    else:
        targets = list(ad.glob("*/*.jsonl"))

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
        title = e.get("title", "") if e else ""
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
        if any(pp.stem == e.get("id") for pp in ad.glob("*/*.jsonl"))
    ]
    save_manifest(keep_entries)
    # 清掉空目录
    for d in ad.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    print(f"\n已清除 {len(removed)} 个备份。")


def cmd_dedupe(args, store: SessionStore):
    """同一会话 id 有多个副本时,保留最新一份,其余移入归档(不删除)。"""
    alls = SessionStore(include_archived=args.archived, dedupe=False).sessions()
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

    dest_dir = archive_dir() / "_dupes"
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
    """给会话改名:向 jsonl 追加一条 custom-title 记录(与 Claude Code 自身改名同格式)。"""
    s = resolve(args.ref, store)
    if s.archived:
        die("这是归档副本,不能直接改名;请对活会话改名")
    if args.title:
        title = flat(" ".join(args.title))
    else:  # 没给标题 → 交互式问(供 pick 里 Ctrl-E 调用)
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            die("没给新标题;非交互下请写成:cgr rename <ref> <新标题>")
        try:
            title = flat(input(f"新标题(回车确定 · 留空取消 · 当前:{s.title})= ").strip())
        except (EOFError, KeyboardInterrupt):
            print()
            title = ""
        if not title:
            print("已取消,标题未改。")
            return
    rec = {"type": "custom-title", "customTitle": title, "sessionId": s.sid}
    with open(s.path, "rb+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":  # 保证追加不会挤到上一行
                f.write(b"\n")
        f.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
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
    common.add_argument("-a", "--archived", action="store_true", help="把备份也纳入数据源")
    common.add_argument("--color", dest="color", action="store_true", default=None, help="强制彩色输出")
    common.add_argument("--no-color", dest="color", action="store_false", help="关闭彩色输出")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    p = argparse.ArgumentParser(
        prog="cgr",
        description="Claude Code 聊天记录管理。不带子命令=列表;cgr 3=打开第3个;cgr 关键词=搜索。",
    )
    sub = p.add_subparsers(dest="cmd", metavar="<命令/数字/关键词>")

    sp = sub.add_parser("list", aliases=SUBCOMMANDS["list"], parents=[common], help="列出会话")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", aliases=SUBCOMMANDS["search"], parents=[common], help="搜索对话内容")
    sp.add_argument("query")
    sp.add_argument("-i", "--ignore-case", dest="ignore_case", action="store_true",
                    default=True, help="忽略大小写(默认)")
    sp.add_argument("-c", "--case-sensitive", dest="ignore_case", action="store_false",
                    help="区分大小写")
    sp.add_argument("--regex", action="store_true")
    sp.add_argument("--role", choices=["user", "assistant"])
    sp.add_argument("--all", action="store_true", help="连工具返回和 thinking 一起搜")
    sp.add_argument("--grep", action="store_true", help="平铺输出,不按会话分组")
    sp.add_argument("--context", type=int, default=60, metavar="N", help="命中前后字数(默认 60)")
    sp.add_argument("--max-hits", type=int, default=5, metavar="N", help="每个会话最多显示几处(默认 5)")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", aliases=SUBCOMMANDS["show"], parents=[common], help="阅读一个会话")
    sp.add_argument("ref", help="id 前缀 / 列表序号 / 标题")
    sp.add_argument("-f", "--full", action="store_true", help="连 thinking 和工具返回一起显示")
    sp.add_argument("--pager", action="store_true", help="用 less 分页")
    sp.add_argument("-r", "--resume", action="store_true", help="直接 claude --resume 此会话")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("pick", aliases=SUBCOMMANDS["pick"], parents=[common],
                        help="fzf 交互选择:Enter 打开 / Ctrl-R 续聊 / Ctrl-D 预览删除")
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
    sp.add_argument("-f", "--force", action="store_true", help="跳过交互直接删(不给则只预览)")
    sp.add_argument("--no-backup", action="store_true", help="删除时不备份(配合 -f 用于脚本)")
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


def main(argv=None):
    global _COLOR_MODE
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "_picklines":  # 内部用:pick 的 reload,不进 <命令> 帮助
        args = _common_parser().parse_args(raw[1:])
        _COLOR_MODE = getattr(args, "color", None)
        if not projects_dir().is_dir():
            die(f"找不到 {projects_dir()}(Claude Code 还没用过?)")
        cmd_picklines(args, SessionStore(include_archived=getattr(args, "archived", False)))
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
    if not getattr(args, "func", None):
        args = build_parser().parse_args(["list"])
    _COLOR_MODE = getattr(args, "color", None)  # --color / --no-color 覆盖自动判断
    if not projects_dir().is_dir() and args.func is not cmd_purge:
        die(f"找不到 {projects_dir()}(Claude Code 还没用过?)")
    store = SessionStore(include_archived=getattr(args, "archived", False))
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
