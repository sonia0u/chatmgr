#!/bin/bash
# 列出 Claude Code 对话：时间 + 大小 + 第一句话
# 用法: ./list-sessions.sh [项目文件夹名]
# 默认项目文件夹名从参数取，不给就用当前工作目录转换

PROJECTS=/Users/sonia/.claude/projects
DIR="$1"

# 没给参数就自动找：优先当前工作目录对应的，否则找名字里含 AI-Chats 的
if [ -z "$DIR" ]; then
  GUESS="$PROJECTS/$(pwd | sed 's|/|-|g')"
  if [ -d "$GUESS" ]; then
    DIR="$GUESS"
  else
    DIR=$(ls -1d "$PROJECTS"/*AI-Chats* 2>/dev/null | head -1)
  fi
fi

if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
  echo "找不到文件夹，可用的有:"
  ls -1 "$PROJECTS"
  echo
  echo "用法: bash $0 /Users/sonia/.claude/projects/<上面某个名字>"
  exit 1
fi

echo "项目文件夹: $DIR"

python3 -I -c '
import json, glob, os, sys, datetime

d = sys.argv[1]
files = sorted(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime, reverse=True)

if not files:
    print("这个文件夹里没有 .jsonl 对话文件")
    sys.exit(0)

print(f"{len(files)} 个对话，新的在上面\n")
for f in files:
    t = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
    kb = os.path.getsize(f) // 1024
    first = "(认不出第一句)"
    for line in open(f, encoding="utf-8", errors="ignore"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "user":
            continue
        c = (rec.get("message") or {}).get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c
                         if isinstance(x, dict) and x.get("type") == "text")
        if isinstance(c, str) and c.strip():
            s = " ".join(c.split())
            if s.startswith("<") or "system-reminder" in s or "Caveat:" in s:
                continue
            first = s[:60]
            break
    print(f"{t}  {kb:>6}KB  {first}")
    print(f"            {os.path.basename(f)}")
' "$DIR"
