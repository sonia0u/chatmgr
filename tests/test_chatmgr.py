#!/usr/bin/env python3
"""chatmgr 的最小测试。只用标准库 unittest,全程在临时目录里跑,绝不碰真实数据。

跑法:
    python3 tests/test_chatmgr.py                 # 直接跑
    python3 -m unittest discover -s tests          # 或让 unittest 发现

每个用例在 setUp 里把 CLAUDE_CONFIG_DIR / CHATMGR_ARCHIVE_DIR 指到临时目录,
再塞假 jsonl(就是 CLAUDE.md「安全测试」那套)。破坏性用例依赖"非 TTY 只预览"
这条约定 —— redirect_stdout 让 sys.stdout 不是 TTY,所以即使在真终端里跑,
rm / purge / dedupe 也只会预览、不会真的动文件。
"""

import contextlib
import datetime as dt
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatmgr  # noqa: E402


# ---------------------------------------------------------------- 造数据的小工具


def user(text, *, cwd=None, meta=False):
    r = {"type": "user", "message": {"content": text}}
    if cwd is not None:
        r["cwd"] = cwd
    if meta:
        r["isMeta"] = True
    return r


def tool_result(text="x"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": text}]}}


def assistant(text="ok", *, model="m", usage=None):
    return {
        "type": "assistant",
        "message": {"model": model, "usage": usage or {}, "content": [{"type": "text", "text": text}]},
    }


def custom_title(t):
    return {"type": "custom-title", "customTitle": t}


def ai_title(t):
    return {"type": "ai-title", "aiTitle": t}


def write_session(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    return path


def hist_row(text, *, project="/Users/me/proj ", ts=1_700_000_000_000, sid="s1"):
    """一条 ~/.claude/history.jsonl 记录(注意 project 带尾随空格,和真实数据一致)。"""
    return {"display": text, "pastedContents": {}, "timestamp": ts, "project": project, "sessionId": sid}


# ---------------------------------------------------------------- 基类


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tmp_root = root
        self.config = root / "config"
        self.archive = root / "archive"
        self.codex_home = root / "codex"  # 空目录:cgr 默认是"全部源",别去读真实的 ~/.codex
        self.opencode_home = root / "opencode"  # 同理,别去碰真实的 opencode.db
        (self.config / "projects").mkdir(parents=True)
        self.archive.mkdir(parents=True)
        self.codex_home.mkdir(parents=True)
        self.opencode_home.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in
                     ("CLAUDE_CONFIG_DIR", "CHATMGR_ARCHIVE_DIR", "NO_COLOR",
                      "CHATMGR_CODEX_DIR", "CHATMGR_CODEX_BIN", "CODEX_HOME",
                      "CHATMGR_OPENCODE_DIR", "CHATMGR_OPENCODE_BIN")}
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)
        os.environ["CHATMGR_ARCHIVE_DIR"] = str(self.archive)
        os.environ["CHATMGR_CODEX_DIR"] = str(self.codex_home)
        os.environ["CHATMGR_CODEX_BIN"] = ""  # 当作"没装 codex",免得测试真去调它
        os.environ["CHATMGR_OPENCODE_DIR"] = str(self.opencode_home)
        os.environ["CHATMGR_OPENCODE_BIN"] = ""
        os.environ.pop("CODEX_HOME", None)
        os.environ["NO_COLOR"] = "1"  # 输出里不带颜色码,断言好写
        chatmgr._COLOR_MODE = None

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def live(self, sid, records, project="proj"):
        """在临时 projects 下写一个活会话文件。"""
        return write_session(self.config / "projects" / project / f"{sid}.jsonl", records)

    def run_cli(self, *argv):
        """跑 main(),返回 (退出码, 合并后的输出)。"""
        buf = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                chatmgr.main(list(argv))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
        return code, buf.getvalue()


# ---------------------------------------------------------------- 解析:时间 / 标题 / 项目


class ParseTests(unittest.TestCase):
    def test_parse_ts_iso_z(self):
        t = chatmgr.parse_ts("2020-06-15T12:00:00Z")  # 12:00Z 对任意时区都落在同一天
        self.assertIsInstance(t, dt.datetime)
        self.assertEqual((t.year, t.month), (2020, 6))

    def test_parse_ts_epoch_small_is_seconds(self):
        # 阈值 1e11:小于当秒,大于当毫秒
        self.assertEqual(chatmgr.parse_ts(1000), dt.datetime.fromtimestamp(1000))

    def test_parse_ts_epoch_large_is_ms(self):
        self.assertEqual(chatmgr.parse_ts(1_600_000_000_000), dt.datetime.fromtimestamp(1_600_000_000))

    def test_parse_ts_garbage(self):
        self.assertIsNone(chatmgr.parse_ts("not a time"))

    def test_flat_strips_control_chars(self):
        """标题/项目里混进 \\b 会让终端退格、吃掉前一个字符,把整列挤歪;\\x1b 更糟(转义序列)。"""
        self.assertEqual(chatmgr.flat("a\x08b"), "ab")
        self.assertEqual(chatmgr.flat("a\x1b[31mb"), "a[31mb")
        self.assertEqual(chatmgr.flat("a\tb\nc"), "a b c")  # 空白照旧压成单个空格

    def test_dwidth_counts_control_chars_as_zero(self):
        self.assertEqual(chatmgr.dwidth("a\x08b"), 2)
        self.assertEqual(chatmgr.dwidth("中文"), 4)
        self.assertEqual(chatmgr.dwidth("你\x08好"), 4)

    def test_parse_when_relative_days(self):
        delta = dt.datetime.now() - chatmgr.parse_when("7d")
        self.assertLess(abs(delta.total_seconds() - 7 * 86400), 5)

    def test_parse_when_absolute(self):
        self.assertEqual(chatmgr.parse_when("2026-01-02"), dt.datetime(2026, 1, 2))

    def test_parse_when_until_takes_end_of_day(self):
        self.assertEqual(
            chatmgr.parse_when("2026-01-02", end_of_day=True), dt.datetime(2026, 1, 2, 23, 59, 59)
        )

    def test_parse_when_bad_format_exits(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            chatmgr.parse_when("上周")


class LoadSessionTests(BaseTest):
    def test_custom_title_beats_ai_and_first_prompt(self):
        p = self.live("aaaa1111", [user("首句"), ai_title("AI 标题"), custom_title("自定义标题")])
        s = chatmgr.load_session(p)
        self.assertEqual(s.title, "自定义标题")
        self.assertTrue(s.title_explicit)

    def test_ai_title_when_no_custom(self):
        p = self.live("aaaa1111", [user("首句"), ai_title("AI 标题")])
        self.assertEqual(chatmgr.load_session(p).title, "AI 标题")

    def test_first_prompt_fallback(self):
        p = self.live("aaaa1111", [user("这是第一句真人输入")])
        s = chatmgr.load_session(p)
        self.assertEqual(s.title, "这是第一句真人输入")
        self.assertFalse(s.title_explicit)

    def test_project_from_cwd_is_stripped(self):
        # cwd 有尾随空格,必须以 strip 后的值为准(不能从文件夹名解码)
        p = self.live("aaaa1111", [user("x", cwd="/Users/me/some proj ")])
        self.assertEqual(chatmgr.load_session(p).project, "/Users/me/some proj")

    def test_project_falls_back_to_folder_name(self):
        p = self.live("aaaa1111", [user("x")], project="myproj")
        self.assertEqual(chatmgr.load_session(p).project, "myproj")

    def test_prompts_skip_noise(self):
        recs = [
            user("真输入"),
            user("<command-name>foo</command-name>"),
            user("带一句 system-reminder 的注入"),
            user("meta", meta=True),
            tool_result("工具返回不算真人输入"),
        ]
        s = chatmgr.load_session(self.live("aaaa1111", recs))
        self.assertEqual(s.prompts, 1)
        self.assertEqual(s.first_prompt, "真输入")


# ---------------------------------------------------------------- 指认:序号 / id 前缀 / 标题


class ResolveTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.live("aaaa1111-0000", [user("first one")])
        self.live("bbbb2222-0000", [user("second thing")], project="other")

    def test_by_index_matches_store_order(self):
        store = chatmgr.SessionStore()
        self.assertEqual(chatmgr.resolve("1", store).sid, store.sessions()[0].sid)

    def test_by_id_prefix(self):
        self.assertEqual(chatmgr.resolve("aaaa1111", chatmgr.SessionStore()).sid, "aaaa1111-0000")

    def test_by_title_fragment(self):
        self.assertEqual(chatmgr.resolve("second", chatmgr.SessionStore()).sid, "bbbb2222-0000")

    def test_ambiguous_title_errors(self):
        self.live("cccc3333", [user("配置 yazi 一")])
        self.live("dddd4444", [user("配置 yazi 二")], project="other")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            chatmgr.resolve("配置", chatmgr.SessionStore())

    def test_not_found_errors(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            chatmgr.resolve("zzzzzz", chatmgr.SessionStore())


# ---------------------------------------------------------------- 破坏性命令的安全底线


class DestructiveSafetyTests(BaseTest):
    def test_rm_without_force_only_previews(self):
        p = self.live("aaaa1111", [user("x")])
        _, out = self.run_cli("rm", "1")
        self.assertTrue(p.exists(), "非交互终端下 rm 不带 -f 绝不能删文件")
        self.assertIn("未删除", out)

    def test_rm_force_deletes(self):
        p = self.live("aaaa1111", [user("x")])
        self.run_cli("rm", "1", "-f")
        self.assertFalse(p.exists())

    def test_purge_without_force_only_previews(self):
        b = write_session(self.archive / "proj" / "aaaa1111.jsonl", [user("backup")])
        self.run_cli("purge")
        self.assertTrue(b.exists(), "非交互终端下 purge 不带 -f 绝不能删备份")

    def test_purge_force_deletes_backup(self):
        b = write_session(self.archive / "proj" / "aaaa1111.jsonl", [user("backup")])
        self.run_cli("purge", "-f")
        self.assertFalse(b.exists())

    def test_purge_never_touches_dupes(self):
        # 回归:dedupe 挪进 _dupes/ 的副本是"刻意保留"的,不是备份,purge 不能删
        backup = write_session(self.archive / "proj" / "aaaa1111.jsonl", [user("backup")])
        dupe = write_session(self.archive / "_dupes" / "bbbb2222.src.jsonl", [user("dupe")])
        self.run_cli("purge", "-f")
        self.assertFalse(backup.exists())
        self.assertTrue(dupe.exists(), "purge 删掉了 _dupes/ 里的重复副本")

    def test_list_archived_hides_dupes(self):
        self.live("aaaa1111", [user("live")])
        write_session(self.archive / "proj" / "bbbb2222.jsonl", [user("backup")])
        write_session(self.archive / "_dupes" / "cccc3333.src.jsonl", [user("dupe")])
        _, out = self.run_cli("list", "-a", "-j")
        ids = {d["id"] for d in json.loads(out)}
        self.assertIn("aaaa1111", ids)
        self.assertIn("bbbb2222", ids)
        self.assertNotIn("cccc3333.src", ids)

    def test_dedupe_without_force_moves_nothing(self):
        p1 = self.live("aaaa1111", [user("v1")], project="proj")
        p2 = self.live("aaaa1111", [user("v2")], project="other")
        self.run_cli("dedupe")
        self.assertTrue(p1.exists() and p2.exists(), "非交互终端下 dedupe 不带 -f 绝不能移动文件")

    def test_dedupe_force_moves_one_copy_to_dupes(self):
        p1 = self.live("aaaa1111", [user("v1")], project="proj")
        p2 = self.live("aaaa1111", [user("v2")], project="other")
        self.run_cli("dedupe", "-f")
        self.assertEqual(sum(p.exists() for p in (p1, p2)), 1, "应只留一份活副本")
        self.assertEqual(len(list((self.archive / "_dupes").glob("*.jsonl"))), 1, "另一份应移入 _dupes/")


# ---------------------------------------------------------------- rename(唯一会写回 jsonl 的命令)


class RenameTests(BaseTest):
    def test_rename_writes_custom_title(self):
        p = self.live("aaaa1111", [user("原始首句")])
        self.run_cli("rename", "1", "我的新标题")
        s = chatmgr.load_session(p)  # 重新解析,验证确实落到了文件里
        self.assertEqual(s.title, "我的新标题")
        self.assertTrue(s.title_explicit)

    def test_rename_appends_newline_when_file_lacks_one(self):
        # 回归:文件末尾没有换行时,追加前必须补一个,否则新记录会和上一行粘成一行
        p = self.config / "projects" / "proj" / "aaaa1111.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(user("首句"), ensure_ascii=False), encoding="utf-8")  # 故意不换行
        self.run_cli("rename", "1", "改过了")
        for line in p.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # 每一行都应是合法 JSON
        self.assertEqual(chatmgr.load_session(p).title, "改过了")

    def test_rename_refuses_archived_copy(self):
        b = write_session(self.archive / "proj" / "aaaa1111.jsonl", [user("备份")])
        code, out = self.run_cli("rename", "aaaa1111", "新标题", "-a")
        self.assertNotEqual(code, 0)
        self.assertIn("归档", out)
        self.assertNotIn("新标题", b.read_text(encoding="utf-8"), "归档副本不该被写入")

    def test_rename_without_title_noninteractive_exits(self):
        self.live("aaaa1111", [user("x")])
        code, out = self.run_cli("rename", "1")  # stdout 被重定向 ⇒ 非 TTY,不该去 input() 卡住
        self.assertNotEqual(code, 0)
        self.assertIn("非交互", out)


# ---------------------------------------------------------------- prompts(提问流水)


class PromptsTests(BaseTest):
    def write_history(self, rows):
        p = self.config / "history.jsonl"
        p.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        return p

    def test_lists_history_newest_first(self):
        self.write_history([hist_row("你好", ts=1_700_000_000_000),
                            hist_row("第二条", ts=1_700_000_100_000)])
        _, out = self.run_cli("prompts", "-j")
        data = json.loads(out)
        self.assertEqual([d["text"] for d in data], ["第二条", "你好"])
        self.assertEqual(data[0]["session"], "s1")

    def test_query_filter_default_ignores_case(self):
        self.write_history([hist_row("MD 渲染"), hist_row("无关")])
        _, out = self.run_cli("prompts", "md", "-j")
        self.assertEqual([d["text"] for d in json.loads(out)], ["MD 渲染"])

    def test_project_filter_matches_short_name(self):
        # project 带尾随空格;按短名(最后一段)过滤要能命中
        self.write_history([hist_row("x", project="/Users/me/anotherproj ")])
        _, out = self.run_cli("prompts", "-p", "anotherproj", "-j")
        self.assertEqual(len(json.loads(out)), 1)

    def test_missing_history_dies(self):
        code, out = self.run_cli("prompts")  # setUp 只建了 projects/,没建 history.jsonl
        self.assertNotEqual(code, 0)
        self.assertIn("找不到", out)


# ---------------------------------------------------------------- 输出契约(轻量)


class OutputContractTests(BaseTest):
    def test_list_json_keys(self):
        self.live("aaaa1111", [user("x")])
        _, out = self.run_cli("list", "-j")
        row = json.loads(out)[0]
        for k in ("id", "title", "project", "archived", "tokens", "path"):
            self.assertIn(k, row)

    def test_search_json_groups_content_hits(self):
        self.live("aaaa1111", [user("alpha"), assistant("beta gamma")])
        _, out = self.run_cli("search", "gamma", "-j")
        data = json.loads(out)
        self.assertEqual(data["query"], "gamma")
        self.assertEqual(data["title_matches"], [])
        self.assertEqual(len(data["content_matches"]), 1)


# ---------------------------------------------------------------- Codex 数据源


def cmeta(sid, *, cwd="/Users/me/proj", ts="2026-10-15T10:00:00.000Z", thread_source="user"):
    return {
        "timestamp": ts, "ordinal": 0, "type": "session_meta",
        "payload": {"session_id": sid, "cwd": cwd, "thread_source": thread_source},
    }


def citem(kind, *, text=None, ts="2026-10-15T10:00:01.000Z", **extra):
    item = {"type": kind}
    if text is not None:
        item["content"] = [{"type": "text", "text": text}]
    item.update(extra)
    return {
        "timestamp": ts, "ordinal": 1, "type": "event_msg",
        "payload": {"type": "item_completed", "item": item},
    }


def cusage(*, inp=1000, cached=0, out=50, reasoning=0, ts="2026-10-15T10:00:02.000Z"):
    return {
        "timestamp": ts, "ordinal": 2, "type": "token_usage_record",
        "payload": {"usage": {
            "input_tokens": inp, "cached_input_tokens": cached,
            "cache_write_input_tokens": 0, "output_tokens": out,
            "reasoning_output_tokens": reasoning,
        }},
    }


class CodexTest(BaseTest):
    """Codex 源。破坏性命令一律走桩脚本 —— 绝不真跑 codex,更不碰真实 ~/.codex。"""

    def setUp(self):
        super().setUp()
        self.codex = self.codex_home  # 用基类已经建好、且已指向的目录
        (self.codex / "sessions" / "2026" / "10" / "15").mkdir(parents=True, exist_ok=True)
        (self.codex / "archived_sessions").mkdir(parents=True, exist_ok=True)
        self.calls = self.tmp_root / "calls.log"
        stub = self.tmp_root / "codex-stub.sh"
        stub.write_text(f'#!/bin/sh\necho "$@" >> "{self.calls}"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)
        self.stub = stub
        # Codex 的真身状态库:标题的真身是这张表的 name 列,不是 session_index.jsonl
        self.db = self.codex / "state_5.sqlite"
        con = sqlite3.connect(self.db)
        con.execute("create table threads (id text primary key, name text)")
        con.commit()
        con.close()
        # 桌面应用自己的库:侧边栏那份会话目录。codex delete 不碰它,得我们补一刀
        (self.codex / "sqlite").mkdir(parents=True, exist_ok=True)
        self.desktop_db = self.codex / "sqlite" / "codex-dev.db"
        con = sqlite3.connect(self.desktop_db)
        con.execute("create table local_thread_catalog (host_id text, thread_id text, display_title text)")
        con.commit()
        con.close()
        os.environ["CHATMGR_CODEX_DIR"] = str(self.codex)
        os.environ["CHATMGR_CODEX_BIN"] = str(stub)

    def conv(self, name, records, *, archived=False, db_name=""):
        d = self.codex / ("archived_sessions" if archived else "sessions/2026/10/15")
        p = write_session(d / f"{name}.jsonl", records)
        sid = next((r["payload"]["session_id"] for r in records
                    if r.get("type") == "session_meta"), None)
        if sid:
            con = sqlite3.connect(self.db)
            with con:
                con.execute("insert or replace into threads(id, name) values (?,?)", (sid, db_name))
            con.close()
            con = sqlite3.connect(self.desktop_db)  # 侧边栏目录
            with con:
                con.execute("insert into local_thread_catalog(host_id, thread_id, display_title)"
                            " values ('local', ?, ?)", (sid, db_name or sid))
            con.close()
        return p

    def in_catalog(self, sid):
        con = sqlite3.connect(f"file:{self.desktop_db}?mode=ro", uri=True)
        n = con.execute("select count(*) from local_thread_catalog where thread_id=?", (sid,)).fetchone()[0]
        con.close()
        return n

    def state_name(self, sid):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        row = list(con.execute("select name from threads where id=?", (sid,)))
        con.close()
        return row[0][0] if row else None

    def index(self, rows):
        p = self.codex / "session_index.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return p

    def called(self):
        return self.calls.read_text(encoding="utf-8").split() if self.calls.exists() else []

    def sessions(self, archived=False):
        return chatmgr.SessionStore(
            include_archived=archived, sources=[chatmgr.SOURCES["codex"]]).sessions()

    # ---- 发现 / 归组

    def test_groups_multi_file_and_skips_guardian(self):
        self.conv("main", [cmeta("s1"), citem("UserMessage", text="问题")])
        self.conv("cont", [cmeta("s1", ts="2026-10-15T10:10:00.000Z"),
                           citem("AgentMessage", text="回答", ts="2026-10-15T10:10:01.000Z")])
        self.conv("guard", [cmeta("s2", thread_source="guardian_review"),
                            citem("UserMessage", text="审批复核(机器写的)")])
        got = self.sessions()
        self.assertEqual([s.sid for s in got], ["s1"], "同一 session_id 的多文件应合成一个会话")
        self.assertEqual(len(got[0].paths), 2)
        self.assertEqual((got[0].prompts, got[0].replies), (1, 1))

    def test_archived_only_listed_with_flag(self):
        self.conv("live", [cmeta("s1"), citem("UserMessage", text="活的")])
        self.conv("arch", [cmeta("s2"), citem("UserMessage", text="归档的")], archived=True)
        self.assertEqual([s.sid for s in self.sessions()], ["s1"])
        self.assertEqual(sorted(s.sid for s in self.sessions(archived=True)), ["s1", "s2"])

    # ---- 标题

    def test_title_uses_last_index_line(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="首句")])
        self.index([
            {"id": "s1", "thread_name": "旧标题", "updated_at": "2026-10-15T10:00:00.000Z"},
            {"id": "s1", "thread_name": "新标题", "updated_at": "2026-10-15T10:05:00.000Z"},
        ])
        self.assertEqual(self.sessions()[0].title, "新标题",
                         "索引是追加式的,必须取最后一行,否则改名后读到旧标题")

    def test_title_falls_back_to_first_prompt(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="没有索引时的首句")])
        self.assertEqual(self.sessions()[0].title, "没有索引时的首句")

    # ---- 事件 / 用量

    def test_item_kinds_map_to_events(self):
        self.conv("a", [
            cmeta("s1"),
            citem("UserMessage", text="我问的"),
            citem("CommandExecution", command=["/bin/zsh", "-lc", "ls"]),
            citem("AgentMessage", text="它答的"),
            citem("Reasoning", text=None, summary_text=["想了下"]),
        ])
        s = self.sessions()[0]
        kinds = [(k, t) for k, t in chatmgr.iter_events(s)]
        self.assertEqual([k for k, _ in kinds], ["user", "tool", "assistant"],
                         "默认不出 thinking;ContextCompaction 之类一律忽略")
        self.assertIn("ls", kinds[1][1])
        full = [k for k, _ in chatmgr.iter_events(s, full=True)]
        self.assertIn("thinking", full)

    def test_token_mapping_excludes_cached(self):
        self.conv("a", [cmeta("s1"), cusage(inp=1000, cached=900, out=50, reasoning=10)])
        s = self.sessions()[0]
        self.assertEqual(s.tokens["input"], 100, "input 要减掉缓存:Codex 的 input_tokens 是含缓存的")
        self.assertEqual(s.tokens["cache_read"], 900)
        self.assertEqual(s.tokens["output"], 50)
        self.assertEqual(s.tokens["thinking"], 10)
        self.assertEqual(s.tok_io, 150)

    def test_session_without_usage_is_flagged(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        s = self.sessions()[0]
        self.assertFalse(s.has_usage, "没记 token 的会话要能区分于『真的是 0』")
        _, out = self.run_cli("list", "-S", "codex")
        self.assertIn("—", out)

    def test_windows_cwd_short_project(self):
        self.conv("a", [cmeta("s1", cwd="E:\\work\\projB"), citem("UserMessage", text="x")])
        s = self.sessions()[0]
        self.assertEqual(s.project, "E:\\work\\projB")
        self.assertEqual(s.short_project, "projB",
                         "反斜杠路径在 macOS 上 Path().name 会把整串当文件名")

    # ---- 读命令

    def test_list_show_search_run(self):
        self.conv("a", [cmeta("s1", cwd="/Users/me/aaa"), citem("UserMessage", text="关键词在这"),
                        citem("AgentMessage", text="回复里也有关键词")])
        code, out = self.run_cli("list", "-S", "codex")
        self.assertEqual(code, 0)
        self.assertIn("aaa", out)
        code, out = self.run_cli("show", "s1", "-S", "codex")
        self.assertIn("关键词在这", out)
        self.assertIn("[Codex]", out, "回复的标签要用源自己的名字,不能写死 Claude")
        _, out = self.run_cli("search", "关键词", "-S", "codex")
        self.assertIn("Codex:", out)

    # ---- 删 / 改名 / 续聊

    def test_rm_noninteractive_never_touches_codex(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        code, out = self.run_cli("rm", "s1", "-S", "codex")
        self.assertIn("非交互终端", out)
        self.assertEqual(self.called(), [], "非 TTY 下一律只预览,连 codex 都不能调")
        self.assertTrue((self.codex / "sessions/2026/10/15/a.jsonl").exists())

    def test_rm_force_delegates_to_codex_delete(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        code, out = self.run_cli("rm", "s1", "-f", "-S", "codex")
        self.assertEqual(code, 0)
        # 必须带 --force:codex delete 自带交互确认,而我们是子进程没有 TTY
        self.assertEqual(self.called(), ["delete", "--force", "s1"])

    def test_rm_force_also_clears_desktop_catalog(self):
        """`codex delete` 不清桌面应用的会话目录 —— 不补这一刀,侧边栏会留个点不开的空壳。"""
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        self.assertEqual(self.in_catalog("s1"), 1)
        code, out = self.run_cli("rm", "s1", "-f", "-S", "codex")
        self.assertEqual(code, 0)
        self.assertEqual(self.in_catalog("s1"), 0, "删会话时要把侧边栏目录里的空壳一起清掉")

    def test_archive_keeps_desktop_catalog(self):
        """归档不是删除,侧边栏那条得留着。"""
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        chatmgr.SOURCES["codex"].delete(self.sessions()[0], backup=True)
        self.assertEqual(self.in_catalog("s1"), 1)

    def test_rm_backup_delegates_to_codex_archive(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        s = self.sessions()[0]
        note = chatmgr.SOURCES["codex"].delete(s, backup=True)
        self.assertEqual(self.called(), ["archive", "s1"])
        self.assertIn("归档", note)

    def test_rename_writes_state_db_and_mirrors_to_index(self):
        """真身在状态库 —— 只往 session_index.jsonl 追加是没用的(实测 Codex 认不到)。"""
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="首句")], db_name="状态库旧名")
        code, out = self.run_cli("rename", "s1", "我改的", "-S", "codex")
        self.assertEqual(code, 0)
        self.assertIn("我改的", out)
        self.assertEqual(self.state_name("s1"), "我改的")
        self.assertIn("我改的", (self.codex / "session_index.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(self.sessions()[0].title, "我改的")

    def test_title_prefers_state_db_over_index(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="首句")], db_name="状态库的名字")
        self.index([{"id": "s1", "thread_name": "索引里的名字", "updated_at": "x"}])
        self.assertEqual(self.sessions()[0].title, "状态库的名字",
                         "Codex 认的是状态库,所以显示也以它为准")

    def test_title_falls_back_to_index_when_state_name_empty(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="首句")])  # 状态库里 name 为空
        self.index([{"id": "s1", "thread_name": "索引里的名字", "updated_at": "x"}])
        self.assertEqual(self.sessions()[0].title, "索引里的名字")

    def test_rename_without_state_db_errors(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        self.db.unlink()
        code, out = self.run_cli("rename", "s1", "改名", "-S", "codex")
        self.assertNotEqual(code, 0)
        self.assertIn("状态库", out)

    def test_rename_refuses_archived(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")], archived=True)
        code, out = self.run_cli("rename", "s1", "改名", "-a", "-S", "codex")
        self.assertNotEqual(code, 0)
        self.assertIn("归档", out)

    def test_resume_argv(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="x")])
        argv = chatmgr.SOURCES["codex"].resume_argv(self.sessions()[0])
        self.assertEqual(argv, [str(self.stub), "resume", "s1"])

    def test_reads_work_without_codex_binary(self):
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="读我")])
        os.environ["CHATMGR_CODEX_BIN"] = ""  # 明确当作"没有 codex"
        self.assertIsNone(chatmgr.codex_bin())
        code, out = self.run_cli("list", "-S", "codex")
        self.assertEqual(code, 0)
        self.assertIn("1 个会话", out)
        code, out = self.run_cli("rm", "s1", "-f", "-S", "codex")
        self.assertNotEqual(code, 0)
        self.assertIn("找不到 codex 命令", out)
        self.assertEqual(self.called(), [])

    def test_codex_home_env_is_honored(self):
        """Codex 自己的 $CODEX_HOME 也要认:否则用户挪走数据后,我们读一处、删另一处。"""
        os.environ.pop("CHATMGR_CODEX_DIR", None)
        os.environ["CODEX_HOME"] = str(self.codex)
        self.assertEqual(chatmgr.codex_dir(), self.codex)
        os.environ["CHATMGR_CODEX_DIR"] = str(self.codex)  # 我们的变量优先级更高
        self.assertEqual(chatmgr.codex_dir(), self.codex)

    def test_cgr_groups_by_source(self):
        """不带 -S 时默认是「全部」:按源分段,段内各自按时间倒序(测试里 argv[0] 认不出命令名,也归全部)。"""
        self.live("aaaa1111", [user("claude 的会话")])
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="codex 的会话")])
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("2 个会话", out)
        self.assertIn("Claude (1)", out, "两个源各一段,段标题写明来源和条数")
        self.assertIn("Codex (1)", out)

    def test_grouped_numbering_is_sequential(self):
        """分段后序号必须是页面顺序 —— 跳号会让人以为坏了。"""
        self.live("aaaa1111", [user("claude 1")])
        self.live("bbbb2222", [user("claude 2")])
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="codex 1")])
        _, out = self.run_cli("list")
        nums = [ln.split()[0] for ln in out.splitlines()
                if re.match(r"^\d+\s", ln) and "个会话" not in ln]  # 只认表格行,别把末尾"3 个会话"算进来
        self.assertEqual(nums, ["1", "2", "3"], f"序号应是连续页面顺序,实际 {nums}")

    def test_single_source_list_keeps_old_shape(self):
        """只看一个源时,输出和以前一样 —— 不分段、也不多列。"""
        self.live("aaaa1111", [user("只有 claude")])
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="codex 的会话")])
        code, out = self.run_cli("list", "-S", "claude")
        self.assertEqual(code, 0)
        self.assertIn("1 个会话", out)
        self.assertNotIn("──", out, "单源不该出现段标题")

    def test_dash_s_narrows_to_one_source(self):
        self.live("aaaa1111", [user("claude 的会话")])
        self.conv("a", [cmeta("s1"), citem("UserMessage", text="codex 的会话")])
        self.assertIn("1 个会话", self.run_cli("list", "-S", "codex")[1])
        self.assertIn("1 个会话", self.run_cli("list", "-S", "claude")[1])

    def test_help_lists_the_entry_points(self):
        """`-h` 里必须能看到 cla / cod —— 否则没人知道它们存在。清单是从
        SOURCES_BY_PROG 反推的,以后加新 agent 会自动出现。"""
        out = chatmgr.build_parser().format_help()
        self.assertIn("cla", out)
        self.assertIn("cod", out)
        self.assertIn("Claude", out)
        self.assertIn("-S", out)

    # ---- 命令名决定默认源

    def test_prog_name_selects_sources(self):
        old = sys.argv[0]
        try:
            sys.argv[0] = "cod"
            self.assertEqual(chatmgr.default_source_keys(), ["codex"])
            sys.argv[0] = "cla"
            self.assertEqual(chatmgr.default_source_keys(), ["claude"])
            sys.argv[0] = "opc"
            self.assertEqual(chatmgr.default_source_keys(), ["opencode"])
            sys.argv[0] = "cgr"
            self.assertEqual(chatmgr.default_source_keys(), ["claude", "codex", "opencode"],
                             "cgr 是「全部」—— 一次搜遍所有 agent 才是它的初衷")
            sys.argv[0] = "/some/where/chatmgr.py"  # picker 自己调自己时的 argv[0]
            self.assertEqual(chatmgr.default_source_keys(), ["claude", "codex", "opencode"])
        finally:
            sys.argv[0] = old

    def test_dash_s_can_pick_and_combine(self):
        class A:
            source = None
        a = A()
        a.source = ["claude"]
        self.assertEqual(chatmgr.selected_source_keys(a), ["claude"])
        a.source = ["codex,claude", "codex"]  # 逗号 + 重复,去重且保持顺序
        self.assertEqual(chatmgr.selected_source_keys(a), ["codex", "claude"])


# ---------------------------------------------------------------- OpenCode 数据源


def otext(t):
    return {"type": "text", "text": t}


def oreason(t):
    return {"type": "reasoning", "text": t}


def otool(name, inp, result=None):
    return {"type": "tool", "name": name, "state": {
        "status": "completed", "input": inp,
        "content": ([{"type": "text", "text": result}] if result else [])}}


def ouser(text, ts=1792143313072):
    return {"time": {"created": ts}, "text": text, "files": [], "agents": []}


def oassistant(parts, *, tokens=None, ts=1792143313082, model="m1"):
    return {"time": {"created": ts, "completed": ts + 1500}, "agent": "build",
            "model": {"id": model}, "content": parts, "finish": "stop", "tokens": tokens or {}}


class OpenCodeTest(BaseTest):
    """OpenCode 源。库是真的 SQLite(在临时目录),但删/导出走桩脚本 —— 绝不真跑 opencode。"""

    def setUp(self):
        super().setUp()
        self.oc = self.opencode_home  # 基类已建好并指向它
        self.calls = self.tmp_root / "oc-calls.log"
        stub = self.tmp_root / "opencode-stub.sh"
        stub.write_text(
            f'#!/bin/sh\necho "$@" >> "{self.calls}"\n'
            'if [ "$1" = "session" ] && [ "$2" = "export" ]; then echo \'{"ok":true}\'; fi\n'
            'exit 0\n', encoding="utf-8")
        stub.chmod(0o755)
        os.environ["CHATMGR_OPENCODE_BIN"] = str(stub)
        self.db = self.oc / "opencode.db"
        con = sqlite3.connect(self.db)
        con.execute("""create table session_v2 (id text primary key, parent_id text,
            directory text, title text, time_created integer, time_updated integer,
            time_archived integer, tokens_input integer, tokens_output integer,
            tokens_reasoning integer, tokens_cache_read integer, tokens_cache_write integer)""")
        con.execute("""create table session_message (id text, session_id text, type text,
            seq integer, time_created integer, data text)""")
        con.commit()
        con.close()

    def sess(self, sid, *, directory="/Users/me/proj", title=None, archived=None,
             created=1792143313038, updated=1792143334684):
        con = sqlite3.connect(self.db)
        with con:
            con.execute("insert or replace into session_v2 values (?,?,?,?,?,?,?,0,0,0,0,0)",
                        (sid, None, directory, title, created, updated, archived))
        con.close()

    def msgs(self, sid, rows):
        con = sqlite3.connect(self.db)
        with con:
            for i, (typ, data) in enumerate(rows):
                con.execute("insert into session_message values (?,?,?,?,?,?)",
                            (f"msg{i}", sid, typ, i + 1, 1792143313000 + i,
                             json.dumps(data, ensure_ascii=False)))
        con.close()

    def called(self):
        return self.calls.read_text(encoding="utf-8").split() if self.calls.exists() else []

    def sessions(self, archived=False):
        return chatmgr.SessionStore(
            include_archived=archived, sources=[chatmgr.SOURCES["opencode"]]).sessions()

    def one(self, sid, rows, **kw):
        self.sess(sid, **kw)
        self.msgs(sid, rows)
        return self.sessions()[0]

    # ---- 发现 / 标题 / 用量

    def test_title_from_column_then_first_prompt(self):
        self.sess("s1", title="库里的标题")
        self.msgs("s1", [("user", ouser("首句提问")), ("assistant", oassistant([otext("回答")]))])
        self.sess("s2")
        self.msgs("s2", [("user", ouser("没有标题时用首句"))])
        got = {s.sid: s for s in self.sessions()}
        self.assertEqual(got["s1"].title, "库里的标题")
        self.assertEqual(got["s2"].title, "没有标题时用首句", "title 为空要回退到首句")

    def test_aggregates_and_token_mapping(self):
        s = self.one("s1", [
            ("user", ouser("问题一")),
            ("assistant", oassistant([oreason("想想"), otext("回答一"), otool("bash", {"command": "ls"}, "out")],
                                     tokens={"input": 1000, "output": 50, "reasoning": 20,
                                             "cache": {"read": 900, "write": 0}}),
            ),
            ("idle", {"time": {"created": 1}, "outcome": "succeeded"}),
            ("user", ouser("问题二")),
        ])
        self.assertEqual((s.prompts, s.replies), (2, 1))
        self.assertEqual(s.first_prompt, "问题一")
        self.assertEqual(s.tools["bash"], 1)
        self.assertEqual(s.tokens["input"], 1000, "OpenCode 的 input **不含**缓存,直接用(拿 stats 对过账)")
        self.assertEqual(s.tokens["cache_read"], 900)
        self.assertEqual(s.tokens["thinking"], 20)
        self.assertEqual(s.tok_io, 1050)
        self.assertTrue(s.has_usage)

    def test_session_without_usage_flagged(self):
        s = self.one("s1", [("user", ouser("没跑成功的对话"))])
        self.assertFalse(s.has_usage, "失败/没产出的会话要能区分于『真的是 0』")

    def test_archived_only_with_flag(self):
        self.sess("s1")
        self.msgs("s1", [("user", ouser("活的"))])
        self.sess("s2", archived=1792143000000)
        self.msgs("s2", [("user", ouser("归档的"))])
        self.assertEqual([s.sid for s in self.sessions()], ["s1"])
        self.assertEqual(sorted(s.sid for s in self.sessions(archived=True)), ["s1", "s2"])

    # ---- 事件流

    def test_events_map_to_kinds(self):
        s = self.one("s1", [
            ("user", ouser("我问的")),
            ("assistant", oassistant([
                oreason("想了下"),
                otext("它答的"),
                otool("websearch", {"query": "查一下"}, "搜索结果正文"),
            ])),
            ("model-switched", {"time": {"created": 1}, "model": {"id": "m2"}}),
        ])
        kinds = [k for k, _ in chatmgr.iter_events(s)]
        self.assertEqual(kinds, ["user", "assistant", "tool"], "idle / model-switched 要跳过;默认不出 thinking")
        _, tool_text = [e for e in chatmgr.iter_events(s) if e[0] == "tool"][0]
        self.assertIn("websearch", tool_text)
        self.assertIn("query=查一下", tool_text)
        full = [k for k, _ in chatmgr.iter_events(s, full=True)]
        self.assertIn("thinking", full)
        self.assertIn("tool_result", full, "--full 才出工具返回")

    # ---- 写操作

    def test_rm_delegates_to_opencode_delete(self):
        self.sess("s1")
        self.msgs("s1", [("user", ouser("x"))])
        code, out = self.run_cli("rm", "s1", "-f", "-S", "opencode")
        self.assertEqual(code, 0)
        self.assertEqual(self.called(), ["session", "delete", "s1"])

    def test_rm_noninteractive_never_calls_opencode(self):
        self.sess("s1")
        self.msgs("s1", [("user", ouser("x"))])
        code, out = self.run_cli("rm", "s1", "-S", "opencode")
        self.assertIn("非交互终端", out)
        self.assertEqual(self.called(), [], "非 TTY 只预览,连 opencode 都不能调")

    def test_rm_backup_exports_before_deleting(self):
        self.sess("s1")
        self.msgs("s1", [("user", ouser("x"))])
        note = chatmgr.SOURCES["opencode"].delete(self.sessions()[0], backup=True)
        self.assertEqual(self.called(),
                         ["session", "export", "s1", "session", "delete", "s1"],
                         "先导出、再删")
        self.assertIn("导出备份", note)

    def test_rename_writes_session_title(self):
        self.sess("s1", title="旧标题")
        self.msgs("s1", [("user", ouser("x"))])
        code, out = self.run_cli("rename", "s1", "我改的", "-S", "opencode")
        self.assertEqual(code, 0)
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        self.assertEqual(con.execute("select title from session_v2 where id='s1'").fetchone()[0], "我改的")
        con.close()
        self.assertEqual(self.sessions()[0].title, "我改的")

    def test_resume_argv(self):
        self.sess("s1")
        self.msgs("s1", [("user", ouser("x"))])
        argv = chatmgr.SOURCES["opencode"].resume_argv(self.sessions()[0])
        self.assertEqual(argv[1:], ["--session", "s1"])

    def test_missing_binary_reads_fine_writes_fail(self):
        self.sess("s1")
        self.msgs("s1", [("user", ouser("读我"))])
        os.environ["CHATMGR_OPENCODE_BIN"] = ""
        self.assertIsNone(chatmgr.opencode_bin())
        self.assertIn("1 个会话", self.run_cli("list", "-S", "opencode")[1])
        code, out = self.run_cli("rm", "s1", "-f", "-S", "opencode")
        self.assertNotEqual(code, 0)
        self.assertIn("找不到 opencode 命令", out)

    def test_missing_db_is_not_an_error(self):
        """没装/没用过 OpenCode 时,不该报错、也不该拖累 cgr。"""
        self.db.unlink()
        self.assertEqual(self.sessions(), [])
        code, out = self.run_cli("list", "-S", "opencode")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
