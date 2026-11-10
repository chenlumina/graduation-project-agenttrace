"""临时脚本：诊断 v1.1 实验中 flash 在 kadane 上的失败。"""
import io
import json
import sqlite3
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
db = "experiments/exp-20261108-191635.db"
conn = sqlite3.connect(db)
aid = conn.execute(
    "select attempt_id from attempts where task_id='fix_kadane_negative' "
    "and json_extract(meta,'$.model')='mimo-v2.6-flash'"
).fetchone()[0]
subprocess.run([sys.executable, "atp.py", "metrics", aid, "--db", db], check=True)
for typ, data in conn.execute(
    "select type, data from events where attempt_id=? order by seq", (aid,)
):
    d = json.loads(data)
    if typ == "llm_call":
        print("== LLM 回复（完整）==")
        print(d["response"][:800])
    elif typ == "error":
        print("== 错误 ==", d["message"][:400])
    elif typ == "tool_call":
        print("== 工具 ==", d["tool"], d["status"])
